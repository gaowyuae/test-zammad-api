"""分派场景所需的非敏感身份映射。"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from support.config import ConfigError, _ConfigLoader


@dataclass(frozen=True)
class AssignmentConfig:
    """同组客服、跨组客服和系统未分派映射。"""

    peer_login: str
    transfer_login: str
    group2_name: str
    unassigned_login: str
    transfer_token_env: str
    peer_token_env: str = "ZAMMAD_A2_TOKEN"


def load_assignment_config(path: Path) -> AssignmentConfig:
    """构造身份映射并校验名称、占位账户和凭据变量，不读取 Token。"""
    try:
        values = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader)
        config = AssignmentConfig(**values)
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (config.peer_login, config.transfer_login, config.group2_name)
        ):
            raise ValueError
        if config.unassigned_login != "-" or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)
            for value in (config.transfer_token_env, config.peer_token_env)
        ):
            raise ValueError
        if config.peer_token_env.casefold() == config.transfer_token_env.casefold():
            raise ValueError
        return config
    except ConfigError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise ConfigError("分派配置必须包含有效且非敏感的约定字段") from None
