"""用户的只读查询及必要证据。"""

from db.base import DbError, DbSession, check_id

_USER_COLUMNS = (
    "id, login, firstname, lastname, email, active, organization_id, "
    "created_by_id, updated_by_id, created_at AT TIME ZONE 'UTC' AS created_at, "
    "updated_at AT TIME ZONE 'UTC' AS updated_at"
)


def check_user_access(db: DbSession) -> None:
    """用户场景才探测新列，并拒绝用户或角色关联对象的写权限。"""
    rows = db.rows(
        "SELECT EXISTS (SELECT 1 FROM (VALUES ('public.users'), ('public.roles'), "
        "('public.roles_users')) AS objects(name) WHERE "
        "pg_catalog.has_table_privilege(current_user, objects.name, "
        "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') OR "
        "pg_catalog.has_any_column_privilege(current_user, objects.name, "
        "'INSERT,UPDATE,REFERENCES')) AS object_write"
    )
    if rows != [{"object_write": False}]:
        raise DbError("用户及角色关联查询要求独立只读权限") from None
    for sql in (
        f"SELECT {_USER_COLUMNS} FROM public.users WHERE FALSE",
        "SELECT id, name, active FROM public.roles WHERE FALSE",
        "SELECT user_id, role_id FROM public.roles_users WHERE FALSE",
    ):
        db.rows(sql)
    db.recorder.record("db-user-access", {"result": "ready"})


def user(db: DbSession, user_id: int) -> dict | None:
    """按明确用户 ID 返回必要非凭据字段，不存在返回 None。"""
    check_id(user_id)
    rows = db.rows(f"SELECT {_USER_COLUMNS} FROM public.users WHERE id = %s", (user_id,))
    result = rows[0] if rows else None
    db.recorder.record("db-user", {"user_id": user_id, "result": result})
    return result


def users_by_login(db: DbSession, login: str) -> list[dict]:
    """按完整合成登录名查询，采用与应用一致的不区分大小写比较。"""
    if not isinstance(login, str) or not login or len(login) > 255:
        raise DbError("用户登录名查询参数无效") from None
    rows = db.rows(
        f"SELECT {_USER_COLUMNS} FROM public.users WHERE lower(login) = lower(%s) ORDER BY id",
        (login,),
    )
    db.recorder.record("db-user-login", {"login": login, "result": rows})
    return rows


def user_roles(db: DbSession, user_id: int) -> list[dict]:
    """核对指定用户角色关联，删除后仍能发现残留的关联键。"""
    check_id(user_id)
    rows = db.rows(
        "SELECT ru.role_id AS id, r.name, r.active FROM public.roles_users AS ru "
        "LEFT JOIN public.roles AS r ON r.id = ru.role_id WHERE ru.user_id = %s "
        "ORDER BY ru.role_id",
        (user_id,),
    )
    db.recorder.record("db-user-roles", {"user_id": user_id, "result": rows})
    return rows


def user_inventory(db: DbSession) -> dict:
    """不存在用户读取只比较数量及最大 ID，不导出全实例用户资料。"""
    rows = db.rows("SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id FROM public.users")
    if (
        len(rows) != 1
        or set(rows[0]) != {"count", "max_id"}
        or any(type(value) is not int or value < 0 for value in rows[0].values())
    ):
        raise DbError("用户数量证据缺失或格式无效") from None
    db.recorder.record("db-user-inventory", rows[0])
    return rows[0]
