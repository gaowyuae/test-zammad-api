"""分派场景的数据加载、只读前置和工单生命周期。"""

from pathlib import Path

import yaml

from clients.http import HttpClient
from fixtures.creation_access import _actor_user, _pure_role
from fixtures.pilot import PilotCase
from fixtures.preflight import PreflightError, list_rows, permissions, preflight, read_json, unique
from support.assignment_config import AssignmentConfig
from support.config import ConfigError, _ConfigLoader
from support.run_config import RunConfig

ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(name: str) -> dict:
    """读取本地用例 YAML，由场景加载器统一处理读取和取值错误。"""
    return yaml.load(
        (ROOT / "data/cases" / name).read_text(encoding="utf-8"),
        Loader=_ConfigLoader,
    )


def load_assignment_case(case_id: str) -> tuple[dict, dict]:
    """读取分派场景并合并基础数据，统一报告读取或字段取值失败。"""
    try:
        scenario = _load_yaml("ticket_assignment.yaml")[case_id]
        # 调用方依此选择 Token，必须符合该用例约定的执行身份。
        actor = "A12" if case_id == "TC_061" else "A1"
        if scenario["actor"] != actor:
            raise ConfigError(f"{case_id}：actor 必须为 {actor}")
        data = _load_yaml("ticket_create.yaml")
        data.update(case_id=case_id, state_name="open", expected=scenario["expected"])
        return data, scenario
    except ConfigError:
        raise
    except (KeyError, OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        raise ConfigError("分派场景数据缺失或结构无效") from None


def _peer_id(users: list[dict], roles: list[dict], login: str, mapping: dict) -> int:
    """确认 A2 为独立且具有 G1 full 权限的普通客服。"""
    peer = unique(users, "login", login)
    grants = _pure_role(peer, roles, "ticket.agent")
    if peer["id"] in {
        mapping["agent_id"],
        mapping["maintenance_id"],
        mapping["customer_id"],
    }:
        raise PreflightError("A2 必须为独立客服")
    if "full" not in grants.get(str(mapping["group_id"]), set()):
        raise PreflightError("A2 对 G1 缺少 full 权限")
    return peer["id"]


def _transfer_mapping(
    actor: HttpClient,
    maintenance: HttpClient,
    users: list[dict],
    roles: list[dict],
    assignment_config: AssignmentConfig,
    mapping: dict,
) -> dict:
    """核对跨组执行身份、起止组及维护清理权限，返回目标映射。"""
    current = _actor_user(actor, users, assignment_config.transfer_login)
    group = unique(list_rows(maintenance, "api/v1/groups"), "name", assignment_config.group2_name)
    grants = _pure_role(current, roles, "ticket.agent")
    maintenance_user = next(user for user in users if user["id"] == mapping["maintenance_id"])
    _, maintenance_grants = permissions(maintenance_user, roles)
    if group["id"] == mapping["group_id"]:
        raise PreflightError("G1 与 G2 必须不同")
    if current["id"] in {
        mapping["agent_id"],
        mapping["maintenance_id"],
        mapping["customer_id"],
    }:
        raise PreflightError("A12 必须为独立客服")
    if any("full" not in grants.get(str(gid), set()) for gid in (mapping["group_id"], group["id"])):
        raise PreflightError("A12 对起止组缺少 full 权限")
    if "full" not in maintenance_grants.get(str(group["id"]), set()):
        raise PreflightError("M 对目标组缺少清理所需的 full 权限")
    return {"transfer_id": current["id"], "target_group_id": group["id"]}


def _unassigned_id(users: list[dict], login: str) -> int:
    """查找唯一、停用且无角色的系统未分派账户。"""
    matches = [user for user in users if user.get("login") == login]
    if len(matches) != 1:
        raise PreflightError("系统未分派占位用户缺失或不唯一")
    unassigned = matches[0]
    if unassigned.get("active") is not False or unassigned.get("role_ids") != []:
        raise PreflightError("系统未分派占位用户必须停用且无角色")
    return unassigned["id"]


def _check_import_mode(maintenance: HttpClient) -> None:
    """确认系统明确关闭导入模式，避免分派规则被绕过。"""
    # settings 不使用通用分页器，只检查本场景依赖的 import_mode。
    settings = read_json(maintenance, "api/v1/settings")
    if not isinstance(settings, list):
        raise PreflightError("设置接口必须返回列表")
    imports = [
        row for row in settings if isinstance(row, dict) and row.get("name") == "import_mode"
    ]
    if len(imports) != 1:
        raise PreflightError("import_mode 设置缺失或不唯一")
    state = imports[0].get("state_current")
    if not isinstance(state, dict) or state.get("value") is not False:
        raise PreflightError("无法确认 import_mode 已关闭")


def assignment_preflight(
    agent: HttpClient,
    maintenance: HttpClient,
    actor: HttpClient,
    config: RunConfig,
    assignment_config: AssignmentConfig,
    data: dict,
    case_id: str,
) -> dict:
    """复用基础前置，编排分派映射、操作 Token 范围和导入模式核查。"""
    mapping = preflight(agent, maintenance, config, data)
    users = list_rows(maintenance, "api/v1/users")
    # 基础前置已核验 A1 身份；这里检查实际操作 Token 的工单范围。
    read_json(actor, "api/v1/ticket_create")
    if case_id in {"TC_059", "TC_061"}:
        roles = list_rows(maintenance, "api/v1/roles")
        if case_id == "TC_061":
            mapping.update(
                _transfer_mapping(actor, maintenance, users, roles, assignment_config, mapping)
            )
        else:
            mapping["peer_id"] = _peer_id(users, roles, assignment_config.peer_login, mapping)
    else:
        mapping["unassigned_id"] = _unassigned_id(users, assignment_config.unassigned_login)
    _check_import_mode(maintenance)
    return mapping


class AssignmentCase(PilotCase):
    """沿用工单生命周期，仅补充跨组场景差异。"""

    def __init__(self, agent, maintenance, run, mapping, data, recorder, db) -> None:
        """跨组场景以 A12 为初始负责人。"""
        super().__init__(agent, maintenance, run, mapping, data, recorder, db)
        if self.case_id == "TC_061":
            self.payload["owner_id"] = mapping["transfer_id"]

    def cleanup_group_ids(self) -> set[int]:
        """清理范围仅包含原组和明确的目标组。"""
        groups = super().cleanup_group_ids()
        if self.case_id == "TC_061":
            groups.add(self.mapping["target_group_id"])
        return groups
