"""按选中场景加载整轮配置和身份。"""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from clients.identity import Identity
from reporting.evidence import EvidenceRecorder
from support.config import ConfigError, load_config
from support.run_config import load_run_config

ROOT = Path(__file__).resolve().parents[2]


def _load_search(context, pytestconfig) -> list[tuple[str, str]]:
    """加载搜索基线定义和四个固定执行身份的配置。"""
    from support.search_config import ALIASES, load_definition, load_search_config

    context.search_config = load_search_config(Path(pytestconfig.getoption("zammad_search_config")))
    context.search_definition = load_definition(ROOT / "data/baseline/search.yaml")
    return [
        (alias, getattr(context.search_config, f"{alias.lower()}_token_env")) for alias in ALIASES
    ]


def _load_creation(context, pytestconfig, selected: set[str], fixtures: set[str]):
    """按创建和管理场景的实际需要加载 AD、C1 及组映射配置。"""
    from support.creation_config import load_creation_config

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
    token_envs = []
    if admin or customer or "creation_case" in fixtures:
        context.creation_config = load_creation_config(
            Path(pytestconfig.getoption("zammad_creation_config"))
        )
        if customer:
            token_envs.append(("C1", context.creation_config.customer_token_env))
        if admin:
            token_envs.append(("AD", context.creation_config.admin_token_env))
    return token_envs


def _load_assignment(context, pytestconfig, selected: set[str]) -> list[tuple[str, str]]:
    """加载分派配置及被文章、宏场景复用的客服身份。"""
    from support.assignment_config import load_assignment_config

    context.assignment_config = load_assignment_config(
        Path(pytestconfig.getoption("zammad_assignment_config"))
    )
    token_envs = []
    if "test_tc_096" in selected:
        token_envs.append(("A2", context.assignment_config.peer_token_env))
    if selected & {"test_tc_061", "test_tc_190"}:
        token_envs.append(("A12", context.assignment_config.transfer_token_env))
    return token_envs


def _load_update(context, pytestconfig, selected: set[str]) -> list[tuple[str, str]]:
    """加载更新权限配置及相应的管理员、客户和只读客服身份。"""
    from support.update_access_config import load_update_access_config

    context.update_access_config = load_update_access_config(
        Path(pytestconfig.getoption("zammad_update_access_config"))
    )
    token_envs = []
    if "test_tc_065" in selected:
        token_envs.append(("AD", context.update_access_config.admin_token_env))
    if selected & {"test_tc_066", "test_tc_091", "test_tc_094"}:
        token_envs.append(("C2", context.update_access_config.customer_token_env))
    if "test_tc_067" in selected:
        token_envs.append(("AR", context.update_access_config.reader_token_env))
    return token_envs


def load_case_configs(context, pytestconfig, items) -> list[tuple[str, str]]:
    """根据本轮实际选中的 fixture 和用例，编排所需配置加载。"""
    selected = {item.originalname for item in items}
    fixtures = {name for item in items for name in item.fixturenames}
    token_envs = []
    if "search_case" in fixtures:
        token_envs.extend(_load_search(context, pytestconfig))
    token_envs.extend(_load_creation(context, pytestconfig, selected, fixtures))
    if "assignment_case" in fixtures or selected & {"test_tc_096", "test_tc_190"}:
        token_envs.extend(_load_assignment(context, pytestconfig, selected))
    if "update_access_case" in fixtures or selected & {"test_tc_091", "test_tc_094"}:
        token_envs.extend(_load_update(context, pytestconfig, selected))
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


def load_context(pytestconfig, items):
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
    token_envs = load_case_configs(context, pytestconfig, items)
    _load_extra_identities(context, token_envs)
    if any(item.get_closest_marker("database") for item in items):
        from db.config import load_db_config

        context.db_config = load_db_config(Path(pytestconfig.getoption("zammad_db_config")))
    return context
