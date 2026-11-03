"""严格读取 M03 所需的非敏感运行配置。"""

import re
from dataclasses import dataclass, replace
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
    """构造运行映射并规范化绝对状态路径，不读取凭据或创建目录。"""
    try:
        values = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader)
        config = RunConfig(**values)
        if any(
            not isinstance(value, str) or not value.strip()
            for name, value in vars(config).items()
            if name != "state_dir" and not name.endswith("_token_env")
        ):
            raise ConfigError("运行配置字段必须为有效非空字符串")
        for value in (config.agent_token_env, config.maintenance_token_env):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise ConfigError("凭据环境变量名无效")
        if (
            config.agent_login.casefold() == config.maintenance_login.casefold()
            or config.agent_token_env.casefold() == config.maintenance_token_env.casefold()
        ):
            raise ConfigError("客服与维护身份必须使用独立登录名和凭据变量")
        if (
            not isinstance(config.state_dir, str)
            or config.state_dir != config.state_dir.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in config.state_dir)
        ):
            raise ConfigError("共享状态目录必须为有效路径字符串")
        state_dir = Path(config.state_dir)
        if not state_dir.is_absolute():
            raise ConfigError("共享状态目录必须为绝对目录路径")
        return replace(config, state_dir=state_dir)
    except ConfigError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, TypeError, RecursionError):
        raise ConfigError("无法读取有效运行配置") from None
