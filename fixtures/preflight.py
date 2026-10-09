"""仅通过只读 API 验证 TC_023 的身份、组及当前字典映射。"""

from clients.http import HttpClient
from fixtures.ticket import valid_id
from support.run_config import RunConfig


class PreflightError(RuntimeError):
    """前置不足，禁止继续创建业务数据。"""


def read_json(client: HttpClient, path: str, **params) -> object:
    """读取成功响应，错误不回显原始对象或凭据。"""
    result = client.request("GET", path, params=params or None)
    if result.response.status_code != 200:
        raise PreflightError("前置只读接口失败，请检查服务、身份和 Token 范围") from None
    return result.get_json()


def list_rows(client: HttpClient, path: str) -> list[dict]:
    """有界读取列表，重复分页或不完整结构时阻塞。"""
    rows = []
    seen = set()
    for page in range(1, 21):
        values = read_json(client, path, page=page, per_page=100, expand="true")
        if not isinstance(values, list):
            raise PreflightError("前置列表格式不符") from None
        for value in values:
            if not isinstance(value, dict) or not valid_id(value.get("id")) or value["id"] in seen:
                raise PreflightError("前置列表包含无效或重复对象") from None
            seen.add(value["id"])
            rows.append(value)
        if len(values) < 100:
            return rows
    raise PreflightError("前置列表超出首版读取范围，未取得完整映射") from None


def unique(rows: list[dict], key: str, value: str) -> dict:
    """逻辑名称必须唯一且对象活跃，不能默认取第一条。"""
    matches = [row for row in rows if row.get(key) == value]
    if len(matches) != 1 or matches[0].get("active") is not True:
        raise PreflightError("前置映射缺失、不唯一或对象未启用") from None
    return matches[0]


def permissions(user: dict, roles: list[dict]) -> tuple[set[str], dict[str, set[str]]]:
    """合并活跃角色权限及用户直接、角色继承的组授权。"""
    role_ids = user.get("role_ids")
    if (
        not isinstance(role_ids, list)
        or not role_ids
        or not all(valid_id(item) for item in role_ids)
    ):
        raise PreflightError("用户角色信息不完整") from None
    selected = [role for role in roles if role["id"] in role_ids]
    if len(selected) != len(set(role_ids)):
        raise PreflightError("用户角色映射不完整") from None
    names: set[str] = set()
    groups: dict[str, set[str]] = {}
    for item in [user, *selected]:
        access = item.get("group_ids", {})
        if not isinstance(access, dict):
            raise PreflightError("组授权结构无法识别") from None
        for group, grants in access.items():
            if not isinstance(grants, list) or not all(isinstance(grant, str) for grant in grants):
                raise PreflightError("组授权内容无法识别") from None
            groups.setdefault(str(group), set()).update(grants)
    for role in selected:
        values = role.get("permissions")
        if (
            role.get("active") is not True
            or not isinstance(values, list)
            or not all(isinstance(item, str) for item in values)
        ):
            raise PreflightError("角色状态或展开权限信息不完整") from None
        names.update(values)
    return names, groups


def preflight(agent: HttpClient, maintenance: HttpClient, config: RunConfig, data: dict) -> dict:
    """确认独立身份和可用映射，整个过程不发送写请求。"""
    people = []
    for client, login in ((agent, config.agent_login), (maintenance, config.maintenance_login)):
        user = read_json(client, "api/v1/users/me")
        if (
            not isinstance(user, dict)
            or user.get("login") != login
            or user.get("active") is not True
            or not valid_id(user.get("id"))
        ):
            raise PreflightError("Token 对应身份与配置不一致或身份未启用") from None
        people.append(user)
    roles = list_rows(maintenance, "api/v1/roles")
    customer = unique(list_rows(maintenance, "api/v1/users"), "login", config.customer_login)
    group = unique(list_rows(maintenance, "api/v1/groups"), "name", config.group_name)
    state = unique(list_rows(maintenance, "api/v1/ticket_states"), "name", data["state_name"])
    priority = unique(
        list_rows(maintenance, "api/v1/ticket_priorities"), "name", data["priority_name"]
    )
    agent_user, maintenance_user = people
    if len({agent_user["id"], maintenance_user["id"], customer["id"]}) != 3:
        raise PreflightError("A1、M、C1 必须为独立用户") from None
    a_permissions, a_groups = permissions(agent_user, roles)
    m_permissions, m_groups = permissions(maintenance_user, roles)
    c_permissions, _ = permissions(customer, roles)
    if (
        "ticket.agent" not in a_permissions
        or "ticket.customer" in a_permissions
        or any(item == "admin" or item.startswith("admin.") for item in a_permissions)
    ):
        raise PreflightError("A1 必须为无管理权限的普通客服") from None
    if not {"admin", "ticket.agent"}.issubset(m_permissions):
        raise PreflightError("M 缺少维护所需的管理员或客服权限") from None
    if (
        "ticket.customer" not in c_permissions
        or "ticket.agent" in c_permissions
        or any(item == "admin" or item.startswith("admin.") for item in c_permissions)
    ):
        raise PreflightError("C1 必须为普通客户") from None
    if customer.get("organization_id") or customer.get("organization_ids"):
        raise PreflightError("C1 应为无组织共享关系的专用客户") from None
    if any("full" not in groups.get(str(group["id"]), set()) for groups in (a_groups, m_groups)):
        raise PreflightError("A1 或 M 对 G1 缺少 full 权限") from None
    if any(grants for key, grants in a_groups.items() if key != str(group["id"])):
        raise PreflightError("A1 存在 G1 以外组授权，请核对专用身份") from None
    return {
        "agent_id": agent_user["id"],
        "maintenance_id": maintenance_user["id"],
        "customer_id": customer["id"],
        "group_id": group["id"],
        "state_id": state["id"],
        "priority_id": priority["id"],
    }
