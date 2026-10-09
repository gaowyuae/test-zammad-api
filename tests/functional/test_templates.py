"""TC_159～162模板配置的创建、校验、局部更新和权限隔离。"""

from copy import deepcopy

import allure
import pytest

from assertions.tickets import check
from fixtures.config_checks import assert_error
from fixtures.templates import assert_template

pytestmark = [pytest.mark.functional, pytest.mark.database]


def prepare(case) -> dict:
    """AD独立创建本条模板P并证明其字段和原集合正确。"""
    result = case.create()
    value = case.snapshot("template-prepared") if case.object_id is not None else None
    check(result.response.status_code == 201 and value is not None, "AD准备模板失败")
    check(value["status"] == 200, "准备模板回读失败")
    for row in (result.get_json(), value["api"], value["db"]):
        assert_template(row, case.payload, case.object_id, case.creator_id)
    case.assert_unchanged_others()
    return value


@allure.title("TC_159 AD创建模板，options持久化且不生成业务工单")
def test_tc_159(template_case) -> None:
    """唯一模板返回201，API及数据库options与提交字典一致。"""
    case, _ = template_case
    with allure.step("AD创建模板并核对响应、配置及业务工单基线"):
        prepare(case)
    case.recorder.record("assertions", {"result": "passed", "template_id": case.object_id})


@allure.title("TC_160 一个空格的模板名称被必填校验拒绝")
def test_tc_160(template_case) -> None:
    """空格原样提交，不用错误权限或截取后的空串替代用例。"""
    case, data = template_case
    check(case.payload["name"] == " ", "TC160必须提交精确一个空格")
    with allure.step("AD提交空白名称并保全创建后集合"):
        result = case.create()
    with allure.step("核查名称必填错误及模板和工单基线不变"):
        assert_error(result, data["expected"]["error_kind"])
        check(
            case.object_id is None and case.db.template_ids() == case.baseline, "空白名称新增了模板"
        )
        case.assert_unchanged_others()
    case.recorder.record("assertions", {"result": "passed", "creation": "rejected"})


@allure.title("TC_161 仅修改模板options，名称启用状态及ID保持")
def test_tc_161(template_case) -> None:
    """本条独立P接受局部PUT，不允许新建副本或重置未提交字段。"""
    case, data = template_case
    with allure.step("AD准备本条独立活跃模板P"):
        prepare(case)
    with allure.step("只提交修改后的options，并先保存回读证据"):
        result = case.update_options()
        after = case.snapshot("template-after-update")
    expected = deepcopy(case.payload)
    expected.update(data["update"])
    with allure.step("核对同ID及API、数据库完整配置"):
        check(
            result.response.status_code == data["expected"]["status_code"]
            and after["status"] == 200,
            "模板局部更新失败",
        )
        for row in (result.get_json(), after["api"], after["db"]):
            assert_template(row, expected, case.object_id, case.creator_id)
        case.assert_unchanged_others()
    case.recorder.record("assertions", {"result": "passed", "template_id": case.object_id})


@allure.title("TC_162 普通A1不能删除AD准备的活跃模板")
def test_tc_162(template_case) -> None:
    """明确权限拒绝且本条P及全部原配置保持，随后由M清理。"""
    case, data = template_case
    before = prepare(case)
    with allure.step("A1请求DELETE后立即由维护身份及DB取证"):
        result = case.delete_as_agent()
        after = case.snapshot("template-after-denied-delete")
    assert_error(result, data["expected"]["error_kind"])
    check(after == before, "拒绝删除后模板配置或集合发生变化")
    for row in (after["api"], after["db"]):
        assert_template(row, case.payload, case.object_id, case.creator_id)
    case.assert_unchanged_others()
    case.recorder.record("assertions", {"result": "passed", "template_id": case.object_id})
