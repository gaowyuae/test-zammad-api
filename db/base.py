"""共享只读数据库会话、结果转换和查询公共工具。"""

import os
import re
from datetime import date, datetime, time
from types import TracebackType

import psycopg
import yaml
from psycopg.rows import dict_row

from db.config import DbConfig
from reporting.evidence import EvidenceRecorder
from support.config import _ConfigLoader


class DbError(RuntimeError):
    """不包含驱动消息或连接字符串的数据库错误。"""


def check_id(value: int) -> None:
    """检查查询边界收到的对象 ID，拒绝布尔和非正整数。"""
    if type(value) is not int or value <= 0:
        raise DbError("数据库查询需要正整数工单 ID") from None


class DbSession:
    """在一个用例内共享连接，每次查询使用独立只读事务。"""

    def __init__(self, config: DbConfig, recorder: EvidenceRecorder) -> None:
        """保存配置和证据器，进入上下文时建立连接。"""
        self.config = config
        self.recorder = recorder
        self._connection = None

    def __enter__(self) -> "DbSession":
        """创建带只读模式及有限超时的数据库连接。"""
        if self._connection is not None:
            raise DbError("数据库查询上下文已打开") from None
        password = os.environ.get(self.config.password_env)
        if not password:
            raise DbError("数据库密码环境变量缺失或为空") from None
        self.recorder.redactor.register(password)
        failed = False
        try:
            self._connection = psycopg.connect(
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.dbname,
                user=self.config.user,
                password=password,
                connect_timeout=self.config.connect_timeout,
                autocommit=True,
                row_factory=dict_row,
                options=(
                    "-c default_transaction_read_only=on -c search_path=pg_catalog "
                    f"-c statement_timeout={self.config.statement_timeout_ms} "
                    f"-c lock_timeout={self.config.lock_timeout_ms}"
                ),
            )
        except psycopg.Error:
            failed = True
        finally:
            password = None
        # 在捕获块外创建异常，避免原驱动消息进入异常上下文。
        if failed:
            raise DbError("数据库连接、只读权限或必需列核查失败") from None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出时释放当前连接。"""
        self.close()

    def close(self) -> None:
        """释放连接，关闭失败不携带原始驱动错误。"""
        connection, self._connection = self._connection, None
        failed = False
        if connection is not None:
            try:
                connection.close()
            except psycopg.Error:
                failed = True
        if failed:
            raise DbError("数据库连接关闭失败") from None

    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        """执行模块内定义的 SQL，并将时间值转换为可取证的文本。"""
        if self._connection is None:
            raise DbError("数据库查询上下文未打开") from None
        failed = False
        rows = []
        try:
            with self._connection.transaction():
                self._connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"
                )
                rows = self._connection.execute(sql, params).fetchall()
        except psycopg.Error:
            failed = True
        if failed:
            raise DbError("数据库查询失败，无法取得必要证据") from None
        return [
            {
                key: value.isoformat() if isinstance(value, (datetime, date, time)) else value
                for key, value in row.items()
            }
            for row in rows
        ]


def store_row(row: dict, field: str) -> dict:
    """解析数据库返回的 Rails store 文本，不执行任意 YAML 标签。"""
    try:
        value = row[field]
        if isinstance(value, str):
            value = yaml.load(value, Loader=_ConfigLoader)
        if not isinstance(value, dict):
            raise ValueError
    except (ValueError, TypeError, KeyError, yaml.YAMLError):
        raise DbError("配置序列化格式无效") from None
    return {**row, field: value}


def inventory(db: DbSession, sql: str, exclude_id: int | None, kind: str) -> dict:
    """执行配置模块的固定摘要查询，核验结果并保留对应证据。"""
    if exclude_id is not None:
        check_id(exclude_id)
    rows = db.rows(sql, (exclude_id, exclude_id))
    if (
        len(rows) != 1
        or set(rows[0]) != {"count", "fingerprint"}
        or type(rows[0]["count"]) is not int
        or rows[0]["count"] < 0
        or not isinstance(rows[0]["fingerprint"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", rows[0]["fingerprint"])
    ):
        raise DbError("配置集合摘要证据无效") from None
    db.recorder.record(f"db-{kind}-inventory", {"exclude_id": exclude_id, **rows[0]})
    return rows[0]
