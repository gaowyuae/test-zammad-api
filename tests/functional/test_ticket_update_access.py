"""TC_062、064—068 的明确更新拒绝与完整持久化核查。"""

import json

import allure
import pytest

from assertions.tickets import (
    FIELDS,
    article_rows,
    assert_fields,
    check,
    ticket_value,
)
from fixtures.ticket import valid_id

pytestmark = [pytest.mark.functional, pytest.mark.database]


def assert_rejection(
    result,
    expected: dict,
    *,
    ticket_id: int | None = None,
    group_id: int | None = None,
    private_values: list[str] | None = None,
) -> None:
    """严格比较状态及业务原因，并禁止客户响应泄露目标内容。"""
    check(result.response.status_code == expected["status_code"], "更新拒绝状态码不符")
    value = result.get_json()
    check(isinstance(value, dict), "更新错误响应结构不符")
    error = expected["error_equals"]
    if "{ticket_id}" in error:
        check(valid_id(ticket_id), "不存在目标 ID 未明确")
        error = error.format(ticket_id=ticket_id)
    if "{group_id}" in error:
        check(valid_id(group_id), "目标组 ID 未明确")
        error = error.format(group_id=group_id)
    check(value.get("error") == error, "更新拒绝原因不符")
    if private_values:
        check(
            not (
                {
                    "title",
                    "body",
                    "subject",
                    "article",
                    "articles",
                    "ticket",
                    "customer_id",
                    "owner_id",
                }
                & set(value)
            ),
            "客户拒绝响应泄露工单字段",
        )
        encoded = json.dumps(value, ensure_ascii=False)
        check(
            not any(secret and secret in encoded for secret in private_values),
            "客户拒绝响应泄露目标内容",
        )


def assert_snapshot(actual: object, before: object) -> None:
    """完整比较已授权快照，包含 updated_at 及文章全部已授权列。"""
    check(actual == before, "拒绝后授权快照发生变化")


def prepare(case, db) -> tuple[dict, dict, list[dict], list[dict]]:
    """M 准备 open 工单，核对请求字段、作者、唯一初始文章及数据库。"""
    created = case.create()
    check(created.response.status_code == 201, "准备工单创建失败")
    before = ticket_value(case.maintenance.read(case.ticket_id))
    check(before.get("id") == case.ticket_id, "准备工单 ID 不符")
    for field in ("title", "customer_id", "group_id", "owner_id", "state_id", "priority_id"):
        check(before.get(field) == case.payload[field], f"准备工单 {field} 不符")
    check(
        before.get("created_by_id") == case.creator_id == case.mapping["maintenance_id"]
        and before.get("article_count") == 1,
        "准备工单作者或文章数量不符",
    )
    stored = db.ticket(case.ticket_id)
    assert_fields(stored, {field: before[field] for field in FIELDS})
    check("updated_at" in stored and "updated_by_id" in stored, "数据库审计字段缺失")
    articles = article_rows(case.maintenance, case.ticket_id)
    stored_articles = db.articles(case.ticket_id)
    check(len(stored_articles) == 1, "数据库初始文章数量不符")
    for rows in (articles, stored_articles):
        row = rows[0]
        check(valid_id(row.get("id")) and case.article_ids == [row["id"]], "初始文章 ID 不符")
        check(
            row.get("ticket_id") == case.ticket_id and row.get("created_by_id") == case.creator_id,
            "初始文章关联或作者不符",
        )
        for field in ("subject", "body", "content_type", "internal"):
            check(row.get(field) == case.payload["article"][field], f"初始文章 {field} 不符")
    for field in ("type", "sender"):
        check(articles[0].get(field) == case.payload["article"][field], f"初始文章 {field} 不符")
    case.recorder.record(
        "update-before",
        {
            "ticket": before,
            "db_ticket": stored,
            "articles": articles,
            "db_articles": stored_articles,
        },
    )
    return before, stored, articles, stored_articles


