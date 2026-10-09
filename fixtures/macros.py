"""M21宏独立准备、跨组批量执行及明确归属后的清理。"""

from copy import deepcopy
from pathlib import Path

import yaml

from api.macros import MacrosAPI
from api.tickets import TicketsAPI
from assertions.tickets import FIELDS, check, ticket_value
from fixtures.assignment import assignment_preflight
from fixtures.config_checks import assert_ownership, new_object_id, pure_actor
from fixtures.pilot import PilotCase
from fixtures.preflight import PreflightError, list_rows, preflight, unique
from fixtures.ticket import CleanupError, valid_id
from fixtures.users import preflight_users
from support.config import ConfigError, _ConfigLoader

CASE_IDS = frozenset({"TC_187", "TC_188", "TC_189", "TC_190"})
ROOT = Path(__file__).resolve().parents[1]


def load_macro_cases(path: Path | None = None) -> dict:
    """严格核对Excel四条宏输入，动态值仅使用已定义逻辑名称。"""
    try:
        rows = yaml.load(
            (path or ROOT / "data/cases/macros.yaml").read_text(encoding="utf-8-sig"),
            Loader=_ConfigLoader,
        )
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        for case_id, data in rows.items():
            payload = {"active": True, "ux_flow_next_up": "none"}
            perform = {"ticket.priority_id": {"value": "high"}}
            if case_id != "TC_189":
                payload["group_ids"] = ["G1"]
            if case_id == "TC_189":
                perform = {"ticket.state_id": {"value": "open"}}
            if case_id == "TC_188":
                perform["ticket.state_id"] = {"value": "open"}
            payload["perform"] = perform
            expected = {"status_code": 201}
            if case_id == "TC_188":
                expected = {"status_code": 200}
            elif case_id == "TC_189":
                expected = {"status_class": 4, "error_kind": "permission"}
            elif case_id == "TC_190":
                expected = {
                    "status_code": 422,
                    "error": "Macro group restrictions do not cover all tickets",
                }
            required = {
                "actor": {"TC_189": "A1", "TC_190": "A12"}.get(case_id, "AD"),
                "name_template": "{run_id}_{case_id}_macro",
                "input": payload,
                "expected": expected,
            }
            if case_id == "TC_188":
                required["update"] = {
                    "perform": {
                        "ticket.priority_id": {"value": "low"},
                        "ticket.state_id": {"value": "open"},
                    }
                }
            if data != required or data["input"]["active"] is not True:
                raise ValueError
        return deepcopy(rows)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise ConfigError("宏场景数据不符合原始用例") from None


def macro_payload(value: dict, mapping: dict) -> dict:
    """只将显式组及动作逻辑值转换为当前环境ID字符串。"""
    payload = deepcopy(value)
    if "group_ids" in payload:
        payload["group_ids"] = [mapping["group_id"]]
    for field, action in payload["perform"].items():
        key = "state_id" if field == "ticket.state_id" else f"{action['value']}_priority_id"
        action["value"] = str(mapping[key])
    return payload


