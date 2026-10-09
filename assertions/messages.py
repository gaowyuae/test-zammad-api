"""消息和附件场景共用的授权快照与业务断言。"""

import json
from copy import deepcopy

from assertions.tickets import FIELDS, assert_fields, check
from fixtures.ticket import valid_id

ARTICLE_FIELDS = (
    "id",
    "ticket_id",
    "body",
    "content_type",
    "internal",
    "created_by_id",
    "type_id",
    "sender_id",
)


def assert_article(value: object, expected: dict) -> None:
    """按显式业务预期核对文章，实际作者不能由sender名称替代。"""
    check(isinstance(value, dict), "文章结构不符")
    for field in ARTICLE_FIELDS:
        check(field in value and value[field] == expected[field], f"文章 {field} 不符")


def assert_no_leak(result, hidden: list[str]) -> None:
    """原响应及解析后内容均不允许出现任一内部正文。"""
    text = result.response.text
    parsed = json.dumps(result.get_json(), ensure_ascii=False)
    for body in hidden:
        check(body and body not in text and body not in parsed, "响应泄露内部文章正文")


def assert_rejection(result, hidden: list[str]) -> None:
    """只接受当前授权路径的明确403，不把其他4xx当作通过。"""
    check(result.response.status_code == 403, "访问拒绝状态码不符")
    value = result.get_json()
    check(isinstance(value, dict) and value.get("error") == "Not authorized", "访问拒绝原因不符")
    assert_no_leak(result, hidden)


def assert_visible(result, public: list[dict], hidden: list[str]) -> None:
    """公开列表必须完整无重复，逐字段核验且不泄露内部内容。"""
    check(result.response.status_code == 200, "客户文章列表读取失败")
    rows = result.get_json()
    check(isinstance(rows, list) and all(isinstance(row, dict) for row in rows), "文章列表结构不符")
    ids = [row.get("id") for row in rows]
    check(all(valid_id(value) for value in ids) and len(ids) == len(set(ids)), "文章ID重复或无效")
    check(set(ids) == {row["id"] for row in public}, "客户可见集合存在遗漏或越权")
    expected = {row["id"]: row for row in public}
    for row in rows:
        for field, value in expected[row["id"]].items():
            check(row.get(field) == value, f"客户文章 {field} 不符")
    assert_no_leak(result, hidden)


def snapshot(case, db, label: str) -> dict:
    """立即保存授权API及数据库快照，工单总量只比较数量和最大ID。"""
    ticket = case.maintenance.read(case.ticket_id)
    articles = case.maintenance.articles(case.ticket_id)
    inventory = db.ticket_inventory()
    value = {
        "ticket_status": ticket.response.status_code,
        "ticket": ticket.get_json(),
        "articles_status": articles.response.status_code,
        "articles": articles.get_json(),
        "db_ticket": db.ticket(case.ticket_id),
        "db_articles": db.articles(case.ticket_id),
        "inventory": {key: inventory[key] for key in ("count", "max_id")},
    }
    case.recorder.record(label, value)
    return value


def check_snapshot(value: dict, ticket_id: int) -> None:
    """确认授权回读成功及API/DB文章集合一致，再使用其中证据。"""
    check(value["ticket_status"] == 200 and value["articles_status"] == 200, "授权快照读取失败")
    check(isinstance(value["ticket"], dict), "授权工单结构不符")
    check(value["ticket"].get("id") == ticket_id, "授权工单ID不符")
    for key in ("articles", "db_articles"):
        rows = value[key]
        check(
            isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
            "快照文章结构不符",
        )
        ids = [row.get("id") for row in rows]
        check(all(valid_id(item) for item in ids) and len(ids) == len(set(ids)), "快照文章ID不符")
        check(all(row.get("ticket_id") == ticket_id for row in rows), "快照文章关联不符")
    check(
        {row["id"] for row in value["articles"]} == {row["id"] for row in value["db_articles"]},
        "API与数据库文章集合不一致",
    )


def assert_posted(
    case, result, payload: dict, snapshot_value: dict, creator_id: int, sender: str, internal: bool
) -> int:
    """响应、单条回读、展开列表及数据库共同验证本次文章。"""
    check(result.response.status_code == 201, "文章创建状态码不符")
    created = result.get_json()
    check(isinstance(created, dict) and valid_id(created.get("id")), "新文章ID缺失")
    article_id = created["id"]
    reread = case.maintenance.article(article_id)
    single = reread.get_json()
    case.recorder.record(
        "article-single-read", {"status": reread.response.status_code, "article": single}
    )
    check(reread.response.status_code == 200 and isinstance(single, dict), "单条文章回读失败")
    expanded = [row for row in snapshot_value["articles"] if row["id"] == article_id]
    stored = [row for row in snapshot_value["db_articles"] if row["id"] == article_id]
    check(len(expanded) == len(stored) == 1, "新文章未唯一保存")
    expanded = expanded[0]
    check(
        expanded.get("type") == payload["type"] and expanded.get("sender") == sender,
        "文章类型或发送方名称不符",
    )
    check(
        valid_id(expanded.get("type_id")) and valid_id(expanded.get("sender_id")), "文章字典ID无效"
    )
    expected = dict(
        id=article_id,
        ticket_id=case.ticket_id,
        body=payload["body"],
        content_type=payload["content_type"],
        internal=internal,
        created_by_id=creator_id,
        type_id=expanded["type_id"],
        sender_id=expanded["sender_id"],
    )
    for value in (created, single, expanded, stored[0]):
        assert_article(value, expected)
    return article_id


def prepare(case, db) -> dict:
    """每条用例独立由M建单，核实初始文章、客户及RUN归属。"""
    created = case.create()
    value = snapshot(case, db, "message-initial")
    check(created.response.status_code == 201, "消息前置工单创建失败")
    check_snapshot(value, case.ticket_id)
    ticket = value["ticket"]
    check(all(field in ticket for field in FIELDS), "初始工单字段不完整")
    expected = {field: ticket[field] for field in FIELDS}
    expected.update(
        id=case.ticket_id, created_by_id=case.mapping["maintenance_id"], article_count=1
    )
    for field in ("title", "customer_id", "group_id", "owner_id", "state_id", "priority_id"):
        expected[field] = case.payload[field]
    for row in (created.get_json(), ticket, value["db_ticket"]):
        assert_fields(row, expected)
    check(case.creator_id == case.mapping["maintenance_id"], "前置工单必须由M创建")
    check(len(value["articles"]) == 1, "初始文章数量不符")
    initial = value["articles"][0]
    check(case.article_ids == [initial["id"]], "初始文章ID不符")
    for row in (initial, value["db_articles"][0]):
        check(row.get("created_by_id") == case.creator_id, "初始文章实际作者不符")
        for field in ("body", "content_type", "internal"):
            check(row.get(field) == case.payload["article"][field], f"初始文章 {field} 不符")
    return value


def payload_for(case, scenario: dict) -> dict:
    """仅填入动态工单ID和RUN，不改写客户故意伪造的属性。"""
    payload = deepcopy(scenario["article"])
    payload.update(ticket_id=case.ticket_id, body=f"{payload['body']}；RUN={case.marker}；被测消息")
    return payload


def assert_ticket_stable(before: dict, after: dict, count: int) -> None:
    """追加文章只允许改变文章数及审计字段，不允许新增工单。"""
    check(after["inventory"] == before["inventory"], "消息请求新增或删除了工单")
    expected = {field: before["ticket"][field] for field in FIELDS}
    expected["article_count"] = count
    for value in (after["ticket"], after["db_ticket"]):
        assert_fields(value, expected)
