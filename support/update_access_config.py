"""更新权限场景的非敏感身份配置。"""

import re
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

from support.config import ConfigError, _ConfigLoader


@dataclass(frozen=True)
class UpdateAccessConfig:
    """独立身份、目标组及系统占位的九项配置。"""

    admin_login: str
    admin_token_env: str
    other_customer_login: str
    customer_token_env: str
    reader_login: str
    reader_token_env: str
    peer_login: str
    group2_name: str
    unassigned_login: str


def load_update_access_config(path: Path) -> UpdateAccessConfig:
    """严格校验名称与凭据环境变量名，不读取或回显凭据。"""
    try:
        values = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader)
        if not isinstance(values, dict) or set(values) != {
            field.name for field in fields(UpdateAccessConfig)
        }:
            raise ValueError
        if any(
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
            for value in values.values()
        ):
            raise ValueError
        envs = [
            values[name] for name in ("admin_token_env", "customer_token_env", "reader_token_env")
        ]
        if (
            any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) for value in envs)
            or len({value.casefold() for value in envs}) != 3
        ):
            raise ValueError
        return UpdateAccessConfig(**values)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise ConfigError("更新权限配置必须包含九个有效且非敏感的约定字段") from None
