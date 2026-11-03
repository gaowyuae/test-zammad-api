"""文本模块的只读查询及必要证据。"""

from db.base import DbError, DbSession, check_id, inventory
from db.sql import access

_TEXT_MODULE_COLUMNS = "id, name, keywords, content, note, active, created_by_id"

_INVENTORY_SQL = (
    "WITH selected AS (SELECT id, name, keywords, content, note, active, created_by_id FROM "
    "public.text_modules WHERE (%s::bigint IS NULL OR id<>%s)), items AS (SELECT 'object' AS "
    "kind,id AS oid,ROW(id, name, keywords, content, note, active, created_by_id)::text AS "
    "value FROM selected UNION ALL SELECT 'group',g.text_module_id,ROW(g.group_id)::text FROM"
    " public.groups_text_modules g JOIN selected r ON r.id=g.text_module_id) SELECT (SELECT "
    "count(*) FROM selected) AS count, md5(COALESCE(string_agg(ROW(kind,oid,value)::text,'' "
    "ORDER BY kind,oid,value),'')) AS fingerprint FROM items"
)


def check_text_module_access(db: DbSession) -> None:
    """仅文本用例需要内容、备注及组关联的最小列。"""
    access.check_tables(db, ("text_modules", "groups_text_modules"))
    db.rows(f"SELECT {_TEXT_MODULE_COLUMNS} FROM public.text_modules WHERE FALSE")
    db.rows("SELECT text_module_id, group_id FROM public.groups_text_modules WHERE FALSE")


def text_module(db: DbSession, object_id: int) -> dict | None:
    """按明确ID读取文本模块的已授权内容及备注。"""
    check_id(object_id)
    rows = db.rows(
        f"SELECT {_TEXT_MODULE_COLUMNS} FROM public.text_modules WHERE id = %s", (object_id,)
    )
    value = rows[0] if rows else None
    db.recorder.record("db-text-module", {"id": object_id, "result": value})
    return value


def text_module_ids(db: DbSession) -> list[int]:
    """仅取ID集合定位本轮异常新增，不导出其他文本正文。"""
    return [row["id"] for row in db.rows("SELECT id FROM public.text_modules ORDER BY id")]


def text_modules_named(db: DbSession, name: str) -> list[dict]:
    """按本轮唯一名称定位模块，不以搜索索引为空证明未保存。"""
    if not isinstance(name, str) or not name or len(name) > 255:
        raise DbError("文本模块名称参数无效") from None
    return db.rows(
        f"SELECT {_TEXT_MODULE_COLUMNS} FROM public.text_modules WHERE name = %s ORDER BY id",
        (name,),
    )


def text_module_groups(db: DbSession, object_id: int) -> list[int]:
    """核对指定文本模块的组ID，删除后还需确认无悬挂关联。"""
    check_id(object_id)
    return [
        row["group_id"]
        for row in db.rows(
            "SELECT group_id FROM public.groups_text_modules WHERE text_module_id = %s ORDER BY group_id",
            (object_id,),
        )
    ]


def text_module_inventory(db: DbSession, exclude_id: int | None = None) -> dict:
    """文本模块及组关联一同摘要，清理不能遗漏组关系。"""
    return inventory(db, _INVENTORY_SQL, exclude_id, "text_module")
