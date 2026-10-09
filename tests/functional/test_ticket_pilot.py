"""P0 认证、创建回滚、部分更新与更新回滚。"""

from copy import deepcopy

import allure
import pytest

pytestmark = [pytest.mark.functional, pytest.mark.pilot, pytest.mark.database]
STABLE_FIELDS = ("id", "number", "title", "group_id", "owner_id", "customer_id", "priority_id")


def check(condition: bool, message: str) -> None:
    """固定断言消息，不将整个响应或凭据放入 pytest 回溯。"""
    if not condition:
        raise AssertionError(message) from None


def error_matches(result, expected: str, invalid_type: str | None = None) -> None:
    """仅接受明确业务错误字段，不能让其他4xx或成功正文冒充拒绝原因。"""
    body = result.get_json()
    error = body.get("error") if isinstance(body, dict) else None
    check(isinstance(error, str) and expected in error, "错误原因与用例要求不符")
    if invalid_type is not None:
        check(invalid_type in error, "错误未指向本次非法文章类型")


def prepare(case, db) -> tuple[dict, list]:
    """维护身份独立准备工单，确认数据库和文章基线后再执行被测更新。"""
    created = case.create()
    check(created.response.status_code == 201, "准备工单失败")
    before = db.ticket(case.ticket_id)
    articles = db.articles(case.ticket_id)
    check(before is not None and len(articles) == 1, "准备基线不完整")
    for field in ("title", "group_id", "owner_id", "customer_id", "state_id", "priority_id"):
        check(before[field] == case.payload[field], "准备字段与预期不符")
    check(before["created_by_id"] == case.mapping["maintenance_id"], "准备工单创建身份不符")
    check(articles[0]["created_by_id"] == case.mapping["maintenance_id"], "准备文章作者不符")
    return before, articles


@allure.title("TC_025 无认证请求拒绝且无工单和文章残留")
def test_tc_025(pilot_case) -> None:
    """匿名会话提交合法业务数据，确认认证错误和数据库无记录。"""
    case, data, db, api = pilot_case
    with allure.step("确认本次唯一标记尚无记录"):
        check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "基线标记冲突")
    with allure.step("未认证身份发送一次创建请求"):
        result = case.create()
        check(result.response.status_code in data["expected"]["statuses"], "未正确拒绝匿名请求")
        error_matches(result, data["expected"]["error_contains"])
    with allure.step("只读核查工单及初始文章均不存在"):
        check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "匿名创建有残留")


@allure.title("TC_033 初始文章类型非法，创建事务整体回滚")
def test_tc_033(pilot_case) -> None:
    """合法客服仅提交非法文章类型，不能留下半成品工单。"""
    case, data, db, api = pilot_case
    with allure.step("确认非法类型不存在且本次标记无记录"):
        check(not db.article_type_exists(data["invalid_type"]), "非法类型已存在")
        check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "基线标记冲突")
    with allure.step("A1 创建附带非法初始文章的工单"):
        case.payload["article"]["type"] = data["invalid_type"]
        result = case.create()
        check(result.response.status_code == data["expected"]["status_code"], "创建拒绝状态码不符")
        error_matches(result, data["expected"]["error_contains"], data["invalid_type"])
    with allure.step("确认工单和文章整体未保存"):
        check(db.by_marker(case.marker) == {"tickets": [], "articles": []}, "创建事务未整体回滚")


@allure.title("TC_058 仅更新状态，未提交字段与文章保持")
def test_tc_058(pilot_case) -> None:
    """从独立新建工单开始，仅提交当前映射的处理中状态。"""
    case, data, db, api = pilot_case
    with allure.step("M 准备本用例工单并保存数据库基线"):
        before, articles = prepare(case, db)
    with allure.step("A1 仅提交 state_id"):
        result = case.update(api, {"state_id": case.mapping["target_state_id"]})
        check(result.response.status_code == data["expected"]["status_code"], "部分更新失败")
    with allure.step("接口回读和数据库核对状态及未提交字段"):
        read = api.read(case.ticket_id)
        check(read.response.status_code == 200, "更新后回读失败")
        for after in (read.get_json(), db.ticket(case.ticket_id)):
            check(isinstance(after, dict), "更新后数据缺失")
            check(after.get("state_id") == case.mapping["target_state_id"], "目标状态未保存")
            check(all(after.get(key) == before[key] for key in STABLE_FIELDS), "未提交字段被改变")
        check(db.articles(case.ticket_id) == articles, "状态更新意外修改文章")


@allure.title("TC_072 更新附非法文章，工单状态与文章整体回滚")
def test_tc_072(pilot_case) -> None:
    """从独立处理中工单开始，失败后不能留下关闭状态或新增文章。"""
    case, data, db, api = pilot_case
    with allure.step("确认非法类型并准备独立处理中工单"):
        check(not db.article_type_exists(data["invalid_type"]), "非法类型已存在")
        before, articles = prepare(case, db)
    with allure.step("A1 同时提交关闭状态与非法文章"):
        article = deepcopy(data["update_article"])
        article["body"] += f"；RUN={case.marker}_update"
        result = case.update(api, {"state_id": case.mapping["target_state_id"], "article": article})
        check(result.response.status_code == data["expected"]["status_code"], "回滚请求状态码不符")
        error_matches(result, data["expected"]["error_contains"], data["invalid_type"])
    with allure.step("回读原状态，数据库快照及文章保持不变"):
        read = api.read(case.ticket_id)
        check(read.response.status_code == 200, "回滚后回读失败")
        value = read.get_json()
        check(isinstance(value, dict), "回读结构错误")
        check(
            all(value.get(key) == before[key] for key in (*STABLE_FIELDS, "state_id")),
            "状态或原字段部分保存",
        )
        check(db.ticket(case.ticket_id) == before, "工单数据库快照未回滚")
        check(db.articles(case.ticket_id) == articles, "文章集合未回滚")
        check(
            db.by_marker(f"{case.marker}_update") == {"tickets": [], "articles": []}, "本次文章残留"
        )
