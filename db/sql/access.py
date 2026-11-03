"""数据库账号权限检查，区别于 Zammad 业务用户权限。"""

from db.base import DbError, DbSession

_ACCESS_SQL = """
SELECT rolcanlogin AS can_login,
       (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) AS elevated,
       pg_catalog.has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
       EXISTS (
           SELECT 1 FROM (VALUES ('public.tickets'), ('public.ticket_articles'),
                                 ('public.ticket_article_types'), ('public.stores'),
                                 ('public.store_objects')) AS objects(name)
           WHERE pg_catalog.has_table_privilege(
               current_user, objects.name, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
              OR pg_catalog.has_any_column_privilege(
               current_user, objects.name, 'INSERT,UPDATE,REFERENCES')
       ) AS object_write
FROM pg_catalog.pg_roles WHERE rolname = current_user
"""


def check_account(db: DbSession) -> None:
    """核实连接账号可登录且没有管理员、建表或业务写权限。"""
    if db.rows(_ACCESS_SQL) != [
        {
            "can_login": True,
            "elevated": False,
            "schema_create": False,
            "object_write": False,
        }
    ]:
        raise DbError("数据库连接、只读权限或必需列核查失败") from None


def check_tables(db: DbSession, tables: tuple[str, ...]) -> None:
    """检查配置模块传入的固定表名对应的账号写权限。"""
    for table in tables:
        rows = db.rows(
            "SELECT (pg_catalog.has_table_privilege(current_user,%s,"
            "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') OR "
            "pg_catalog.has_any_column_privilege(current_user,%s,'INSERT,UPDATE,REFERENCES')) AS object_write",
            (f"public.{table}", f"public.{table}"),
        )
        if rows != [{"object_write": False}]:
            raise DbError("配置对象查询要求独立只读权限") from None
