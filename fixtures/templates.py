"""M19模板独立数据、最小字段更新和归属确认后的API清理。"""

from copy import deepcopy
from pathlib import Path

import yaml

from api.templates import TemplatesAPI
from assertions.tickets import check
from fixtures.config_checks import assert_ownership, new_object_id, pure_actor
from fixtures.preflight import PreflightError
from fixtures.ticket import CleanupError, valid_id
from fixtures.users import preflight_users
from support.config import ConfigError, _ConfigLoader

CASE_IDS = frozenset({"TC_159", "TC_160", "TC_161", "TC_162"})


def load_template_cases(path: Path | None = None) -> dict:
    """锁定四条Excel输入，160名称保留精确一个空格。"""
    path = path or Path(__file__).resolve().parents[1] / "data/cases/templates.yaml"
    try:
        rows = yaml.load(path.read_text(encoding="utf-8"), Loader=_ConfigLoader)
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        for case_id, data in rows.items():
            fields = {"actor", "input", "expected"}
            expected_input = {
                "active": True,
                "options": {
                    "ticket.title": {
                        "value": {"TC_159": "接口模板标题", "TC_160": "标题"}.get(
                            case_id, "模板测试标题"
                        )
                    }
                },
            }
            if case_id == "TC_160":
                expected_input["name"] = " "
            else:
                fields.add("name_template")
                if data["name_template"] != "{run_id}_{case_id}_template":
                    raise ValueError
            if case_id == "TC_161":
                fields.add("update")
                if data["update"] != {"options": {"ticket.title": {"value": "接口修改后的标题"}}}:
                    raise ValueError
            expected = {
                "TC_159": {"status_code": 201},
                "TC_160": {"status_class": 4, "error_kind": "name_required"},
                "TC_161": {"status_code": 200},
                "TC_162": {"status_class": 4, "error_kind": "permission"},
            }[case_id]
            if (
                set(data) != fields
                or data["actor"] != ("A1" if case_id == "TC_162" else "AD")
                or data["input"] != expected_input
                or data["input"]["active"] is not True
                or data["expected"] != expected
            ):
                raise ValueError
        return deepcopy(rows)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise ConfigError("模板场景数据不符合原始输入") from None


def preflight_templates(ad, maintenance, agent, context, db) -> dict:
    """写入前核实AD/M及可选的普通A1，不用认证失败冒充拒删。"""
    db.check_template_access()
    mapping = preflight_users(ad, maintenance, context, db)
    if agent is not None:
        mapping["agent_id"] = pure_actor(
            agent, maintenance, context.config.agent_login, "Agent", db
        )
    return mapping


def assert_template(value: object, expected: dict, object_id: int, creator_id: int) -> None:
    """模板名称、启用状态及options字典必须完整一致。"""
    __tracebackhide__ = True
    fields = {"id": object_id, "created_by_id": creator_id, **expected}
    check(
        isinstance(value, dict)
        and all(key in value and value[key] == val for key, val in fields.items()),
        "模板业务字段或options不一致",
    )
    check(value["active"] is expected["active"], "模板启用状态类型不符")


