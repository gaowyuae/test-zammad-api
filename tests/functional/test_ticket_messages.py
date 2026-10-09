"""TC_087～091：实际文章作者、客户属性强制、可见性及跨客户拒绝。"""

import allure
import pytest

from assertions.messages import (
    ARTICLE_FIELDS,
    assert_posted,
    assert_rejection,
    assert_ticket_stable,
    assert_visible,
    check_snapshot,
    payload_for,
    prepare,
    snapshot,
)
from assertions.messages import (
    assert_article as assert_article,
)
from assertions.messages import (
    assert_no_leak as assert_no_leak,
)
from assertions.tickets import check

pytestmark = [pytest.mark.functional, pytest.mark.database]


def mixed_baseline(case, db) -> dict:
    """初始公开文章加一公开三内部，普通辅助文章由M逐一创建登记。"""
    before = prepare(case, db)
    for index, internal in enumerate((False, True, True, True), start=1):
        payload = dict(
            ticket_id=case.ticket_id,
            type="note",
            sender="Agent",
            content_type="text/plain",
            internal=internal,
            body=f"{case.marker}；{'内部' if internal else '公开'}消息{index}",
        )
        result = case.post_article(case.maintenance, payload)
        after = snapshot(case, db, f"message-baseline-{index}")
        check_snapshot(after, case.ticket_id)
        assert_posted(case, result, payload, after, case.creator_id, "Agent", internal)
        assert_ticket_stable(before, after, index + 1)
    check(len(after["articles"]) == 5, "混合文章总数不符")
    for rows in (after["articles"], after["db_articles"]):
        check(
            sum(row.get("internal") is False for row in rows) == 2
            and sum(row.get("internal") is True for row in rows) == 3,
            "混合公开和内部数量不符",
        )
    return after


def verify_create(message_case) -> None:
    """验证A1真实作者及C1伪造属性由服务端安全覆盖。"""
    case, scenario, db, api = message_case
    with allure.step("M准备独立工单并保存初始快照"):
        before = prepare(case, db)
    payload = payload_for(case, scenario)
    with allure.step("指定执行身份直接追加原始消息参数"):
        created = case.post_article(api, payload)
    after = snapshot(case, db, "message-after")
    with allure.step("核对响应、单条回读、实际作者和数据库保存"):
        check_snapshot(after, case.ticket_id)
        article_id = assert_posted(
            case,
            created,
            payload,
            after,
            case.mapping["actor_id"],
            scenario["expected"]["sender"],
            scenario["expected"]["internal"],
        )
        assert_ticket_stable(before, after, 2)
        for key in ("articles", "db_articles"):
            check(
                [row for row in after[key] if row["id"] != article_id] == before[key],
                "追加改变了初始文章",
            )
        case.recorder.record(
            "assertions",
            {"result": "passed", "ticket_id": case.ticket_id, "article_id": article_id},
        )


@allure.title("TC_087 客服实际追加公开备注并关联原工单")
def test_tc_087(message_case) -> None:
    """A1实际发出创建请求，核对作者且不新增其他工单。"""
    verify_create(message_case)


@allure.title("TC_088 客户伪造Agent内部消息仍保存为Customer公开消息")
def test_tc_088(message_case) -> None:
    """保留伪造输入，验证服务端覆盖及真实客户作者。"""
    verify_create(message_case)


@allure.title("TC_089 客户文章列表恰好只含两篇公开文章")
def test_tc_089(message_case) -> None:
    """完整比较可见集合和内容，拒绝重复、遗漏和内部泄露。"""
    case, scenario, db, api = message_case
    with allure.step("独立准备两公开三内部文章"):
        before = mixed_baseline(case, db)
    result = api.articles(case.ticket_id)
    after = snapshot(case, db, "message-after")
    public = [
        {field: row[field] for field in ARTICLE_FIELDS}
        for row in before["articles"]
        if row["internal"] is False
    ]
    hidden = [row["body"] for row in before["articles"] if row["internal"] is True]
    with allure.step("核对客户精确可见集合及只读前后不变"):
        assert_visible(result, public, hidden)
        check(after == before, "客户列表读取改变了工单或文章")
        case.recorder.record(
            "assertions", {"result": "passed", "public_ids": [row["id"] for row in public]}
        )


@allure.title("TC_090 客户不能按ID直接读取自己的内部文章")
def test_tc_090(message_case) -> None:
    """先确认目标文章真实存在，再验证403及全文不泄露。"""
    case, scenario, db, api = message_case
    with allure.step("准备并由维护身份确认内部文章存在"):
        before = mixed_baseline(case, db)
    hidden = [row for row in before["articles"] if row["internal"] is True]
    result = api.article(hidden[0]["id"])
    after = snapshot(case, db, "message-after")
    with allure.step("核对明确拒绝、正文保护及目标不变"):
        assert_rejection(result, [row["body"] for row in hidden])
        check(after == before, "客户访问内部文章改变了工单或文章")
        case.recorder.record("assertions", {"result": "passed", "article_id": hidden[0]["id"]})


@allure.title("TC_091 C2不能向C1工单追加消息")
def test_tc_091(message_case) -> None:
    """合法客户Token遭业务权限拒绝，工单及完整文章快照保持。"""
    case, scenario, db, api = message_case
    with allure.step("M准备归属C1的独立工单"):
        before = prepare(case, db)
    result = case.post_article(api, payload_for(case, scenario))
    after = snapshot(case, db, "message-after")
    with allure.step("核对跨客户拒绝且API及DB无消息或字段变化"):
        assert_rejection(result, [row["body"] for row in before["articles"]])
        check(after == before, "越权追加改变了目标工单或文章")
        case.recorder.record("assertions", {"result": "passed", "ticket_id": case.ticket_id})
