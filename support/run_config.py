"""严格读取 M03 所需的非敏感运行配置。"""

import re
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

from support.config import ConfigError, _ConfigLoader


@dataclass(frozen=True)
class RunConfig:
    """共享状态目录及独立身份的非敏感映射。"""

    state_dir: Path
    agent_login: str
    maintenance_login: str
    customer_login: str
    group_name: str
    agent_token_env: str
    maintenance_token_env: str


def load_run_config(path: Path) -> RunConfig:
    """读取七字段 YAML，只校验映射而不读取凭据或创建目录。"""
    try:
        values = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader)
    except ConfigError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, RecursionError):
        raise ConfigError("无法读取有效运行配置") from None
    names = {field.name for field in fields(RunConfig)}
    if not isinstance(values, dict) or values.keys() != names:
        raise ConfigError("运行配置必须且仅包含七个约定字段") from None
    for name in names:
        value = values[name]
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
        ):
            raise ConfigError("运行配置字段必须为有效非空字符串") from None
    for name in ("agent_token_env", "maintenance_token_env"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", values[name]):
            raise ConfigError("凭据环境变量名无效") from None
    if (
        values["agent_login"].casefold() == values["maintenance_login"].casefold()
        or values["agent_token_env"].casefold() == values["maintenance_token_env"].casefold()
    ):
        raise ConfigError("客服与维护身份必须使用独立登录名和凭据变量") from None
    state_dir = Path(values["state_dir"])
    try:
        invalid = not state_dir.is_absolute() or (state_dir.exists() and not state_dir.is_dir())
    except OSError:
        raise ConfigError("共享状态目录不可访问") from None
    if invalid:
        raise ConfigError("共享状态目录必须为绝对目录路径") from None
    return RunConfig(**{**values, "state_dir": state_dir})
