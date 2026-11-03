"""模板的只读查询及必要证据。"""

from db.base import DbError, DbSession, check_id, inventory, store_row
from db.sql import access

_TEMPLATE_COLUMNS = "id, name, options, active, created_by_id"

_INVENTORY_SQL = (
    "WITH selected AS (SELECT id, name, options, active, created_by_id FROM public.templates "
    "WHERE (%s::bigint IS NULL OR id<>%s)), items AS (SELECT 'object' AS kind,id AS "
    "oid,ROW(id, name, options, active, created_by_id)::text AS value FROM selected) SELECT "
    "(SELECT count(*) FROM selected) AS count, "
    "md5(COALESCE(string_agg(ROW(kind,oid,value)::text,'' ORDER BY kind,oid,value),'')) AS "
    "fingerprint FROM items"
)


def check_template_access(db: DbSession) -> None:
    """仅模板用例需要本表五个已批准列。"""
    access.check_tables(db, ("templates",))
    db.rows(f"SELECT {_TEMPLATE_COLUMNS} FROM public.templates WHERE FALSE")


def template(db: DbSession, object_id: int) -> dict | None:
    """按明确ID读取模板并安全解码预填字段。"""
    check_id(object_id)
    rows = db.rows(f"SELECT {_TEMPLATE_COLUMNS} FROM public.templates WHERE id = %s", (object_id,))
    value = store_row(rows[0], "options") if rows else None
    db.recorder.record("db-template", {"id": object_id, "result": value})
    return value


def template_ids(db: DbSession) -> list[int]:
    """只读模板ID集合，识别空白名称场景的异常新增。"""
    return [row["id"] for row in db.rows("SELECT id FROM public.templates ORDER BY id")]


def templates_named(db: DbSession, name: str) -> list[dict]:
    """精确按原始名称查询，负向空格不被strip改写。"""
    if not isinstance(name, str) or len(name) > 255:
        raise DbError("模板名称参数无效") from None
    rows = db.rows(
        f"SELECT {_TEMPLATE_COLUMNS} FROM public.templates WHERE name = %s ORDER BY id", (name,)
    )
    return [store_row(row, "options") for row in rows]


def template_inventory(db: DbSession, exclude_id: int | None = None) -> dict:
    """模板修改前后核对既有配置或排除本轮明确ID。"""
    return inventory(db, _INVENTORY_SQL, exclude_id, "template")
