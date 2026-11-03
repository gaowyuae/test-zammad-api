"""宏的只读查询及必要证据。"""

from db.base import DbError, DbSession, check_id, inventory, store_row
from db.sql import access

_MACRO_COLUMNS = "id, name, perform, active, ux_flow_next_up, created_by_id"

_INVENTORY_SQL = (
    "WITH selected AS (SELECT id, name, perform, active, ux_flow_next_up, created_by_id FROM "
    "public.macros WHERE (%s::bigint IS NULL OR id<>%s)), items AS (SELECT 'object' AS "
    "kind,id AS oid,ROW(id, name, perform, active, ux_flow_next_up, created_by_id)::text AS "
    "value FROM selected UNION ALL SELECT 'group',g.macro_id,ROW(g.group_id)::text FROM "
    "public.groups_macros g JOIN selected r ON r.id=g.macro_id) SELECT (SELECT count(*) FROM "
    "selected) AS count, md5(COALESCE(string_agg(ROW(kind,oid,value)::text,'' ORDER BY "
    "kind,oid,value),'')) AS fingerprint FROM items"
)


def check_macro_access(db: DbSession) -> None:
    """宏用例才探测动作与组关联的最小只读列。"""
    access.check_tables(db, ("macros", "groups_macros"))
    db.rows(f"SELECT {_MACRO_COLUMNS} FROM public.macros WHERE FALSE")
    db.rows("SELECT macro_id, group_id FROM public.groups_macros WHERE FALSE")


def macro(db: DbSession, object_id: int) -> dict | None:
    """按明确ID安全解码宏动作，不读取其他宏内容。"""
    check_id(object_id)
    rows = db.rows(f"SELECT {_MACRO_COLUMNS} FROM public.macros WHERE id = %s", (object_id,))
    value = store_row(rows[0], "perform") if rows else None
    db.recorder.record("db-macro", {"id": object_id, "result": value})
    return value


def macro_ids(db: DbSession) -> list[int]:
    """只读取宏ID集合，识别新增而不导出现有配置。"""
    return [row["id"] for row in db.rows("SELECT id FROM public.macros ORDER BY id")]


def macros_named(db: DbSession, name: str) -> list[dict]:
    """按唯一运行名称定位宏，值以参数绑定。"""
    if not isinstance(name, str) or not name or len(name) > 255:
        raise DbError("宏名称参数无效") from None
    rows = db.rows(
        f"SELECT {_MACRO_COLUMNS} FROM public.macros WHERE name = %s ORDER BY id", (name,)
    )
    return [store_row(row, "perform") for row in rows]


def macro_groups(db: DbSession, object_id: int) -> list[int]:
    """核对明确宏的组集合，删除后确认关联无残留。"""
    check_id(object_id)
    return [
        row["group_id"]
        for row in db.rows(
            "SELECT group_id FROM public.groups_macros WHERE macro_id = %s ORDER BY group_id",
            (object_id,),
        )
    ]


def macro_inventory(db: DbSession, exclude_id: int | None = None) -> dict:
    """宏及组关联整体摘要用于保护既有配置。"""
    return inventory(db, _INVENTORY_SQL, exclude_id, "macro")
