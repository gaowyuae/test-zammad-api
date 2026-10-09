"""M18角色场景的动态映射、归属证明及获准停用保留。"""

from copy import deepcopy
from pathlib import Path

import yaml

from api.roles import RolesAPI
from assertions.tickets import check
from fixtures.preflight import PreflightError, list_rows, permissions, read_json, unique
from fixtures.ticket import CleanupError, valid_id
from fixtures.users import preflight_users
from support.config import ConfigError, _ConfigLoader

CASE_IDS = frozenset({"TC_145", "TC_146", "TC_147", "TC_148"})
ROLE_FIELDS = ("id", "name", "active", "default_at_signup")


def load_role_cases(path: Path | None = None) -> dict:
    """固定四条原始输入与错误语义，禁止给147和148补active。"""
    path = path or Path(__file__).resolve().parents[1] / "data/cases/roles.yaml"
    try:
        rows = yaml.load(path.read_text(encoding="utf-8"), Loader=_ConfigLoader)
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        for case_id, data in rows.items():
            fields = {"actor", "input", "permission_names", "expected"}
            expected_input = {"default_at_signup": False}
            if case_id in {"TC_145", "TC_146"}:
                expected_input["active"] = True
            if case_id in {"TC_145", "TC_148"}:
                fields.add("name_template")
                if data["name_template"] != "{run_id}_{case_id}_role":
                    raise ValueError
            else:
                expected_input["name"] = "" if case_id == "TC_146" else "agent"
            expected_permissions = {
                "TC_145": ["ticket.agent", "user_preferences"],
                "TC_148": ["admin"],
            }.get(case_id, ["ticket.agent"])
            expected = (
                {"status_code": 201}
                if case_id == "TC_145"
                else {
                    "status_class": 4,
                    "error_kind": {
                        "TC_146": "required",
                        "TC_147": "duplicate",
                        "TC_148": "permission",
                    }[case_id],
                }
            )
            if (
                set(data) != fields
                or data["input"] != expected_input
                or data["permission_names"] != expected_permissions
                or data["expected"] != expected
                or data["actor"] != ("C1" if case_id == "TC_148" else "AD")
                or data["input"]["default_at_signup"] is not False
                or ("active" in expected_input and data["input"]["active"] is not True)
            ):
                raise ValueError
        return deepcopy(rows)
    except (OSError, KeyError, ValueError, TypeError, yaml.YAMLError):
        raise ConfigError("角色场景数据缺失或不符合原始输入") from None


def preflight_roles(ad, maintenance, customer, context, db) -> dict:
    """只读验证独立身份、合法权限ID、Agent基线及客户真实角色。"""
    db.check_role_access()
    mapping = preflight_users(ad, maintenance, context, db)
    roles = list_rows(maintenance, "api/v1/roles")
    mapping["agent_role_id"] = unique(roles, "name", "Agent")["id"]
    mapping["permission_ids"] = {}
    for name in ("ticket.agent", "user_preferences", "admin"):
        row = db.permission(name)
        if (
            not isinstance(row, dict)
            or not valid_id(row.get("id"))
            or row.get("name") != name
            or row.get("active") is not True
        ):
            raise PreflightError("角色权限映射不存在或未启用") from None
        mapping["permission_ids"][name] = row["id"]
    if customer is not None:
        current = read_json(customer, "api/v1/users/me")
        if (
            not isinstance(current, dict)
            or current.get("login") != context.config.customer_login
            or not valid_id(current.get("id"))
            or current.get("active") is not True
        ):
            raise PreflightError("C1身份与配置不一致") from None
        names, _ = permissions(current, roles)
        actual_roles = db.user_roles(current["id"])
        actual = db.user_permissions(current["id"])
        actual_names = {row["name"] for row in actual if row.get("active") is True}
        if (
            "ticket.customer" not in names
            or "ticket.agent" in names
            or any(name == "admin" or name.startswith("admin.") for name in names | actual_names)
            or "ticket.customer" not in actual_names
            or "ticket.agent" in actual_names
            or sorted(row["id"] for row in actual_roles) != sorted(current.get("role_ids", []))
            or current.get("role_ids") != [mapping["customer_role_id"]]
            or [row["id"] for row in actual_roles] != [mapping["customer_role_id"]]
            or any(row.get("active") is not True for row in actual_roles)
        ):
            raise PreflightError("C1必须是无管理权限的真实Customer") from None
        read_json(customer, "api/v1/ticket_create")
        mapping["customer_id"] = current["id"]
    return mapping


