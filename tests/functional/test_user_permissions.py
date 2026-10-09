"""TC_141～144：角色提升保护、精确组授权及撤权后的新会话验证。"""

import allure
import pytest

from api.tickets import TicketsAPI
from assertions.tickets import FIELDS, check
from fixtures.user_permissions import assert_grants, assert_permission_denied, assert_ticket_denied

pytestmark = [pytest.mark.functional, pytest.mark.database]


def assert_identity(case, client) -> None:
    """临时Token必须仍能认证为本条U，排除凭据失效导致的假拒绝。"""
    result = client.request("GET", "api/v1/users/me")
    value = result.get_json()
    check(
        result.response.status_code == 200
        and isinstance(value, dict)
        and value.get("id") == case.user.user_id
        and value.get("login") == case.user.login,
        "临时Token未认证为本条U",
    )
    check(case.user.no_password(result), "自身身份响应泄露临时密码")


def assert_unchanged(before: dict, after: dict) -> None:
    """拒绝时保留核心字段、审计字段和文章，避免比较无关SLA或AI字段。"""
    for key in ("ticket", "db_ticket"):
        fields = (*FIELDS, "updated_by_id", "updated_at")
        check(
            all(
                field in after[key]
                and field in before[key]
                and after[key][field] == before[key][field]
                for field in fields
            ),
            "权限拒绝后工单业务或审计字段改变",
        )
    for key in ("articles", "db_articles"):
        check(after[key] == before[key], "权限拒绝后文章改变")


def prove_write(case, api, ticket) -> None:
    """合法且不同的优先级变更必须在响应、回读及数据库全部生效。"""
    before = case.ticket_snapshot(ticket, "permission-write-before")
    read = api.read(ticket.ticket_id)
    check(
        read.response.status_code == 200
        and isinstance(read.get_json(), dict)
        and read.get_json().get("id") == ticket.ticket_id,
        "U不能读取授权工单",
    )
    target = case.mapping["target_priority_id"]
    check(target != before["ticket"]["priority_id"], "写权限探针未实际改变字段")
    result = case.update_ticket(api, ticket, {"priority_id": target})
    after = case.ticket_snapshot(ticket, "permission-write-after")
    check(result.response.status_code == 200, "U合法更新未成功")
    expected = {field: before["ticket"][field] for field in FIELDS}
    expected["priority_id"] = target
    for value in (result.get_json(), after["ticket"], after["db_ticket"]):
        check(
            isinstance(value, dict)
            and all(field in value and value[field] == expected[field] for field in FIELDS),
            "合法更新响应或持久化字段不符",
        )
        check(value.get("updated_by_id") == case.user.user_id, "合法更新实际执行人不是U")
    for key in ("articles", "db_articles"):
        check(after[key] == before[key], "合法优先级更新改变了文章")


def deny_write(case, api, ticket, *, deny_read: bool) -> None:
    """直接调用GET和PUT，拒绝后再由M及数据库核查目标未改变。"""
    before = case.ticket_snapshot(ticket, "permission-denied-before")
    read = api.read(ticket.ticket_id)
    if deny_read:
        assert_ticket_denied(read, before)
    else:
        value = read.get_json()
        check(
            read.response.status_code == 200
            and isinstance(value, dict)
            and value.get("id") == ticket.ticket_id,
            "只读组未获得读取能力",
        )
    target = (
        case.mapping["target_priority_id"]
        if before["ticket"]["priority_id"] == case.mapping["priority_id"]
        else case.mapping["priority_id"]
    )
    result = case.update_ticket(api, ticket, {"priority_id": target})
    after = case.ticket_snapshot(ticket, "permission-denied-after")
    assert_permission_denied(result)
    assert_unchanged(before, after)


