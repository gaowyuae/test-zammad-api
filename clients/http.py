"""独立会话的同步请求客户端；原始响应只用于业务断言。"""

import base64
from dataclasses import dataclass, field
from time import perf_counter

import requests

from clients.identity import Identity
from reporting.evidence import EvidenceRecorder
from support.config import Settings
from support.redaction import content_summary

_MISSING = object()
_PROTECTED = {"authorization", "proxy-authorization", "cookie", "set-cookie", "host"}


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
    """将项目内部接口路径拼接到基础地址。"""
    return f"{settings.base_url.rstrip('/')}/{path.lstrip('/')}"


def _headers(headers: dict[str, str] | None) -> dict[str, str]:
    """复制附加头，禁止覆盖认证、Cookie 或目标主机。"""
    result = dict(headers or {})
    if any(key.lower() in _PROTECTED for key in result):
        raise HttpError("请求参数不能覆盖身份或 Cookie") from None
    return result


class HttpClient:
    """每个身份拥有独立 Session，调用方通过 request 传参而不修改会话。"""

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
            if identity.token is not None:
                raise HttpError("Token 与密码认证不能同时使用") from None
            username, password = basic_auth
            encoded = base64.b64encode(f"{username}:{password}".encode("ascii")).decode("ascii")
            recorder.redactor.register(password)
            recorder.redactor.register(encoded)
            self._authorization = f"Basic {encoded}"
        self.session = requests.Session()
        self.session.trust_env = False
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
        method = method.upper()
        url = _url(self.settings, path)
        request_headers = _headers(headers)
        if self._authorization is not None:
            request_headers["Authorization"] = self._authorization
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
            if json is None:
                # Requests 的 json=None 不发送正文，需要显式发送 JSON null。
                options["data"] = "null"
                request_headers.setdefault("Content-Type", "application/json")
            else:
                options["json"] = json
        self.recorder.record("request", request_data)
        started = perf_counter()
        try:
            response = self.session.request(method, url, **options)
        except requests.RequestException:
            self.recorder.record("network-error", {"error": "network-failure", "method": method})
            raise NetworkError("网络请求失败") from None
        except (ValueError, TypeError):
            self.recorder.record("request-error", {"error": "request-invalid", "method": method})
            raise HttpError("无法构造有效请求") from None
        finally:
            # 响应可能设置 Cookie，请求结束后清空，避免影响后续身份。
            self.session.cookies.clear()
        elapsed = perf_counter() - started
        try:
            body = response.json()
        except ValueError:
            body = content_summary(response.content)
        size = len(response.content)
        self.recorder.record(
            "response",
            {
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": body,
                "elapsed": elapsed,
                "size": size,
            },
        )
        return HttpResult(response, elapsed, size)

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
