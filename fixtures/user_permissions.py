"""M17 临时用户的实际角色权限、组授权及依赖资源保护。"""

from copy import deepcopy
from pathlib import Path

import yaml

from api.tickets import TicketsAPI
from api.users import UsersAPI
from assertions.messages import assert_no_leak
from assertions.tickets import FIELDS, check
from clients.http import HttpClient
from fixtures.pilot import PilotCase
from fixtures.preflight import PreflightError, list_rows, permissions, preflight, read_json, unique
from fixtures.ticket import CleanupError, valid_id
from fixtures.users import UserCase, preflight_users
from support.config import ConfigError, _ConfigLoader

CASE_IDS = frozenset({"TC_141", "TC_142", "TC_143", "TC_144"})


def load_permission_cases(path: Path | None = None) -> dict:
    """严格限定四条权限场景及非敏感输入，不接受任意执行参数。"""
    path = path or Path(__file__).resolve().parents[1] / "data/cases/user_permissions.yaml"
    try:
        rows = yaml.load(path.read_text(encoding="utf-8"), Loader=_ConfigLoader)
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        for case_id, data in rows.items():
            keys = {"actor", "role", "input"}
            if case_id == "TC_141":
                keys.add("token_role_name")
                name = data["token_role_name"]
                if not isinstance(name, str) or not name.strip() or name != name.strip():
                    raise ValueError
            if (
                set(data) != keys
                or data["actor"] != {"TC_141": "U", "TC_142": "A1"}.get(case_id, "AD")
                or data["role"] != ("Customer" if case_id in {"TC_141", "TC_142"} else "Agent")
                or set(data["input"]) != {"firstname", "lastname", "active"}
                or data["input"]["active"] is not True
                or not all(
                    isinstance(data["input"][key], str) and data["input"][key]
                    for key in ("firstname", "lastname")
                )
            ):
                raise ValueError
        return deepcopy(rows)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise ConfigError("权限场景数据缺失或结构无效") from None


def ticket_data(case_id: str) -> dict:
    """构造独立open工单，身份和字典值由前置映射提供。"""
    return {
        "case_id": case_id,
        "title": "临时用户组授权验证",
        "state_name": "open",
        "priority_name": "2 normal",
        "article": {
            "subject": "权限基线",
            "body": "临时权限工单",
            "type": "note",
            "sender": "Agent",
            "content_type": "text/plain",
            "internal": False,
        },
    }


def preflight_permissions(ad_client, maintenance_client, agent_client, context, queries) -> dict:
    """只读核验身份、两组维护权限、角色及数据库最小授权。"""
    queries.check_permission_access()
    mapping = preflight_users(ad_client, maintenance_client, context, queries)
    mapping.update(
        preflight(agent_client, maintenance_client, context.config, ticket_data("TC_143"))
    )
    # /me成功只说明凭据有效，还须证明A1的Token具备被测业务范围。
    read_json(agent_client, "api/v1/ticket_create")
    roles = list_rows(maintenance_client, "api/v1/roles")
    for name in ("Admin", "Agent"):
        role = unique(roles, "name", name)
        mapping[f"{name.lower()}_role_id"] = role["id"]
        if name == "Agent":
            names, groups = permissions({"role_ids": [role["id"]], "group_ids": {}}, roles)
            if (
                groups
                or "ticket.agent" not in names
                or any(item == "admin" or item.startswith("admin.") for item in names)
                or "ticket.customer" in names
            ):
                raise PreflightError("Agent角色必须无继承组和管理或客户权限") from None
    temp = [
        role for role in roles if role.get("name") == context.permission_config["temp_role_name"]
    ]
    permission = queries.permission("user_preferences.access_token")
    if (
        len(temp) != 1
        or not isinstance(permission, dict)
        or not valid_id(permission.get("id"))
        or permission.get("active") is not True
    ):
        raise PreflightError("临时Token角色或权限映射不唯一") from None
    mapping.update(temp_role_id=temp[0]["id"], temp_permission_id=permission["id"])
    group = unique(
        list_rows(maintenance_client, "api/v1/groups"), "name", context.creation_config.group2_name
    )
    _, m_groups = permissions(read_json(maintenance_client, "api/v1/users/me"), roles)
    if group["id"] == mapping["group_id"] or "full" not in m_groups.get(str(group["id"]), set()):
        raise PreflightError("G2必须独立且M具备full维护权限") from None
    mapping["group2_id"] = group["id"]
    priority = unique(list_rows(maintenance_client, "api/v1/ticket_priorities"), "name", "3 high")
    if priority["id"] == mapping["priority_id"]:
        raise PreflightError("权限写入探针必须使用不同合法优先级") from None
    mapping["target_priority_id"] = priority["id"]
    return mapping