def verify_escalation(case) -> None:
    """提交原始Admin加Customer角色请求，安全忽略或权限拒绝都须保留实际角色。"""
    case.prepare()
    payload = {"role_ids": [case.mapping["admin_role_id"], case.mapping["customer_role_id"]]}
    if case.case_id == "TC_141":
        case.issue_token()
    before = case.snapshot("permission-escalation-before")
    with allure.step("指定身份直接提交Admin与Customer角色，先保全最终角色证据"):
        if case.case_id == "TC_141":
            with case.client() as client:
                assert_identity(case, client)
                result = case.update_user(client, payload)
        else:
            result = case.update_user(case.agent, payload)
        after = case.snapshot("permission-escalation-after")
        check(case.user.no_password(result), "提权响应泄露临时密码")
        if not 200 <= result.response.status_code < 300:
            assert_permission_denied(result)
        case.assert_user(after)
        assert_grants(after, {})
        check(after["permissions"] == before["permissions"], "提权请求改变了实际角色权限")
        check(after["others"] == before["others"], "提权请求影响了其他用户授权")
    case.recorder.record("assertions", {"result": "passed", "user_id": case.user.user_id})


@allure.title("TC_141 客户自身Token不能授予Admin角色")
def test_tc_141(user_permission_case) -> None:
    """数据库实际权限必须保持普通Customer，受限Token不能掩盖提权。"""
    case, _ = user_permission_case
    verify_escalation(case)


@allure.title("TC_142 普通A1不能将临时客户提升为Admin")
def test_tc_142(user_permission_case) -> None:
    """允许安全忽略或明确权限拒绝，任意4xx不算通过。"""
    case, _ = user_permission_case
    verify_escalation(case)


@allure.title("TC_143 AD精确赋予G1只读和G2全部权限")
def test_tc_143(user_permission_case) -> None:
    """新U会话分别证明G1可读不可写、G2可读可写。"""
    case, _ = user_permission_case
    baseline = case.prepare()
    groups = {str(case.mapping["group_id"]): ["read"], str(case.mapping["group2_id"]): ["full"]}
    with allure.step("AD只为临时Agent设置精确组授权"):
        case.set_groups(groups)
        case.issue_token()
        g1 = case.prepare_ticket(case.mapping["group_id"], "g1")
        g2 = case.prepare_ticket(case.mapping["group2_id"], "g2")
    with allure.step("新U会话实际验证两组读写边界"):
        with case.client() as client:
            assert_identity(case, client)
            deny_write(case, TicketsAPI(client), g1, deny_read=False)
            prove_write(case, TicketsAPI(client), g2)
    final = case.snapshot("permission-final")
    case.assert_user(final)
    assert_grants(final, groups)
    check(final["others"] == baseline["others"], "本条操作改变了其他用户组授权")
    case.recorder.record("assertions", {"result": "passed", "user_id": case.user.user_id})


@allure.title("TC_144 清空G1授权后新U会话不能读取或更新工单")
def test_tc_144(user_permission_case) -> None:
    """先证明Token可读写，再撤权验证实际访问与持久化均受保护。"""
    case, _ = user_permission_case
    baseline = case.prepare()
    with allure.step("为临时Agent设置G1full并证明Token读写能力"):
        case.set_groups({str(case.mapping["group_id"]): ["full"]})
        case.issue_token()
        ticket = case.prepare_ticket(case.mapping["group_id"], "g1")
        with case.client() as client:
            assert_identity(case, client)
            prove_write(case, TicketsAPI(client), ticket)
    before = case.ticket_snapshot(ticket, "permission-before-revoke")
    with allure.step("AD清空组授权，新U会话直接GET和PUT均被拒绝"):
        case.set_groups({})
        with case.client() as client:
            assert_identity(case, client)
            deny_write(case, TicketsAPI(client), ticket, deny_read=True)
    after = case.ticket_snapshot(ticket, "permission-after-revoke")
    assert_unchanged(before, after)
    final = case.snapshot("permission-final")
    case.assert_user(final)
    assert_grants(final, {})
    check(final["others"] == baseline["others"], "撤销U权限影响了其他用户")
    case.recorder.record("assertions", {"result": "passed", "user_id": case.user.user_id})
