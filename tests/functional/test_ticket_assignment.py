"""TC_059、060、061、063 的负责人和转组持久化核查。"""

import allure
import pytest

from assertions.tickets import FIELDS, article_rows, assert_fields, check, ticket_value
from fixtures.ticket import valid_id

pytestmark = [pytest.mark.functional, pytest.mark.database]


def verify_assignment(assignment_case) -> None:
    """只更新指定字段并比较响应、API、数据库及不变文章。"""
    case, scenario, db, api = assignment_case
    with allure.step("M 创建独立 open 工单并核对初始字段及文章作者"):
        created = case.create()
        check(created.response.status_code == 201, "准备工单创建失败")
        before = ticket_value(api.read(case.ticket_id))
        check(before.get("id") == case.ticket_id, "初始工单 ID 不符")
        expected = {field: before[field] for field in FIELDS}
        for field in ("title", "customer_id", "group_id", "owner_id", "state_id", "priority_id"):
            check(before[field] == case.payload[field], f"初始 {field} 不符")
        check(
            before["created_by_id"] == case.creator_id and before["article_count"] == 1,
            "初始作者或文章数不符",
        )
        assert_fields(db.ticket(case.ticket_id), expected)
        articles = article_rows(api, case.ticket_id)
        stored_articles = db.articles(case.ticket_id)
        check(len(stored_articles) == 1, "数据库初始文章数量不符")
        for rows in (articles, stored_articles):
            article = rows[0]
            check(
                valid_id(article.get("id")) and case.article_ids == [article["id"]],
                "初始文章 ID 与创建响应不一致",
            )
            check(
                article.get("ticket_id") == case.ticket_id
                and article.get("created_by_id") == case.creator_id,
                "初始文章关联或作者不符",
            )
            for field in ("subject", "body", "content_type", "internal"):
                check(
                    article.get(field) == case.payload["article"][field], f"初始文章 {field} 不符"
                )
        for field in ("type", "sender"):
            check(
                articles[0].get(field) == case.payload["article"][field], f"初始文章 {field} 不符"
            )
    with allure.step(f"{scenario['actor']} 仅提交原用例指定分派字段"):
        if case.case_id == "TC_059":
            payload = {"owner_id": case.mapping["peer_id"]}
        elif case.case_id == "TC_061":
            payload = {
                "group_id": case.mapping["target_group_id"],
                "owner_id": case.mapping["transfer_id"],
            }
            expected["group_id"] = payload["group_id"]
        else:
            payload = {
                "owner_id": case.mapping["customer_id"]
                if case.case_id == "TC_063"
                else case.mapping["unassigned_id"]
            }
        expected["owner_id"] = (
            case.mapping["unassigned_id"]
            if case.case_id in {"TC_060", "TC_063"}
            else payload["owner_id"]
        )
        changed = case.update(api, payload)
        assert_fields(ticket_value(changed), expected)
    with allure.step("严格比较 API 与数据库字段以及完整不变文章"):
        assert_fields(ticket_value(api.read(case.ticket_id)), expected)
        assert_fields(db.ticket(case.ticket_id), expected)
        check(article_rows(api, case.ticket_id) == articles, "API 文章发生变化")
        check(db.articles(case.ticket_id) == stored_articles, "数据库文章发生变化")
        case.recorder.record("assertions", {"result": "passed", "ticket_id": case.ticket_id})


@allure.title("TC_059 同组负责人从 A1 改为 A2")
def test_tc_059(assignment_case) -> None:
    """同组更新只改变负责人。"""
    verify_assignment(assignment_case)


@allure.title("TC_060 A1 取消负责人分派")
def test_tc_060(assignment_case) -> None:
    """使用运行时映射的系统占位用户。"""
    verify_assignment(assignment_case)


@allure.title("TC_061 A12 转组到 G2 并保留本人负责人")
def test_tc_061(assignment_case) -> None:
    """由具有双组 full 权限的 A12 更新。"""
    verify_assignment(assignment_case)


@allure.title("TC_063 客户负责人被归为未分派")
def test_tc_063(assignment_case) -> None:
    """客户不得持久化为负责人，工单保持 open。"""
    verify_assignment(assignment_case)
