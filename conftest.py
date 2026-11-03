"""统一业务前置和环境占用；unit 与仅收集不触发业务操作。"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import allure
import pytest
import yaml

from api.tickets import TicketsAPI
from clients.http import HttpClient
from clients.identity import Identity
from fixtures.preflight import PreflightError, list_rows, preflight, unique
from fixtures.ticket import TicketCase
from reporting.evidence import EvidenceRecorder
from support.config import ConfigError, _ConfigLoader, load_config
from support.run_config import load_run_config
from support.runtime import FINISHED, EnvironmentRun

ROOT = Path(__file__).resolve().parent


def _finish_case(case, evidence, message: str) -> None:
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


def _check_search_baseline(context, *, index: bool) -> None:
    """在整轮锁内只读核对长期基线；仅准备阶段有限等待索引。"""
    from db.queries import Queries
    from fixtures.search_baseline import SearchBaseline, load_mapping
    from fixtures.search_baseline import preflight as search_preflight
    from support.search_config import ALIASES
    from tools.metadata import _hash_file

    evidence = _CaseEvidence(context.recorder, "SEARCH_BASELINE")
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


@pytest.fixture
def search_case(environment_run, request):
    """搜索用例独享身份会话及只读连接，不创建或重建长期基线。"""
    from db.queries import Queries
    from fixtures.search import SearchCase, load_search_cases
    from support.search_config import ALIASES

    context = environment_run
    case_id = request.node.originalname.removeprefix("test_").upper()
    data = {**load_search_cases()[case_id], "case_id": case_id}
    evidence = _CaseEvidence(context.recorder, case_id)
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


@pytest.fixture
def macro_case(environment_run, request):
    """宏与跨组工单在同一整轮锁下独立准备和清理。"""
    yield from _configuration_case(environment_run, request, "macro")


@pytest.fixture
def template_case(environment_run, request):
    """模板用例独立会话和资源，在同一整轮锁内完成。"""
    yield from _configuration_case(environment_run, request, "template")


@pytest.fixture
def text_module_case(environment_run, request):
    """文本模块用例按指定身份读取，维护身份负责清理。"""
    yield from _configuration_case(environment_run, request, "text_module")


def _configuration_case(context, request, kind):
    """配置fixture仅共享会话与退出处理，不解释业务步骤。"""
    from db.queries import Queries

    if kind == "template":
        from fixtures.templates import TemplateCase, load_template_cases, preflight_templates

        case_type, load_cases, preflight_check = (
            TemplateCase,
            load_template_cases,
            preflight_templates,
        )
    elif kind == "macro":
        from fixtures.macros import MacroCase, load_macro_cases, preflight_macros

        case_type, load_cases, preflight_check = MacroCase, load_macro_cases, preflight_macros
    else:
        from fixtures.text_modules import (
            TextModuleCase,
            load_text_module_cases,
            preflight_text_modules,
        )

        case_type, load_cases, preflight_check = (
            TextModuleCase,
            load_text_module_cases,
            preflight_text_modules,
        )
    case_id = request.node.originalname.removeprefix("test_").upper()
    data = {**load_cases()[case_id], "case_id": case_id}
    evidence = _CaseEvidence(context.recorder, case_id)
    with ExitStack() as stack:
        ad = stack.enter_context(
            HttpClient(context.settings, context.extra_identities["AD"], evidence)
        )
        maintenance = stack.enter_context(
            HttpClient(context.settings, context.maintenance, evidence)
        )
        actor = None
        if kind == "template":
            actor = stack.enter_context(HttpClient(context.settings, context.agent, evidence))
        elif case_id == "TC_174":
            actor = stack.enter_context(
                HttpClient(context.settings, context.extra_identities["C1"], evidence)
            )
        if kind == "macro":
            agent = stack.enter_context(HttpClient(context.settings, context.agent, evidence))
            actor = ad if case_id not in {"TC_189", "TC_190"} else agent
            if case_id == "TC_190":
                actor = stack.enter_context(
                    HttpClient(context.settings, context.extra_identities["A12"], evidence)
                )
        failed = False
        try:
            with allure.step(f"{case_id} 核验配置管理身份及最小只读权限"):
                db = stack.enter_context(Queries(context.db_config, evidence))
                if kind == "macro":
                    mapping = preflight_check(ad, maintenance, agent, actor, context, db, case_id)
                else:
                    mapping = preflight_check(ad, maintenance, actor, context, db)
        except Exception:
            failed = True
        if failed:
            pytest.fail("配置对象前置失败，请核对身份及列授权", pytrace=False)
        evidence.summary_only = False
        evidence.record(
            "preflight", {"run_id": context.run.run_id, "mapping": mapping, "result": "ready"}
        )
        evidence.ensure_complete()
        case = case_type(ad, maintenance, actor, context, mapping, data, evidence, db)
        try:
            yield case, data
        finally:
            _finish_case(case, evidence, f"{case_id} 配置对象清理或取证失败")


@pytest.fixture
def role_case(environment_run, request):
    """角色用例按独立身份执行，获准保留前完成取证及隔离核查。"""
    from db.queries import Queries
    from fixtures.roles import RoleCase, load_role_cases, preflight_roles

    context = environment_run
    case_id = request.node.originalname.removeprefix("test_").upper()
    data = {**load_role_cases()[case_id], "case_id": case_id}
    evidence = _CaseEvidence(context.recorder, case_id)
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
            _finish_case(case, evidence, f"{case_id} 保留处置或取证失败")


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
    evidence = _CaseEvidence(context.recorder, case_id)
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
            _finish_case(case, evidence, f"{case_id} 清理或取证失败")


@pytest.fixture
def user_case(environment_run, request):
    """用户用例独立会话与短只读连接，保持整轮同一环境锁。"""
    from db.queries import Queries
    from fixtures.users import UserCase, load_user_cases, preflight_users

    context = environment_run
    case_id = request.node.originalname.removeprefix("test_").upper()
    data = {**load_user_cases()[case_id], "case_id": case_id}
    evidence = _CaseEvidence(context.recorder, case_id)
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
            _finish_case(case, evidence, f"{case_id} 清理或取证失败")


class _CaseEvidence:
    """共享整轮预算，前置仅记录摘要，附件带 TC 编号与序号。"""

    def __init__(self, recorder: EvidenceRecorder, case_id: str = "TC_023") -> None:
        """使用同一脱敏器和预算，不另建证据缓冲。"""
        self.target = recorder
        self.redactor = recorder.redactor
        self.summary_only = True
        self.sequence = 0
        self.case_id = case_id

    def record(self, name: str, data: object) -> bool:
        """避免前置用户列表的无关资料进入附件。"""
        self.sequence += 1
        if self.summary_only and isinstance(data, dict):
            allowed = {"identity", "method", "url", "params", "status", "elapsed", "size", "error"}
            data = {key: value for key, value in data.items() if key in allowed}
        return self.target.record(f"{self.case_id}-{name}-{self.sequence}", data)

    def ensure_complete(self) -> None:
        """检查同一整轮取证预算是否完整。"""
        self.target.ensure_complete()


def pytest_addoption(parser) -> None:
    """直接 pytest 和统一入口使用同一配置参数。"""
    group = parser.getgroup("zammad")
    group.addoption("--zammad-config", default=str(ROOT / "config/local.yaml"))
    group.addoption("--zammad-run-config", default=str(ROOT / "config/run.local.yaml"))
    group.addoption("--zammad-db-config", default=str(ROOT / "config/db.local.yaml"))
    group.addoption("--zammad-search-config", default=str(ROOT / "config/search.local.yaml"))
    group.addoption("--zammad-creation-config", default=str(ROOT / "config/creation.local.yaml"))
    group.addoption(
        "--zammad-assignment-config", default=str(ROOT / "config/assignment.local.yaml")
    )

    group.addoption(
        "--zammad-update-access-config", default=str(ROOT / "config/update_access.local.yaml")
    )


def _load_case_configs(context, pytestconfig, items) -> list[tuple[str, str]]:
    """按实际 fixture 依赖加载配置，仅保留身份差异所需的用例分支。"""
    selected = {item.originalname for item in items}
    fixtures = {name for item in items for name in item.fixturenames}
    admin = (
        bool(
            fixtures
            & {
                "user_case",
                "user_permission_case",
                "role_case",
                "template_case",
                "text_module_case",
                "macro_case",
            }
        )
        or "test_tc_034" in selected
    )
    token_envs = []
    if "search_case" in fixtures:
        from support.search_config import ALIASES, load_definition, load_search_config

        context.search_config = load_search_config(
            Path(pytestconfig.getoption("zammad_search_config"))
        )
        context.search_definition = load_definition(ROOT / "data/baseline/search.yaml")
        token_envs.extend(
            (alias, getattr(context.search_config, f"{alias.lower()}_token_env"))
            for alias in ALIASES
        )
    customer = bool(
        selected
        & {
            "test_tc_032",
            "test_tc_088",
            "test_tc_089",
            "test_tc_090",
            "test_tc_148",
            "test_tc_174",
        }
    )
    if admin or customer or "creation_case" in fixtures:
        from support.creation_config import load_creation_config

        context.creation_config = load_creation_config(
            Path(pytestconfig.getoption("zammad_creation_config"))
        )
        if customer:
            token_envs.append(("C1", context.creation_config.customer_token_env))
        if admin:
            token_envs.append(("AD", context.creation_config.admin_token_env))
    if "assignment_case" in fixtures or selected & {"test_tc_096", "test_tc_190"}:
        from support.assignment_config import load_assignment_config

        context.assignment_config = load_assignment_config(
            Path(pytestconfig.getoption("zammad_assignment_config"))
        )
        if "test_tc_096" in selected:
            token_envs.append(("A2", context.assignment_config.peer_token_env))
        if selected & {"test_tc_061", "test_tc_190"}:
            token_envs.append(("A12", context.assignment_config.transfer_token_env))
    if "update_access_case" in fixtures or selected & {"test_tc_091", "test_tc_094"}:
        from support.update_access_config import load_update_access_config

        context.update_access_config = load_update_access_config(
            Path(pytestconfig.getoption("zammad_update_access_config"))
        )
        if "test_tc_065" in selected:
            token_envs.append(("AD", context.update_access_config.admin_token_env))
        if selected & {"test_tc_066", "test_tc_091", "test_tc_094"}:
            token_envs.append(("C2", context.update_access_config.customer_token_env))
        if "test_tc_067" in selected:
            token_envs.append(("AR", context.update_access_config.reader_token_env))
    if "user_permission_case" in fixtures:
        from fixtures.user_permissions import load_permission_cases

        context.permission_config = {
            "temp_role_name": load_permission_cases()["TC_141"]["token_role_name"]
        }
    return token_envs


def _load_extra_identities(context, token_envs: list[tuple[str, str]]) -> None:
    """每组凭据只读取一次，跨配置的同代号身份必须一致。"""
    for alias, env in dict.fromkeys(token_envs):
        identity = Identity.from_env(alias, env)
        if alias in context.extra_identities and context.extra_identities[alias] != identity:
            raise ConfigError("同一身份代号的凭据配置不一致") from None
        context.extra_identities[alias] = identity
    if context.search_config is not None:
        from support.search_config import ALIASES

        tokens = {context.extra_identities[alias].token for alias in ALIASES}
        if len(tokens | {context.maintenance.token}) != 5:
            raise ConfigError("搜索基线身份凭据必须独立") from None


def _load_context(pytestconfig, items):
    """在创建目录及获取环境锁前完成配置、凭据与输出安全检查。"""
    if not pytestconfig.getoption("allure_report_dir", default=None):
        pytest.fail("业务执行需要 Allure 输出，请使用 python -m tools.run", pytrace=False)
    if (
        pytestconfig.getoption("showlocals")
        or pytestconfig.getoption("fulltrace")
        or pytestconfig.getoption("tbstyle") not in {"short", "line", "no"}
    ):
        pytest.fail("业务执行不能输出局部变量或完整参数回溯，以免泄露凭据", pytrace=False)
    settings = load_config(Path(pytestconfig.getoption("zammad_config")))
    config = load_run_config(Path(pytestconfig.getoption("zammad_run_config")))
    if urlsplit(settings.base_url).path not in ("", "/"):
        raise ConfigError("M03 的 base_url 必须为实例根地址，不包含 API 路径") from None
    if config.state_dir.resolve().is_relative_to(ROOT):
        raise ConfigError("共享状态目录必须位于项目工作区外") from None
    context = SimpleNamespace(
        settings=settings,
        config=config,
        recorder=EvidenceRecorder(),
        agent=Identity.from_env("A1", config.agent_token_env),
        maintenance=Identity.from_env("M", config.maintenance_token_env),
        extra_identities={},
        db_config=None,
        creation_config=None,
        assignment_config=None,
        update_access_config=None,
        permission_config=None,
        search_config=None,
        search_definition=None,
    )
    token_envs = _load_case_configs(context, pytestconfig, items)
    _load_extra_identities(context, token_envs)
    if any(item.get_closest_marker("database") for item in items):
        from db.config import load_db_config

        context.db_config = load_db_config(Path(pytestconfig.getoption("zammad_db_config")))
    return context


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
    context = _load_context(pytestconfig, request.session.items)
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


def load_case() -> dict:
    """读取仓库维护的基础输入，加载错误不输出源文件内容。"""
    try:
        data = yaml.load(
            (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        # 基础编号参与资源登记，不能把其他场景误记为 TC_023。
        if data["case_id"] != "TC_023":
            raise ValueError
        return data
    except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError):
        raise ConfigError("TC_023 数据文件缺失或结构无效") from None


@pytest.fixture
def ticket_case(environment_run):
    """独立创建身份会话，在断言结束后保全证据并清理本次对象。"""
    context = environment_run
    data = load_case()
    evidence = _CaseEvidence(context.recorder)
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
            _finish_case(case, evidence, "TC_023 清理或取证失败")


def load_pilot_case(case_id: str) -> tuple[dict, dict]:
    """读取显式 P0 数据覆盖；只合并约定字段，不解释 YAML 表达式。"""
    try:
        rows = yaml.load(
            (ROOT / "data/cases/ticket_pilot.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        scenario = rows[case_id]
        data = load_case()
        data.update(
            case_id=case_id,
            title=scenario["title"],
            state_name=scenario["state_name"],
            expected=scenario["expected"],
        )
        return data, scenario
    except (KeyError, OSError, ValueError, TypeError, yaml.YAMLError, ConfigError):
        raise ConfigError("P0 数据文件缺失或结构无效") from None


@pytest.fixture
def pilot_case(environment_run, request):
    """为每条 P0 提供独立数据及既有数据库生命周期。"""
    from fixtures.pilot import PilotCase

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_pilot_case(case_id)
    yield from _database_case(environment_run, case_id, data, scenario, PilotCase)


@pytest.fixture
def title_case(environment_run, request):
    """标题场景使用同一身份、连接和清理路径，数据由明确场景加载。"""
    from fixtures.title import TitleCase, load_title_case

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_title_case(case_id)
    yield from _database_case(environment_run, case_id, data, scenario, TitleCase)


@pytest.fixture
def creation_case(environment_run, request):
    """只为当前创建权限用例提供相应身份，不预先写入任何辅助业务数据。"""
    from fixtures.creation_access import CreationCase, load_creation_case

    case_id = request.node.originalname.removeprefix("test_").upper()
    data, scenario = load_creation_case(case_id)
    yield from _database_case(
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
    yield from _database_case(
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
    yield from _database_case(
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
    yield from _database_case(environment_run, case_id, data, scenario, StateCase, kind="state")


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
    yield from _database_case(
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
    yield from _database_case(
        environment_run, case_id, data, scenario, ArticleCheckCase, kind="article", login=login
    )


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


def _database_case(
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

    evidence = _CaseEvidence(context.recorder, case_id)
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
            _finish_case(case, evidence, f"{case_id} 清理或取证失败")
