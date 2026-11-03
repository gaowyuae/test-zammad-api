"""创建权限场景所需的非敏感身份映射。"""

import re
from dataclasses import dataclass
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
    """构造创建身份映射并校验名称及凭据变量，不读取凭据。"""
    try:
        values = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader)
        config = CreationConfig(**values)
        if any(
            not isinstance(value, str) or not value.strip()
            for name, value in vars(config).items()
            if not name.endswith("_token_env")
        ):
            raise ValueError
        for value in (config.customer_token_env, config.admin_token_env):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise ValueError
        if config.customer_token_env.casefold() == config.admin_token_env.casefold():
            raise ValueError
        return config
    except ConfigError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise ConfigError("创建权限配置必须包含六个有效且非敏感的约定字段") from None
