"""角色用例的前置、取证与保留处置装配。"""

from contextlib import ExitStack

import allure
import pytest

from clients.http import HttpClient
from fixtures.plugins._lifecycle import finish_case
from reporting.evidence import CaseEvidence


@pytest.fixture
def role_case(environment_run, request):
    """角色用例按独立身份执行，获准保留前完成取证及隔离核查。"""
    from db.queries import Queries
    from fixtures.roles import RoleCase, load_role_cases, preflight_roles

    context = environment_run
    case_id = request.node.originalname.removeprefix("test_").upper()
    data = {**load_role_cases()[case_id], "case_id": case_id}
    evidence = CaseEvidence(context.recorder, case_id)
    with ExitStack() as stack:
        ad = stack.enter_context(
            HttpClient(context.settings, context.extra_identities["AD"], evidence)
        )
        maintenance = stack.enter_context(
            HttpClient(context.settings, context.maintenance, evidence)
        )
        customer = None
        if case_id == "TC_148":
            customer = stack.enter_context(
                HttpClient(context.settings, context.extra_identities["C1"], evidence)
            )
        failed = False
        try:
            with allure.step(f"{case_id} 核验角色身份、权限映射及只读列"):
                db = stack.enter_context(Queries(context.db_config, evidence))
                mapping = preflight_roles(ad, maintenance, customer, context, db)
        except Exception:
            failed = True
        if failed:
            pytest.fail("角色前置核验失败，请核对身份、权限映射和列授权", pytrace=False)
        evidence.summary_only = False
        evidence.record(
            "preflight", {"run_id": context.run.run_id, "mapping": mapping, "result": "ready"}
        )
        evidence.ensure_complete()
        case = RoleCase(ad, maintenance, customer, context, mapping, data, evidence, db)
        try:
            yield case, data
        finally:
            finish_case(case, evidence, f"{case_id} 保留处置或取证失败")
