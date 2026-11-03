"""项目 pytest 配置参数注册。"""

from fixtures.plugins._context import ROOT


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
