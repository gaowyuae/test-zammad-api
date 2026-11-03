"""只读核对创建权限前置，并保持请求身份与清理归属分离。"""

from pathlib import Path

import yaml

from fixtures.pilot import PilotCase
from fixtures.preflight import PreflightError, list_rows, permissions, preflight, read_json, unique
from support.config import ConfigError, _ConfigLoader
from support.creation_config import CreationConfig
from support.run_config import RunConfig

ROOT = Path(__file__).resolve().parents[1]


def load_creation_case(case_id: str) -> tuple[dict, dict]:
    """合并仓库场景与基础输入，仅约束实际选择 Token 的执行身份。"""
    try:
        scenarios = yaml.load(
            (ROOT / "data/cases/ticket_creation_access.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        scenario = scenarios[case_id]
        # database_case 依此选择客户端，不能把负向用例换成其他身份。
        actor = {"TC_032": "C1", "TC_034": "AD"}.get(case_id, "A1")
        if scenario["actor"] != actor:
            raise ConfigError(f"{case_id}：actor 必须为 {actor}")
        data = yaml.load(
            (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        data.update(case_id=case_id, expected=scenario["expected"])
        if case_id == "TC_032":
            data.update(title=scenario["title"], article=scenario["article"])
        return data, scenario
    except ConfigError:
        raise
    except (KeyError, OSError, TypeError, ValueError, AttributeError, yaml.YAMLError):
        raise ConfigError("创建权限场景数据缺失或结构无效") from None


def _pure_role(user: dict, roles: list[dict], role: str) -> dict[str, set[str]]:
    """核对活跃独立角色语义，拒绝管理或工单角色交叉授权。"""
    names, groups = permissions(user, roles)
    admin = any(name == "admin" or name.startswith("admin.") for name in names)
    invalid = user.get("active") is not True or role not in names
    if role == "admin":
        invalid |= bool({"ticket.agent", "ticket.customer"} & names)
    else:
        other = "ticket.customer" if role == "ticket.agent" else "ticket.agent"
        invalid |= admin or other in names
    if invalid:
        raise PreflightError("创建场景身份角色不符合约定") from None
    return groups


def _actor_user(actor, users: list[dict], login: str) -> dict:
    """同时核验 Token 当前身份和维护接口读取的账户资料。"""
    known = unique(users, "login", login)
    current = read_json(actor, "api/v1/users/me")
    if (
        not isinstance(current, dict)
        or current.get("id") != known["id"]
        or current.get("login") != login
        or current.get("active") is not True
        or current.get("role_ids") != known.get("role_ids")
    ):
        raise PreflightError("创建场景 Token 身份与配置不一致") from None
    return known


def _group_mapping(maintenance, config: RunConfig, access: CreationConfig, mapping: dict) -> dict:
    """确认 G2 隔离、合法负责人及维护清理权限。"""
    users = list_rows(maintenance, "api/v1/users")
    roles = list_rows(maintenance, "api/v1/roles")
    group = unique(list_rows(maintenance, "api/v1/groups"), "name", access.group2_name)
    if group["id"] == mapping["group_id"]:
        raise PreflightError("G1 与 G2 必须不同") from None
    owner = unique(users, "login", access.owner_login)
    owner_groups = _pure_role(owner, roles, "ticket.agent")
    a_user = unique(users, "login", config.agent_login)
    m_user = unique(users, "login", config.maintenance_login)
    _, a_groups = permissions(a_user, roles)
    _, m_groups = permissions(m_user, roles)
    group_key = str(group["id"])
    if (
        a_groups.get(group_key)
        or "full" not in owner_groups.get(group_key, set())
        or "full" not in m_groups.get(group_key, set())
        or owner["id"] in {mapping["agent_id"], mapping["maintenance_id"]}
    ):
        raise PreflightError("G2 负责人、客服隔离或维护清理权限不足") from None
    return {"group_id": group["id"], "owner_id": owner["id"]}


def _customer_mapping(actor, maintenance, config: RunConfig, access: CreationConfig) -> dict:
    """核实客户 Token、独立冒用目标及其组织隔离。"""
    users = list_rows(maintenance, "api/v1/users")
    customer = _actor_user(actor, users, config.customer_login)
    read_json(actor, "api/v1/ticket_create")
    other = unique(users, "login", access.other_customer_login)
    # 基础 preflight 已核验 C1 的纯客户权限和无组织关系，这里只补 C2。
    _pure_role(other, list_rows(maintenance, "api/v1/roles"), "ticket.customer")
    if other.get("organization_id") or other.get("organization_ids"):
        raise PreflightError("两个专用客户不能存在组织共享") from None
    if customer["id"] == other["id"]:
        raise PreflightError("冒用目标必须是另一名独立客户") from None
    return {"creator_id": customer["id"], "claimed_customer_id": other["id"]}


def _admin_mapping(actor, maintenance, access: CreationConfig, mapping: dict) -> dict:
    """核实独立纯管理员及其管理 Token 可用性。"""
    users = list_rows(maintenance, "api/v1/users")
    roles = list_rows(maintenance, "api/v1/roles")
    admin = _actor_user(actor, users, access.admin_login)
    _pure_role(admin, roles, "admin")
    # 管理列表成功证明 admin 范围可用，不能把无效 Token 当作角色隔离。
    list_rows(actor, "api/v1/roles")
    if admin["id"] in {mapping["agent_id"], mapping["maintenance_id"], mapping["customer_id"]}:
        raise PreflightError("AD 必须为独立纯管理员") from None
    return {"creator_id": admin["id"]}


def creation_preflight(
    agent, maintenance, actor, config: RunConfig, access: CreationConfig, data: dict, case_id: str
) -> dict:
    """编排基础身份与当前创建场景的只读核查。"""
    mapping = preflight(agent, maintenance, config, data)
    if case_id in {"TC_030", "TC_031"}:
        # 身份查询不证明 ticket.agent Token 范围，先验证只读建单入口。
        read_json(agent, "api/v1/ticket_create")
    if case_id == "TC_030":
        users = list_rows(maintenance, "api/v1/users")
        candidate = max(user["id"] for user in users) + 1000
        checked = maintenance.request("GET", f"api/v1/users/{candidate}")
        if checked.response.status_code != 404:
            raise PreflightError("无法确认负向客户 ID 不存在") from None
        mapping["missing_customer_id"] = candidate
    elif case_id == "TC_031":
        mapping.update(_group_mapping(maintenance, config, access, mapping))
    elif case_id == "TC_032":
        mapping.update(_customer_mapping(actor, maintenance, config, access))
    else:
        mapping.update(_admin_mapping(actor, maintenance, access, mapping))
    return mapping


class CreationCase(PilotCase):
    """只补齐四个创建场景的差异，清理沿用已验收生命周期。"""

    def __init__(self, agent, maintenance, run, mapping, data, recorder, db) -> None:
        """请求客户与预期持久化客户分开保存，不改写业务期望。"""
        super().__init__(agent, maintenance, run, mapping, data, recorder, db)
        if self.case_id == "TC_030":
            self.payload["customer_id"] = mapping["missing_customer_id"]
        elif self.case_id == "TC_031":
            self.payload["owner_id"] = mapping["owner_id"]
        elif self.case_id == "TC_032":
            self.payload["customer_id"] = mapping["claimed_customer_id"]
            del self.payload["owner_id"]
            del self.payload["priority_id"]

    def cleanup_customer_ids(self) -> set[int]:
        """客户冒用缺陷也只能清理已知双方及本轮 RUN、作者均匹配的对象。"""
        ids = {self.mapping["customer_id"]}
        if self.case_id == "TC_032":
            ids.add(self.mapping["claimed_customer_id"])
        return ids
