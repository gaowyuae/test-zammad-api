"""M16 临时客户的身份核查、创建登记和安全清理。"""

import json
import secrets
from copy import deepcopy
from pathlib import Path

import allure
import yaml

from api.users import UsersAPI
from clients.http import HttpClient
from clients.identity import Identity
from fixtures.preflight import PreflightError, list_rows, permissions, read_json, unique
from fixtures.ticket import CleanupError, valid_id
from support.config import ConfigError, _ConfigLoader
from support.runtime import user_login

CASE_IDS = frozenset({"TC_137", "TC_138", "TC_139", "TC_140"})
USER_FIELDS = (
    "id",
    "login",
    "firstname",
    "lastname",
    "email",
    "active",
    "organization_id",
    "created_by_id",
    "created_at",
)


def load_user_cases(path: Path | None = None) -> dict:
    """读取四条明确场景，拒绝凭据、表达式及未登记字段。"""
    path = path or Path(__file__).resolve().parents[1] / "data/cases/users.yaml"
    try:
        rows = yaml.load(path.read_text(encoding="utf-8"), Loader=_ConfigLoader)
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        for case_id, data in rows.items():
            keys = {"actor", "expected"}
            if case_id != "TC_140":
                keys.add("input")
                if set(data["input"]) != {"firstname", "lastname", "active"}:
                    raise ValueError
                if data["input"]["active"] is not True:
                    raise ValueError
                if not all(
                    isinstance(data["input"][name], str) and data["input"][name]
                    for name in ("firstname", "lastname")
                ):
                    raise ValueError
            if case_id in {"TC_138", "TC_139"}:
                field = "duplicate" if case_id == "TC_138" else "update"
                keys.add(field)
                if (
                    set(data[field]) != {"firstname"}
                    or not isinstance(data[field]["firstname"], str)
                    or not data[field]["firstname"]
                    or data[field]["firstname"] == data["input"]["firstname"]
                ):
                    raise ValueError
            expected = {
                "TC_137": {"status_code": 201, "role": "Customer"},
                "TC_138": {"status_class": 4, "role": "Customer"},
                "TC_139": {"status_code": 200, "role": "Customer"},
                "TC_140": {"status_code": 404},
            }[case_id]
            if set(data) != keys or data["actor"] != "AD" or data["expected"] != expected:
                raise ValueError
        return deepcopy(rows)
    except (OSError, KeyError, ValueError, TypeError, yaml.YAMLError):
        raise ConfigError("用户场景数据缺失或结构无效") from None


def preflight_users(ad_client, maintenance_client, context, queries) -> dict:
    """写入前核实独立 AD、M、客户角色及本模块只读列。"""
    queries.check_user_access()
    people = []
    for client, login in (
        (ad_client, context.creation_config.admin_login),
        (maintenance_client, context.config.maintenance_login),
    ):
        user = read_json(client, "api/v1/users/me")
        if (
            not isinstance(user, dict)
            or user.get("login") != login
            or not valid_id(user.get("id"))
            or user.get("active") is not True
        ):
            raise PreflightError("用户场景身份与配置不一致") from None
        people.append(user)
    roles = list_rows(maintenance_client, "api/v1/roles")
    admin, maintenance = people
    a_permissions, _ = permissions(admin, roles)
    m_permissions, _ = permissions(maintenance, roles)
    if (
        admin["id"] == maintenance["id"]
        or "admin" not in a_permissions
        or {"ticket.agent", "ticket.customer"} & a_permissions
        or not {"admin", "ticket.agent"}.issubset(m_permissions)
    ):
        raise PreflightError("AD 必须为纯管理员且与维护 M 独立") from None
    customer = unique(roles, "name", "Customer")
    customer_permissions = set(customer.get("permissions", []))
    if (
        "ticket.customer" not in customer_permissions
        or "ticket.agent" in customer_permissions
        or any(name == "admin" or name.startswith("admin.") for name in customer_permissions)
    ):
        raise PreflightError("Customer 角色不是普通客户权限") from None
    return {
        "admin_id": admin["id"],
        "maintenance_id": maintenance["id"],
        "customer_role_id": customer["id"],
    }


