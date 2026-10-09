"""宏配置API保留显式身份及原始请求响应。"""

from clients.http import HttpClient, HttpResult


class MacrosAPI:
    """只提供本模块需要的宏CRUD，不负责业务断言。"""

    def __init__(self, client: HttpClient) -> None:
        """保存调用者指定的独立客户端。"""
        self.client = client

    def create(self, payload: dict) -> HttpResult:
        """提交宏字段，不补齐负向请求的组或权限。"""
        return self.client.request("POST", "api/v1/macros", json=payload)

    def get(self, object_id: int) -> HttpResult:
        """读取明确的宏ID。"""
        return self.client.request("GET", f"api/v1/macros/{object_id}")

    def update(self, object_id: int, payload: dict) -> HttpResult:
        """只提交调用方指定的字段。"""
        return self.client.request("PUT", f"api/v1/macros/{object_id}", json=payload)

    def delete(self, object_id: int) -> HttpResult:
        """对已经确认归属的宏仅发送一次删除。"""
        return self.client.request("DELETE", f"api/v1/macros/{object_id}")
