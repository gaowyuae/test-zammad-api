"""TC_137～140 用户创建、重复保护、局部更新及不存在读取。"""

from datetime import datetime

import allure
import pytest

from assertions.tickets import check
from fixtures.ticket import valid_id
from fixtures.users import USER_FIELDS

pytestmark = [pytest.mark.functional, pytest.mark.database]


def has_user_details(value: object) -> bool:
    """错误响应也不能在嵌套对象或列表中夹带账号资料。"""
    fields = {"id", "login", "email", "firstname", "lastname", "role_ids", "organization_id"}
    if isinstance(value, dict):
        return bool(fields & {str(key).lower() for key in value}) or any(
            has_user_details(item) for item in value.values()
        )
    return isinstance(value, list) and any(has_user_details(item) for item in value)


def user_value(case, result, status: int) -> dict:
    """用固定诊断检查原始响应，密码不参与 pytest 表达式展开。"""
    check(case.no_password(result), "用户响应泄露了本条临时密码")
    check(result.response.status_code == status, "用户响应状态码不符")
    value = result.get_json()
    check(isinstance(value, dict), "用户响应结构不符")
    return value


def comparable(value: dict) -> dict:
    """提取约定业务字段并统一创建时间，允许合法审计更新时间变化。"""
    check(
        isinstance(value, dict) and all(name in value for name in USER_FIELDS), "用户业务字段缺失"
    )
    result = {name: value[name] for name in USER_FIELDS}
    try:
        result["created_at"] = datetime.fromisoformat(result["created_at"])
    except (ValueError, TypeError):
        raise AssertionError("用户创建时间格式不符") from None
    check(result["created_at"].tzinfo is not None, "用户创建时间缺少时区")
    return result


def assert_saved(case, snapshot: dict, firstname: str) -> None:
    """同时验证接口、列级只读证据及恰好 Customer 的角色关系。"""
    __tracebackhide__ = True  # 原始快照可能含异常回显的秘密，禁止展示函数实参。
    check(snapshot["status"] == 200 and snapshot["no_password"], "用户回读失败或泄露密码")
    expected = {name: case.payload[name] for name in ("login", "lastname", "email", "active")}
    expected.update(
        id=case.user_id,
        firstname=firstname,
        organization_id=None,
        created_by_id=case.mapping["admin_id"],
    )
    for row in (snapshot["api"], snapshot["db"]):
        check(isinstance(row, dict), "用户保存记录缺失")
        for name, value in expected.items():
            check(name in row and row[name] == value, f"用户 {name} 保存不符")
    check(comparable(snapshot["api"]) == comparable(snapshot["db"]), "API 与 DB 用户字段不符")
    check(
        snapshot["api"].get("role_ids") == [case.mapping["customer_role_id"]],
        "接口角色不是唯一 Customer",
    )
    check(
        snapshot["roles"]
        == [{"id": case.mapping["customer_role_id"], "name": "Customer", "active": True}],
        "数据库角色不是唯一 Customer",
    )
    check(
        len(snapshot["same_login"]) == 1 and snapshot["same_login"][0]["id"] == case.user_id,
        "登录名未对应唯一客户",
    )


def prepare(case) -> dict:
    """本条独立创建客户，先取证再核对关联及属性。"""
    created = case.create()
    response = user_value(case, created, 201)
    check(valid_id(response.get("id")) and response["id"] == case.user_id, "新用户 ID 无效")
    before = case.snapshot("user-before")
    assert_saved(case, before, case.payload["firstname"])
    check(comparable(response) == comparable(before["api"]), "创建响应与保存资料不一致")
    check(response.get("role_ids") == [case.mapping["customer_role_id"]], "创建响应角色不符")
    return before


def assert_authentication(case) -> None:
    """确认原密码确实认证为本条临时用户，不接受管理员会话串用。"""
    value = user_value(case, case.authenticate(), 200)
    check(
        value.get("id") == case.user_id and value.get("login") == case.login,
        "原密码未认证为本条临时客户",
    )


