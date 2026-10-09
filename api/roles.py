"""角色接口保留原始输入及响应，不提供未支持的删除接口。"""

from clients.http import HttpClient, HttpResult


class RolesAPI:
    """只提供显式身份的创建、读取和更新。"""

    def __init__(self, client: HttpClient) -> None:
        """保存调用方明确选择的客户端。"""
        self.client = client

    def create(self, payload: dict) -> HttpResult:
        """原样提交字段，故意省略的active不自动补齐。"""
        return self.client.request("POST", "api/v1/roles", json=payload)

    def get(self, role_id: int) -> HttpResult:
        """读取明确角色ID，不使用列表首项替代。"""
        return self.client.request("GET", f"api/v1/roles/{role_id}")

    def update(self, role_id: int, payload: dict) -> HttpResult:
        """只提交调用方提供的更新字段，不解释响应。"""
        return self.client.request("PUT", f"api/v1/roles/{role_id}", json=payload)
