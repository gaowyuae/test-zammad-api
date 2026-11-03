"""工单公共前置、身份选择和逐用例资源生命周期。"""

from contextlib import ExitStack
from types import SimpleNamespace

import allure
import pytest

from api.tickets import TicketsAPI
from clients.http import HttpClient
from clients.identity import Identity
from fixtures.preflight import PreflightError, list_rows, preflight, unique
from reporting.evidence import CaseEvidence


def finish_case(case, evidence, message: str) -> None:
    """清理失败仍检查证据，并完整保留两类收尾异常。"""
    errors = []
    try:
        case.cleanup()
    except Exception as error:
        errors.append(error)
    try:
        evidence.ensure_complete()
    except Exception as error:
        errors.append(error)
    if errors:
        raise ExceptionGroup(message, errors) from None


def _ticket_clients(stack, context, evidence, actor_alias: str, peer: bool):
    """建立独立会话，明确选择被测身份及可选文章作者。"""
    clients = SimpleNamespace(
        agent=stack.enter_context(HttpClient(context.settings, context.agent, evidence)),
        maintenance=stack.enter_context(
            HttpClient(context.settings, context.maintenance, evidence)
        ),
        anonymous=stack.enter_context(HttpClient(context.settings, Identity.anonymous(), evidence)),
    )
    if actor_alias == "anonymous":
        clients.actor = clients.anonymous
    elif actor_alias == "A1":
        clients.actor = clients.agent
    else:
        clients.actor = stack.enter_context(
            HttpClient(context.settings, context.extra_identities[actor_alias], evidence)
        )
    clients.peer = (
        stack.enter_context(HttpClient(context.settings, context.extra_identities["A2"], evidence))
        if peer
        else None
    )
    return clients


def _ticket_preflight(context, clients, data: dict, kind: str, login: str | None) -> dict:
    """调用场景已有前置能力，保留实际身份、组及 Token 范围检查。"""
    agent, maintenance, actor = clients.agent, clients.maintenance, clients.actor
    case_id = data["case_id"]
    if kind == "article":
        from fixtures.article_checks import article_check_preflight

        peer_login = context.assignment_config.peer_login if clients.peer is not None else None
        return article_check_preflight(
            agent,
            maintenance,
            actor,
            clients.peer,
            context.config,
            login,
            peer_login,
            data,
            case_id,
        )
    if kind == "message":
        from fixtures.messages import message_preflight

        return message_preflight(agent, maintenance, actor, context.config, login, data, case_id)
    if kind == "state":
        from fixtures.state_change import state_preflight

        return state_preflight(agent, maintenance, context.config, data, case_id)
    if kind == "update_access":
        from fixtures.update_access import update_preflight

        return update_preflight(
            agent, maintenance, actor, context.config, context.update_access_config, data, case_id
        )
    if kind == "assignment":
        from fixtures.assignment import assignment_preflight

        return assignment_preflight(
            agent, maintenance, actor, context.config, context.assignment_config, data, case_id
        )
    if kind == "creation":
        from fixtures.creation_access import creation_preflight

        return creation_preflight(
            agent, maintenance, actor, context.config, context.creation_config, data, case_id
        )
    return preflight(agent, maintenance, context.config, data)


def _check_case_database(db, mapping: dict, case_id: str, kind: str) -> None:
    """写入前检查专项只读列，并为不存在工单场景确定安全 ID。"""
    if kind == "state":
        db.check_pending_access()
    if case_id in {"TC_093", "TC_094"}:
        db.check_attachment_access()
    if case_id == "TC_062":
        maximum = db.ticket_inventory()["max_id"]
        if maximum > 2_147_482_647:
            raise PreflightError("无法安全选择不存在工单ID")
        mapping["missing_ticket_id"] = maximum + 1000


def database_case(
    context,
    case_id: str,
    data: dict,
    scenario: dict,
    case_type,
    *,
    kind: str = "pilot",
    login: str | None = None,
):
    """编排工单前置和逐用例生命周期，残留由上游 business_guard 阻断。"""
    from db.queries import Queries

    evidence = CaseEvidence(context.recorder, case_id)
    actor_alias = (
        scenario["actor"]
        if kind in {"creation", "assignment", "update_access", "message", "article"}
        else "A1"
    )
    with ExitStack() as stack:
        clients = _ticket_clients(
            stack, context, evidence, actor_alias, kind == "article" and case_id == "TC_096"
        )
        problem = None
        try:
            with allure.step(f"{case_id} 核对身份、字典与只读数据库"):
                mapping = _ticket_preflight(context, clients, data, kind, login)
                if "target_state" in scenario:
                    mapping["target_state_id"] = unique(
                        list_rows(clients.maintenance, "api/v1/ticket_states"),
                        "name",
                        scenario["target_state"],
                    )["id"]
                db = stack.enter_context(Queries(context.db_config, evidence))
                _check_case_database(db, mapping, case_id, kind)
        except Exception:
            problem = "工单前置核查失败，请核对身份、字典和只读数据库"
        if problem:
            pytest.fail(problem, pytrace=False)
        evidence.summary_only = False
        evidence.record(
            "preflight", {"run_id": context.run.run_id, "mapping": mapping, "result": "ready"}
        )
        evidence.ensure_complete()
        # 维护身份准备更新目标，被测请求继续使用独立 actor 会话。
        creator = clients.anonymous if case_id == "TC_025" else clients.actor
        if kind in {"state", "message", "article", "assignment", "update_access"} or case_id in {
            "TC_058",
            "TC_072",
        }:
            creator = clients.maintenance
            mapping["creator_id"] = mapping["maintenance_id"]
        case = case_type(creator, clients.maintenance, context.run, mapping, data, evidence, db)
        if kind == "article":
            case.peer_api = TicketsAPI(clients.peer) if clients.peer is not None else None
        try:
            yield case, scenario, db, TicketsAPI(clients.actor)
        finally:
            finish_case(case, evidence, f"{case_id} 清理或取证失败")