def assert_duplicate(result) -> None:
    """必须明确指出登录名重复，权限错误和任意 4xx 均不能通过。"""
    check(400 <= result.response.status_code < 500, "重复登录名未被 4xx 拒绝")
    value = result.get_json()
    check(isinstance(value, dict), "重复拒绝响应结构不符")
    error = value.get("error")
    check(isinstance(error, str), "重复拒绝没有明确原因")
    error = error.casefold()
    check(
        "login" in error and any(word in error for word in ("taken", "exists", "unique")),
        "拒绝原因不是登录名重复",
    )


@allure.title("TC_137 管理员创建普通客户并核查角色与持久化")
def test_tc_137(user_case) -> None:
    """创建新客户，核对无额外高权限及无密码明文。"""
    case, _ = user_case
    with allure.step("AD创建本轮唯一客户，核对201、API及数据库"):
        prepare(case)
    case.recorder.record("assertions", {"result": "passed", "user_id": case.user_id})


@allure.title("TC_138 重复登录名拒绝且原资料、角色和密码保持")
def test_tc_138(user_case) -> None:
    """独立证明原密码有效，再核对重复请求未覆盖客户资料。"""
    case, _ = user_case
    with allure.step("独立创建受控客户并以原密码确认身份"):
        before = prepare(case)
        assert_authentication(case)
    with allure.step("AD以相同登录名和不同资料密码再次创建"):
        result = case.duplicate()
        after = case.snapshot("user-after-duplicate")
        check(case.no_password(result), "重复响应泄露临时密码")
        assert_duplicate(result)
        assert_saved(case, after, case.payload["firstname"])
        check(comparable(after["api"]) == comparable(before["api"]), "重复请求覆盖原用户资料")
        check(after["roles"] == before["roles"], "重复请求覆盖原角色")
    with allure.step("使用原密码重新认证，确认密码未被覆盖"):
        assert_authentication(case)
    case.recorder.record("assertions", {"result": "passed", "user_id": case.user_id})


@allure.title("TC_139 仅更新 firstname，其他业务字段和角色保持")
def test_tc_139(user_case) -> None:
    """只发送 firstname，以前后快照核对局部更新。"""
    case, data = user_case
    with allure.step("AD准备本条独立客户"):
        before = prepare(case)
    with allure.step("仅更新firstname并核对响应、API和数据库"):
        result = case.update_firstname()
        after = case.snapshot("user-after-update")
        response = user_value(case, result, data["expected"]["status_code"])
        firstname = data["update"]["firstname"]
        assert_saved(case, after, firstname)
        expected = {**comparable(before["api"]), "firstname": firstname}
        check(
            comparable(response) == expected and comparable(after["api"]) == expected,
            "局部更新改变了其他业务字段",
        )
        check(response.get("role_ids") == before["api"]["role_ids"], "更新响应角色改变")
        check(after["roles"] == before["roles"], "局部更新改变角色")
    case.recorder.record("assertions", {"result": "passed", "user_id": case.user_id})


@allure.title("TC_140 读取已确认不存在用户返回404且不生成记录")
def test_tc_140(user_case) -> None:
    """从数据库最大 ID 推导目标，只读核实后执行 GET，不发 PUT。"""
    case, data = user_case
    with allure.step("动态选取并确认不存在的用户ID"):
        before = case.db.user_inventory()
        check(before["max_id"] <= 2_147_482_647, "无法安全选择不存在用户ID")
        user_id = (before["max_id"] or 0) + 1000
        check(case.db.user(user_id) is None, "目标用户实际存在")
    with allure.step("AD读取不存在用户并检查明确404"):
        result = case.api.read(user_id)
        value = user_value(case, result, data["expected"]["status_code"])
        check(not has_user_details(value), "404 响应泄露其他用户资料")
        error = value.get("error")
        check(
            isinstance(error, str)
            and "user" in error.casefold()
            and ("find" in error.casefold() or "not found" in error.casefold()),
            "404 原因不是目标用户不存在",
        )
        after = case.db.user_inventory()
        target = case.db.user(user_id)
        case.recorder.record(
            "user-missing", {"user_id": user_id, "before": before, "after": after, "target": target}
        )
        check(target is None and after == before, "只读请求生成了用户记录")
    case.recorder.record("assertions", {"result": "passed", "missing_user_id": user_id})
