"""分派场景的明确映射及只读前置。"""

from pathlib import Path

import yaml

from fixtures.creation_access import _actor_user, _pure_role
from fixtures.pilot import PilotCase
from fixtures.preflight import PreflightError, list_rows, permissions, preflight, read_json, unique
from support.assignment_config import AssignmentConfig
from support.config import ConfigError, _ConfigLoader
from support.run_config import RunConfig

ROOT = Path(__file__).resolve().parents[1]
CASE_IDS = frozenset({"TC_059", "TC_060", "TC_061", "TC_063"})


def load_assignment_case(case_id: str) -> tuple[dict, dict]:
    """复制 TC_023，仅覆盖固定场景和 open 状态。"""
    try:
        rows = yaml.load(
            (ROOT / "data/cases/ticket_assignment.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        scenario = rows[case_id]
        owner = {"TC_059": "A2", "TC_061": "A12"}.get(case_id, "unassigned")
        if scenario != {
            "actor": "A12" if case_id == "TC_061" else "A1",
            "expected": {"status_code": 200, "owner": owner},
        }:
            raise ValueError
        data = yaml.load(
            (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(data, dict) or not isinstance(data.get("article"), dict):
            raise ValueError
        data.update(case_id=case_id, state_name="open", expected=scenario["expected"])
        return data, scenario
    except (KeyError, OSError, ValueError, TypeError, yaml.YAMLError):
        raise ConfigError("分派场景数据缺失或结构无效") from None


def assignment_preflight(
    agent,
    maintenance,
    actor,
    config: RunConfig,
    assignment_config: AssignmentConfig,
    data: dict,
    case_id: str,
) -> dict:
    """写入前核验客服、组 full、Token 身份及系统占位用户。"""
    if case_id not in CASE_IDS:
        raise PreflightError("不支持的分派场景") from None
    mapping = preflight(agent, maintenance, config, data)
    users = list_rows(maintenance, "api/v1/users")
    roles = list_rows(maintenance, "api/v1/roles")
    current = _actor_user(
        actor,
        users,
        assignment_config.transfer_login if case_id == "TC_061" else config.agent_login,
    )
    read_json(agent, "api/v1/ticket_create")
    if case_id == "TC_061":
        read_json(actor, "api/v1/ticket_create")
        group = unique(
            list_rows(maintenance, "api/v1/groups"), "name", assignment_config.group2_name
        )
        grants = _pure_role(current, roles, "ticket.agent")
        _, maintenance_grants = permissions(unique(users, "login", config.maintenance_login), roles)
        if (
            group["id"] == mapping["group_id"]
            or current["id"]
            in {mapping["agent_id"], mapping["maintenance_id"], mapping["customer_id"]}
            or any(
                "full" not in grants.get(str(gid), set())
                for gid in (group["id"], mapping["group_id"])
            )
            or "full" not in maintenance_grants.get(str(group["id"]), set())
        ):
            raise PreflightError("跨组客服身份或起止组 full 权限不足") from None
        mapping.update(transfer_id=current["id"], target_group_id=group["id"])
    elif case_id == "TC_059":
        peer = unique(users, "login", assignment_config.peer_login)
        grants = _pure_role(peer, roles, "ticket.agent")
        if peer["id"] in {
            mapping["agent_id"],
            mapping["maintenance_id"],
            mapping["customer_id"],
        } or "full" not in grants.get(str(mapping["group_id"]), set()):
            raise PreflightError("同组客服身份或 full 权限不足") from None
        mapping["peer_id"] = peer["id"]
    else:
        matches = [
            user for user in users if user.get("login") == assignment_config.unassigned_login
        ]
        if (
            assignment_config.unassigned_login != "-"
            or len(matches) != 1
            or matches[0].get("active") is not False
            or matches[0].get("role_ids") != []
            or matches[0]["id"]
            in {mapping["agent_id"], mapping["maintenance_id"], mapping["customer_id"]}
        ):
            raise PreflightError("系统未分派占位用户无法唯一确认") from None
        mapping["unassigned_id"] = matches[0]["id"]
    # settings 返回完整列表且忽略分页，不能套用业务字典分页读取。
    settings = read_json(maintenance, "api/v1/settings")
    if not isinstance(settings, list) or not all(
        isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"]
        for row in settings
    ):
        raise PreflightError("设置列表结构无法确认") from None
    imports = [row for row in settings if row.get("name") == "import_mode"]
    if (
        len(imports) != 1
        or not isinstance(imports[0].get("state_current"), dict)
        or imports[0]["state_current"].get("value") is not False
    ):
        raise PreflightError("无法确认 import_mode 已关闭") from None
    return mapping


class AssignmentCase(PilotCase):
    """维护身份建单，跨组清理仅容许明确起止组。"""

    def __init__(self, agent, maintenance, run, mapping, data, recorder, db) -> None:
        """跨组场景初始负责人必须为 A12。"""
        super().__init__(agent, maintenance, run, mapping, data, recorder, db)
        if self.case_id == "TC_061":
            self.payload["owner_id"] = mapping["transfer_id"]

    def cleanup_group_ids(self) -> set[int]:
        """容許本场景原组及明确转入组。"""
        groups = super().cleanup_group_ids()
        if self.case_id == "TC_061":
            groups.add(self.mapping["target_group_id"])
        return groups
