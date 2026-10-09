"""创建权限场景所需的非敏感身份映射。"""

import re
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

from support.config import ConfigError, _ConfigLoader


@dataclass(frozen=True)
class CreationConfig:
    """G2、合法负责人、其他客户及纯管理员的逻辑名称。"""

    group2_name: str
    owner_login: str
    other_customer_login: str
    admin_login: str
    customer_token_env: str
    admin_token_env: str


def load_creation_config(path: Path) -> CreationConfig:
    """严格校验六字段，不读取凭据或回显无效配置。"""
    try:
        values = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader)
        names = {field.name for field in fields(CreationConfig)}
        if not isinstance(values, dict) or set(values) != names:
            raise ValueError
        if any(
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
            for value in values.values()
        ):
            raise ValueError
        for name in ("customer_token_env", "admin_token_env"):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", values[name]):
                raise ValueError
        if values["customer_token_env"].casefold() == values["admin_token_env"].casefold():
            raise ValueError
        return CreationConfig(**values)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise ConfigError("创建权限配置必须包含六个有效且非敏感的约定字段") from None