def assert_rejection(result, kind: str) -> None:
    """拒绝必须指向名称必填、重名或权限，不能接受无关4xx。"""
    check(
        400 <= result.response.status_code < 500 and result.response.status_code != 401,
        "角色请求未被业务校验或权限明确拒绝",
    )
    value = result.get_json()
    error = value.get("error") if isinstance(value, dict) else None
    check(isinstance(error, str), "角色拒绝缺少明确原因")
    words = error.casefold()
    if kind == "required":
        accepted = "name" in words and any(
            word in words for word in ("required", "blank", "empty", "presence")
        )
    elif kind == "duplicate":
        accepted = "name" in words and any(
            word in words for word in ("taken", "exists", "unique", "duplicate")
        )
    elif kind == "permission":
        accepted = any(
            word in words
            for word in (
                "not authorized",
                "not authorised",
                "access denied",
                "permission denied",
                "forbidden",
                "token authorization failed",
            )
        )
    else:
        accepted = False
    check(accepted, "角色拒绝原因与原用例不符")


def assert_permissions(rows: object, expected: dict[str, int]) -> None:
    """权限集合必须精确匹配且全部活跃，不允许额外管理权限。"""
    check(
        isinstance(rows, list)
        and len(rows) == len(expected)
        and all(
            isinstance(row, dict)
            and row.get("active") is True
            and valid_id(row.get("id"))
            and expected.get(row.get("name")) == row["id"]
            for row in rows
        ),
        "角色实际权限集合不符",
    )
    check({row["name"] for row in rows} == set(expected), "角色权限重复或缺失")


