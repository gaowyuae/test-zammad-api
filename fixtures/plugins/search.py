"""搜索用例的独立会话和长期基线读取。"""

from contextlib import ExitStack

import pytest

from clients.http import HttpClient
from clients.identity import Identity
from reporting.evidence import CaseEvidence


@pytest.fixture
def search_case(environment_run, request):
    """搜索用例独享身份会话及只读连接，不创建或重建长期基线。"""
    from db.queries import Queries
    from fixtures.search import SearchCase, load_search_cases
    from support.search_config import ALIASES

    context = environment_run
    case_id = request.node.originalname.removeprefix("test_").upper()
    data = {**load_search_cases()[case_id], "case_id": case_id}
    evidence = CaseEvidence(context.recorder, case_id)
    identities = {alias: context.extra_identities[alias] for alias in ALIASES}
    identities.update({"A1": context.agent, "M": context.maintenance, "Ø": Identity.anonymous()})
    with ExitStack() as stack:
        clients = {
            alias: stack.enter_context(HttpClient(context.settings, identity, evidence))
            for alias, identity in identities.items()
        }
        db = stack.enter_context(Queries(context.db_config, evidence))
        if case_id == "TC_118":
            from fixtures.config_checks import pure_actor

            problem = False
            try:
                pure_actor(clients["A1"], clients["M"], context.config.agent_login, "Agent", db)
            except Exception:
                problem = True
            if problem:
                pytest.fail("TC_118的A1身份、角色或Token范围前置不符合要求", pytrace=False)
        evidence.summary_only = False
        case = SearchCase(clients, db, context, context.search_mapping, data, evidence)
        try:
            yield case, data
        finally:
            case.finish()
