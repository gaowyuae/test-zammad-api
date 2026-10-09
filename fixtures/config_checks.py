"""模板及文本模块共用的纯断言与只读身份核查，不管理资源流程。"""

from assertions.tickets import check
from fixtures.preflight import PreflightError, list_rows, permissions, read_json
from fixtures.ticket import CleanupError, valid_id


def assert_error(result, kind: str) -> None:
    """4xx必须指向场景字段或已核实身份的管理权限拒绝。"""
    __tracebackhide__ = True
    check(
        400 <= result.response.status_code < 500 and result.response.status_code != 401,
        "配置请求未被明确业务校验或权限拒绝",
    )
    value = result.get_json()
    error = value.get("error") if isinstance(value, dict) else None
    check(isinstance(error, str), "配置拒绝缺少明确错误原因")
    words = error.casefold()
    if kind == "name_required":
        matched = "name" in words and any(
            word in words for word in ("required", "blank", "empty", "presence")
        )
    elif kind == "note_length":
        matched = (
            "note" in words
            and any(word in words for word in ("too long", "longer", "maximum", "length", "limit"))
            and "250" in words
        )
    elif kind == "permission":
        matched = any(
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
        matched = False
    check(matched, "配置拒绝原因不符合用例")


def pure_actor(client, maintenance, login: str, role: str, db) -> int:
    """正向证明Token身份及工单scope，并核对API和DB纯业务角色。"""
    current = read_json(client, "api/v1/users/me")
    if (
        not isinstance(current, dict)
        or current.get("login") != login
        or current.get("active") is not True
        or not valid_id(current.get("id"))
    ):
        raise PreflightError("配置场景业务身份与登录名不一致") from None
    roles = list_rows(maintenance, "api/v1/roles")
    names, _ = permissions(current, roles)
    actual_roles = db.user_roles(current["id"])
    actual_permissions = db.user_permissions(current["id"])
    if (
        len(actual_roles) != 1
        or actual_roles[0].get("name") != role
        or actual_roles[0].get("active") is not True
        or current.get("role_ids") != [actual_roles[0]["id"]]
        or any(row.get("active") is not True for row in actual_permissions)
    ):
        raise PreflightError("业务身份不是唯一活跃目标角色") from None
    actual = {row["name"] for row in actual_permissions}
    required = f"ticket.{role.lower()}"
    other = "ticket.agent" if role == "Customer" else "ticket.customer"
    if (
        required not in names
        or required not in actual
        or other in names | actual
        or any(name == "admin" or name.startswith("admin.") for name in names | actual)
    ):
        raise PreflightError("业务身份存在管理权限或角色混用") from None
    read_json(client, "api/v1/ticket_create")
    return current["id"]


def new_object_id(result, baseline: list[int], after: list[int]) -> int | None:
    """只从单个新增ID确认候选，任何已有ID或其他集合变化都阻塞。"""
    __tracebackhide__ = True
    added = set(after) - set(baseline)
    value = result.get_json()
    returned = value.get("id") if isinstance(value, dict) else None
    if (
        not set(baseline).issubset(after)
        or len(added) > 1
        or (
            returned is not None
            and (not valid_id(returned) or returned in baseline or returned not in added)
        )
    ):
        raise CleanupError("配置创建返回已有ID或发生不可归属的集合变化") from None
    return next(iter(added)) if added else None


def assert_ownership(snapshot: dict, object_id: int, name: str, creator_id: int) -> None:
    """API和数据库必须指向同一ID、名称和实际创建人。"""
    __tracebackhide__ = True
    expected = {"id": object_id, "name": name, "created_by_id": creator_id}
    if snapshot["status"] != 200 or any(
        not isinstance(row, dict) or any(row.get(key) != value for key, value in expected.items())
        for row in (snapshot["api"], snapshot["db"])
    ):
        raise CleanupError("配置对象归属不符合本条已确认资源") from None