def ticket_data() -> dict:
    """复用已验收创建数据，独立设置190与open状态。"""
    data = yaml.load(
        (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8-sig"),
        Loader=_ConfigLoader,
    )
    data.update(case_id="TC_190", state_name="open")
    return data


def preflight_macros(ad, maintenance, agent, actor, context, db, case_id: str) -> dict:
    """先核实管理身份、真实业务权限和动作映射，再允许任何写入。"""
    db.check_macro_access()
    mapping = preflight_users(ad, maintenance, context, db)
    data = ticket_data()
    if case_id == "TC_190":
        mapping.update(
            assignment_preflight(
                agent, maintenance, actor, context.config, context.assignment_config, data, "TC_061"
            )
        )
        pure_actor(actor, maintenance, context.assignment_config.transfer_login, "Agent", db)
    else:
        mapping.update(preflight(agent, maintenance, context.config, data))
    if case_id == "TC_189":
        pure_actor(actor, maintenance, context.config.agent_login, "Agent", db)
    priorities = list_rows(maintenance, "api/v1/ticket_priorities")
    mapping["high_priority_id"] = unique(priorities, "name", "3 high")["id"]
    mapping["low_priority_id"] = unique(priorities, "name", "1 low")["id"]
    if len({mapping["priority_id"], mapping["high_priority_id"], mapping["low_priority_id"]}) != 3:
        raise PreflightError("宏优先级映射未形成独立高、中、低值") from None
    mapping["creator_id"] = mapping["agent_id"] if case_id == "TC_189" else mapping["admin_id"]
    return mapping


def assert_macro(value: object, expected: dict, object_id: int, creator_id: int) -> None:
    """宏动作与字段完整比较，ID不靠字符串与数字宽松等价。"""
    __tracebackhide__ = True
    fields = {"id": object_id, "created_by_id": creator_id, **expected}
    check(
        isinstance(value, dict) and all(value.get(key) == val for key, val in fields.items()),
        "宏字段或动作配置不一致",
    )
    check(value["active"] is True, "宏启用状态类型不符")


class MacroCase:
    """只管理本条宏及190两张临时工单，不操作既有宏。"""

    def __init__(self, ad, maintenance, actor, context, mapping, data, recorder, db) -> None:
        """记录明确客户端和资源状态，不在构造时写入环境。"""
        self.admin, self.maintenance, self.actor = (
            MacrosAPI(ad),
            MacrosAPI(maintenance),
            MacrosAPI(actor),
        )
        self.ticket_actor = TicketsAPI(actor)
        self.maintenance_client = maintenance
        self.run, self.mapping, self.data, self.recorder, self.db = (
            context.run,
            mapping,
            deepcopy(data),
            recorder,
            db,
        )
        self.case_id = data["case_id"]
        self.marker = self.name = f"{self.run.run_id}_{self.case_id}_macro"
        self.payload = {"name": self.name, **macro_payload(data["input"], mapping)}
        self.creator_id = mapping["creator_id"]
        self.key = self.object_id = None
        self.write_unknown = self.integrity_unknown = False
        self.baseline = []
        self.before = self.tickets_before = None
        self.tickets = []
        self.mass_attempted = False

    def snapshot(self, label: str) -> dict:
        """在断言前保存维护接口、DB对象、组关联和集合。"""
        result = self.maintenance.get(self.object_id)
        value = {
            "status": result.response.status_code,
            "api": result.get_json(),
            "db": self.db.macro(self.object_id),
            "groups": self.db.macro_groups(self.object_id),
            "ids": self.db.macro_ids(),
        }
        self.recorder.record(label, value)
        return value

    def owned(self) -> dict:
        """只有本条新增ID及两层归属一致才允许修改或删除。"""
        if not valid_id(self.object_id) or self.object_id in self.baseline:
            raise CleanupError("宏不是本条新增资源") from None
        value = self.snapshot("macro-ownership")
        assert_ownership(value, self.object_id, self.name, self.creator_id)
        return value

    def create(self):
        """记录创建意图后只发一次请求，189实际使用A1。"""
        if self.key is not None:
            raise CleanupError("宏创建不可重复提交") from None
        self.baseline, self.before = self.db.macro_ids(), self.db.macro_inventory()
        self.tickets_before = self.db.ticket_inventory()
        if self.db.macros_named(self.name):
            raise PreflightError("本轮宏名称已经存在") from None
        self.recorder.ensure_complete()
        self.key = self.run.config_intent(
            "macro", self.case_id, self.marker, self.name, self.creator_id
        )
        self.write_unknown = True
        result = (self.actor if self.case_id == "TC_189" else self.admin).create(self.payload)
        self.write_unknown = result.response.status_code >= 500
        self.integrity_unknown = True
        after = self.db.macro_ids()
        self.recorder.record(
            "macro-create-inventory",
            {
                "before": self.baseline,
                "after": after,
                "inventory": self.db.macro_inventory(),
                "status": result.response.status_code,
            },
        )
        if self.write_unknown:
            raise CleanupError("宏创建写入结果未知") from None
        self.object_id = new_object_id(result, self.baseline, after)
        if self.object_id is not None:
            self.owned()
            self.run.config_created(self.key, self.object_id)
        self.integrity_unknown = False
        return result

    def assert_unchanged_others(self) -> None:
        """原宏及本轮工单准备完成后的集合不得被配置操作改变。"""
        check(
            self.db.macro_inventory(exclude_id=self.object_id) == self.before, "其他宏配置发生变化"
        )
        check(self.db.ticket_inventory() == self.tickets_before, "宏操作改变了业务工单")
        expected = set(self.baseline) | ({self.object_id} if self.object_id is not None else set())
        check(set(self.db.macro_ids()) == expected, "宏集合出现复制或遗漏")

    def update(self):
        """188只提交perform，不补齐名称、组或启用字段。"""
        self.owned()
        self.write_unknown = True
        result = self.admin.update(self.object_id, macro_payload(self.data["update"], self.mapping))
        self.write_unknown = result.response.status_code >= 500
        return result

    def prepare_tickets(self) -> None:
        """M分别创建两组工单，实际负责人A12且初始优先级与宏不同。"""
        if self.case_id != "TC_190" or self.tickets or self.key is not None:
            raise PreflightError("跨组工单只能在190创建宏之前准备一次") from None
        for index, group_id in enumerate(
            (self.mapping["group_id"], self.mapping["target_group_id"]), 1
        ):
            mapping = {
                **self.mapping,
                "group_id": group_id,
                "agent_id": self.mapping["transfer_id"],
                "creator_id": self.mapping["maintenance_id"],
            }
            data = ticket_data()
            data["title"] += f" G{index}"
            data["article"]["body"] += f" G{index}"
            ticket = PilotCase(
                self.maintenance_client,
                self.maintenance_client,
                self.run,
                mapping,
                data,
                self.recorder,
                self.db,
            )
            original_marker = ticket.marker
            ticket.marker = f"{original_marker}_G{index}"
            ticket.payload["title"] = ticket.payload["title"].replace(
                original_marker, ticket.marker
            )
            ticket.payload["article"]["body"] = ticket.payload["article"]["body"].replace(
                original_marker, ticket.marker
            )
            self.tickets.append(ticket)
            result = ticket.create()
            ticket.write_unknown = result.response.status_code >= 500
            check(result.response.status_code == 201, "跨组宏前置工单创建失败")
            row = ticket_value(self.ticket_actor.read(ticket.ticket_id))
            expected = {
                key: ticket.payload[key]
                for key in (
                    "title",
                    "customer_id",
                    "group_id",
                    "owner_id",
                    "state_id",
                    "priority_id",
                )
            }
            expected["created_by_id"] = ticket.creator_id
            for value in (row, self.db.ticket(ticket.ticket_id)):
                check(
                    isinstance(value, dict) and all(value.get(k) == v for k, v in expected.items()),
                    "跨组宏前置工单字段不符",
                )
            check(
                row.get("article_count") == 1
                and row.get("priority_id") != self.mapping["high_priority_id"],
                "前置工单必须可观察宏优先级变化",
            )

    def ticket_snapshot(self, label: str) -> list[dict]:
        """两张工单保存业务字段与完整数据库和文章快照。"""
        values = []
        for ticket in self.tickets:
            api = ticket_value(self.ticket_actor.read(ticket.ticket_id))
            values.append(
                {
                    "api": {field: api[field] for field in FIELDS},
                    "db": self.db.ticket(ticket.ticket_id),
                    "articles": self.db.articles(ticket.ticket_id),
                }
            )
        self.recorder.record(label, values)
        return values

    def mass_macro(self):
        """A12向一次批量请求提交两张ID，任何未知结果均阻断冲突清理。"""
        if self.case_id != "TC_190" or len(self.tickets) != 2 or self.mass_attempted:
            raise PreflightError("跨组宏请求必须含独立两工单且只执行一次") from None
        self.owned()
        self.recorder.ensure_complete()
        self.mass_attempted = self.write_unknown = True
        for ticket in self.tickets:
            ticket.write_unknown = True
        result = self.ticket_actor.mass_macro(
            self.object_id, [ticket.ticket_id for ticket in self.tickets]
        )
        self.write_unknown = result.response.status_code >= 500
        for ticket in self.tickets:
            ticket.write_unknown = self.write_unknown
        return result

    def cleanup_macro(self) -> None:
        """先删除宏并核对关联清零，再由独立工单生命周期处理两工单。"""
        if self.key is None:
            return
        try:
            self.recorder.ensure_complete()
            if self.write_unknown or self.integrity_unknown:
                raise CleanupError("宏写入未知或归属不明")
            if self.object_id is None:
                if (
                    self.case_id != "TC_189"
                    or self.db.macro_ids() != self.baseline
                    or self.db.macro_inventory() != self.before
                ):
                    raise CleanupError("宏负向请求无法证明无新增")
                check(self.db.ticket_inventory() == self.tickets_before, "宏负向请求改变了业务工单")
                self.run.config_absent(self.key)
                return
            self.owned()
            self.recorder.ensure_complete()
            self.write_unknown = True
            result = self.maintenance.delete(self.object_id)
            self.write_unknown = result.response.status_code >= 500
            if (
                result.response.status_code != 200
                or self.maintenance.get(self.object_id).response.status_code != 404
                or self.db.macro(self.object_id) is not None
                or self.db.macro_groups(self.object_id)
            ):
                raise CleanupError("宏API删除或关联清理失败")
            if self.db.macro_ids() != self.baseline or self.db.macro_inventory() != self.before:
                raise CleanupError("宏清理后原配置基线不符")
            if self.case_id != "TC_190" and self.db.ticket_inventory() != self.tickets_before:
                raise CleanupError("宏配置清理后业务工单基线不符")
            self.recorder.ensure_complete()
            self.run.cleaned(self.key)
            self.recorder.record("macro-cleanup", {"id": self.object_id, "result": "cleaned"})
        except Exception:
            self.run.attention(
                self.key, "create_unknown" if self.write_unknown else "cleanup_failed"
            )
            raise CleanupError("宏清理未完成，已保留定位信息") from None

    def cleanup(self) -> None:
        """未知批量写入时停止所有冲突清理，否则尽力处理独立资源。"""
        errors = []
        try:
            self.cleanup_macro()
        except Exception as error:
            errors.append(error)
        for ticket in reversed(self.tickets):
            try:
                ticket.cleanup()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("宏或关联临时工单清理未完成", errors) from None
