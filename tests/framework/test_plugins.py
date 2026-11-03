"""覆盖插件迁移容易改变的身份需求、清理与离线装配行为。"""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def test_cleanup_keeps_both_failures():
    """清理失败时仍检查证据，并将两类失败完整返回。"""
    from fixtures.plugins._lifecycle import finish_case

    events = []

    def cleanup():
        """模拟清理资源失败。"""
        events.append("cleanup")
        raise RuntimeError("cleanup")

    def ensure_complete():
        """模拟证据写入失败。"""
        events.append("evidence")
        raise ValueError("evidence")

    with pytest.raises(ExceptionGroup) as result:
        finish_case(
            SimpleNamespace(cleanup=cleanup),
            SimpleNamespace(ensure_complete=ensure_complete),
            "收尾失败",
        )
    assert events == ["cleanup", "evidence"]
    assert [type(error) for error in result.value.exceptions] == [RuntimeError, ValueError]


@pytest.mark.parametrize(
    ("name", "fixtures", "expected_options", "expected_aliases"),
    [
        ("test_tc_025", ["pilot_case"], [], []),
        ("test_tc_096", ["article_check_case"], ["zammad_assignment_config"], ["A2"]),
        (
            "test_tc_190",
            ["macro_case"],
            ["zammad_creation_config", "zammad_assignment_config"],
            ["AD", "A12"],
        ),
        ("test_tc_094", ["article_check_case"], ["zammad_update_access_config"], ["C2"]),
        ("test_tc_174", ["text_module_case"], ["zammad_creation_config"], ["C1", "AD"]),
        ("test_tc_109", ["search_case"], ["zammad_search_config"], ["BA1", "BA2", "BC1", "BC2"]),
    ],
)
def test_selected_cases_load_only_needed_identities(
    name, fixtures, expected_options, expected_aliases
):
    """单独筛选跨领域场景时只加载需要的配置和固定身份。"""
    # 使用仓库维护的配置示例实际加载，避免在测试内镜像配置字段。
    from pathlib import Path

    from fixtures.plugins._context import load_case_configs

    root = Path(__file__).resolve().parents[2]
    options = []

    def getoption(option):
        """将配置参数定位到已有示例并记录按需读取行为。"""
        options.append(option)
        stem = option.removeprefix("zammad_").removesuffix("_config")
        return str(root / "config" / f"{stem}.example.yaml")

    context = SimpleNamespace()
    identities = load_case_configs(
        context,
        SimpleNamespace(getoption=getoption),
        [SimpleNamespace(originalname=name, fixturenames=fixtures)],
    )
    assert options == expected_options
    assert [alias for alias, _ in identities] == expected_aliases


def test_unit_guard_does_not_start_environment():
    """普通离线测试不会因为全局 autouse 插件而加载业务环境。"""
    from pathlib import Path

    from fixtures.plugins.environment import business_guard

    def getfixturevalue(name):
        """发现意外环境请求就直接失败。"""
        pytest.fail(f"离线测试不应请求 {name}")

    request = SimpleNamespace(
        node=SimpleNamespace(path=Path(__file__), get_closest_marker=lambda name: None),
        getfixturevalue=getfixturevalue,
    )
    business_guard.__wrapped__(request)


def test_case_evidence_shares_run_budget_and_redactor():
    """用例证据包装保留整轮记录器和摘要切换行为。"""
    from reporting.evidence import CaseEvidence

    events = []
    target = SimpleNamespace(
        redactor=object(),
        record=lambda name, data: events.append((name, data)),
        ensure_complete=lambda: events.append("complete"),
    )
    evidence = CaseEvidence(target, "TC_109")
    evidence.record("http", {"status": 200, "body": "private"})
    evidence.summary_only = False
    evidence.record("http", {"status": 200, "body": "public"})
    evidence.ensure_complete()
    assert evidence.redactor is target.redactor
    assert events == [
        ("TC_109-http-1", {"status": 200}),
        ("TC_109-http-2", {"status": 200, "body": "public"}),
        "complete",
    ]


def test_environment_lock_covers_baseline_and_final_evidence(tmp_path, monkeypatch):
    """整轮基线及取证收尾仍持有真实文件锁，退出后才允许下一轮进入。"""
    from fixtures.plugins import environment
    from support.runtime import EnvironmentRun

    events = []
    state = tmp_path / "state"

    def while_locked(label):
        """确认关键阶段不能启动同一环境的第二轮运行。"""
        with pytest.raises(RuntimeError):
            with EnvironmentRun(state, "test-env"):
                pytest.fail("整轮检查结束前不应释放环境锁")
        events.append(label)

    context = SimpleNamespace(
        config=SimpleNamespace(state_dir=state),
        settings=SimpleNamespace(env_id="test-env"),
        search_config=object(),
        recorder=SimpleNamespace(
            record=lambda name, data: while_locked(name),
            ensure_complete=lambda: while_locked("evidence"),
        ),
    )
    monkeypatch.setattr(environment, "load_context", lambda config, items: context)
    monkeypatch.setattr(environment, "_check_database", lambda context, config: while_locked("db"))
    monkeypatch.setattr(
        environment,
        "_check_search_baseline",
        lambda context, *, index: while_locked("before" if index else "after"),
    )
    fixture = environment.environment_run.__wrapped__(
        None, SimpleNamespace(session=SimpleNamespace(items=[]))
    )
    assert next(fixture) is context
    while_locked("case")
    with pytest.raises(StopIteration):
        next(fixture)
    assert events == ["db", "before", "case", "run-summary", "after", "evidence"]
    with EnvironmentRun(state, "test-env"):
        pass


@pytest.mark.parametrize(
    ("kind", "case_id", "expected_actor", "expected_sessions"),
    [
        ("template", "TC_159", "A1", ["AD", "M", "A1"]),
        ("text_module", "TC_171", None, ["AD", "M"]),
        ("text_module", "TC_174", "C1", ["AD", "M", "C1"]),
        ("macro", "TC_187", "AD", ["AD", "M", "A1"]),
        ("macro", "TC_189", "A1", ["AD", "M", "A1"]),
        ("macro", "TC_190", "A12", ["AD", "M", "A1", "A12"]),
    ],
)
def test_configuration_uses_required_actor(
    kind, case_id, expected_actor, expected_sessions, monkeypatch
):
    """配置对象按场景使用独立被测身份，避免负向用例误用管理员。"""
    from contextlib import ExitStack, contextmanager

    from fixtures.plugins import _configuration

    opened, closed = [], []

    @contextmanager
    def client(settings, identity, evidence):
        """仅替换外部 HTTP 会话，记录身份和释放行为。"""
        opened.append(identity)
        try:
            yield identity
        finally:
            closed.append(identity)

    monkeypatch.setattr(_configuration, "HttpClient", client)
    context = SimpleNamespace(
        settings=None,
        agent="A1",
        maintenance="M",
        extra_identities={"AD": "AD", "C1": "C1", "A12": "A12"},
    )
    with ExitStack() as stack:
        clients = _configuration._clients(stack, context, SimpleNamespace(case_id=case_id), kind)
        assert clients.actor == expected_actor
        assert opened == expected_sessions
    assert closed == expected_sessions[::-1]
