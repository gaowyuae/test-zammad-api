"""业务权限的只读查询及必要证据。"""

import re

from db.base import DbError, DbSession, check_id


def check_permission_access(db: DbSession) -> None:
    """仅权限用例核验新增关联列及无写权限，不影响旧用例前置。"""
    rows = db.rows(
        "SELECT EXISTS (SELECT 1 FROM (VALUES ('public.groups_users'), "
        "('public.roles_groups'), ('public.permissions_roles'), ('public.permissions')) "
        "AS objects(name) WHERE pg_catalog.has_table_privilege(current_user, objects.name, "
        "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') OR "
        "pg_catalog.has_any_column_privilege(current_user, objects.name, "
        "'INSERT,UPDATE,REFERENCES')) AS object_write"
    )
    if rows != [{"object_write": False}]:
        raise DbError("权限关联查询要求独立只读权限") from None
    for sql in (
        "SELECT user_id, group_id, access FROM public.groups_users WHERE FALSE",
        "SELECT role_id, group_id, access FROM public.roles_groups WHERE FALSE",
        "SELECT role_id, permission_id FROM public.permissions_roles WHERE FALSE",
        "SELECT id, name, active FROM public.permissions WHERE FALSE",
    ):
        db.rows(sql)
    db.recorder.record("db-permission-access", {"result": "ready"})


def permission(db: DbSession, name: str) -> dict | None:
    """参数化读取明确权限名称，不接受查询表达式。"""
    if not isinstance(name, str) or not name or len(name) > 255:
        raise DbError("权限名称参数无效") from None
    rows = db.rows("SELECT id, name, active FROM public.permissions WHERE name = %s", (name,))
    if len(rows) > 1:
        raise DbError("权限名称映射不唯一") from None
    return rows[0] if rows else None


def user_groups(db: DbSession, user_id: int) -> list[dict]:
    """读取本条用户的直接组授权，保留每个access值。"""
    check_id(user_id)
    rows = db.rows(
        "SELECT group_id, access FROM public.groups_users WHERE user_id = %s ORDER BY group_id, access",
        (user_id,),
    )
    db.recorder.record("db-user-groups", {"user_id": user_id, "result": rows})
    return rows


def inherited_groups(db: DbSession, user_id: int) -> list[dict]:
    """按用户当前角色取得继承组权限，角色活跃状态单独核验。"""
    check_id(user_id)
    rows = db.rows(
        "SELECT rg.role_id, rg.group_id, rg.access FROM public.roles_groups AS rg "
        "JOIN public.roles_users AS ru ON ru.role_id = rg.role_id WHERE ru.user_id = %s "
        "ORDER BY rg.role_id, rg.group_id, rg.access",
        (user_id,),
    )
    db.recorder.record("db-inherited-groups", {"user_id": user_id, "result": rows})
    return rows


def user_permissions(db: DbSession, user_id: int) -> list[dict]:
    """读取当前角色关联的真实权限，避免Token范围遮蔽提权。"""
    check_id(user_id)
    rows = db.rows(
        "SELECT DISTINCT p.id, p.name, p.active FROM public.permissions AS p "
        "JOIN public.permissions_roles AS pr ON pr.permission_id = p.id "
        "JOIN public.roles_users AS ru ON ru.role_id = pr.role_id "
        "WHERE ru.user_id = %s ORDER BY p.id",
        (user_id,),
    )
    db.recorder.record("db-user-permissions", {"user_id": user_id, "result": rows})
    return rows


def other_user_groups(db: DbSession, user_id: int) -> dict:
    """在服务端比较排除本条U的授权集合，仅返回数量和摘要。"""
    check_id(user_id)
    rows = db.rows(
        "SELECT COUNT(*) AS count, md5(COALESCE(string_agg("
        "ROW(kind, actor_id, group_id, access)::text, '' ORDER BY kind, actor_id, group_id, access), '')) "
        "AS fingerprint FROM (SELECT 'user' AS kind, user_id AS actor_id, group_id, access "
        "FROM public.groups_users WHERE user_id <> %s UNION ALL SELECT 'role', role_id, "
        "group_id, access FROM public.roles_groups UNION ALL SELECT 'membership', user_id, "
        "role_id, '' FROM public.roles_users WHERE user_id <> %s) AS grants",
        (user_id, user_id),
    )
    if (
        len(rows) != 1
        or set(rows[0]) != {"count", "fingerprint"}
        or type(rows[0]["count"]) is not int
        or rows[0]["count"] < 0
        or not isinstance(rows[0]["fingerprint"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", rows[0]["fingerprint"])
    ):
        raise DbError("其他用户组权限摘要证据无效") from None
    db.recorder.record("db-other-user-groups", rows[0])
    return rows[0]
