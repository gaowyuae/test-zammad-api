"""文章与附件的只读查询及必要证据。"""

import re

from db.base import DbError, DbSession, check_id

_ARTICLE_COLUMNS = (
    "id, ticket_id, type_id, sender_id, subject, body, content_type, internal, "
    "created_by_id, updated_by_id, updated_at"
)


def check_access(db: DbSession) -> None:
    """在连接初始化时探测文章及类型的基础列授权。"""
    db.rows(f"SELECT {_ARTICLE_COLUMNS} FROM public.ticket_articles WHERE FALSE")
    db.rows("SELECT name FROM public.ticket_article_types WHERE FALSE")


def articles(db: DbSession, ticket_id: int) -> list[dict]:
    """仅读取指定工单文章，并按 ID 固定顺序返回。"""
    check_id(ticket_id)
    result = db.rows(
        f"SELECT {_ARTICLE_COLUMNS} FROM public.ticket_articles WHERE ticket_id = %s ORDER BY id",
        (ticket_id,),
    )
    db.recorder.record(
        "db-articles", {"query": "articles", "ticket_id": ticket_id, "result": result}
    )
    return result


def article_type_exists(db: DbSession, name: str) -> bool:
    """参数化确认指定文章类型是否存在，不读取整个字典。"""
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise DbError("文章类型名称无效") from None
    rows = db.rows(
        "SELECT EXISTS(SELECT 1 FROM public.ticket_article_types WHERE name = %s) AS present",
        (name,),
    )
    result = rows[0]["present"]
    db.recorder.record(
        "db-article-type", {"query": "article-type-exists", "name": name, "result": result}
    )
    return result


def check_attachment_access(db: DbSession) -> None:
    """仅附件用例在造数据前探测已批准的元数据列权限。"""
    db.rows("SELECT id, store_object_id, o_id, size, filename FROM public.stores WHERE FALSE")
    db.rows("SELECT id, name FROM public.store_objects WHERE FALSE")


def attachments(db: DbSession, article_id: int) -> list[dict]:
    """参数化读取指定文章附件元数据，不读取文件存储正文。"""
    check_id(article_id)
    rows = db.rows(
        "SELECT s.id, s.o_id AS article_id, s.filename, s.size "
        "FROM public.stores AS s JOIN public.store_objects AS o ON o.id = s.store_object_id "
        "WHERE o.name = 'Ticket::Article' AND s.o_id = %s ORDER BY s.id",
        (str(article_id),),
    )
    result = []
    seen = set()
    for row in rows:
        if (
            set(row) != {"id", "article_id", "filename", "size"}
            or type(row["id"]) is not int
            or row["id"] <= 0
            or row["id"] in seen
            or row["article_id"] != str(article_id)
            or not isinstance(row["filename"], str)
            or not row["filename"]
            or not isinstance(row["size"], str)
            or not re.fullmatch(r"[0-9]{1,20}", row["size"])
        ):
            raise DbError("附件元数据证据无效") from None
        seen.add(row["id"])
        result.append({**row, "article_id": article_id, "size": int(row["size"])})
    db.recorder.record("db-attachments", {"article_id": article_id, "rows": result})
    return result
