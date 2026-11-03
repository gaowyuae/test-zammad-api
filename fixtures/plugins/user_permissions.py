"""用户权限用例的会话、前置和统一清理。"""

from contextlib import ExitStack

import allure
import pytest

from clients.http import HttpClient
from fixtures.plugins._lifecycle import finish_case
from reporting.evidence import CaseEvidence


@pytest.fixture
def user_permission_case(environment_run, request):
    """M17独立用户、工单和Token共享整轮环境锁及有限只读连接。"""
    from db.queries import Queries
    from fixtures.user_permissions import (
        UserPermissionCase,
        load_permission_cases,
        preflight_permissions,
    )

    context = environment_run
    case_id = request.node.originalname.removeprefix("test_").upper()
    data = {**load_permission_cases()[case_id], "case_id": case_id}
    evidence = CaseEvidence(context.recorder, case_id)
    with ExitStack() as stack:
        ad = stack.enter_context(
            HttpClient(context.settings, context.extra_identities["AD"], evidence)
        )
        maintenance = stack.enter_context(
            HttpClient(context.settings, context.maintenance, evidence)
        )
        agent = stack.enter_context(HttpClient(context.settings, context.agent, evidence))
        failed = False
        try:
            with allure.step(f"{case_id} 只读核验身份、组及最小权限查询"):
                db = stack.enter_context(Queries(context.db_config, evidence))
                mapping = preflight_permissions(ad, maintenance, agent, context, db)
        except Exception:
            failed = True
        if failed:
            pytest.fail("用户权限场景前置核查失败，请核对映射、身份及只读列", pytrace=False)
        evidence.summary_only = False
        evidence.record(
            "preflight", {"run_id": context.run.run_id, "mapping": mapping, "result": "ready"}
        )
        evidence.ensure_complete()
        case = UserPermissionCase(ad, maintenance, agent, context, mapping, data, evidence, db)
        try:
            yield case, data
        finally:
            finish_case(case, evidence, f"{case_id} 清理或取证失败")
