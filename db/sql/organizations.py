"""组织关联的只读查询及必要证据。"""

from db.base import DbError, DbSession, check_id
from db.sql import users


def check_search_access(db: DbSession) -> None:
    """基线才校验主次组织最小列和相关只读边界。"""
    users.check_user_access(db)
    for table in ("organizations", "organizations_users"):
        rows = db.rows(
            "SELECT (pg_catalog.has_table_privilege(current_user,%s,"
            "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') OR "
            "pg_catalog.has_any_column_privilege(current_user,%s,'INSERT,UPDATE,REFERENCES')) AS object_write",
            (f"public.{table}", f"public.{table}"),
        )
        if rows != [{"object_write": False}]:
            raise DbError("基线组织证据要求只读账号") from None
    db.rows("SELECT id, shared, active FROM public.organizations WHERE FALSE")
    db.rows("SELECT user_id, organization_id FROM public.organizations_users WHERE FALSE")


def user_organizations(db: DbSession, user_id: int) -> list[dict]:
    """合并主次组织并保留悬挂关联错误，不能静默当作无共享。"""
    check_id(user_id)
    rows = db.rows(
        "WITH linked AS (SELECT organization_id AS id FROM public.users "
        "WHERE id = %s AND organization_id IS NOT NULL UNION SELECT organization_id "
        "FROM public.organizations_users WHERE user_id = %s) "
        "SELECT l.id,o.shared,o.active FROM linked l LEFT JOIN public.organizations o "
        "ON o.id=l.id ORDER BY l.id",
        (user_id, user_id),
    )
    if any(
        not isinstance(row.get("shared"), bool) or not isinstance(row.get("active"), bool)
        for row in rows
    ):
        raise DbError("组织关联证据缺失或格式无效") from None
    db.recorder.record("db-user-organizations", {"user_id": user_id, "result": rows})
    return rows
