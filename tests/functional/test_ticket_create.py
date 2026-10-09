"""TC_023：客服创建工单和初始公开电话消息。"""

import allure
import pytest

from fixtures.ticket import valid_id

pytestmark = [pytest.mark.functional, pytest.mark.pilot]


def check(condition: bool, message: str) -> None:
    """只抛出固定业务结论，不让 pytest 回显整个响应。"""
    if not condition:
        raise AssertionError(f"TC_023：{message}") from None


@allure.title("TC_023 客服创建工单及初始公开电话消息")
def test_tc_023(ticket_case) -> None:
    """依据 Excel 核对创建响应、工单回读、初始文章及作者。"""
    case, data = ticket_case
    with allure.step("A1 发送一次创建请求"):
        created = case.create()
        check(
            created.response.status_code == data["expected"]["status_code"], "创建状态码不符合预期"
        )
        value = created.get_json()
        check(isinstance(value, dict), "创建响应格式错误")
        check(valid_id(value.get("id")), "创建响应没有有效 ID")
        check(
            isinstance(value.get("number"), str) and bool(value["number"].strip()),
            "创建响应缺少工单编号",
        )
    with allure.step("A1 回读工单并比较提交字段"):
        detail = case.api.read(case.ticket_id)
        check(detail.response.status_code == 200, "工单回读失败")
        ticket = detail.get_json()
        check(isinstance(ticket, dict), "工单回读结构错误")
        check(
            ticket.get("id") == case.ticket_id and ticket.get("number") == value["number"],
            "工单 ID 或编号不一致",
        )
        for field in ("title", "customer_id", "group_id", "owner_id", "state_id", "priority_id"):
            check(ticket.get(field) == case.payload[field], f"{field} 保存不一致")
        check(ticket.get("created_by_id") == case.mapping["agent_id"], "工单实际创建身份不是 A1")
        check(
            ticket.get("article_count") == data["expected"]["article_count"], "初始文章计数不一致"
        )
    with allure.step("回读初始文章，核对 phone、Customer、公开及正文"):
        result = case.api.articles(case.ticket_id)
        check(result.response.status_code == 200, "文章回读失败")
        rows = result.get_json()
        check(
            isinstance(rows, list) and len(rows) == data["expected"]["article_count"],
            "初始文章缺失或重复",
        )
        article = rows[0]
        check(isinstance(article, dict) and valid_id(article.get("id")), "初始文章 ID 错误")
        check(article.get("ticket_id") == case.ticket_id, "文章关联了其他工单")
        for field in ("subject", "body", "type", "sender", "content_type", "internal"):
            check(article.get(field) == case.payload["article"][field], f"文章 {field} 保存不一致")
        check(article.get("created_by_id") == case.mapping["agent_id"], "文章实际创建身份不是 A1")
        check(case.article_ids == [article["id"]], "创建响应的文章 ID 与回读不一致")
        case.recorder.record(
            "assertions",
            {"result": "passed", "ticket_id": case.ticket_id, "article_id": article["id"]},
        )
