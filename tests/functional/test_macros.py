"""TC187～190宏配置与一次跨组批量请求的真实业务断言。"""

from copy import deepcopy

import allure
import pytest

from assertions.tickets import check
from fixtures.config_checks import assert_error
from fixtures.macros import assert_macro, macro_payload

pytestmark = [pytest.mark.functional, pytest.mark.database]


def verify_macro(case, result, expected: dict, label: str) -> dict:
    """先取证再比较响应、API及数据库动作和独立组关联。"""
    value = case.snapshot(label)
    check(value["status"] == 200, "宏配置回读失败")
    stored = (
        {**value["db"], "group_ids": value["groups"]} if isinstance(value["db"], dict) else None
    )
    for row in (result.get_json(), value["api"], stored):
        assert_macro(row, expected, case.object_id, case.creator_id)
    check(value["groups"] == expected.get("group_ids", []), "宏数据库组关联不符")
    case.assert_unchanged_others()
    return value


def prepare(case) -> dict:
    """AD为当前用例独立创建宏，验证持久化后才执行被测操作。"""
    result = case.create()
    check(result.response.status_code == 201 and case.object_id is not None, "AD宏创建失败")
    return verify_macro(case, result, case.payload, "macro-prepared")


@allure.title("TC_187 AD创建宏，动作、适用组及完成后行为正确")
def test_tc_187(macro_case) -> None:
    """优先级动作使用真实高优先级ID，API和数据库完整一致。"""
    case, _ = macro_case
    with allure.step("AD创建本条宏并核对配置持久化"):
        prepare(case)
    case.recorder.record("assertions", {"result": "passed", "macro_id": case.object_id})


@allure.title("TC_188 只修改宏perform，同ID及原组限制保留")
def test_tc_188(macro_case) -> None:
    """初始高优先级及open动作改为低优先级及open，不新增副本。"""
    case, data = macro_case
    with allure.step("AD独立创建本条双动作宏"):
        prepare(case)
    with allure.step("只提交perform并先保存响应及回读"):
        result = case.update()
        expected = deepcopy(case.payload)
        expected.update(macro_payload(data["update"], case.mapping))
        verify_macro(case, result, expected, "macro-after-update")
    check(result.response.status_code == data["expected"]["status_code"], "宏动作更新未成功")
    case.recorder.record("assertions", {"result": "passed", "macro_id": case.object_id})


@allure.title("TC_189 普通A1创建宏被管理权限拒绝且无残留")
def test_tc_189(macro_case) -> None:
    """使用已核实A1身份，只接受明确权限原因，401不能冒充通过。"""
    case, data = macro_case
    with allure.step("A1原样提交无group_ids的宏创建请求"):
        result = case.create()
    with allure.step("核对权限原因和唯一名称无新增"):
        assert_error(result, data["expected"]["error_kind"])
        check(case.object_id is None and not case.db.macros_named(case.name), "越权请求新增了宏")
        case.assert_unchanged_others()
    case.recorder.record("assertions", {"result": "passed", "creation": "rejected"})


@allure.title("TC_190 A12一次批量宏跨适用组时422，两张工单均不变化")
def test_tc_190(macro_case) -> None:
    """明确区分宏适用组限制和客服授权失败，不逐张调用替代批量。"""
    case, data = macro_case
    with allure.step("M准备G1/G2独立工单，AD准备仅适用G1的高优先级宏"):
        case.prepare_tickets()
        prepare(case)
        before = case.ticket_snapshot("mass-macro-before")
    with allure.step("A12一次POST同时提交G1及G2的两张工单ID"):
        result = case.mass_macro()
        after = case.ticket_snapshot("mass-macro-after")
    with allure.step("检查422明确宏组错误、阻塞G2对象及两单完整状态不变"):
        value = result.get_json()
        check(
            result.response.status_code == data["expected"]["status_code"],
            "跨组批量宏未按预期422拒绝",
        )
        check(
            isinstance(value, dict) and value.get("error") == data["expected"]["error"],
            "拒绝原因不是宏适用组限制",
        )
        check(
            value.get("blocking_tickets") == [case.tickets[1].ticket_id], "阻塞对象不是且仅为G2工单"
        )
        check(after == before, "批量宏拒绝后工单或文章被部分修改")
        case.assert_unchanged_others()
    case.recorder.record(
        "assertions",
        {
            "result": "passed",
            "macro_id": case.object_id,
            "ticket_ids": [ticket.ticket_id for ticket in case.tickets],
        },
    )
