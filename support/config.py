"""读取并校验非敏感配置，不连接业务环境。"""

import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from yaml.nodes import MappingNode


class ConfigError(ValueError):
    """配置错误；消息只包含固定诊断信息。"""


@dataclass(frozen=True)
class Settings:
    """经过校验的四项基础配置。"""

    env_id: str
    base_url: str
    connect_timeout: float
    read_timeout: float


class _ConfigLoader(yaml.SafeLoader):
    """独立检查重复键，避免修改全局 SafeLoader。"""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict:
        """检查每个 mapping 的重复键并安全构造值。"""
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError:
                raise ConfigError("YAML 键类型无效") from None
            if duplicate:
                mark = key_node.start_mark
                raise ConfigError(
                    f"YAML 存在重复键（行 {mark.line + 1}，列 {mark.column + 1}）"
                ) from None
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _timeout(value: object, field: str) -> float:
    """严格接受可表示为有限正浮点数的数值。"""
    if type(value) not in (int, float):
        raise ConfigError(f"{field} 必须为有限正数") from None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ConfigError(f"{field} 必须为有限正数") from None
    return number


def _base_url(value: object) -> str:
    """校验 HTTP 地址，并拒绝凭据、附加参数和危险字符。"""
    message = "base_url 必须为有效且不含凭据的 HTTP/HTTPS 地址"
    if not isinstance(value, str) or not value:
        raise ConfigError(message) from None
    # urlsplit 会忽略部分控制字符，必须在解析之前检查原始字符。
    if any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise ConfigError(message) from None
    if "?" in value or "#" in value:
        raise ConfigError(message) from None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ConfigError(message) from None
    if (
        parsed.scheme not in ("http", "https")
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or port == 0
    ):
        raise ConfigError(message) from None
    return value


def load_config(path: Path) -> Settings:
    """由配置类检查字段，校验请求目标和超时并统一报告加载错误。"""
    try:
        settings = Settings(**yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader))
        if not isinstance(settings.env_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*", settings.env_id
        ):
            raise ConfigError("env_id 必须为非空安全代号") from None
        return replace(
            settings,
            base_url=_base_url(settings.base_url),
            connect_timeout=_timeout(settings.connect_timeout, "connect_timeout"),
            read_timeout=_timeout(settings.read_timeout, "read_timeout"),
        )
    except ConfigError:
        raise
    except yaml.YAMLError as error:
        # 不格式化原始异常；其上下文可能包含凭据或整个 YAML 行。
        mark = getattr(error, "problem_mark", None)
        position = "" if mark is None else f"（行 {mark.line + 1}，列 {mark.column + 1}）"
        raise ConfigError(f"YAML 格式无效{position}") from None
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        raise ConfigError("配置文件无法读取或字段无效") from None
