"""读取并校验非敏感配置，不连接业务环境。"""

import ipaddress
import math
import re
from dataclasses import dataclass
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
    try:
        number = float(value)
    except OverflowError:
        raise ConfigError(f"{field} 必须为有限正数") from None
    if not math.isfinite(number) or number <= 0:
        raise ConfigError(f"{field} 必须为有限正数") from None
    return number


def _valid_host(host: str) -> bool:
    """校验 IP 地址或由合法 DNS 标签构成的主机名。"""
    if ":" in host or ("." in host and all(part.isdecimal() for part in host.split("."))):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return False
        return True
    try:
        name = host.encode("idna").decode("ascii").removesuffix(".")
    except UnicodeError:
        return False
    if not name or len(name) > 253:
        return False
    return all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in name.split(".")
    )


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
        or (port is not None and not 1 <= port <= 65535)
        or not _valid_host(host)
    ):
        raise ConfigError(message) from None
    return value


def load_config(path: Path) -> Settings:
    """读取非敏感 YAML，严格校验字段后返回不可变配置。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise ConfigError("无法读取配置文件") from None
    try:
        values = yaml.load(text, Loader=_ConfigLoader)
    except ConfigError:
        raise
    except yaml.YAMLError as error:
        # 不格式化原始异常；其上下文可能包含凭据或整个 YAML 行。
        mark = getattr(error, "problem_mark", None)
        position = "" if mark is None else f"（行 {mark.line + 1}，列 {mark.column + 1}）"
        raise ConfigError(f"YAML 格式无效{position}") from None
    except (ValueError, RecursionError):
        raise ConfigError("YAML 内容无效") from None
    if not isinstance(values, dict):
        raise ConfigError("配置根节点必须为 mapping") from None
    fields = {"env_id", "base_url", "connect_timeout", "read_timeout"}
    if values.keys() - fields:
        raise ConfigError("配置存在未知字段") from None
    for field in sorted(fields):
        if field not in values:
            raise ConfigError(f"缺少必需字段：{field}") from None
    env_id = values["env_id"]
    if not isinstance(env_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", env_id):
        raise ConfigError("env_id 必须为非空安全代号") from None
    return Settings(
        env_id=env_id,
        base_url=_base_url(values["base_url"]),
        connect_timeout=_timeout(values["connect_timeout"], "connect_timeout"),
        read_timeout=_timeout(values["read_timeout"], "read_timeout"),
    )
