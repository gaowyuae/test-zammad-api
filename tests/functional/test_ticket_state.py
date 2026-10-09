"""TC_069～071 的状态原子更新、挂起时间保存与自动清空。"""

from datetime import UTC, datetime

import allure
import pytest

from assertions.tickets import FIELDS, assert_fields, check
from fixtures.state_change import update_payload
from fixtures.ticket import valid_id

pytestmark = [pytest.mark.functional, pytest.mark.database]


def instant(value: object) -> datetime:
    """只接受带偏移的原始时间，并转成UTC时刻比较。"""
    check(isinstance(value, str), "pending_time 不是时间字符串")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise AssertionError("pending_time 格式无效") from None
    check(parsed.tzinfo is not None and parsed.utcoffset() is not None, "pending_time 缺少时区")
    return parsed.astimezone(UTC)


def assert_pending(ticket: object, expected: str | None) -> None:
    """明确区分字段缺失、null和带时区时刻。"""
    check(isinstance(ticket, dict) and "pending_time" in ticket, "pending_time 字段缺失")
    if expected is None:
        check(ticket["pending_time"] is None, "pending_time 未明确清空")
    else:
        check(instant(ticket["pending_time"]) == instant(expected), "pending_time 时刻不符")


def ticket_value(result, status: int = 200) -> dict:
    """核对响应状态与结构，挂起时间由场景单独验证。"""
    check(result.response.status_code == status, "工单响应状态码不符")
    value = result.get_json()
    check(isinstance(value, dict), "工单响应结构不符")
    return value


def assert_rejection(result, state_id: int) -> None:
    """只接受明确非法状态拒绝，不接受权限错误或5xx。"""
    value = ticket_value(result, 422)
    check(
        value.get("error") == f"Invalid value for param 'state_id': {state_id}",
        "拒绝原因不是本次非法状态",
    )


def snapshot(case, db, label: str) -> dict:
    """先保存授权API和DB完整快照，再由调用方断言。"""
    ticket = case.maintenance.read(case.ticket_id)
    articles = case.maintenance.articles(case.ticket_id)
    value = {
        "ticket_status": ticket.response.status_code,
        "ticket": ticket.get_json(),
        "articles_status": articles.response.status_code,
        "articles": articles.get_json(),
        "db_ticket": db.ticket(case.ticket_id),
        "db_pending": db.ticket_pending(case.ticket_id),
        "db_articles": db.articles(case.ticket_id),
        "planned_pending_at": case.pending_at,
        "expected_pending_at": case.pending_at
        if (case.case_id, label) in {("TC_071", "state-before"), ("TC_070", "state-after")}
        else None,
    }
    case.recorder.record(label, value)
    return value


def assert_saved(value: dict, expected: dict, pending_at: str | None) -> None:
    """同时核对API、原授权工单列及独立挂起时间查询。"""
    check(value["ticket_status"] == 200 and value["articles_status"] == 200, "授权快照回读失败")
    for ticket in (value["ticket"], value["db_ticket"]):
        assert_fields(ticket, expected)
    stored = value["db_pending"]
    check(
        isinstance(stored, dict)
        and stored.get("id") == expected["id"]
        and stored.get("state_id") == expected["state_id"],
        "数据库挂起对象或状态不符",
    )
    assert_pending(value["ticket"], pending_at)
    assert_pending(stored, pending_at)


def prepare(case, db) -> dict:
    """独立建单并核验状态时间、业务字段及唯一初始文章作者。"""
    created = case.create()
    check(created.response.status_code == 201, "准备工单创建失败")
    before = snapshot(case, db, "state-before")
    ticket = before["ticket"]
    check(
        isinstance(ticket, dict) and all(field in ticket for field in FIELDS), "初始工单字段不完整"
    )
    expected = {field: ticket[field] for field in FIELDS}
    expected.update(id=case.ticket_id, created_by_id=case.creator_id, article_count=1)
    for field in ("title", "customer_id", "group_id", "owner_id", "state_id", "priority_id"):
        expected[field] = case.payload[field]
    check(case.creator_id == case.mapping["maintenance_id"], "准备身份必须为M")
    pending_at = case.pending_at if case.case_id == "TC_071" else None
    assert_saved(before, expected, pending_at)
    assert_fields(ticket_value(created, 201), expected)
    assert_pending(created.get_json(), pending_at)
    for key in ("ticket", "db_ticket"):
        check("updated_at" in before[key] and "updated_by_id" in before[key], "初始审计字段缺失")
    for key in ("articles", "db_articles"):
        rows = before[key]
        check(
            isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict),
            "初始文章数量或结构不符",
        )
        article = rows[0]
        check(valid_id(article.get("id")) and case.article_ids == [article["id"]], "初始文章ID不符")
        check(
            article.get("ticket_id") == case.ticket_id
            and article.get("created_by_id") == case.creator_id,
            "初始文章关联或作者不符",
        )
        fields = ("subject", "body", "content_type", "internal")
        if key == "articles":
            fields += ("type", "sender")
        for field in fields:
            check(article.get(field) == case.payload["article"][field], f"初始文章 {field} 不符")
    return before


def verify_state(state_case) -> None:
    """提交最小更新，先取证再验证响应、保存结果和文章无副作用。"""
    case, scenario, db, api = state_case
    with allure.step("M准备本用例独立工单及初始文章"):
        before = prepare(case, db)
    with allure.step("A1仅提交原场景要求的字段"):
        changed = case.update(api, update_payload(case))
    after = snapshot(case, db, "state-after")
    with allure.step("核对响应、API、数据库与完整文章快照"):
        if case.case_id == "TC_069":
            assert_rejection(changed, case.mapping["invalid_state_id"])
            check(after == before, "非法状态更新产生部分保存或快照变化")
        else:
            expected = {field: before["ticket"][field] for field in FIELDS}
            expected["state_id"] = case.mapping["target_state_id"]
            pending_at = case.pending_at if case.case_id == "TC_070" else None
            response = ticket_value(changed, scenario["expected"]["status_code"])
            assert_fields(response, expected)
            assert_pending(response, pending_at)
            assert_saved(after, expected, pending_at)
            for key in ("articles", "db_articles"):
                check(after[key] == before[key], "状态更新改变了初始文章")
        case.recorder.record("assertions", {"result": "passed", "ticket_id": case.ticket_id})


@allure.title("TC_069 非法状态与合法新优先级均不得保存")
def test_tc_069(state_case) -> None:
    """验证非法状态导致完整拒绝和持久化不变。"""
    verify_state(state_case)


@allure.title("TC_070 挂起提醒状态及未来24小时时刻一致保存")
def test_tc_070(state_case) -> None:
    """验证带时区时间在响应、API和数据库中的同一时刻。"""
    verify_state(state_case)


@allure.title("TC_071 仅恢复open状态后服务端自动清空挂起时间")
def test_tc_071(state_case) -> None:
    """独立准备挂起工单，不依赖070且不提交空pending_time。"""
    verify_state(state_case)
