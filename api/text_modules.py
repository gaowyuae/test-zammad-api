"""文本模块API保留HTML转换和权限响应供业务断言核对。"""

from clients.http import HttpClient, HttpResult


class TextModulesAPI:
    """只提供本批次需要的创建、详情和删除，不使用列表代替详情。"""

    def __init__(self, client: HttpClient) -> None:
        """绑定明确身份的独立客户端。"""
        self.client = client

    def create(self, payload: dict) -> HttpResult:
        """原样提交内容、备注及可选组，不截断note。"""
        return self.client.request("POST", "api/v1/text_modules", json=payload)

    def get(self, object_id: int) -> HttpResult:
        """按ID读取详情，客户场景不发列表请求。"""
        return self.client.request("GET", f"api/v1/text_modules/{object_id}")

    def delete(self, object_id: int) -> HttpResult:
        """只删除调用方已经核实归属的对象。"""
        return self.client.request("DELETE", f"api/v1/text_modules/{object_id}")
