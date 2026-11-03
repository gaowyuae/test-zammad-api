"""角色的只读查询及必要证据。"""

import re

from db.base import DbError, DbSession, check_id
from db.sql import permissions, users


def check_role_access(db: DbSession) -> None:
    """角色用例写入前检查已批准列及关联只读权限。"""
    users.check_user_access(db)
    permissions.check_permission_access(db)
    db.rows("SELECT id, name, active, default_at_signup FROM public.roles WHERE FALSE")


def role(db: DbSession, role_id: int) -> dict | None:
    """按明确ID读取角色安全属性，不读未授权审计列。"""
    check_id(role_id)
    rows = db.rows(
        "SELECT id, name, active, default_at_signup FROM public.roles WHERE id = %s", (role_id,)
    )
    result = rows[0] if rows else None
    db.recorder.record("db-role", {"role_id": role_id, "result": result})
    return result


def role_ids(db: DbSession) -> list[int]:
    """仅获取ID集合用于识别空名称负向请求是否新建对象。"""
    rows = db.rows("SELECT id FROM public.roles ORDER BY id")
    return [row["id"] for row in rows]


def roles_named(db: DbSession, name: str) -> list[dict]:
    """大小写不敏感匹配角色名，允许故意为空的负向输入。"""
    if not isinstance(name, str) or len(name) > 255:
        raise DbError("角色名称参数无效") from None
    rows = db.rows(
        "SELECT id, name, active, default_at_signup FROM public.roles "
        "WHERE lower(name) = lower(%s) ORDER BY id",
        (name,),
    )
    db.recorder.record("db-roles-named", {"name": name, "rows": rows})
    return rows


def role_permissions(db: DbSession, role_id: int) -> list[dict]:
    """读取单个角色的精确权限集合，包含活跃状态。"""
    check_id(role_id)
    rows = db.rows(
        "SELECT p.id, p.name, p.active FROM public.permissions AS p "
        "JOIN public.permissions_roles AS pr ON pr.permission_id = p.id "
        "WHERE pr.role_id = %s ORDER BY p.id",
        (role_id,),
    )
    db.recorder.record("db-role-permissions", {"role_id": role_id, "rows": rows})
    return rows


def role_groups(db: DbSession, role_id: int) -> list[dict]:
    """读取单个角色的组关联，用于确认获准保留时没有继承授权。"""
    check_id(role_id)
    return db.rows(
        "SELECT group_id, access FROM public.roles_groups WHERE role_id = %s "
        "ORDER BY group_id, access",
        (role_id,),
    )


def role_inventory(db: DbSession, exclude_id: int | None = None) -> dict:
    """在服务端比较角色及关联，排除本次新对象后证明旧角色未变。"""
    if exclude_id is not None:
        check_id(exclude_id)
    rows = db.rows(
        "WITH selected AS (SELECT id,name,active,default_at_signup FROM public.roles "
        "WHERE (%s::bigint IS NULL OR id <> %s)), "
        "items AS (SELECT 'role' AS kind, id AS rid, "
        "ROW(id,name,active,default_at_signup)::text AS value FROM selected UNION ALL "
        "SELECT 'permission',pr.role_id,ROW(pr.permission_id)::text FROM public.permissions_roles pr "
        "JOIN selected r ON r.id=pr.role_id UNION ALL "
        "SELECT 'group',rg.role_id,ROW(rg.group_id,rg.access)::text FROM public.roles_groups rg "
        "JOIN selected r ON r.id=rg.role_id UNION ALL "
        "SELECT 'user',ru.role_id,ROW(ru.user_id)::text FROM public.roles_users ru "
        "JOIN selected r ON r.id=ru.role_id) "
        "SELECT (SELECT count(*) FROM selected) AS count, "
        "md5(COALESCE(string_agg(ROW(kind,rid,value)::text,'' ORDER BY kind,rid,value),'')) "
        "AS fingerprint FROM items",
        (exclude_id, exclude_id),
    )
    if (
        len(rows) != 1
        or set(rows[0]) != {"count", "fingerprint"}
        or type(rows[0]["count"]) is not int
        or rows[0]["count"] < 0
        or not isinstance(rows[0]["fingerprint"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", rows[0]["fingerprint"])
    ):
        raise DbError("角色集合摘要证据无效") from None
    db.recorder.record("db-role-inventory", {"exclude_id": exclude_id, **rows[0]})
    return rows[0]


def role_users(db: DbSession, role_id: int) -> list[int]:
    """只核查临时管理角色的关联用户ID，不导出其他用户资料。"""
    check_id(role_id)
    return [
        row["user_id"]
        for row in db.rows(
            "SELECT user_id FROM public.roles_users WHERE role_id = %s ORDER BY user_id",
            (role_id,),
        )
    ]
