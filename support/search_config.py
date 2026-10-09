"""只读加载搜索基线定义和非敏感身份映射。"""

import hashlib
import json
import re
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

from support.config import ConfigError, _ConfigLoader

ALIASES = ("BA1", "BA2", "BC1", "BC2")
RELATIONS = (
    ("BC1", "BA1", "closed"),
    ("BC1", "BA2", "closed"),
    ("BC2", "BA1", "closed"),
    ("BC2", "BA2", "closed"),
    ("BC1", "BA1", "open"),
    ("BC1", "BA1", "new"),
    ("BC1", "BA1", "new"),
    ("BC2", "BA2", "open"),
    ("BC1", "BA2", "open"),
)


@dataclass(frozen=True)
class SearchConfig:
    """只保存逻辑名称及凭据变量名，不保存秘密。"""

    group_name: str
    ba1_login: str
    ba2_login: str
    bc1_login: str
    bc2_login: str
    ba1_token_env: str
    ba2_token_env: str
    bc1_token_env: str
    bc2_token_env: str


def _read_yaml(path: Path) -> dict:
    """安全读取并拒绝未知结构，不输出文件内容。"""
    try:
        result = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_ConfigLoader)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, RecursionError):
        raise ConfigError("搜索配置或定义无法读取") from None
    if not isinstance(result, dict):
        raise ConfigError("搜索配置或定义必须为对象") from None
    return result


def load_search_config(path: Path) -> SearchConfig:
    """校验四个独立身份及环境变量，加载时不读取凭据。"""
    value = _read_yaml(path)
    if set(value) != {item.name for item in fields(SearchConfig)}:
        raise ConfigError("搜索配置字段不符合约定") from None
    if any(
        not isinstance(item, str)
        or not item
        or item != item.strip()
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in item)
        for item in value.values()
    ):
        raise ConfigError("搜索配置包含无效文本") from None
    for suffix in ("login", "token_env"):
        values = [value[f"{alias.lower()}_{suffix}"] for alias in ALIASES]
        if len({item.casefold() for item in values}) != 4:
            raise ConfigError("搜索身份和凭据变量必须独立") from None
        if suffix == "token_env" and any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) is None for item in values
        ):
            raise ConfigError("搜索凭据变量名无效") from None
    return SearchConfig(**value)


def load_definition(path: Path) -> dict:
    """校验已经确认的九张十三篇关系，记录规范化内容摘要。"""
    value = _read_yaml(path)
    if set(value) != {"version", "priority", "title_keyword", "tickets"}:
        raise ConfigError("基线定义字段不符合约定") from None
    if (
        value["version"] != "search-v1"
        or value["priority"] != "2 normal"
        or not isinstance(value["title_keyword"], str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{5,80}", value["title_keyword"])
        or not isinstance(value["tickets"], list)
        or len(value["tickets"]) != 9
    ):
        raise ConfigError("基线版本、检索词或数量不符合约定") from None
    for index, (row, relation) in enumerate(zip(value["tickets"], RELATIONS, strict=True), 1):
        expected = {
            "code": f"B{index:02}",
            "customer": relation[0],
            "owner": relation[1],
            "state": relation[2],
            "articles": [False, False, True, True, True] if index == 2 else [False],
        }
        if row != expected or any(type(flag) is not bool for flag in row["articles"]):
            raise ConfigError("基线业务关系或文章可见性不符合已确认方案") from None
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return {**value, "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
