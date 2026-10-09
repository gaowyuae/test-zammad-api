"""通过当前身份管理自身Token，不提供代签或跨用户撤销。"""

from clients.http import HttpClient, HttpResult


class TokensAPI:
    """保留真实响应，生命周期调用者负责核实作用域与归属。"""

    def __init__(self, client: HttpClient) -> None:
        """绑定明确身份的客户端。"""
        self.client = client

    def list(self) -> HttpResult:
        """读取当前用户Token元数据。"""
        return self.client.request("GET", "api/v1/user_access_token")

    def create(self, name: str, permission: str) -> HttpResult:
        """仅提交一个已批准权限，不使用管理员代签。"""
        return self.client.request(
            "POST", "api/v1/user_access_token", json={"name": name, "permission": [permission]}
        )

    def delete(self, token_id: int) -> HttpResult:
        """撤销本条已核实的Token ID，不自动重试。"""
        return self.client.request("DELETE", f"api/v1/user_access_token/{token_id}")