def assert_permission_denied(result) -> None:
    """只接受明确权限拒绝，401认证失败及无关4xx不能通过。"""
    check(
        400 <= result.response.status_code < 500 and result.response.status_code != 401,
        "请求未被明确权限拒绝",
    )
    value = result.get_json()
    error = value.get("error") if isinstance(value, dict) else None
    check(isinstance(error, str), "权限拒绝缺少错误原因")
    words = " ".join(error.casefold().split())
    check("token" not in words, "Token范围错误不能冒充业务权限拒绝")
    check(
        any(
            part in words
            for part in (
                "not authorized",
                "not authorised",
                "access denied",
                "permission denied",
                "not permitted",
                "insufficient permission",
                "forbidden",
                "authorization failed",
            )
        ),
        "拒绝原因不是业务权限",
    )


def assert_ticket_denied(result, snapshot: dict) -> None:
    """拒绝读取也不能夹带工单字段或将正文藏入错误文本。"""
    assert_permission_denied(result)

    def has_details(value) -> bool:
        """递归识别正常工单与文章数据，不依赖包装层名称。"""
        if isinstance(value, dict):
            return bool({"title", "number", "body", "ticket_id", "articles"} & value.keys()) or any(
                has_details(child) for child in value.values()
            )
        return isinstance(value, list) and any(has_details(child) for child in value)

    check(not has_details(result.get_json()), "拒绝响应夹带工单或文章字段")
    assert_no_leak(
        result, [snapshot["ticket"]["title"], *[row["body"] for row in snapshot["articles"]]]
    )


def assert_role(value: dict, role_id: int, role: str) -> None:
    """角色关系及数据库实际权限同时成立，不能用受限Token掩盖提权。"""
    check(value["api"].get("role_ids") == [role_id], "API角色不是唯一目标角色")
    check(
        value["roles"] == [{"id": role_id, "name": role, "active": True}],
        "数据库角色不是唯一活跃目标角色",
    )
    rows = value["permissions"]
    check(
        isinstance(rows, list)
        and bool(rows)
        and all(
            isinstance(row, dict)
            and valid_id(row.get("id"))
            and isinstance(row.get("name"), str)
            and row.get("active") is True
            for row in rows
        ),
        "角色实际权限证据缺失或未启用",
    )
    names = {row["name"] for row in rows}
    check(f"ticket.{role.lower()}" in names, "目标角色缺少工单权限")
    check(
        not any(name == "admin" or name.startswith("admin.") for name in names),
        "临时用户实际获得管理权限",
    )
    other = "ticket.agent" if role == "Customer" else "ticket.customer"
    check(other not in names, "临时用户混入另一业务角色权限")


def assert_grants(value: dict, expected: dict[str, list[str]]) -> None:
    """精确核对API及DB直接授权，额外角色继承授权必须为空。"""
    check(value["api"].get("group_ids") == expected, "API组授权与请求不一致")
    rows = sorted(
        (
            {"group_id": int(group), "access": grant}
            for group, grants in expected.items()
            for grant in grants
        ),
        key=lambda row: (row["group_id"], row["access"]),
    )
    check(value["groups"] == rows, "数据库直接组授权不精确")
    check(value["inherited"] == [], "临时用户存在角色继承组授权")


