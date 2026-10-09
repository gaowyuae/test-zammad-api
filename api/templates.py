"""模板API只透传指定身份的原始请求与响应。"""

from clients.http import HttpClient, HttpResult


class TemplatesAPI:
    """提供模板创建、详情、局部更新和删除。"""

    def __init__(self, client: HttpClient) -> None:
        """保存调用方明确选定的身份客户端。"""
        self.client = client

    def create(self, payload: dict) -> HttpResult:
        """原样提交名称和options，不strip空格或补齐字段。"""
        return self.client.request("POST", "api/v1/templates", json=payload)

    def get(self, object_id: int) -> HttpResult:
        """读取明确模板ID。"""
        return self.client.request("GET", f"api/v1/templates/{object_id}")

    def update(self, object_id: int, payload: dict) -> HttpResult:
        """只提交指定字段，不将PUT变为重新创建。"""
        return self.client.request("PUT", f"api/v1/templates/{object_id}", json=payload)

    def delete(self, object_id: int) -> HttpResult:
        """对已确认的模板发送一次删除请求。"""
        return self.client.request("DELETE", f"api/v1/templates/{object_id}")
