"""更新权限身份前置与未知写入保全。"""

from pathlib import Path

import yaml

from fixtures.creation_access import _actor_user, _pure_role
from fixtures.pilot import PilotCase
from fixtures.preflight import PreflightError, list_rows, permissions, preflight, read_json, unique
from fixtures.ticket import CleanupError
from support.config import ConfigError, _ConfigLoader
from support.update_access_config import UpdateAccessConfig

ROOT = Path(__file__).resolve().parents[1]
ACTORS = {
    "TC_062": "A1",
    "TC_064": "anonymous",
    "TC_065": "AD",
    "TC_066": "C2",
    "TC_067": "AR",
    "TC_068": "A1",
}


def load_update_case(case_id: str) -> tuple[dict, dict]:
    """合并更新场景与基础输入，保留实际客户端的身份约束。"""
    try:
        rows = yaml.load(
            (ROOT / "data/cases/ticket_update_access.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        scenario = rows[case_id]
        # conftest._database_case 使用 actor 选择会话，不能改变被测执行身份。
        actor = ACTORS[case_id]
        if scenario["actor"] != actor:
            raise ConfigError(f"{case_id}：actor 必须为 {actor}")
        data = yaml.load(
            (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        data.update(case_id=case_id, state_name="open", expected=scenario["expected"])
        return data, scenario
    except ConfigError:
        raise
    except (KeyError, OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        raise ConfigError("更新权限场景数据缺失或结构无效") from None


def _check_actor(
    actor, maintenance, access: UpdateAccessConfig, mapping: dict, case_id: str
) -> None:
    """核实独立的管理员、客户或只读客服及各自场景所需权限。"""
    users = list_rows(maintenance, "api/v1/users")
    roles = list_rows(maintenance, "api/v1/roles")
    login = {
        "TC_065": access.admin_login,
        "TC_066": access.other_customer_login,
        "TC_067": access.reader_login,
    }[case_id]
    current = _actor_user(actor, users, login)
    if current["id"] in {
        mapping["agent_id"],
        mapping["maintenance_id"],
        mapping["customer_id"],
    }:
        raise PreflightError("被测身份必须独立于 A1、M、C1") from None
    if case_id == "TC_065":
        _pure_role(current, roles, "admin")
        list_rows(actor, "api/v1/roles")
        peer = unique(users, "login", access.peer_login)
        grants = _pure_role(peer, roles, "ticket.agent")
        if peer["id"] in {
            mapping["agent_id"],
            mapping["maintenance_id"],
            mapping["customer_id"],
            current["id"],
        } or "full" not in grants.get(str(mapping["group_id"]), set()):
            raise PreflightError("A2 必须是独立有效同组客服") from None
        mapping["peer_id"] = peer["id"]
    elif case_id == "TC_066":
        _pure_role(current, roles, "ticket.customer")
        if current.get("organization_id") or current.get("organization_ids"):
            raise PreflightError("C2 不得有组织共享") from None
        read_json(actor, "api/v1/ticket_create")
    else:
        grants = _pure_role(current, roles, "ticket.agent")
        if grants.get(str(mapping["group_id"])) != {"read"} or any(
            value for key, value in grants.items() if key != str(mapping["group_id"])
        ):
            raise PreflightError("AR 必须仅有 G1 read，不得继承写入或其他组授权") from None
        mapping["reader_id"] = current["id"]


def _check_target_group(maintenance, config, access: UpdateAccessConfig, mapping: dict) -> None:
    """核对 G2 隔离、维护清理权限和系统未分派账户。"""
    users = list_rows(maintenance, "api/v1/users")
    roles = list_rows(maintenance, "api/v1/roles")
    group = unique(list_rows(maintenance, "api/v1/groups"), "name", access.group2_name)
    current = unique(users, "login", config.agent_login)
    _, a_grants = permissions(current, roles)
    _, m_grants = permissions(unique(users, "login", config.maintenance_login), roles)
    if (
        group["id"] == mapping["group_id"]
        or a_grants.get(str(group["id"]))
        or "full" not in m_grants.get(str(group["id"]), set())
    ):
        raise PreflightError("G2 隔离或维护清理授权不足") from None
    matches = [user for user in users if user.get("login") == access.unassigned_login]
    if (
        access.unassigned_login != "-"
        or len(matches) != 1
        or matches[0].get("active") is not False
        or matches[0].get("role_ids") != []
    ):
        raise PreflightError("系统未分派占位无法唯一确认") from None
    mapping.update(target_group_id=group["id"], unassigned_id=matches[0]["id"])


def update_preflight(
    agent, maintenance, actor, config, access: UpdateAccessConfig, data: dict, case_id: str
) -> dict:
    """复用基础身份核验，按被测执行人编排只读权限前置。"""
    mapping = preflight(agent, maintenance, config, data)
    mapping["creator_id"] = mapping["maintenance_id"]
    if case_id == "TC_064":
        return mapping
    if case_id in {"TC_062", "TC_068"}:
        # conftest 对这两条场景复用 agent；基础前置已验证其身份和普通客服权限。
        read_json(agent, "api/v1/ticket_create")
        if case_id == "TC_068":
            _check_target_group(maintenance, config, access, mapping)
    else:
        _check_actor(actor, maintenance, access, mapping, case_id)
    return mapping


class UpdateAccessCase(PilotCase):
    """保留不存在目标及非归属库存变化的人工核查证据。"""

    def __init__(self, agent, maintenance, run, mapping, data, recorder, db) -> None:
        """将不存在目标编码进 RUN 标记，哨兵 ID 独立登记。"""
        super().__init__(agent, maintenance, run, mapping, data, recorder, db)
        self.integrity_unknown = False
        if self.case_id == "TC_062":
            old = self.marker
            self.marker += f"_missing_{mapping['missing_ticket_id']}"
            self.payload["title"] = self.payload["title"].replace(old, self.marker)
            self.payload["article"]["body"] = self.payload["article"]["body"].replace(
                old, self.marker
            )

    def update_missing(self, api, payload: dict):
        """仅更新已核实不存在的目标，不覆盖登记哨兵 ID。"""
        self.write_unknown = True
        result = api.update(self.mapping["missing_ticket_id"], payload)
        self.write_unknown = False
        return result

    def cleanup_group_ids(self) -> set[int]:
        """转组缺陷清理仅容许已明确授权的起止组。"""
        groups = super().cleanup_group_ids()
        if self.case_id == "TC_068":
            groups.add(self.mapping["target_group_id"])
        return groups

    def cleanup(self) -> None:
        """库存异常时保留哨兵及目标线索，避免自动删除掩盖误修改。"""
        if self.integrity_unknown:
            if self.key is not None:
                self.run.attention(self.key, "ownership_unconfirmed")
            raise CleanupError("不存在目标或库存发生异常变化，需人工核查") from None
        super().cleanup()
