"""配置对象 fixture 的身份会话、业务装配和统一收尾。"""

from contextlib import ExitStack
from types import SimpleNamespace

import allure
import pytest

from clients.http import HttpClient
from fixtures.macros import MacroCase, load_macro_cases, preflight_macros
from fixtures.plugins._lifecycle import finish_case
from fixtures.templates import TemplateCase, load_template_cases, preflight_templates
from fixtures.text_modules import TextModuleCase, load_text_module_cases, preflight_text_modules
from reporting.evidence import CaseEvidence

_CASES = {
    "template": (TemplateCase, load_template_cases, preflight_templates),
    "text_module": (TextModuleCase, load_text_module_cases, preflight_text_modules),
    "macro": (MacroCase, load_macro_cases, preflight_macros),
}


def _clients(stack, context, evidence, kind: str):
    """根据配置对象和场景建立独立的管理、维护及被测身份会话。"""
    clients = SimpleNamespace(
        admin=stack.enter_context(
            HttpClient(context.settings, context.extra_identities["AD"], evidence)
        ),
        maintenance=stack.enter_context(
            HttpClient(context.settings, context.maintenance, evidence)
        ),
        actor=None,
    )
    case_id = evidence.case_id
    if kind == "template":
        clients.actor = stack.enter_context(HttpClient(context.settings, context.agent, evidence))
    elif case_id == "TC_174":
        clients.actor = stack.enter_context(
            HttpClient(context.settings, context.extra_identities["C1"], evidence)
        )
    if kind == "macro":
        clients.agent = stack.enter_context(HttpClient(context.settings, context.agent, evidence))
        clients.actor = clients.agent if case_id in {"TC_189", "TC_190"} else clients.admin
        if case_id == "TC_190":
            clients.actor = stack.enter_context(
                HttpClient(context.settings, context.extra_identities["A12"], evidence)
            )
    return clients


def configuration_case(context, request, kind: str):
    """编排配置对象前置、业务对象构建和清理，复用同一整轮环境。"""
    from db.queries import Queries

    case_type, load_cases, preflight_check = _CASES[kind]
    case_id = request.node.originalname.removeprefix("test_").upper()
    data = {**load_cases()[case_id], "case_id": case_id}
    evidence = CaseEvidence(context.recorder, case_id)
    with ExitStack() as stack:
        clients = _clients(stack, context, evidence, kind)
        failed = False
        try:
            with allure.step(f"{case_id} 核验配置管理身份及最小只读权限"):
                db = stack.enter_context(Queries(context.db_config, evidence))
                if kind == "macro":
                    mapping = preflight_check(
                        clients.admin,
                        clients.maintenance,
                        clients.agent,
                        clients.actor,
                        context,
                        db,
                        case_id,
                    )
                else:
                    mapping = preflight_check(
                        clients.admin, clients.maintenance, clients.actor, context, db
                    )
        except Exception:
            failed = True
        if failed:
            pytest.fail("配置对象前置失败，请核对身份及列授权", pytrace=False)
        evidence.summary_only = False
        evidence.record(
            "preflight", {"run_id": context.run.run_id, "mapping": mapping, "result": "ready"}
        )
        evidence.ensure_complete()
        case = case_type(
            clients.admin, clients.maintenance, clients.actor, context, mapping, data, evidence, db
        )
        try:
            yield case, data
        finally:
            finish_case(case, evidence, f"{case_id} 配置对象清理或取证失败")
