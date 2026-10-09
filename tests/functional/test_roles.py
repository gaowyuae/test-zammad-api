"""TC_145～148角色创建、名称校验及客户管理权限隔离。"""

import allure
import pytest

from assertions.tickets import check
from fixtures.roles import ROLE_FIELDS, assert_permissions, assert_rejection
from fixtures.ticket import valid_id

pytestmark = [pytest.mark.functional, pytest.mark.database]


@allure.title("TC_145 AD创建非默认注册角色且权限精确保存")
def test_tc_145(role_case) -> None:
    """同时核查响应、维护回读及数据库，没有额外权限或用户关联。"""
    case, data = role_case
    with allure.step("AD提交本轮唯一角色名称及指定权限"):
        result = case.create()
        check(result.response.status_code == data["expected"]["status_code"], "角色创建状态码不符")
        response = result.get_json()
        check(
            isinstance(response, dict)
            and valid_id(response.get("id"))
            and response["id"] == case.role_id,
            "角色创建响应ID不符",
        )
    with allure.step("核对API和DB属性与完整权限集合"):
        saved = case.owned()
        expected = {
            "id": case.role_id,
            "name": case.name,
            "active": True,
            "default_at_signup": False,
        }
        permissions = {
            name: case.mapping["permission_ids"][name] for name in data["permission_names"]
        }
        for row in (response, saved["api"], saved["db"]):
            check(
                all(field in row and row[field] == expected[field] for field in ROLE_FIELDS),
                "角色属性保存不符",
            )
        for row in (response, saved["api"]):
            check(row.get("created_by_id") == case.creator_id, "角色实际创建身份不符")
            ids = row.get("permission_ids")
            check(
                isinstance(ids, list)
                and len(ids) == len(permissions)
                and set(ids) == set(permissions.values()),
                "接口角色权限不精确",
            )
            check(row.get("group_ids") == {}, "新角色出现额外组授权")
        assert_permissions(saved["permissions"], permissions)
        check(saved["groups"] == [] and saved["users"] == [], "新角色出现组或用户关联")
        case.assert_existing_unchanged()
    case.recorder.record("assertions", {"result": "passed", "role_id": case.role_id})


def verify_rejection(role_case) -> None:
    """错误原因、角色全量集合和原有授权均需保持，异常新建不能通过。"""
    case, data = role_case
    with allure.step("指定身份提交原始名称和权限，不补缺失字段"):
        if case.case_id in {"TC_147", "TC_148"}:
            check("active" not in case.payload, "原用例未提交的active被意外补齐")
        if case.case_id == "TC_146":
            check(case.payload.get("name") == "", "空名称被意外补齐")
        if case.case_id == "TC_147":
            check(case.payload.get("name") == "agent", "大小写重名输入被改写")
        result = case.create()
    with allure.step("核查明确拒绝、无新角色及原角色和关联不变"):
        assert_rejection(result, data["expected"]["error_kind"])
        check(case.role_id is None and case.db.role_ids() == case.baseline, "负向请求新增了角色")
        case.assert_existing_unchanged()
    case.recorder.record("assertions", {"result": "passed", "creation": "rejected"})


@allure.title("TC_146 空角色名称被必填校验拒绝")
def test_tc_146(role_case) -> None:
    """空串原样提交，不能用权限或其他字段报错代替名称必填。"""
    verify_rejection(role_case)


@allure.title("TC_147 agent与已有Agent大小写重名被拒绝")
def test_tc_147(role_case) -> None:
    """不提交active，原Agent完整回读和所有授权关系保持。"""
    verify_rejection(role_case)


@allure.title("TC_148 客户C1不能创建具有admin权限的角色")
def test_tc_148(role_case) -> None:
    """C1的身份及customer Token范围已正向验证，拒绝后没有角色残留。"""
    verify_rejection(role_case)
