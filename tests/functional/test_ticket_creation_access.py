"""TC_030、031、032、034：创建关联与业务身份隔离。"""

import allure
import pytest

from fixtures.ticket import valid_id

pytestmark = [pytest.mark.functional, pytest.mark.database]


def check(condition: bool, message: str) -> None:
    """仅输出固定结论，避免完整响应进入异常文本。"""
    if not condition:
        raise AssertionError(message) from None


def assert_rejection(result, expected: dict, *, customer_id: int | None = None) -> None:
    """错误状态和明确原因必须同时符合，不接受任意 4xx。"""
    check(400 <= result.response.status_code < 500, "创建未被业务校验拒绝")
    check(result.response.status_code != 401, "认证失败不能作为业务权限通过")
    value = result.get_json()
    error = value.get("error") if isinstance(value, dict) else None
    check(isinstance(error, str), "响应缺少明确错误原因")
    if expected["error_kind"] == "customer":
        check(valid_id(customer_id), "缺少已核实不存在的客户 ID")
        check(result.response.status_code == expected["status_code"], "无效关联拒绝状态码不符")
        check(error == f"{expected['error_prefix']}{customer_id}", "不是本次客户 ID 的无效关联错误")
    elif expected["error_kind"] in {"group", "customer_permission"}:
        check(
            result.response.status_code == 403 and error == "Not authorized", "非预期业务权限拒绝"
        )
    elif expected["error_kind"] == "ticket_role":
        check(result.response.status_code == expected["status_code"], "角色拒绝状态码不符")
        check(error == expected["error_equals"], "非预期 Token 上下文角色拒绝")
    else:
        check(False, "未知创建拒绝类型")


def verify_rejection(creation_case) -> None:
    """核查标记不存在后发出一次请求，拒绝原因和持久化均验证。"""
    case, scenario, db, api = creation_case
    with allure.step("只读确认本轮 RUN 不存在"):
        check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "本轮标记已存在")
    with allure.step("使用明确业务身份提交一次负向创建"):
        response = case.create()
        assert_rejection(
            response, scenario["expected"], customer_id=case.mapping.get("missing_customer_id")
        )
    with allure.step("数据库确认本次工单和初始文章均未保存"):
        check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "拒绝后存在本次记录")
    case.recorder.record("assertions", {"result": "passed", "creation": "rejected"})


def assert_customer_ticket(case, ticket: object) -> None:
    """以真实客户身份作为归属预期，不采用伪造请求中的客户 ID。"""
    check(isinstance(ticket, dict), "客户工单数据缺失")
    check(ticket.get("id") == case.ticket_id, "客户工单 ID 不一致")
    check(ticket.get("customer_id") == case.mapping["customer_id"], "工单未归属真实请求客户")
    check(ticket.get("customer_id") != case.mapping["claimed_customer_id"], "客户冒用造成错误归属")
    for field in ("title", "group_id", "state_id"):
        check(ticket.get(field) == case.payload[field], f"客户工单 {field} 不一致")
    check(ticket.get("created_by_id") == case.creator_id, "客户工单创建人不一致")
    check(ticket.get("article_count") == 1, "客户工单初始文章计数不一致")


def assert_customer_article(case, article: object, *, api_fields: bool) -> None:
    """核对文章原始内容、公开属性、真实作者及返回关联。"""
    check(isinstance(article, dict) and valid_id(article.get("id")), "客户初始文章 ID 无效")
    check(article.get("ticket_id") == case.ticket_id, "客户文章工单关联不符")
    check(article.get("created_by_id") == case.creator_id, "客户文章实际作者不符")
    fields = ("subject", "body", "content_type", "internal")
    if api_fields:
        fields += ("type", "sender")
    for field in fields:
        check(article.get(field) == case.payload["article"][field], f"客户文章 {field} 不符")
    check(case.article_ids == [article["id"]], "客户文章关联缺失或重复")


@allure.title("TC_030 不存在客户不能创建工单或初始文章")
def test_tc_030(creation_case) -> None:
    """通过实时映射及 404 前置确认不存在关联。"""
    verify_rejection(creation_case)


@allure.title("TC_031 无 G2 权限客服不能在 G2 创建工单")
def test_tc_031(creation_case) -> None:
    """合法 G2 负责人排除错误负责人造成的假权限通过。"""
    verify_rejection(creation_case)


@allure.title("TC_032 客户不能冒用另一客户创建工单")
def test_tc_032(creation_case) -> None:
    """安全覆盖归属或明确业务拒绝均需完整业务证据。"""
    case, scenario, db, api = creation_case
    with allure.step("核对客户输入及本轮标记"):
        check(
            "owner_id" not in case.payload and "priority_id" not in case.payload, "客户请求被补齐"
        )
        check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "本轮标记已存在")
    with allure.step("C1 提交 customer_id=C2 的客户创建请求"):
        response = case.create()
        if response.response.status_code != scenario["expected"]["status_code"]:
            assert_rejection(response, scenario["expected"])
            check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "拒绝后存在记录")
            case.recorder.record("assertions", {"result": "passed", "creation": "rejected"})
            return
        value = response.get_json()
        check(isinstance(value, dict) and valid_id(value.get("id")), "创建返回 ID 无效")
        check(
            isinstance(value.get("number"), str) and bool(value["number"].strip()), "工单编号缺失"
        )
        assert_customer_ticket(case, value)
    with allure.step("C1 回读工单与初始文章，确认归属和真实作者"):
        saved = api.read(case.ticket_id)
        check(saved.response.status_code == 200, "客户无法回读自己的工单")
        assert_customer_ticket(case, saved.get_json())
        check(saved.get_json().get("number") == value["number"], "回读工单编号不一致")
        articles = api.articles(case.ticket_id)
        check(articles.response.status_code == 200, "客户初始文章回读失败")
        rows = articles.get_json()
        check(isinstance(rows, list) and len(rows) == 1, "客户初始文章缺失或重复")
        assert_customer_article(case, rows[0], api_fields=True)
    with allure.step("数据库确认真实归属与唯一初始文章"):
        stored = db.ticket(case.ticket_id)
        assert_customer_ticket(case, stored)
        check(stored.get("number") == value["number"], "数据库工单编号不一致")
        rows = db.articles(case.ticket_id)
        check(len(rows) == 1, "数据库初始文章缺失或重复")
        assert_customer_article(case, rows[0], api_fields=False)
    case.recorder.record("assertions", {"result": "passed", "customer": "current_user"})


@allure.title("TC_034 纯 Admin 不能替代客服创建工单")
def test_tc_034(creation_case) -> None:
    """使用独立纯管理员，不能用维护管理员或无效 Token 代替。"""
    verify_rejection(creation_case)
