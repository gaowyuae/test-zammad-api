"""递归生成安全副本，隐藏敏感字段与已知凭据。"""

import hashlib
import math
import re

_MASK = "[REDACTED]"
_KEYS = {
    "authorization",
    "proxyauthorization",
    "cookie",
    "setcookie",
    "password",
    "passwordconfirmation",
    "sessioncookie",
    "passwd",
    "pwd",
    "token",
    "secret",
    "dsn",
    "apikey",
    "credential",
    "credentials",
}
_CREDENTIAL_TEXT = re.compile(
    r"(?i)[\"']?\b(?:proxy[-_]?authorization|authorization|set[-_]?cookie|session[-_]?cookie|cookie|password(?:[-_]?confirmation)?|passwd|pwd|(?:access[-_]?|refresh[-_]?|api[-_]?)?token|(?:client[-_]?)?secret|dsn|api[-_]?key|credentials?)[\"']?\s*[:=]"
)
_DSN = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")


def sensitive_key(key: str) -> bool:
    """统一键名分隔符与大小写后识别凭据字段。"""
    name = re.sub(r"[^a-z0-9]", "", key.lower())
    return name in _KEYS or name.endswith(("password", "token", "secret", "dsn", "apikey"))


def content_summary(content: bytes) -> dict[str, object]:
    """仅提供原始字节的长度和摘要，不导出正文。"""
    return {"type": "binary", "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


class Redactor:
    """持有显式登记的秘密，不修改调用者输入。"""

    def __init__(self) -> None:
        """初始化当前取证范围的秘密集合。"""
        self._secrets: set[str] = set()

    def register(self, secret: str) -> None:
        """登记后续可能出现在普通文本中的秘密。"""
        if not isinstance(secret, str) or not secret:
            raise ValueError("登记秘密必须为非空文本") from None
        self._secrets.add(secret)

    def _text(self, value: str) -> str:
        """隐藏已知秘密和常见凭据文本。"""
        for secret in sorted(self._secrets, key=len, reverse=True):
            value = value.replace(secret, _MASK)
        # 自由文本不能可靠区分转义和尾部，因此有凭据格式就隐藏整段。
        if _CREDENTIAL_TEXT.search(value):
            return _MASK
        return _DSN.sub("[REDACTED-DSN]@", value)

    def redact(self, value: object) -> object:
        """生成可 JSON 序列化的脱敏副本。"""
        return self._walk(value, set(), 0)

    def _walk(self, value: object, active: set[int], depth: int) -> object:
        """递归处理 JSON 形状，循环和未知对象只给安全摘要。"""
        if type(value) is str:
            return self._text(value)
        if value is None or type(value) in (bool, int):
            return value
        if type(value) is float:
            return value if math.isfinite(value) else {"type": "nonfinite"}
        if type(value) in (bytes, bytearray):
            return content_summary(bytes(value))
        if type(value) not in (dict, list, tuple):
            return {"type": "unsupported"}
        if id(value) in active:
            return {"type": "cycle"}
        if depth >= 64:
            return {"type": "depth-limit"}
        active.add(id(value))
        try:
            if type(value) is dict:
                return {
                    self._text(key) if type(key) is str else "[unsupported-key]": (
                        _MASK
                        if type(key) is str and sensitive_key(key)
                        else self._walk(item, active, depth + 1)
                    )
                    for key, item in value.items()
                }
            # Requests 接受重复键的键值对序列；仅脱敏副本，不合并原始参数。
            if len(value) == 2 and type(value[0]) is str and sensitive_key(value[0]):
                return [self._text(value[0]), _MASK]
            return [self._walk(item, active, depth + 1) for item in value]
        finally:
            active.remove(id(value))