class UserCase:
    """仅持有当前用例新建客户，不修改固定执行身份。"""

    def __init__(self, ad, maintenance, run, mapping, data, recorder, db, settings) -> None:
        """创建独立输入与密码，秘密先登记后才能进入请求链。"""
        self.api = UsersAPI(ad)
        self.maintenance = UsersAPI(maintenance)
        self.run, self.mapping, self.recorder, self.db = run, mapping, recorder, db
        self.settings = settings
        self.data = deepcopy(data)
        self.case_id = data["case_id"]
        self.marker = f"{run.run_id}_{self.case_id}"
        self.login = user_login(run.run_id, self.case_id)
        self.user_id = None
        self.resources = []
        self.write_unknown = False
        self._password = "AzBy47!" + secrets.token_urlsafe(24)
        self._replacement = "CxDy58!" + secrets.token_urlsafe(24)
        for password in (self._password, self._replacement):
            recorder.redactor.register(password)
        self.payload = {
            **data.get("input", {}),
            "login": self.login,
            "email": self.login,
            "password": self._password,
            "role_ids": [mapping["customer_role_id"]],
        }

    def create(self):
        """确认唯一登录名并先登记意图，发送一次独立创建。"""
        if self.resources or self.db.users_by_login(self.login):
            raise PreflightError("新客户登录名已存在或已提交") from None
        return self._create(self.payload, self.marker)

    def _create(self, payload: dict, marker: str):
        """保存每次创建结果；超时及服务错误保留未知状态。"""
        __tracebackhide__ = True  # 上下层同样隐藏含密码参数的回溯帧。
        self.recorder.ensure_complete()
        key = self.run.user_intent(self.case_id, marker, self.login)
        entry = {"key": key, "id": None, "status": None}
        self.resources.append(entry)
        self.write_unknown = True
        result = self.api.create(payload)
        self.write_unknown = result.response.status_code >= 500
        entry["status"] = result.response.status_code
        value = result.get_json()
        if isinstance(value, dict) and valid_id(value.get("id")):
            entry["id"] = value["id"]
            self.run.user_created(key, value["id"])
            if self.user_id is None:
                self.user_id = value["id"]
        return result

    def duplicate(self):
        """只复用本条已建登录名，刻意提交不同资料和密码。"""
        if self.user_id is None or len(self.resources) != 1:
            raise RuntimeError("重复创建必须先准备本条独立客户") from None
        payload = {
            **self.payload,
            **self.data["duplicate"],
            "email": f"duplicate.{self.login}",
            "password": self._replacement,
        }
        return self._create(payload, f"{self.marker}_duplicate")

    def update_firstname(self):
        """仅提交 firstname，更新超时不重发也不启动冲突清理。"""
        self.write_unknown = True
        result = self.api.update(self.user_id, deepcopy(self.data["update"]))
        self.write_unknown = result.response.status_code >= 500
        return result

    def authenticate(self):
        """只对本条新客户以原密码验证自身，不创建 Token。"""
        with HttpClient(
            self.settings, Identity("U"), self.recorder, basic_auth=(self.login, self._password)
        ) as client:
            return client.request("GET", "api/v1/users/me")

    def no_password(self, result) -> bool:
        """检查完整原始响应而非脱敏副本，不向断言输出秘密。"""
        raw = result.response.content
        try:
            decoded = json.dumps(result.get_json(), ensure_ascii=False).encode()
        except (ValueError, TypeError):
            decoded = b""
        return not any(
            password.encode() in body
            for password in (self._password, self._replacement)
            for body in (raw, decoded)
        )

    def snapshot(self, label: str) -> dict:
        """先保全接口、数据库与角色快照，随后由测试核对。"""
        result = self.api.read(self.user_id)
        snapshot = {
            "status": result.response.status_code,
            "api": result.get_json(),
            "db": self.db.user(self.user_id),
            "roles": self.db.user_roles(self.user_id),
            "same_login": self.db.users_by_login(self.login),
            "no_password": self.no_password(result),
        }
        self.recorder.record(label, snapshot)
        return snapshot

    def cleanup(self) -> None:
        """独立清理已知本轮用户，未知写入不与可能在途请求竞争。"""
        if not self.resources:
            return
        if self.write_unknown:
            for entry in self.resources:
                self.run.attention(entry["key"], "create_unknown")
            raise CleanupError("用户写入结果未知，已保留清单待人工核查") from None
        failures = []
        # 先核对被拒绝的重复请求，此时原对象仍存在，不能误判为新增残留。
        for entry in sorted(self.resources, key=lambda item: item["id"] is not None):
            try:
                self._cleanup_one(entry)
            except Exception:
                failures.append(entry["key"])
        if failures:
            raise CleanupError("用户清理未完成，已保留残留清单") from None

    def _cleanup_one(self, entry: dict) -> None:
        """通过明确 ID、RUN 登录名及创建人核实归属，再执行 API 删除。"""
        key, user_id = entry["key"], entry["id"]
        reason = "ownership_unconfirmed"
        try:
            if user_id is None:
                rows = self.db.users_by_login(self.login)
                if (
                    self.case_id == "TC_138"
                    and entry["status"] is not None
                    and 400 <= entry["status"] < 500
                    and len(rows) == 1
                    and rows[0]["id"] == self.user_id
                ):
                    self.run.user_absent(key)
                    self.recorder.record("user-duplicate-cleanup", {"result": "absent"})
                    return
                raise CleanupError("用户创建结果无法确认")
            with allure.step("M确认本轮临时客户归属并保存删除前证据"):
                result = self.maintenance.read(user_id)
                value = result.get_json()
                stored = self.db.user(user_id)
                self.recorder.record(
                    "user-pre-cleanup",
                    {"status": result.response.status_code, "api": value, "db": stored},
                )
                expected = {
                    "id": user_id,
                    "login": self.login,
                    "created_by_id": self.mapping["admin_id"],
                }
                if result.response.status_code != 200 or any(
                    not isinstance(row, dict)
                    or any(row.get(name) != val for name, val in expected.items())
                    for row in (value, stored)
                ):
                    raise CleanupError("用户清理归属无法确认")
            reason = "cleanup_failed"
            with allure.step("M删除临时客户并核查用户及角色关联无残留"):
                if self.maintenance.delete(user_id).response.status_code != 200:
                    raise CleanupError("用户 API 删除失败")
                if (
                    self.maintenance.read(user_id).response.status_code != 404
                    or self.db.user(user_id) is not None
                    or self.db.user_roles(user_id)
                ):
                    raise CleanupError("删除后的用户或角色关联仍存在")
                self.run.cleaned(key)
                self.recorder.record("user-cleanup", {"result": "cleaned", "user_id": user_id})
        except Exception:
            self.run.attention(key, reason)
            raise CleanupError("用户清理证据不完整或归属不符") from None