class RoleCase:
    """只管理本条新角色，原系统角色永不进入保留修改目标。"""

    def __init__(self, ad, maintenance, customer, context, mapping, data, recorder, db) -> None:
        """构造原始请求；name模板和权限逻辑名只在此映射一次。"""
        self.admin, self.maintenance = RolesAPI(ad), RolesAPI(maintenance)
        self.api = RolesAPI(customer if data["actor"] == "C1" else ad)
        self.run, self.mapping, self.data, self.recorder, self.db = (
            context.run,
            mapping,
            deepcopy(data),
            recorder,
            db,
        )
        self.case_id = data["case_id"]
        self.marker = f"{self.run.run_id}_{self.case_id}_role"
        self.payload = deepcopy(data["input"])
        if "name_template" in data:
            self.payload["name"] = self.marker
        self.name = self.payload["name"]
        self.payload["permission_ids"] = [
            mapping["permission_ids"][name] for name in data["permission_names"]
        ]
        self.creator_id = mapping["customer_id"] if data["actor"] == "C1" else mapping["admin_id"]
        self.key = self.role_id = None
        self.write_unknown = False
        self.integrity_unknown = False
        self.baseline = []
        self.before = None
        self.agent_before = None
        self.customer_before = None

    def customer_snapshot(self) -> dict:
        """核对C1实际权限与直接和继承组，避免Token范围隐藏提权。"""
        user_id = self.mapping["customer_id"]
        value = {
            "roles": self.db.user_roles(user_id),
            "permissions": self.db.user_permissions(user_id),
            "direct_groups": self.db.user_groups(user_id),
            "inherited_groups": self.db.inherited_groups(user_id),
        }
        self.recorder.record("customer-authorization", {"user_id": user_id, **value})
        return value

    def snapshot(self, role_id: int, label: str) -> dict:
        """由维护身份完整读取角色及DB关联，保留业务核查证据。"""
        result = self.maintenance.get(role_id)
        value = {
            "status": result.response.status_code,
            "api": result.get_json(),
            "db": self.db.role(role_id),
            "permissions": self.db.role_permissions(role_id),
            "groups": self.db.role_groups(role_id),
            "users": self.db.role_users(role_id),
        }
        self.recorder.record(label, value)
        return value

    def owned(self) -> dict:
        """API创建人、独立名称、数据库及ID差集共同证明本条归属。"""
        if not valid_id(self.role_id) or self.role_id in self.baseline:
            raise CleanupError("角色不是本条新建对象，禁止修改") from None
        value = self.snapshot(self.role_id, "role-ownership")
        if (
            value["status"] != 200
            or not isinstance(value["api"], dict)
            or not isinstance(value["db"], dict)
            or value["api"].get("created_by_id") != self.creator_id
            or any(
                row.get("id") != self.role_id or row.get("name") != self.name
                for row in (value["api"], value["db"])
            )
        ):
            raise CleanupError("本条角色归属无法确认") from None
        return value

    def create(self):
        """登记意图后仅POST一次，负向异常新建也保全证据并登记。"""
        if self.key is not None:
            raise CleanupError("同一角色场景不得重复创建") from None
        self.baseline = self.db.role_ids()
        self.before = self.db.role_inventory()
        if self.case_id in {"TC_145", "TC_148"} and self.db.roles_named(self.name):
            raise PreflightError("本轮角色名称已存在") from None
        if self.case_id == "TC_147":
            rows = self.db.roles_named("agent")
            if (
                len(rows) != 1
                or rows[0].get("id") != self.mapping["agent_role_id"]
                or rows[0].get("name") != "Agent"
                or rows[0].get("active") is not True
            ):
                raise PreflightError(
                    "大小写重名基线必须只有一个活跃Agent，不能有既有agent"
                ) from None
            self.agent_before = self.snapshot(self.mapping["agent_role_id"], "role-agent-before")
        if self.case_id == "TC_148":
            self.customer_before = self.customer_snapshot()
        self.recorder.ensure_complete()
        self.key = self.run.role_intent(self.case_id, self.marker, self.name, self.creator_id)
        self.write_unknown = True
        result = self.api.create(self.payload)
        self.write_unknown = result.response.status_code >= 500
        self.integrity_unknown = True
        ids = self.db.role_ids()
        added = set(ids) - set(self.baseline)
        self.recorder.record(
            "role-after-create",
            {
                "status": result.response.status_code,
                "before_ids": self.baseline,
                "after_ids": ids,
                "inventory": self.db.role_inventory(),
            },
        )
        if self.write_unknown:
            raise CleanupError("角色创建结果未知") from None
        value = result.get_json()
        returned = value.get("id") if isinstance(value, dict) else None
        if returned is not None and (
            not valid_id(returned) or returned in self.baseline or returned not in added
        ):
            raise CleanupError("创建响应返回了无效或已有角色ID") from None
        if not set(self.baseline).issubset(ids) or len(added) > 1:
            raise CleanupError("角色集合出现无法归属的变化") from None
        if added:
            self.role_id = next(iter(added))
            self.owned()
            self.run.role_created(self.key, self.role_id)
        self.integrity_unknown = False
        return result

    def assert_existing_unchanged(self) -> None:
        """排除本条新ID后精确验证所有原角色和关系保持。"""
        current = self.db.role_inventory(exclude_id=self.role_id)
        check(current == self.before, "本次请求改变了原有角色或关联")
        if self.case_id == "TC_148":
            check(self.customer_snapshot() == self.customer_before, "客户实际权限或组授权发生改变")
        if self.case_id == "TC_147":
            check(
                self.snapshot(self.mapping["agent_role_id"], "role-agent-after")
                == self.agent_before,
                "大小写重名请求改变了原Agent角色",
            )

    def cleanup(self) -> None:
        """无新增标absent；本条新角色停用清权限后明确标retained。"""
        if self.key is None:
            return
        try:
            self.recorder.ensure_complete()
            if self.write_unknown or self.integrity_unknown:
                raise CleanupError("角色写入或归属未知，禁止保留更新")
            if self.role_id is None:
                if (
                    self.case_id == "TC_145"
                    or self.db.role_ids() != self.baseline
                    or self.db.role_inventory() != self.before
                ):
                    raise CleanupError("角色负向请求无法证明无新增")
                self.assert_existing_unchanged()
                self.run.role_absent(self.key)
                return
            value = self.owned()
            if value["users"]:
                raise CleanupError("本条角色已关联用户，禁止修改")
            self.write_unknown = True
            result = self.admin.update(
                self.role_id,
                {
                    "active": False,
                    "permission_ids": [],
                    "group_ids": {},
                    "default_at_signup": False,
                },
            )
            self.write_unknown = result.response.status_code >= 500
            if result.response.status_code != 200:
                raise CleanupError("角色停用保留更新失败")
            saved = self.owned()
            for row in (saved["api"], saved["db"]):
                if row.get("active") is not False or row.get("default_at_signup") is not False:
                    raise CleanupError("角色保留属性不符")
            if (
                saved["api"].get("permission_ids") != []
                or saved["api"].get("group_ids") != {}
                or saved["permissions"]
                or saved["groups"]
                or saved["users"]
            ):
                raise CleanupError("角色保留仍存在权限或关联")
            self.assert_existing_unchanged()
            self.recorder.ensure_complete()
            self.run.role_retained(self.key)
            self.recorder.record(
                "role-retained",
                {
                    "role_id": self.role_id,
                    "name": self.name,
                    "active": False,
                    "permissions": [],
                    "groups": [],
                    "users": [],
                    "result": "retained",
                },
            )
        except Exception:
            self.run.attention(
                self.key, "create_unknown" if self.write_unknown else "cleanup_failed"
            )
            raise CleanupError("角色保留核查未完成，已标记待人工处理") from None
