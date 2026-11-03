"""工单的只读查询及必要证据。"""

import re

from db.base import DbError, DbSession, check_id
from db.sql.articles import _ARTICLE_COLUMNS

_TICKET_COLUMNS = (
    "id, number, title, group_id, owner_id, customer_id, state_id, priority_id, "
    "article_count, created_by_id, updated_by_id, updated_at"
)


def check_access(db: DbSession) -> None:
    """在连接初始化时探测工单基础列授权。"""
    db.rows(f"SELECT {_TICKET_COLUMNS} FROM public.tickets WHERE FALSE")


def ticket_number_exists(db: DbSession, number: str) -> bool:
    """通过精确编号确认空搜索的前置，不读取无关工单内容。"""
    if not isinstance(number, str) or re.fullmatch(r"[0-9]{1,100}", number) is None:
        raise DbError("工单编号必须为有限长度的数字文本") from None
    rows = db.rows(
        "SELECT EXISTS(SELECT 1 FROM public.tickets WHERE number = %s) AS present",
        (number,),
    )
    if len(rows) != 1 or type(rows[0].get("present")) is not bool:
        raise DbError("工单编号存在性证据无效") from None
    db.recorder.record("db-ticket-number", {"number": number, "present": rows[0]["present"]})
    return rows[0]["present"]


def ticket(db: DbSession, ticket_id: int) -> dict | None:
    """按工单 ID 返回必要字段快照，不存在时返回 None。"""
    check_id(ticket_id)
    rows = db.rows(f"SELECT {_TICKET_COLUMNS} FROM public.tickets WHERE id = %s", (ticket_id,))
    result = rows[0] if rows else None
    db.recorder.record("db-ticket", {"query": "ticket", "ticket_id": ticket_id, "result": result})
    return result


def group_ticket_ids(db: DbSession, group_id: int) -> list[int]:
    """仅查询专用组工单集合，不把实例总量当作基线规模。"""
    check_id(group_id)
    rows = db.rows("SELECT id FROM public.tickets WHERE group_id = %s ORDER BY id", (group_id,))
    db.recorder.record("db-group-tickets", {"group_id": group_id, "result": rows})
    return [row["id"] for row in rows]


def check_pending_access(db: DbSession) -> None:
    """仅M11在造数据前核验pending_time列权限，不扩展旧用例的必需列。"""
    db.rows("SELECT pending_time FROM public.tickets WHERE FALSE")
    db.recorder.record("db-pending-access", {"query": "pending-access", "result": "ready"})


def ticket_pending(db: DbSession, ticket_id: int) -> dict | None:
    """按ID读取状态及挂起时间，明确将Rails的无时区存储值解释为UTC。"""
    check_id(ticket_id)
    rows = db.rows(
        "SELECT id, state_id, pending_time AT TIME ZONE 'UTC' AS pending_time "
        "FROM public.tickets WHERE id = %s",
        (ticket_id,),
    )
    result = rows[0] if rows else None
    db.recorder.record(
        "db-pending", {"query": "ticket-pending", "ticket_id": ticket_id, "result": result}
    )
    return result


def ticket_inventory(db: DbSession) -> dict:
    """只返回已授权工单列的服务端摘要，用于核对不存在目标的更新无副作用。"""
    rows = db.rows(
        "SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id, "
        f"md5(COALESCE(string_agg(md5(ROW({_TICKET_COLUMNS})::text), '' ORDER BY id), '')) "
        "AS fingerprint FROM public.tickets"
    )
    if len(rows) != 1:
        raise DbError("工单摘要证据缺失") from None
    value = rows[0]
    if (
        set(value) != {"count", "max_id", "fingerprint"}
        or type(value["count"]) is not int
        or value["count"] < 0
        or type(value["max_id"]) is not int
        or value["max_id"] < 0
        or not isinstance(value["fingerprint"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", value["fingerprint"])
    ):
        raise DbError("工单摘要证据格式不符") from None
    db.recorder.record("db-inventory", {"query": "ticket-inventory", "result": value})
    return value


def by_marker(db: DbSession, marker: str) -> dict[str, list[dict]]:
    """按完整 RUN 和 TC 字面标记定位本次工单及文章残留。"""
    if (
        not isinstance(marker, str)
        or len(marker) > 200
        or not re.fullmatch(r"RUN_[A-Za-z0-9][A-Za-z0-9_-]*_TC_[0-9]+(?:_[A-Za-z0-9_-]+)?", marker)
    ):
        raise DbError("数据库查询需要安全的 RUN 和 TC 标记") from None
    result = {
        "tickets": db.rows(
            f"SELECT {_TICKET_COLUMNS} FROM public.tickets WHERE POSITION(%s IN title) > 0 ORDER BY id",
            (marker,),
        ),
        "articles": db.rows(
            f"SELECT {_ARTICLE_COLUMNS} FROM public.ticket_articles WHERE POSITION(%s IN body) > 0 ORDER BY id",
            (marker,),
        ),
    }
    db.recorder.record("db-marker", {"query": "by-marker", "marker": marker, "result": result})
    return result


def ticket_ids_for_creator(
    db: DbSession, group_id: int, customer_id: int, creator_id: int
) -> list[int]:
    """在专用组、客户及创建人范围对比ID，覆盖无标题且无文章的孤儿工单。"""
    for value in (group_id, customer_id, creator_id):
        check_id(value)
    rows = db.rows(
        "SELECT id FROM public.tickets WHERE group_id = %s AND customer_id = %s "
        "AND created_by_id = %s ORDER BY id",
        (group_id, customer_id, creator_id),
    )
    result = [row["id"] for row in rows]
    db.recorder.record(
        "db-creator-scope",
        {
            "group_id": group_id,
            "customer_id": customer_id,
            "creator_id": creator_id,
            "ticket_ids": result,
        },
    )
    return result
