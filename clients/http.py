"""独立会话的同步请求客户端；原始响应只用于业务断言。"""

import base64
import re
from dataclasses import dataclass, field
from time import perf_counter
from urllib.parse import unquote, urlsplit

import requests
from requests.adapters import HTTPAdapter

from clients.identity import Identity
from reporting.evidence import EvidenceRecorder
from support.config import Settings
from support.redaction import content_summary

_MISSING = object()
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_PROTECTED = {"authorization", "proxyauthorization", "cookie", "setcookie", "host"}


class HttpError(ValueError):
    """请求输入或解析错误，消息不包含原始内容。"""


class NetworkError(RuntimeError):
    """网络发送失败，消息不包含 Requests 原始异常。"""


@dataclass(frozen=True)
class HttpResult:
    """完整响应供断言使用，展示仅包含耗时与字节数。"""

    response: requests.Response = field(repr=False)
    elapsed: float
    size: int

    def get_json(self) -> object:
        """解析原始 JSON，失败时隐藏正文和解析器异常。"""
        try:
            return self.response.json()
        except ValueError:
            raise HttpError("响应不是有效 JSON") from None


def _url(settings: Settings, path: str) -> str:
    """拒绝绝对地址及目录跳转，始终在基础路径下拼接。"""
    message = "请求路径必须位于当前基础地址下"
    if (
        not isinstance(path, str)
        or not path
        or any(char.isspace() or ord(char) < 32 for char in path)
    ):
        raise HttpError(message) from None
    try:
        parsed = urlsplit(path)
        decoded = unquote(path)
    except ValueError:
        raise HttpError(message) from None
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "\\" in decoded
        or "%" in decoded
        or decoded.startswith("//")
        or any(part in (".", "..") for part in decoded.split("/"))
    ):
        raise HttpError(message) from None
    return settings.base_url.rstrip("/") + "/" + path.lstrip("/")


def _headers(headers: dict[str, str] | None) -> dict[str, str]:
    """复制附加头，禁止覆盖认证、Cookie 或目标主机。"""
    if headers is None:
        return {}
    if type(headers) is not dict:
        raise HttpError("请求头无效") from None
    result = {}
    for key, value in headers.items():
        if (
            type(key) is not str
            or type(value) is not str
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise HttpError("请求头无效") from None
        if re.sub(r"[^a-z]", "", key.lower()) in _PROTECTED:
            raise HttpError("请求参数不能覆盖身份或 Cookie") from None
        result[key] = value
    return result


class HttpClient:
    """每个身份拥有独立 Session，并在每次发送前清空 Cookie。"""

    def __init__(
        self,
        settings: Settings,
        identity: Identity,
        recorder: EvidenceRecorder,
        *,
        basic_auth: tuple[str, str] | None = None,
    ) -> None:
        """创建禁用系统认证、代理、自动重试的独立会话。"""
        __tracebackhide__ = True  # pytest长回溯默认会展示参数，不能展示密码认证元组。
        self.settings = settings
        self.identity = identity
        self.recorder = recorder
        self._authorization = identity.authorization
        if basic_auth is not None:
            if (
                identity.token is not None
                or not isinstance(basic_auth, tuple)
                or len(basic_auth) != 2
                or any(
                    not isinstance(value, str)
                    or not value
                    or not value.isascii()
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)
                    for value in basic_auth
                )
                or ":" in basic_auth[0]
            ):
                raise HttpError("密码认证需要独立身份及有效的临时凭据") from None
            encoded = base64.b64encode(":".join(basic_auth).encode("ascii")).decode("ascii")
            recorder.redactor.register(basic_auth[1])
            recorder.redactor.register(encoded)
            self._authorization = f"Basic {encoded}"
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.auth = None
        self.session.mount("http://", HTTPAdapter(max_retries=0))
        self.session.mount("https://", HTTPAdapter(max_retries=0))
        self._closed = False
        if identity.token is not None:
            recorder.redactor.register(identity.token)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: object = None,
        json: object = _MISSING,
        headers: dict[str, str] | None = None,
    ) -> HttpResult:
        """即时取证后发送一次请求，返回未经改写的完整响应。"""
        __tracebackhide__ = True  # 请求参数可能含密码，详细证据统一使用脱敏副本。
        if self._closed:
            raise HttpError("客户端已关闭") from None
        if not isinstance(method, str) or method.upper() not in _METHODS:
            raise HttpError("请求方法无效") from None
        method = method.upper()
        url = _url(self.settings, path)
        request_headers = _headers(headers)
        if self._authorization is not None:
            request_headers["Authorization"] = self._authorization
        # 会话可由响应设置 Cookie，因此每次发送前后都清空，匿名也适用。
        self.session.cookies.clear()
        self.session.auth = None
        self.session.trust_env = False
        for key in list(self.session.headers):
            if re.sub(r"[^a-z]", "", key.lower()) in _PROTECTED:
                del self.session.headers[key]
        request_data = {
            "identity": self.identity.alias,
            "method": method,
            "url": url,
            "params": params,
            "headers": request_headers,
        }
        options = {
            "params": params,
            "headers": request_headers,
            "timeout": (self.settings.connect_timeout, self.settings.read_timeout),
            "allow_redirects": False,
        }
        if json is not _MISSING:
            request_data["json"] = json
            options["json"] = json
            if json is None:
                options.pop("json")
                options["data"] = "null"
                request_headers.setdefault("Content-Type", "application/json")
        self.recorder.record("request", request_data)
        started = perf_counter()
        try:
            response = self.session.request(method, url, **options)
        except requests.RequestException:
            self.recorder.record("network-error", {"error": "network-failure", "method": method})
            raise NetworkError("网络请求失败") from None
        except (ValueError, TypeError, UnicodeError):
            self.recorder.record("request-error", {"error": "request-invalid", "method": method})
            raise HttpError("无法构造有效请求") from None
        finally:
            self.session.cookies.clear()
        elapsed = perf_counter() - started
        try:
            body = response.json()
        except ValueError:
            body = content_summary(response.content)
        self.recorder.record(
            "response",
            {
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": body,
                "elapsed": elapsed,
                "size": len(response.content),
            },
        )
        return HttpResult(response, elapsed, len(response.content))

    def close(self) -> None:
        """关闭拥有的会话；重复关闭安全。"""
        self.session.close()
        self._authorization = None
        self._closed = True

    def __enter__(self) -> "HttpClient":
        """进入客户端上下文。"""
        if self._closed:
            raise HttpError("客户端已关闭") from None
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """无论上下文是否成功都关闭会话。"""
        self.close()
