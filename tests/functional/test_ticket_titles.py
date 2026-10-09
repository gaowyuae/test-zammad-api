"""TC_026～029、035～037：标题输入保真、长度边界及持久化核查。"""

import allure
import pytest

from fixtures.ticket import valid_id

pytestmark = [pytest.mark.functional, pytest.mark.database]


def check(condition: bool, message: str) -> None:
    """仅输出固定结论，避免整份响应出现在异常回溯中。"""
    if not condition:
        raise AssertionError(message) from None


def assert_title_rejection(result, expected: dict) -> None:
    """4xx 必须明确指向场景要求的标题校验，认证或其他错误不能通过。"""
    check(400 <= result.response.status_code < 500, "未按标题校验拒绝创建")
    value = result.get_json()
    error = value.get("error") if isinstance(value, dict) else None
    required = expected["error_contains"]
    if isinstance(required, str):
        required = [required]
    check(
        isinstance(error, str) and all(fragment in error for fragment in required),
        "错误原因不是预期的标题校验",
    )


def assert_saved_ticket(case, ticket) -> None:
    """核对工单 ID、完整标题、业务字段及真实创建身份。"""
    check(isinstance(ticket, dict), "保存的工单数据缺失")
    check(ticket.get("id") == case.ticket_id, "工单 ID 不一致")
    for field in ("title", "customer_id", "group_id", "owner_id", "state_id", "priority_id"):
        check(ticket.get(field) == case.payload[field], f"工单 {field} 保存不一致")
    check(ticket.get("created_by_id") == case.creator_id, "工单创建身份不一致")
    check(ticket.get("article_count") == 1, "初始文章计数不一致")


def assert_initial_article(case, article: dict, *, api_fields: bool) -> None:
    """核对初始文章关联、正文和作者；API 额外核对类型名称。"""
    check(isinstance(article, dict) and valid_id(article.get("id")), "初始文章 ID 无效")
    check(article.get("ticket_id") == case.ticket_id, "初始文章关联错误")
    fields = ("subject", "body", "content_type", "internal")
    if api_fields:
        fields += ("type", "sender")
    for field in fields:
        check(article.get(field) == case.payload["article"][field], f"初始文章 {field} 不一致")
    check(article.get("created_by_id") == case.creator_id, "初始文章作者不一致")
    check(case.article_ids == [article["id"]], "创建响应与初始文章 ID 不一致")


def verify_rejection(title_case) -> None:
    """只发送一次负向创建，核查专用范围和正文标记均无新增记录。"""
    case, scenario, db, api = title_case
    with allure.step("确认本次唯一正文标记无既有数据"):
        check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "本次标记已存在")
    with allure.step("A1 提交原始标题输入，不补齐或改写字段"):
        created = case.create()
        assert_title_rejection(created, scenario["expected"])
    with allure.step("核对工单范围集合不变，工单和文章标记无残留"):
        check(case.scoped_ticket_ids() == case.creation_baseline, "拒绝后出现范围外预期的新工单")
        check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "拒绝后有本次记录")
        case.recorder.record("assertions", {"result": "passed", "creation": "rejected"})


def verify_creation(title_case, *, database: bool) -> None:
    """分别核对创建响应、授权接口回读及场景要求的数据库原文。"""
    case, scenario, db, api = title_case
    with allure.step("确认字面标题长度并由 A1 创建一次"):
        expected = scenario["expected"]
        check(case.payload["title"] == "A" * expected["title_length"], "标题输入长度不符合原用例")
        created = case.create()
        check(created.response.status_code == expected["status_code"], "创建状态码不符")
        value = created.get_json()
        check(isinstance(value, dict) and valid_id(value.get("id")), "创建响应缺少有效 ID")
        check(
            isinstance(value.get("number"), str) and bool(value["number"].strip()), "工单编号缺失"
        )
    with allure.step("A1 回读完整标题及初始文章"):
        read = api.read(case.ticket_id)
        check(read.response.status_code == 200, "工单回读失败")
        ticket = read.get_json()
        assert_saved_ticket(case, ticket)
        check(ticket.get("number") == value["number"], "工单编号回读不一致")
        article_result = api.articles(case.ticket_id)
        check(article_result.response.status_code == 200, "文章回读失败")
        articles = article_result.get_json()
        check(isinstance(articles, list) and len(articles) == 1, "初始文章缺失或重复")
        assert_initial_article(case, articles[0], api_fields=True)
    if database:
        with allure.step(f"只读数据库确认完整 {expected['title_length']} 字符标题和唯一初始文章"):
            stored = db.ticket(case.ticket_id)
            assert_saved_ticket(case, stored)
            check(stored.get("number") == value["number"], "数据库工单编号不一致")
            stored_articles = db.articles(case.ticket_id)
            check(len(stored_articles) == 1, "数据库初始文章缺失或重复")
            assert_initial_article(case, stored_articles[0], api_fields=False)
    case.recorder.record(
        "assertions", {"result": "passed", "ticket_id": case.ticket_id, "database": database}
    )


@allure.title("TC_026 不传 title，标题校验拒绝且无残留")
def test_tc_026(title_case) -> None:
    """完全移除 title，不以 null 或空串替代。"""
    check("title" not in title_case[0].payload, "TC_026 必须不传 title")
    verify_rejection(title_case)


@allure.title("TC_027 title 为空串，标题校验拒绝且无残留")
def test_tc_027(title_case) -> None:
    """保留显式空字符串，正文仍带独立 RUN。"""
    check(title_case[0].payload.get("title") == "", "TC_027 必须传空 title")
    verify_rejection(title_case)


@allure.title("TC_028 200 字符标题创建成功并完整回读")
def test_tc_028(title_case) -> None:
    """以原始 200 个 A 验证正常标题保存。"""
    verify_creation(title_case, database=False)


@allure.title("TC_029 201 字符标题在接口和数据库完整保存")
def test_tc_029(title_case) -> None:
    """保留原 Excel 201 成功预期，不套用假定的 200 字符上限。"""
    verify_creation(title_case, database=True)


@allure.title("TC_035 249 字符标题在接口和数据库完整保存")
def test_tc_035(title_case) -> None:
    """验证上限前一个字符完整保存，初始文章关联正确。"""
    verify_creation(title_case, database=True)


@allure.title("TC_036 250 字符标题在接口和数据库完整保存")
def test_tc_036(title_case) -> None:
    """验证上限字符数完整保存，初始文章关联正确。"""
    verify_creation(title_case, database=True)


@allure.title("TC_037 251 字符标题被明确长度校验拒绝且无残留")
def test_tc_037(title_case) -> None:
    """超长标题不得截断保存，服务异常也不能冒充业务校验通过。"""
    case, scenario, db, api = title_case
    check(
        case.payload["title"] == "A" * scenario["expected"]["title_length"],
        "标题输入长度不符合原用例",
    )
    verify_rejection(title_case)
