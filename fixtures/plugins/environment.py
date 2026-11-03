"""整轮环境锁、基线检查和功能用例残留守卫。"""

from contextlib import ExitStack
from pathlib import Path

import allure
import pytest

from clients.http import HttpClient
from fixtures.plugins._context import ROOT, load_context
from reporting.evidence import CaseEvidence
from support.runtime import FINISHED, EnvironmentRun


def _check_search_baseline(context, *, index: bool) -> None:
    """在整轮锁内只读核对长期基线；仅准备阶段有限等待索引。"""
    from db.queries import Queries
    from fixtures.search_baseline import SearchBaseline, load_mapping
    from fixtures.search_baseline import preflight as search_preflight
    from support.search_config import ALIASES
    from tools.metadata import _hash_file

    evidence = CaseEvidence(context.recorder, "SEARCH_BASELINE")
    path = context.config.state_dir / context.settings.env_id / "search-baseline.json"
    with ExitStack() as stack:
        clients = {
            alias: stack.enter_context(
                HttpClient(context.settings, context.extra_identities[alias], evidence)
            )
            for alias in ALIASES
        }
        clients["M"] = stack.enter_context(
            HttpClient(context.settings, context.maintenance, evidence)
        )
        db = stack.enter_context(Queries(context.db_config, evidence))
        mapping = load_mapping(path, context.settings.env_id, context.search_definition)
        identity = search_preflight(
            clients, context.search_config, context.config, context.search_definition, db
        )
        if not index and mapping != context.search_mapping:
            raise RuntimeError("长期基线映射在整轮期间发生变化") from None
        baseline = SearchBaseline(
            clients, db, context.run, evidence, identity, context.search_definition
        )
        # 身份列表只留摘要；专用基线保留即时快照，漂移时无需重发请求补证。
        evidence.summary_only = False
        baseline.validate(mapping, index=index)
        context.search_identity = identity
        context.search_mapping = mapping
        evidence.record(
            "check",
            {
                "phase": "before" if index else "after",
                "status": "passed",
                "mapping_sha256": _hash_file(path),
                "definition_sha256": context.search_definition["sha256"],
                "ticket_count": 9,
                "article_count": 13,
            },
        )
        evidence.ensure_complete()


def _check_database(context, pytestconfig) -> None:
    """持锁后验证数据库和历史保留角色，非数据库用例按需补载配置。"""
    if context.run.retained_roles and context.db_config is None:
        from db.config import load_db_config

        context.db_config = load_db_config(Path(pytestconfig.getoption("zammad_db_config")))
    if context.db_config is None:
        return
    from db.queries import Queries
    from fixtures.retained_roles import check_retained_roles

    problem = None
    try:
        # 每用例仍建立独立只读连接；这里先排除整轮无法执行的情况。
        with Queries(context.db_config, context.recorder) as db:
            check_retained_roles(context.run, db)
    except Exception:
        problem = "数据库或历史保留角色前置不可用，请核对只读配置及角色隔离状态"
    if problem:
        pytest.fail(problem, pytrace=False)


def _finish_run(context) -> None:
    """持锁完成基线复核、证据与残留检查，保留全部结束异常。"""
    with allure.step("整轮证据和残留检查"):
        context.recorder.record(
            "run-summary", {"run_id": context.run.run_id, "resources": context.run.resources}
        )
        errors = []
        if context.search_config is not None:
            try:
                _check_search_baseline(context, index=False)
            except Exception:
                errors.append(RuntimeError("整轮结束搜索基线核查失败，请查看脱敏证据"))
        try:
            context.recorder.ensure_complete()
        except Exception as error:
            errors.append(error)
        if any(item["state"] not in FINISHED for item in context.run.resources):
            errors.append(RuntimeError("本轮存在未解决资源，请查看共享运行清单"))
        if errors:
            raise ExceptionGroup("整轮结束检查失败", errors) from None


@pytest.fixture(scope="session")
def environment_run(pytestconfig, request):
    """持有整轮主机锁，直到业务取证和所有资源清理结束。"""
    context = load_context(pytestconfig, request.session.items)
    with EnvironmentRun(context.config.state_dir, context.settings.env_id) as run:
        context.run = run
        _check_database(context, pytestconfig)
        if context.search_config is not None:
            problem = False
            try:
                with allure.step("整轮搜索基线与索引前置检查"):
                    _check_search_baseline(context, index=True)
            except Exception:
                problem = True
            if problem:
                pytest.fail("搜索基线或索引前置失败；不自动修复，请查看脱敏证据", pytrace=False)
        try:
            yield context
        finally:
            _finish_run(context)


@pytest.fixture(autouse=True)
def business_guard(request):
    """所有功能用例共享同一个运行上下文，直接运行也不绕过。"""
    functional_path = ROOT / "tests/functional"
    if request.node.get_closest_marker("functional") or Path(request.node.path).is_relative_to(
        functional_path
    ):
        context = request.getfixturevalue("environment_run")
        if any(item["state"] not in FINISHED for item in context.run.resources):
            pytest.fail("前一用例有未处理残留，停止后续业务执行", pytrace=False)
