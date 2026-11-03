"""工单各领域 fixture 的业务装配。"""

from contextlib import ExitStack

import allure
import pytest

from clients.http import HttpClient
from fixtures.pilot import load_pilot_case
from fixtures.plugins._lifecycle import database_case, finish_case
from fixtures.preflight import PreflightError, preflight
from fixtures.ticket import TicketCase, load_case
from reporting.evidence import CaseEvidence


@pytest.fixture
def ticket_case(environment_run):
    """独立创建身份会话，在断言结束后保全证据并清理本次对象。"""
    context = environment_run
    data = load_case()
    evidence = CaseEvidence(context.recorder)
    with ExitStack() as stack:
        agent = stack.enter_context(HttpClient(context.settings, context.agent, evidence))
        maintenance = stack.enter_context(
            HttpClient(context.settings, context.maintenance, evidence)
        )
        with allure.step("TC_023 只读核验身份、权限及当前映射"):
            problem = None
            try:
                mapping = preflight(agent, maintenance, context.config, data)
            except PreflightError as error:
                problem = str(error)
            except Exception:
                problem = "前置核查异常，请查看已脱敏的请求摘要"
            if problem is not None:
                evidence.record("preflight-error", {"error": problem})
                # 在 except 之外失败，避免默认 funcargs 或异常链回显用户列表。
                pytest.fail(problem, pytrace=False)
        evidence.summary_only = False
        evidence.record(
            "preflight", {"run_id": context.run.run_id, "mapping": mapping, "result": "ready"}
        )
        evidence.ensure_complete()
        case = TicketCase(agent, maintenance, context.run, mapping, data, evidence)
        try:
            yield case, data
        finally:
            finish_case(case, evidence, "TC_023 清理或取证失败")


@pytest.fixture
def pilot_case(environment_run, request):
    """为每条 P0 提供独立数据及既有数据库生命周期。"""
    from fixtures.pilot import PilotCase

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_pilot_case(case_id)
    yield from database_case(environment_run, case_id, data, scenario, PilotCase)


@pytest.fixture
def title_case(environment_run, request):
    """标题场景使用同一身份、连接和清理路径，数据由明确场景加载。"""
    from fixtures.title import TitleCase, load_title_case

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_title_case(case_id)
    yield from database_case(environment_run, case_id, data, scenario, TitleCase)


@pytest.fixture
def creation_case(environment_run, request):
    """只为当前创建权限用例提供相应身份，不预先写入任何辅助业务数据。"""
    from fixtures.creation_access import CreationCase, load_creation_case

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_creation_case(case_id)
    yield from database_case(
        environment_run,
        case_id,
        data,
        scenario,
        CreationCase,
        kind="creation",
    )


@pytest.fixture
def assignment_case(environment_run, request):
    """每条分派用例由维护身份准备，使用明确客服身份更新。"""
    from fixtures.assignment import AssignmentCase, load_assignment_case

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_assignment_case(case_id)
    yield from database_case(
        environment_run,
        case_id,
        data,
        scenario,
        AssignmentCase,
        kind="assignment",
    )


@pytest.fixture
def update_access_case(environment_run, request):
    """权限负向更新使用各自会话，由维护身份独立准备与清理。"""
    from fixtures.update_access import UpdateAccessCase, load_update_case

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_update_case(case_id)
    yield from database_case(
        environment_run,
        case_id,
        data,
        scenario,
        UpdateAccessCase,
        kind="update_access",
    )


@pytest.fixture
def state_case(environment_run, request):
    """状态更新复用独立工单生命周期，挂起时间新增列在创建前核验。"""
    from fixtures.state_change import StateCase, load_state_case

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_state_case(case_id)
    yield from database_case(environment_run, case_id, data, scenario, StateCase, kind="state")


@pytest.fixture
def message_case(environment_run, request):
    """复用现有 C1/C2 配置，为消息场景提供独立身份及清理。"""
    from fixtures.messages import MessageCase, load_message_case

    case_id = request.node.originalname.upper().removeprefix("TEST_")
    data, scenario = load_message_case(case_id)
    login = environment_run.config.customer_login
    if case_id == "TC_087":
        login = environment_run.config.agent_login
    elif case_id == "TC_091":
        login = environment_run.update_access_config.other_customer_login
    yield from database_case(
        environment_run, case_id, data, scenario, MessageCase, kind="message", login=login
    )


@pytest.fixture
def article_check_case(environment_run, request):
    """仅M13加载所需作者及客户会话，复用整轮占用与证据。"""
    from fixtures.article_checks import ArticleCheckCase, load_article_check_case

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_article_check_case(case_id)
    login = environment_run.config.agent_login
    if case_id == "TC_094":
        login = environment_run.update_access_config.other_customer_login
    yield from database_case(
        environment_run, case_id, data, scenario, ArticleCheckCase, kind="article", login=login
    )
