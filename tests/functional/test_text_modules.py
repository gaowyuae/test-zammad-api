"""TC_171～174文本详情、备注长度边界及客户正文隔离。"""

import allure
import pytest

from assertions.tickets import check
from fixtures.config_checks import assert_error
from fixtures.text_modules import assert_no_content_leak, assert_text

pytestmark = [pytest.mark.functional, pytest.mark.database]


def prepare(case) -> dict:
    """AD创建本条P，正文按HTML可见内容核对且无组限制。"""
    result = case.create()
    saved = case.snapshot("text-module-prepared") if case.object_id is not None else None
    check(result.response.status_code == 201 and saved is not None, "文本模块准备失败")
    assert_text(result.get_json(), case.payload, case.object_id, case.creator_id)
    check(result.get_json().get("group_ids") == [], "创建响应包含额外组限制")
    case.assert_saved(saved)
    case.assert_unchanged_others()
    return saved


@allure.title("TC_171 AD按ID获取文本模块详情，字段与无组限制保持")
def test_tc_171(text_module_case) -> None:
    """正式请求GET详情，不能以准备响应或列表代替被测行为。"""
    case, data = text_module_case
    before = prepare(case)
    with allure.step("AD仅GET本条文本模块详情并保存授权回读"):
        result = case.detail_as_actor()
        after = case.snapshot("text-module-after-detail")
    check(result.response.status_code == data["expected"]["status_code"], "文本模块详情读取失败")
    assert_text(result.get_json(), case.payload, case.object_id, case.creator_id)
    check(result.get_json().get("group_ids") == [], "详情组限制不为空")
    case.assert_saved(after)
    check(after == before, "文本详情读取改变了原配置")
    case.assert_unchanged_others()
    case.recorder.record("assertions", {"result": "passed", "text_module_id": case.object_id})


@allure.title("TC_172 250字符note完整保存，正文可见内容正确")
def test_tc_172(text_module_case) -> None:
    """note必须恰好250个A，正文HTML包装变化不算内容变化。"""
    case, _ = text_module_case
    check(case.payload["note"] == "A" * 250, "250字符note输入不符")
    with allure.step("AD创建并核对响应、回读和数据库原始note"):
        prepare(case)
    case.recorder.record("assertions", {"result": "passed", "text_module_id": case.object_id})


@allure.title("TC_173 251字符note被明确长度校验拒绝且无新增")
def test_tc_173(text_module_case) -> None:
    """静默截断或服务错误保持业务失败，异常新对象仍按真实归属清理。"""
    case, data = text_module_case
    check(case.payload["note"] == "A" * 251, "251字符note输入不符")
    with allure.step("AD提交原始251字符note并保存创建后证据"):
        result = case.create()
    assert_error(result, data["expected"]["error_kind"])
    check(
        case.object_id is None and case.db.text_module_ids() == case.baseline,
        "超长note创建了文本模块",
    )
    case.assert_unchanged_others()
    case.recorder.record("assertions", {"result": "passed", "creation": "rejected"})


@allure.title("TC_174 Customer C1不能读取文本模块详情或获得原正文")
def test_tc_174(text_module_case) -> None:
    """用真实P排除不存在假拒绝，仅GET详情且核查嵌套和HTML正文泄露。"""
    case, data = text_module_case
    before = prepare(case)
    with allure.step("C1按已知ID请求详情，由M及DB保全目标不变证据"):
        result = case.detail_as_actor()
        after = case.snapshot("text-module-after-denied-detail")
    assert_error(result, data["expected"]["error_kind"])
    assert_no_content_leak(result, case.payload["content"])
    check(after == before, "客户详情读取改变了文本模块")
    case.assert_saved(after)
    case.assert_unchanged_others()
    case.recorder.record("assertions", {"result": "passed", "text_module_id": case.object_id})