def verify_update(update_access_case) -> None:
    """仅发送原场景更新字段，验证拒绝及授权 API、DB 的完整不变快照。"""
    case, scenario, db, api = update_access_case
    before, stored, articles, stored_articles = prepare(case, db)
    missing = case.mapping.get("missing_ticket_id")
    inventory = None
    if case.case_id == "TC_062":
        check(case.maintenance.read(missing).response.status_code == 404, "目标不存在前置不符")
        check(db.ticket(missing) is None, "数据库目标已存在")
        inventory = db.ticket_inventory()
        payload = {"state_id": case.mapping["state_id"]}
    elif case.case_id == "TC_065":
        payload = {"owner_id": case.mapping["peer_id"]}
    elif case.case_id == "TC_068":
        payload = {
            "group_id": case.mapping["target_group_id"],
            "owner_id": case.mapping["unassigned_id"],
        }
    else:
        payload = {"state_id": case.mapping["target_state_id"]}
    if case.case_id == "TC_067":
        # AR 必须确实能读准备工单，防止无效 Token 导致假权限通过。
        reader = ticket_value(api.read(case.ticket_id))
        check(reader.get("id") == case.ticket_id, "AR 未读到准备工单")
    with allure.step(f"{scenario['actor']} 仅提交场景指定更新字段"):
        changed = (
            case.update_missing(api, payload)
            if case.case_id == "TC_062"
            else case.update(api, payload)
        )
    # 无论状态是否正确，先保全持久化证据与库存异常，防止断言提前退出后盲删。
    if case.case_id == "TC_062":
        case.integrity_unknown = True
        missing_after = db.ticket(missing)
        inventory_after = db.ticket_inventory()
        case.recorder.record(
            "update-missing-inventory",
            {
                "before": inventory,
                "after": inventory_after,
                "missing_id": missing,
                "target": missing_after,
            },
        )
        case.integrity_unknown = missing_after is not None or inventory_after != inventory
        check(not case.integrity_unknown, "不存在目标出现或库存摘要发生变化")
    after = ticket_value(case.maintenance.read(case.ticket_id))
    stored_after = db.ticket(case.ticket_id)
    articles_after = article_rows(case.maintenance, case.ticket_id)
    stored_articles_after = db.articles(case.ticket_id)
    case.recorder.record(
        "update-after",
        {
            "ticket": after,
            "db_ticket": stored_after,
            "articles": articles_after,
            "db_articles": stored_articles_after,
        },
    )
    assert_rejection(
        changed,
        scenario["expected"],
        ticket_id=missing,
        group_id=case.mapping.get("target_group_id"),
        private_values=[before["title"], articles[0]["body"], articles[0]["subject"]]
        if case.case_id == "TC_066"
        else None,
    )
    for actual, expected in (
        (after, before),
        (stored_after, stored),
        (articles_after, articles),
        (stored_articles_after, stored_articles),
    ):
        assert_snapshot(actual, expected)
    case.recorder.record(
        "assertions", {"result": "passed", "update": "rejected", "ticket_id": case.ticket_id}
    )


@allure.title("TC_062 不存在工单更新明确404且不产生持久化变化")
def test_tc_062(update_access_case) -> None:
    """核对未知目标及哨兵库存。"""
    verify_update(update_access_case)


@allure.title("TC_064 匿名不能关闭工单")
def test_tc_064(update_access_case) -> None:
    """独立无认证会话必须明确拒绝。"""
    verify_update(update_access_case)


@allure.title("TC_065 纯管理员不能变更负责人")
def test_tc_065(update_access_case) -> None:
    """有效 admin Token 不能替代客服授权。"""
    verify_update(update_access_case)


@allure.title("TC_066 客户不能关闭他人工单或获取其正文")
def test_tc_066(update_access_case) -> None:
    """无组织共享的独立 C2 必须明确拒绝且无泄露。"""
    verify_update(update_access_case)


@allure.title("TC_067 G1只读客服不能关闭工单")
def test_tc_067(update_access_case) -> None:
    """验证可读但无写权限。"""
    verify_update(update_access_case)


@allure.title("TC_068 无G2权限客服不能转组并取消分派")
def test_tc_068(update_access_case) -> None:
    """合法系统占位排除非法负责人造成的假通过。"""
    verify_update(update_access_case)
