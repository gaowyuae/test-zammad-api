"""TC_109～118搜索筛选、分页、权限及只读完整性。"""

import allure
import pytest

from assertions.search import assert_error, assert_ids, assert_no_leak
from assertions.tickets import check

pytestmark = [pytest.mark.functional, pytest.mark.database]


def success(case) -> None:
    """保留稳定编号及只读业务结论。"""
    case.recorder.record("assertions", {"result": "passed", "case_id": case.data["case_id"]})


@allure.title("TC_109 按真实编号搜索完整集合与保存字段一致")
def test_tc_109(search_case) -> None:
    """B05只应命中自身，搜索内容匹配查询前数据库快照。"""
    case, _ = search_case
    with allure.step("BA1按基线B05编号搜索一次并核对完整结果"):
        case.assert_result(case.request())
    success(case)


@allure.title("TC_110 关闭状态搜索恰为四张关闭基线工单")
def test_tc_110(search_case) -> None:
    """四张完整集合及state_id逐项一致，不混入其他状态。"""
    case, _ = search_case
    with allure.step("BA1按已映射closed状态搜索并核查四张集合"):
        rows = case.assert_result(case.request())
        check(
            all(row["state_id"] == case.identity["states"]["closed"] for row in rows),
            "混入非关闭工单",
        )
    success(case)


@allure.title("TC_111 BC1搜索BC2工单编号不返回工单或正文")
def test_tc_111(search_case) -> None:
    """已验证客户独立身份和无共享组织，查询条件不扩大可见范围。"""
    case, _ = search_case
    with allure.step("BC1使用独立会话搜索BC2的B08编号"):
        result = case.request()
        rows = case.assert_result(result)
        assert_no_leak(rows, case.forbidden("B08"))
    success(case)


@allure.title("TC_112 已确认不存在的编号查询返回200空集合")
def test_tc_112(search_case) -> None:
    """每轮动态生成编号并先查DB，不永久假定历史编号不存在。"""
    case, _ = search_case
    with allure.step("数据库确认不存在后，BA1按该编号正式查询一次"):
        case.assert_result(case.request())
    success(case)


@allure.title("TC_113 客户六张工单逐页至空页，与大页及DB精确一致")
def test_tc_113(search_case) -> None:
    """每页两条按ID升序，不能用提前达到数量代替空页终止。"""
    case, _ = search_case
    with allure.step("BA1逐页读取BC1集合，保持每页2条和ID升序至空页"):
        merged = case.paginated()
    with allure.step("同查询同排序读取100条大页，核对完整DB集合"):
        large = case.assert_result(case.request(page=1, per_page=100), ordered=True)
        expected = sorted(
            row["id"]
            for row in case.stored.values()
            if row["customer_id"] == case.identity["users"]["BC1"]
        )
        assert_ids(merged, expected, ordered=True)
        assert_ids(large, expected, ordered=True)
    success(case)


@allure.title("TC_114 负责人和新建状态组合搜索恰为B06及B07")
def test_tc_114(search_case) -> None:
    """两项筛选必须同时满足，检查完整集合及每条保存字段。"""
    case, _ = search_case
    with allure.step("BA1按负责人AND新建状态查询一次"):
        rows = case.assert_result(case.request())
        check(
            all(
                row["owner_id"] == case.identity["users"]["BA1"]
                and row["state_id"] == case.identity["states"]["new"]
                for row in rows
            ),
            "组合查询未同时满足负责人和新建状态",
        )
    success(case)


@allure.title("TC_115 无Cookie与认证头的搜索明确拒绝且不泄露详情")
def test_tc_115(search_case) -> None:
    """匿名请求保留真实认证错误，不能接受任意4xx或业务对象。"""
    case, _ = search_case
    with allure.step("匿名独立客户端搜索有效的B05编号"):
        result = case.request()
        value = assert_error(result, "authentication")
        assert_no_leak(value, case.forbidden("B05"))
    success(case)


@allure.title("TC_116 标题搜索与同身份详情的关键字段一致")
def test_tc_116(search_case) -> None:
    """B05标题检索与详情对应同一工单且不接受其他命中。"""
    case, _ = search_case
    with allure.step("BA1按合成标题词搜索并核查完整集合"):
        rows = case.assert_result(case.request())
    with allure.step("以搜索得到的明确ID回读详情并比较关键字段"):
        result = case.api.read(rows[0]["id"])
        value = result.get_json()
        case.recorder.record(
            "search-detail", {"status": result.response.status_code, "value": value}
        )
        check(result.response.status_code == 200 and isinstance(value, dict), "搜索后详情读取失败")
        check(
            all(
                field in value and value[field] == rows[0][field]
                for field in ("id", "number", "title", "state_id", "owner_id")
            ),
            "搜索与详情关键字段不一致",
        )
    success(case)


@allure.title("TC_117 连续三次搜索不修改工单和文章业务字段及时间")
def test_tc_117(search_case) -> None:
    """三次是原场景明确步骤，任意一次失败即失败而非重试。"""
    case, data = search_case
    ticket_id = case.tickets["B05"]["id"]
    with allure.step("保存B05工单及文章字段、更新时间和数量"):
        before = {"ticket": case.db.ticket(ticket_id), "articles": case.db.articles(ticket_id)}
        case.recorder.record("search-readonly-before", before)
    with allure.step("连续三次正式查询，每次都核查完整正确结果"):
        for _ in range(data["repetitions"]):
            case.assert_result(case.request())
    with allure.step("查询后DB完整快照须与查询前一致"):
        after = {"ticket": case.db.ticket(ticket_id), "articles": case.db.articles(ticket_id)}
        case.recorder.record("search-readonly-after", after)
        check(after == before, "搜索修改了工单或文章字段、时间或数量")
    success(case)


@allure.title("TC_118 不存在工单ID返回404且不创建占位对象")
def test_tc_118(search_case) -> None:
    """从实际最大ID推导缺失目标，只发A1的GET并核对全表摘要。"""
    case, _ = search_case
    with allure.step("选择并通过只读数据库确认不存在ID"):
        before = case.db.ticket_inventory()
        check(before["max_id"] <= 2_147_482_647, "无法安全生成不存在工单ID")
        ticket_id = before["max_id"] + 1000
        check(case.db.ticket(ticket_id) is None, "目标工单ID实际存在")
    with allure.step("A1读取不存在ID，保存请求后对象及集合证据"):
        result = case.api.read(ticket_id)
        after, target = case.db.ticket_inventory(), case.db.ticket(ticket_id)
        case.recorder.record(
            "search-missing-id",
            {"id": ticket_id, "before": before, "after": after, "target": target},
        )
        value = assert_error(result, "missing")
        for code in case.tickets:
            assert_no_leak(value, case.forbidden(code))
        check(target is None and after == before, "不存在详情请求创建或修改了业务记录")
    success(case)
