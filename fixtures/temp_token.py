"""M17一次性Token及Customer短暂个人Token管理权限的生命周期。"""

from contextlib import contextmanager

import allure

from api.tokens import TokensAPI
from api.users import UsersAPI
from clients.http import HttpClient
from clients.identity import Identity
from fixtures.ticket import CleanupError, valid_id


class TempToken:
    """仅本条临时用户可使用，清理前恢复临时授权并验证撤销。"""

    def __init__(self, user, context, mapping: dict, recorder) -> None:
        """保存非敏感对象引用，Token值只存身份对象的隐藏字段。"""
        self.user, self.context, self.mapping, self.recorder = user, context, mapping, recorder
        self.key = None
        self.token_id = None
        self.identity = None
        self.unknown = False
        self.role_started = False
        self.name = f"{user.run.run_id}_{user.case_id}"
        self.customer = user.case_id == "TC_141"
        self.scope = "ticket.customer" if self.customer else "ticket.agent"
        self.role_ids = [mapping["customer_role_id"] if self.customer else mapping["agent_role_id"]]

    def _write(self, operation):
        """未知写入保持阻塞，明确业务拒绝则交由上层报告。"""
        self.unknown = True
        result = operation()
        self.unknown = result.response.status_code >= 500
        if result.response.status_code != 200:
            raise CleanupError("临时Token或授权操作未成功") from None
        return result

    def _owned(self, client) -> None:
        """所有权限准备前先核对本条用户ID、登录名和创建者。"""
        result = UsersAPI(client).read(self.user.user_id)
        value = result.get_json()
        if (
            result.response.status_code != 200
            or not isinstance(value, dict)
            or any(
                value.get(key) != expected
                for key, expected in {
                    "id": self.user.user_id,
                    "login": self.user.login,
                    "created_by_id": self.mapping["admin_id"],
                }.items()
            )
        ):
            raise CleanupError("临时Token用户归属无法确认") from None

    def _role(self, client) -> dict:
        """读取被批准的既有临时角色，不接受其他角色或默认注册角色。"""
        result = client.request("GET", f"api/v1/roles/{self.mapping['temp_role_id']}")
        value = result.get_json()
        if (
            result.response.status_code != 200
            or not isinstance(value, dict)
            or value.get("name") != self.context.permission_config["temp_role_name"]
            or value.get("default_at_signup") is not False
            or value.get("group_ids")
        ):
            raise CleanupError("临时Token管理角色前置不符") from None
        return value

    def _enable(self, client) -> None:
        """仅Customer管理Token期间启用空角色，不改系统Customer。"""
        self._owned(client)
        role = self._role(client)
        if (
            role.get("active") is not False
            or role.get("permission_ids")
            or self.user.db.role_users(role["id"])
        ):
            raise CleanupError("临时角色必须停用、空权限且无人关联") from None
        self.role_started = True
        self._write(
            lambda: client.request(
                "PUT",
                f"api/v1/roles/{role['id']}",
                json={
                    "active": True,
                    "permission_ids": [self.mapping["temp_permission_id"]],
                    "default_at_signup": False,
                },
            )
        )
        self._write(
            lambda: UsersAPI(client).update(
                self.user.user_id, {"role_ids": [*self.role_ids, role["id"]]}
            )
        )

    def _restore(self, client) -> None:
        """先恢复U原角色，再恢复临时角色停用空权限并验证。"""
        self._owned(client)
        assigned = self.user.db.role_users(self.mapping["temp_role_id"])
        if any(user_id != self.user.user_id for user_id in assigned):
            raise CleanupError("临时角色出现其他用户关联，停止修改") from None
        self._write(lambda: UsersAPI(client).update(self.user.user_id, {"role_ids": self.role_ids}))
        if self.user.db.role_users(self.mapping["temp_role_id"]):
            raise CleanupError("临时角色关联未解除") from None
        self._write(
            lambda: client.request(
                "PUT",
                f"api/v1/roles/{self.mapping['temp_role_id']}",
                json={
                    "active": False,
                    "permission_ids": [],
                    "default_at_signup": False,
                },
            )
        )
        role = self._role(client)
        if role.get("active") is not False or role.get("permission_ids"):
            raise CleanupError("临时角色恢复不完整") from None
        if [row["id"] for row in self.user.db.user_roles(self.user.user_id)] != self.role_ids:
            raise CleanupError("临时客户角色恢复不完整") from None
        self.role_started = False
        self.recorder.record(
            "temporary-role-restored",
            {"role_id": role["id"], "active": False, "assigned_users": []},
        )

    @contextmanager
    def _management(self):
        """使用U密码管理自身Token，Customer授权窗口仅覆盖此上下文。"""
        with HttpClient(
            self.context.settings, self.context.maintenance, self.recorder
        ) as maintenance:
            if self.customer:
                # 中途失败也必须尝试恢复明确已完成的授权，未知写入除外。
                try:
                    self._enable(maintenance)
                except Exception:
                    if self.role_started and not self.unknown:
                        self._restore(maintenance)
                    raise
            try:
                with HttpClient(
                    self.context.settings,
                    Identity("U"),
                    self.recorder,
                    basic_auth=(self.user.login, self.user._password),
                ) as client:
                    yield TokensAPI(client)
            finally:
                if self.customer and self.role_started and not self.unknown:
                    self._restore(maintenance)

    def _rows(self, api: TokensAPI) -> list[dict]:
        """读取完整且只属于当前用户的非敏感Token列表。"""
        result = api.list()
        value = result.get_json()
        if (
            result.response.status_code != 200
            or not isinstance(value, dict)
            or not isinstance(value.get("tokens"), list)
        ):
            raise CleanupError("临时Token元数据查询失败") from None
        rows = value["tokens"]
        if any(not isinstance(row, dict) or not valid_id(row.get("id")) for row in rows):
            raise CleanupError("临时Token元数据结构无效") from None
        return rows

    def issue(self) -> Identity:
        """先登记意图，仅签发一次，恢复纯身份后交给业务用例。"""
        if self.key is not None:
            raise CleanupError("同一用例不可重复签发Token") from None
        self.key = self.user.run.token_intent(
            self.user.case_id, self.name + "_token", self.user.user_id, self.name
        )
        with allure.step("签发本条U一次性Token并恢复原角色"), self._management() as api:
            if self._rows(api):
                raise CleanupError("新建临时用户存在非本条Token") from None
            result = self._write(lambda: api.create(self.name, self.scope))
            value = result.get_json()
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("token"), str)
                or not value["token"]
            ):
                raise CleanupError("签发响应未提供有效Token") from None
            self.recorder.redactor.register(value["token"])
            self.identity = Identity("U", value["token"])
            rows = self._rows(api)
            if (
                len(rows) != 1
                or rows[0].get("name") != self.name
                or rows[0].get("preferences", {}).get("permission") != [self.scope]
            ):
                raise CleanupError("Token归属、唯一性或作用域不符") from None
            self.token_id = rows[0]["id"]
            self.user.run.token_created(self.key, self.token_id)
        return self.identity

    def cleanup(self) -> None:
        """撤销本次Token、恢复临时权限，并确认旧Token不能继续认证。"""
        if self.key is None:
            return
        if self.unknown:
            self.user.run.attention(self.key, "create_unknown")
            raise CleanupError("Token或临时授权写入未知，禁止冲突清理") from None
        try:
            with allure.step("撤销本条Token并恢复临时权限"), self._management() as api:
                rows = self._rows(api)
                matching = [row for row in rows if row.get("name") == self.name]
                if len(matching) > 1 or (
                    self.token_id is not None
                    and (len(matching) != 1 or matching[0]["id"] != self.token_id)
                ):
                    raise CleanupError("Token清理归属或数量不符") from None
                if matching:
                    if self.token_id is None:
                        self.token_id = matching[0]["id"]
                        self.user.run.token_created(self.key, self.token_id)
                    self._write(lambda: api.delete(self.token_id))
                if self._rows(api):
                    raise CleanupError("临时用户仍有Token残留") from None
            if self.identity is not None:
                with HttpClient(self.context.settings, self.identity, self.recorder) as client:
                    result = client.request("GET", "api/v1/users/me")
                    if result.response.status_code not in {401, 403}:
                        raise CleanupError("撤销后的Token未被明确拒绝") from None
            if self.token_id is None:
                self.user.run.token_absent(self.key)
            else:
                self.user.run.cleaned(self.key)
            self.identity = None
            self.recorder.record(
                "temporary-token-cleanup",
                {"user_id": self.user.user_id, "token_id": self.token_id, "result": "revoked"},
            )
        except Exception:
            self.user.run.attention(
                self.key, "create_unknown" if self.unknown else "cleanup_failed"
            )
            raise CleanupError("临时Token清理或角色恢复未完成") from None
