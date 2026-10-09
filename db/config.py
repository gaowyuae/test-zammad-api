"""读取独立只读账号的非敏感数据库配置。"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from support.config import ConfigError, _ConfigLoader, _valid_host


@dataclass(frozen=True)
class DbConfig:
    """仅保存连接位置、账号及密码变量名，不持有实际密码。"""

    host: str
    port: int
    dbname: str
    user: str
    password_env: str
    connect_timeout: int = 5
    statement_timeout_ms: int = 10000
    lock_timeout_ms: int = 3000

    def __post_init__(self) -> None:
        """同时校验直接构造和 YAML 加载，阻止超时选项被注入。"""
        if not isinstance(self.host, str) or not _valid_host(self.host):
            raise ConfigError("数据库 host 必须为合法主机名或 IP") from None
        for name in ("dbname", "user"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(
                r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value
            ):
                raise ConfigError(f"数据库 {name} 必须为非空安全名称") from None
        if not isinstance(self.password_env, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", self.password_env
        ):
            raise ConfigError("数据库 password_env 必须为环境变量名称") from None
        limits = {
            "port": 65535,
            "connect_timeout": 2147483647,
            "statement_timeout_ms": 2147483647,
            "lock_timeout_ms": 2147483647,
        }
        for name, limit in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= limit:
                raise ConfigError(f"数据库 {name} 必须为有效正整数") from None


def load_db_config(path: Path) -> DbConfig:
    """严格读取非敏感 YAML，拒绝密码、未知字段和重复键。"""
    try:
        values = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader)
    except ConfigError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, RecursionError):
        raise ConfigError("数据库配置文件无法读取或格式无效") from None
    required = {"host", "port", "dbname", "user", "password_env"}
    optional = {"connect_timeout", "statement_timeout_ms", "lock_timeout_ms"}
    if not isinstance(values, dict) or values.keys() - required - optional:
        raise ConfigError("数据库配置根节点或字段无效") from None
    if required - values.keys():
        raise ConfigError("数据库配置缺少必需字段") from None
    return DbConfig(**values)