class TemplateCase:
    """只管理当前用例AD创建的模板，不复用既有模板。"""

    def __init__(self, ad, maintenance, agent, context, mapping, data, recorder, db) -> None:
        """运行时只注入唯一名称，160空格和options保持原样。"""
        self.admin, self.maintenance, self.agent = (
            TemplatesAPI(ad),
            TemplatesAPI(maintenance),
            TemplatesAPI(agent),
        )
        self.run, self.mapping, self.data, self.recorder, self.db = (
            context.run,
            mapping,
            deepcopy(data),
            recorder,
            db,
        )
        self.case_id = data["case_id"]
        self.marker = f"{self.run.run_id}_{self.case_id}_template"
        self.payload = deepcopy(data["input"])
        if "name_template" in data:
            self.payload["name"] = self.marker
        self.name = self.saved_name = self.payload["name"]
        self.creator_id = mapping["admin_id"]
        self.key = self.object_id = None
        self.write_unknown = self.integrity_unknown = False
        self.baseline = []
        self.before = self.tickets_before = None
        self.agent_delete_status = None

    def snapshot(self, label: str) -> dict:
        """在断言前保全维护API、数据库对象及配置集合。"""
        result = self.maintenance.get(self.object_id)
        value = {
            "status": result.response.status_code,
            "api": result.get_json(),
            "db": self.db.template(self.object_id),
            "ids": self.db.template_ids(),
        }
        self.recorder.record(label, value)
        return value

    def owned(self) -> dict:
        """本条新ID和API、DB共同匹配才允许管理或清理。"""
        if not valid_id(self.object_id) or self.object_id in self.baseline:
            raise CleanupError("模板不是本条新对象，禁止修改或删除") from None
        value = self.snapshot("template-ownership")
        assert_ownership(value, self.object_id, self.saved_name, self.creator_id)
        if self.case_id == "TC_160" and any(
            row.get("options") != self.payload["options"] or row.get("active") is not True
            for row in (value["api"], value["db"])
        ):
            raise CleanupError("空白名称模板缺少请求字段的归属证据") from None
        return value

    def create(self):
        """先登记意图再发送一次POST，错误响应的意外新增也要登记。"""
        if self.key is not None:
            raise CleanupError("模板创建不可重复提交") from None
        self.baseline = self.db.template_ids()
        self.before = self.db.template_inventory()
        self.tickets_before = self.db.ticket_inventory()
        if self.case_id != "TC_160" and self.db.templates_named(self.name):
            raise PreflightError("本轮模板名称已经存在") from None
        self.recorder.ensure_complete()
        self.key = self.run.config_intent(
            "template", self.case_id, self.marker, self.name, self.creator_id
        )
        self.write_unknown = True
        result = self.admin.create(self.payload)
        self.write_unknown = result.response.status_code >= 500
        self.integrity_unknown = True
        after = self.db.template_ids()
        self.recorder.record(
            "template-create-inventory",
            {
                "before": self.baseline,
                "after": after,
                "inventory": self.db.template_inventory(),
                "status": result.response.status_code,
            },
        )
        if self.write_unknown:
            raise CleanupError("模板创建写入结果未知") from None
        self.object_id = new_object_id(result, self.baseline, after)
        if self.object_id is not None:
            if self.case_id == "TC_160":
                value = self.snapshot("template-whitespace-created")
                response = result.get_json()
                actual_name = value["api"].get("name") if isinstance(value["api"], dict) else None
                if actual_name != self.name:
                    if (
                        not isinstance(actual_name, str)
                        or not isinstance(response, dict)
                        or response.get("id") != self.object_id
                        or response.get("name") != actual_name
                        or response.get("created_by_id") != self.creator_id
                        or response.get("options") != self.payload["options"]
                    ):
                        raise CleanupError("改名模板缺少响应新ID及请求关联证明") from None
                    self.saved_name = actual_name
            self.owned()
            self.run.config_created(self.key, self.object_id)
            self.recorder.record(
                "template-created-ownership",
                {"id": self.object_id, "requested_name": self.name, "actual_name": self.saved_name},
            )
        self.integrity_unknown = False
        return result

    def assert_unchanged_others(self) -> None:
        """原模板集合和业务工单必须保持，仅本条配置可改变。"""
        check(
            self.db.template_inventory(exclude_id=self.object_id) == self.before,
            "其他模板配置发生变化",
        )
        check(self.db.ticket_inventory() == self.tickets_before, "模板配置操作生成或修改了业务工单")
        expected = set(self.baseline) | ({self.object_id} if self.object_id is not None else set())
        check(set(self.db.template_ids()) == expected, "模板集合出现复制或遗漏")

    def update_options(self):
        """正式PUT只提交原用例指定options，未知写入禁止清理。"""
        self.owned()
        self.write_unknown = True
        result = self.admin.update(self.object_id, deepcopy(self.data["update"]))
        self.write_unknown = result.response.status_code >= 500
        return result

    def delete_as_agent(self):
        """普通A1只尝试删除本条P，不能替换为管理员请求。"""
        self.owned()
        self.write_unknown = True
        result = self.agent.delete(self.object_id)
        self.write_unknown = result.response.status_code >= 500
        self.agent_delete_status = result.response.status_code
        return result

    def cleanup(self) -> None:
        """M确认归属后API删除，404和数据库及基线复原才标cleaned。"""
        if self.key is None:
            return
        try:
            self.recorder.ensure_complete()
            if self.write_unknown or self.integrity_unknown:
                raise CleanupError("模板写入未知或归属不明")
            if self.object_id is None:
                if (
                    self.case_id != "TC_160"
                    or self.db.template_ids() != self.baseline
                    or self.db.template_inventory() != self.before
                ):
                    raise CleanupError("模板负向请求无法证明无新增")
                check(self.db.ticket_inventory() == self.tickets_before, "模板负向请求改变业务工单")
                self.run.config_absent(self.key)
                return
            if self.case_id == "TC_162" and self.agent_delete_status == 200:
                # 业务上的越权成功仍失败；资源已确实消失时不能重复DELETE。
                gone = self.maintenance.get(self.object_id)
                if gone.response.status_code == 404 and self.db.template(self.object_id) is None:
                    if (
                        self.db.template_ids() != self.baseline
                        or self.db.template_inventory() != self.before
                        or self.db.ticket_inventory() != self.tickets_before
                    ):
                        raise CleanupError("越权删除后的原模板或工单基线不符")
                    self.recorder.record(
                        "template-cleanup",
                        {"id": self.object_id, "result": "already-absent-after-agent-delete"},
                    )
                    self.recorder.ensure_complete()
                    self.run.cleaned(self.key)
                    return
            self.owned()
            self.recorder.ensure_complete()
            self.write_unknown = True
            result = self.maintenance.delete(self.object_id)
            self.write_unknown = result.response.status_code >= 500
            if (
                result.response.status_code != 200
                or self.maintenance.get(self.object_id).response.status_code != 404
                or self.db.template(self.object_id) is not None
            ):
                raise CleanupError("模板API删除或不存在核查失败")
            if (
                self.db.template_ids() != self.baseline
                or self.db.template_inventory() != self.before
                or self.db.ticket_inventory() != self.tickets_before
            ):
                raise CleanupError("模板清理后原集合或工单基线不符")
            self.recorder.ensure_complete()
            self.run.cleaned(self.key)
            self.recorder.record("template-cleanup", {"id": self.object_id, "result": "cleaned"})
        except Exception:
            self.run.attention(
                self.key, "create_unknown" if self.write_unknown else "cleanup_failed"
            )
            raise CleanupError("模板清理未完成，已保留定位信息") from None
