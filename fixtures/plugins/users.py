"""用户用例的独立身份会话与资源收尾。"""

from contextlib import ExitStack

import allure
import pytest

from clients.http import HttpClient
from fixtures.plugins._lifecycle import finish_case
from reporting.evidence import CaseEvidence


@pytest.fixture
def user_case(environment_run, request):
    """用户用例独立会话与短只读连接，保持整轮同一环境锁。"""
    from db.queries import Queries
    from fixtures.users import UserCase, load_user_cases, preflight_users

    context = environment_run
    case_id = request.node.originalname.removeprefix("test_").upper()
    data = {**load_user_cases()[case_id], "case_id": case_id}
    evidence = CaseEvidence(context.recorder, case_id)
    with ExitStack() as stack:
        admin = stack.enter_context(
            HttpClient(context.settings, context.extra_identities["AD"], evidence)
        )
        maintenance = stack.enter_context(
            HttpClient(context.settings, context.maintenance, evidence)
        )
        problem = False
        try:
            with allure.step(f"{case_id} 核验独立管理员及用户只读权限"):
                db = stack.enter_context(Queries(context.db_config, evidence))
                mapping = preflight_users(admin, maintenance, context, db)
        except Exception:
            problem = True
        if problem:
            pytest.fail("用户前置核查失败，请检查身份及用户只读授权", pytrace=False)
        evidence.summary_only = False
        evidence.record(
            "preflight", {"run_id": context.run.run_id, "mapping": mapping, "result": "ready"}
        )
        evidence.ensure_complete()
        case = UserCase(
            admin, maintenance, context.run, mapping, data, evidence, db, context.settings
        )
        try:
            yield case, data
        finally:
            finish_case(case, evidence, f"{case_id} 清理或取证失败")
