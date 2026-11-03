"""离线验证查询接口和连接生命周期，不访问 PostgreSQL。"""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import psycopg
import pytest

from db.config import DbConfig
from db.queries import DbError, Queries

pytestmark = pytest.mark.unit


class Recorder:
    """在内存中保存查询证据和已登记的秘密。"""

    def __init__(self):
        """初始化独立证据列表。"""
        self.events = []
        self.secrets = []
        self.redactor = SimpleNamespace(register=self.secrets.append)

    def record(self, name, data):
        """记录查询对外提供的证据。"""
        self.events.append((name, data))


class Connection:
    """代替外部驱动连接，保留执行顺序和事务边界。"""

    def __init__(self):
        """设置正常只读账号及可安排的查询结果。"""
        self.calls = []
        self.transactions = []
        self.closed = False
        self.results = []
        self.failure = None
        self.access = {
            "can_login": True,
            "elevated": False,
            "schema_create": False,
            "object_write": False,
        }

    @contextmanager
    def transaction(self):
        """标记每次查询的独立事务和退出。"""
        self.transactions.append("begin")
        try:
            yield
        finally:
            self.transactions.append("end")

    def execute(self, sql, params=()):
        """记录真实组件发出的 SQL，返回安排好的外部数据。"""
        self.calls.append((sql, params))
        if self.failure:
            raise self.failure
        if sql.startswith("SET TRANSACTION") or "WHERE FALSE" in sql:
            rows = []
        elif "rolcanlogin" in sql:
            rows = [self.access]
        else:
            rows = self.results.pop(0)
        return SimpleNamespace(fetchall=lambda: rows)

    def close(self):
        """标记连接已释放。"""
        self.closed = True


@pytest.fixture
def database(monkeypatch):
    """替换唯一外部连接点，其余查询流程使用真实实现。"""
    connection = Connection()
    recorder = Recorder()
    config = DbConfig("localhost", 5432, "test", "reader", "TEST_DB_PASSWORD")
    options = []

    def connect(**kwargs):
        """保存实际连接参数并提供假连接。"""
        options.append(kwargs)
        return connection

    monkeypatch.setenv("TEST_DB_PASSWORD", "fixture-password")
    monkeypatch.setattr(psycopg, "connect", connect)
    return SimpleNamespace(connection=connection, recorder=recorder, config=config, options=options)


def test_session_uses_short_readonly_transactions(database):
    """连续读取使用独立只读事务，并统一转换数据库时间。"""
    from db.base import DbSession

    database.connection.results = [[{"at": datetime(2026, 1, 1, tzinfo=UTC)}], []]
    with DbSession(database.config, database.recorder) as session:
        assert session.rows("SELECT at FROM sample WHERE id=%s", (7,)) == [
            {"at": "2026-01-01T00:00:00+00:00"}
        ]
        assert session.rows("SELECT id FROM sample WHERE id=%s", (8,)) == []
    assert database.connection.transactions == ["begin", "end", "begin", "end"]
    assert database.connection.closed
    assert len(database.options) == 1
    assert database.options[0]["autocommit"] is True
    assert "default_transaction_read_only=on" in database.options[0]["options"]
    assert database.recorder.secrets == ["fixture-password"]
    assert database.connection.calls[0] == (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY",
        (),
    )


def test_driver_failure_is_redacted_and_connection_closed(database):
    """真实驱动失败不会把消息或异常上下文带到业务层。"""
    from db.base import DbSession

    with pytest.raises(DbError) as error:
        with DbSession(database.config, database.recorder) as session:
            database.connection.failure = psycopg.OperationalError("fixture-password")
            session.rows("SELECT 1")
    assert "fixture-password" not in str(error.value)
    assert error.value.__context__ is None
    assert database.connection.closed


def test_queries_reject_writable_account_before_business_reads(database):
    """账号检查失败时释放连接，并且不开始业务查询。"""
    database.connection.access["object_write"] = True
    with pytest.raises(DbError):
        with Queries(database.config, database.recorder):
            pytest.fail("不应进入业务上下文")
    assert database.connection.closed
    assert not any("WHERE id = %s" in sql for sql, _ in database.connection.calls)


def test_ticket_and_articles_share_connection_and_record_results(database):
    """跨领域查询保持绑定参数、返回值、取证名称和单连接生命周期。"""
    database.connection.results = [[{"id": 7, "title": "sample"}], [{"id": 8, "ticket_id": 7}]]
    with Queries(database.config, database.recorder) as queries:
        assert queries.ticket(7) == {"id": 7, "title": "sample"}
        assert queries.articles(7) == [{"id": 8, "ticket_id": 7}]
    assert len(database.options) == 1
    assert database.connection.closed
    assert [params for sql, params in database.connection.calls if "= %s" in sql] == [(7,), (7,)]
    assert database.recorder.events[-2:] == [
        ("db-ticket", {"query": "ticket", "ticket_id": 7, "result": {"id": 7, "title": "sample"}}),
        (
            "db-articles",
            {"query": "articles", "ticket_id": 7, "result": [{"id": 8, "ticket_id": 7}]},
        ),
    ]


def test_template_decodes_store_without_changing_name_parameter(database):
    """模板负向名称原样绑定，数据库 store 字段仍解析成字典。"""
    database.connection.results = [[{"id": 3, "options": "---\nticket.title: test\n"}]]
    with Queries(database.config, database.recorder) as queries:
        assert queries.templates_named("  ") == [{"id": 3, "options": {"ticket.title": "test"}}]
    assert database.connection.calls[-1][1] == ("  ",)


def test_attachments_keep_metadata_conversion(database):
    """附件数据库字符串字段转换后仍供原有业务断言使用。"""
    database.connection.results = [
        [{"id": 1, "article_id": "8", "filename": "a.txt", "size": "12"}]
    ]
    with Queries(database.config, database.recorder) as queries:
        assert queries.attachments(8) == [
            {"id": 1, "article_id": 8, "filename": "a.txt", "size": 12}
        ]
    assert database.connection.calls[-1][1] == ("8",)