class UserPermissionCase:
    """管理本条U、U的Token及M创建工单，不修改固定身份。"""

    def __init__(self, ad, maintenance, agent, context, mapping, data, recorder, db) -> None:
        """所有依赖先登记到实例，确保准备失败也能有序清理。"""
        self.context, self.mapping, self.data = context, mapping, deepcopy(data)
        self.recorder, self.db, self.run = recorder, db, context.run
        self.ad, self.maintenance, self.agent = ad, maintenance, agent
        self.case_id = data["case_id"]
        self.user = UserCase(
            ad, maintenance, context.run, mapping, data, recorder, db, context.settings
        )
        self.user.marker += "_user"
        self.role = data["role"]
        self.role_id = mapping[f"{self.role.lower()}_role_id"]
        self.user.payload.update(role_ids=[self.role_id], group_ids={})
        self.tickets = []
        self.token = None
        self.identity = None

    def snapshot(self, label: str) -> dict:
        """完整取证后返回业务比较所需角色与直接继承授权。"""
        value = self.user.snapshot(label)
        user_id = self.user.user_id
        value.update(
            groups=self.db.user_groups(user_id),
            inherited=self.db.inherited_groups(user_id),
            permissions=self.db.user_permissions(user_id),
            others=self.db.other_user_groups(user_id),
        )
        self.recorder.record(f"{label}-permissions", value)
        return value

    def prepare(self) -> dict:
        """AD创建本条独立U，验证无默认组及唯一指定角色。"""
        created = self.user.create()
        check(
            created.response.status_code == 201 and self.user.no_password(created),
            "临时用户创建失败或泄露密码",
        )
        response = created.get_json()
        check(
            isinstance(response, dict)
            and valid_id(response.get("id"))
            and response["id"] == self.user.user_id,
            "临时用户ID无效",
        )
        value = self.snapshot("permission-user-created")
        self.assert_user(value)
        assert_grants(value, {})
        return value

    def assert_user(self, value: dict) -> None:
        """核对实际临时用户归属、基本字段及真实角色权限。"""
        __tracebackhide__ = True
        check(value["status"] == 200 and value["no_password"], "用户回读失败或泄露密码")
        expected = {
            key: self.user.payload[key]
            for key in ("login", "email", "firstname", "lastname", "active")
        }
        expected.update(
            id=self.user.user_id, created_by_id=self.mapping["admin_id"], organization_id=None
        )
        for row in (value["api"], value["db"]):
            check(
                isinstance(row, dict)
                and all(key in row and row[key] == val for key, val in expected.items()),
                "临时用户归属或资料改变",
            )
        check(
            len(value["same_login"]) == 1 and value["same_login"][0]["id"] == self.user.user_id,
            "临时登录名未对应唯一用户",
        )
        assert_role(value, self.role_id, self.role)

    def issue_token(self) -> None:
        """按批准范围签发U自身Token，helper恢复临时签发授权。"""
        from fixtures.temp_token import TempToken

        self.token = TempToken(self.user, self.context, self.mapping, self.recorder)
        self.identity = self.token.issue()
        self.assert_user(self.snapshot("permission-after-token"))

    def client(self):
        """每次返回独立U会话，禁止复用管理员或旧Cookie。"""
        check(self.identity is not None, "临时用户Token尚未签发")
        return HttpClient(self.context.settings, self.identity, self.recorder)

    def update_user(self, client, payload: dict):
        """仅更新本条U，网络异常及5xx均保持写入未知。"""
        check(valid_id(self.user.user_id), "缺少本条临时用户ID")
        self.user.write_unknown = True
        result = UsersAPI(client).update(self.user.user_id, deepcopy(payload))
        self.user.write_unknown = result.response.status_code >= 500
        return result

    def set_groups(self, groups: dict[str, list[str]]) -> dict:
        """AD只提交本条U组授权，响应和持久化都必须精确匹配。"""
        before = self.snapshot("permission-before-groups")
        result = self.update_user(self.ad, {"group_ids": groups})
        after = self.snapshot("permission-after-groups")
        check(
            result.response.status_code == 200 and self.user.no_password(result),
            "组授权更新失败或泄露密码",
        )
        response = result.get_json()
        check(
            isinstance(response, dict)
            and response.get("id") == self.user.user_id
            and response.get("group_ids") == groups
            and response.get("role_ids") == [self.role_id],
            "组授权响应对象或权限不符",
        )
        self.assert_user(after)
        assert_grants(after, groups)
        check(after["others"] == before["others"], "修改U授权影响了其他用户")
        return after

    def prepare_ticket(self, group_id: int, suffix: str) -> PilotCase:
        """M在指定组创建独立工单，marker与真实标题正文始终一致。"""
        mapping = {
            **self.mapping,
            "group_id": group_id,
            "agent_id": self.mapping["maintenance_id"],
            "creator_id": self.mapping["maintenance_id"],
        }
        ticket = PilotCase(
            self.maintenance,
            self.maintenance,
            self.run,
            mapping,
            ticket_data(self.case_id),
            self.recorder,
            self.db,
        )
        old = ticket.marker
        ticket.marker += f"_{suffix}"
        ticket.payload["title"] = ticket.payload["title"].replace(old, ticket.marker)
        ticket.payload["article"]["body"] = ticket.payload["article"]["body"].replace(
            old, ticket.marker
        )
        self.tickets.append(ticket)
        created = ticket.create()
        ticket.write_unknown = created.response.status_code >= 500
        check(
            created.response.status_code == 201 and valid_id(ticket.ticket_id), "M准备权限工单失败"
        )
        value = self.ticket_snapshot(ticket, "permission-ticket-created")
        for row in (value["ticket"], value["db_ticket"]):
            check(
                all(
                    row.get(key) == ticket.payload[key]
                    for key in (
                        "title",
                        "group_id",
                        "owner_id",
                        "customer_id",
                        "state_id",
                        "priority_id",
                    )
                ),
                "准备工单字段不符",
            )
            check(
                row.get("created_by_id") == self.mapping["maintenance_id"]
                and row.get("article_count") == 1,
                "准备工单作者或文章计数不符",
            )
        for key in ("articles", "db_articles"):
            rows = value[key]
            check(
                len(rows) == 1
                and rows[0].get("id") in ticket.article_ids
                and rows[0].get("ticket_id") == ticket.ticket_id
                and rows[0].get("created_by_id") == self.mapping["maintenance_id"],
                "初始文章作者或关联不符",
            )
            check(
                all(
                    rows[0].get(field) == ticket.payload["article"][field]
                    for field in ("body", "subject", "internal", "content_type")
                ),
                "初始文章内容不符",
            )
        return ticket

    def ticket_snapshot(self, ticket: PilotCase, label: str) -> dict:
        """保全完整证据，仅用工单业务字段和文章快照判断拒绝副作用。"""
        result = ticket.maintenance.read(ticket.ticket_id)
        articles = ticket.maintenance.articles(ticket.ticket_id)
        value = {
            "ticket": result.get_json(),
            "articles": articles.get_json(),
            "db_ticket": self.db.ticket(ticket.ticket_id),
            "db_articles": self.db.articles(ticket.ticket_id),
        }
        self.recorder.record(label, value)
        check(
            result.response.status_code == 200 and articles.response.status_code == 200,
            "M授权工单快照读取失败",
        )
        for key in ("ticket", "db_ticket"):
            row = value[key]
            check(
                isinstance(row, dict) and all(field in row for field in FIELDS),
                "工单快照缺少业务字段",
            )
            check(row["id"] == ticket.ticket_id, "工单快照目标不符")
        check(
            isinstance(value["articles"], list) and isinstance(value["db_articles"], list),
            "文章快照格式不符",
        )
        return value

    def update_ticket(self, api: TicketsAPI, ticket: PilotCase, payload: dict):
        """被测更新不重试，任何5xx或网络异常阻止后续删除。"""
        ticket.write_unknown = True
        result = api.update(ticket.ticket_id, payload)
        ticket.write_unknown = result.response.status_code >= 500
        return result

    def attention(self) -> None:
        """依赖不安全时保留全部已登记资源供人工核查。"""
        keys = [entry["key"] for entry in self.user.resources]
        keys.extend(ticket.key for ticket in self.tickets if ticket.key is not None)
        if self.token is not None and self.token.key is not None:
            keys.append(self.token.key)
        for key in keys:
            self.run.attention(key, "cleanup_failed")

    def cleanup_owner(self) -> dict:
        """每次关联写入前，用M及DB重新核对本条已登记U的归属。"""
        __tracebackhide__ = True
        user_id = self.user.user_id
        if not valid_id(user_id) or not any(
            entry.get("id") == user_id for entry in self.user.resources
        ):
            raise CleanupError("关联清理目标未登记为本条用户") from None
        result = self.user.maintenance.read(user_id)
        value = result.get_json()
        expected = {
            "id": user_id,
            "login": self.user.login,
            "created_by_id": self.mapping["admin_id"],
        }
        if result.response.status_code != 200 or any(
            not isinstance(row, dict) or any(row.get(key) != val for key, val in expected.items())
            for row in (value, self.db.user(user_id))
        ):
            raise CleanupError("关联清理用户归属不符") from None
        return value

    def cleanup_agent_links(self) -> None:
        """只处理临时Agent的直接组及自身设备，不增加Customer权限。"""
        self.cleanup_owner()
        result = self.update_user(self.ad, {"group_ids": {}})
        if result.response.status_code != 200:
            raise CleanupError("清空临时用户组授权失败") from None
        value = self.cleanup_owner()
        if (
            value.get("group_ids") != {}
            or self.db.user_groups(self.user.user_id)
            or self.db.inherited_groups(self.user.user_id)
        ):
            raise CleanupError("临时用户组关联未清空") from None
        self.recorder.record(
            "permission-groups-cleanup", {"user_id": self.user.user_id, "status": 200}
        )
        self.cleanup_devices()

    def cleanup_devices(self) -> None:
        """仅清理阶段由M代本条U操作，避免U认证异步重建设备。"""
        __tracebackhide__ = True
        self.cleanup_owner()
        summary = getattr(self.recorder, "summary_only", False)
        self.recorder.summary_only = True
        evidence = []
        try:
            with HttpClient(
                self.context.settings,
                self.context.maintenance,
                self.recorder,
            ) as client:
                headers = {"From": str(self.user.user_id)}
                result = client.request("GET", "api/v1/user_devices", headers=headers)
                evidence.append({"operation": "list", "status": result.response.status_code})
                rows = result.get_json()
                if (
                    result.response.status_code != 200
                    or not isinstance(rows, list)
                    or any(
                        not isinstance(row, dict)
                        or not valid_id(row.get("id"))
                        or type(row.get("user_id")) is not int
                        or row["user_id"] != self.user.user_id
                        for row in rows
                    )
                ):
                    raise CleanupError("设备列表结构或用户归属不符") from None
                ids = [row["id"] for row in rows]
                if len(set(ids)) != len(ids):
                    raise CleanupError("设备列表包含重复ID") from None
                for device_id in ids:
                    self.cleanup_owner()
                    self.user.write_unknown = True
                    result = client.request(
                        "DELETE", f"api/v1/user_devices/{device_id}", headers=headers
                    )
                    self.user.write_unknown = result.response.status_code >= 500
                    evidence.append({"device_id": device_id, "status": result.response.status_code})
                    if result.response.status_code != 200:
                        raise CleanupError("临时用户设备删除失败") from None
        finally:
            self.recorder.summary_only = summary
            self.recorder.record(
                "permission-devices-cleanup",
                {"authentication": "M", "on_behalf_of": self.user.user_id, "operations": evidence},
            )

    def cleanup(self) -> None:
        """依次清工单、Token、Agent关联和U，未知写入或失败立即停止。"""
        if self.user.write_unknown or any(ticket.write_unknown for ticket in self.tickets):
            self.attention()
            raise CleanupError("权限场景写入未知，保留依赖待人工核查") from None
        try:
            for ticket in reversed(self.tickets):
                ticket.cleanup()
            if self.token is not None:
                self.token.cleanup()
            agent = self.case_id in {"TC_143", "TC_144"} and self.user.user_id is not None
            if agent:
                self.cleanup_agent_links()
            self.user.cleanup()
            if agent and self.db.user(self.user.user_id) is not None:
                raise CleanupError("关联清理后临时用户仍存在") from None
            if self.user.user_id is not None and (
                self.db.user_groups(self.user.user_id)
                or self.db.inherited_groups(self.user.user_id)
            ):
                raise CleanupError("用户删除后仍有直接或继承组关联") from None
        except Exception:
            self.attention()
            raise CleanupError("权限资源清理失败，已保留后续依赖") from None
