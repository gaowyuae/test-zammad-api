"""分派场景所需的非敏感身份映射。"""

import re
from dataclasses import dataclass, fields
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
    """严格检查五个必需字段及可选A2凭据变量名，不读取 Token。"""
    try:
        values = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader)
        if isinstance(values, dict):
            values.setdefault("peer_token_env", "ZAMMAD_A2_TOKEN")
        if not isinstance(values, dict) or set(values) != {
            f.name for f in fields(AssignmentConfig)
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
        if values["unassigned_login"] != "-" or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", values["transfer_token_env"]
        ):
            raise ValueError
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", values["peer_token_env"])
            or values["peer_token_env"].casefold() == values["transfer_token_env"].casefold()
        ):
            raise ValueError
        return AssignmentConfig(**values)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise ConfigError("分派配置必须包含有效且非敏感的约定字段") from None
