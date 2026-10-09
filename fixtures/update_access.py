"""更新权限身份前置与未知写入保全。"""

from copy import deepcopy
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
CASE_IDS = frozenset(ACTORS)


def load_update_case(case_id: str) -> tuple[dict, dict]:
    """复制 TC_023，严格固定六场景、原始输入和明确错误原因。"""
    try:
        rows = yaml.load(
            (ROOT / "data/cases/ticket_update_access.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        errors = {
            "TC_062": "Couldn't find Ticket with 'id'=\"{ticket_id}\"",
            "TC_064": "Authentication required",
            "TC_065": "Not authorized",
            "TC_068": "Invalid value '{group_id}' for field 'group_id'!",
        }
        for key, actor in ACTORS.items():
            expected_scenario = {
                "actor": actor,
                "expected": {
                    "status_code": {"TC_062": 404, "TC_068": 422}.get(key, 403),
                    "error_equals": errors.get(key, "Not authorized"),
                },
            }
            if key in {"TC_064", "TC_066", "TC_067"}:
                expected_scenario["target_state"] = "closed"
            if rows[key] != expected_scenario:
                raise ValueError
        data = yaml.load(
            (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(data, dict) or not isinstance(data.get("article"), dict):
            raise ValueError
        data = deepcopy(data)
        scenario = deepcopy(rows[case_id])
        data.update(case_id=case_id, state_name="open", expected=deepcopy(scenario["expected"]))
        return data, scenario
    except (KeyError, OSError, ValueError, TypeError, yaml.YAMLError):
        raise ConfigError("更新权限场景数据缺失或结构无效") from None


def update_preflight(
    agent, maintenance, actor, config, access: UpdateAccessConfig, data: dict, case_id: str
) -> dict:
    """只读核查独立身份、有效 Token 范围和真实直接及继承授权。"""
    if case_id not in CASE_IDS:
        raise PreflightError("不支持的更新权限场景") from None
    mapping = preflight(agent, maintenance, config, data)
    read_json(agent, "api/v1/ticket_create")
    users = list_rows(maintenance, "api/v1/users")
    roles = list_rows(maintenance, "api/v1/roles")
    mapping["creator_id"] = mapping["maintenance_id"]
    if case_id == "TC_064":
        return mapping
    login = {
        "TC_065": access.admin_login,
        "TC_066": access.other_customer_login,
        "TC_067": access.reader_login,
    }.get(case_id, config.agent_login)
    current = _actor_user(actor, users, login)
    if case_id in {"TC_065", "TC_066", "TC_067"} and current["id"] in {
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
    elif case_id == "TC_067":
        grants = _pure_role(current, roles, "ticket.agent")
        if grants.get(str(mapping["group_id"])) != {"read"} or any(
            value for key, value in grants.items() if key != str(mapping["group_id"])
        ):
            raise PreflightError("AR 必须仅有 G1 read，不得继承写入或其他组授权") from None
        mapping["reader_id"] = current["id"]
    elif case_id == "TC_068":
        group = unique(list_rows(maintenance, "api/v1/groups"), "name", access.group2_name)
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
