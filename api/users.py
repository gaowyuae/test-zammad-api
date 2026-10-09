"""用户接口保留原始响应，由调用方选择身份和断言。"""

from clients.http import HttpClient, HttpResult


class UsersAPI:
    """只提供 M16 需要的用户操作。"""

    def __init__(self, client: HttpClient) -> None:
        """保存显式身份的客户端。"""
        self.client = client

    def create(self, payload: dict) -> HttpResult:
        """提交原始创建字段，不邀请或自动发送通知。"""
        __tracebackhide__ = True  # payload含临时密码，禁止pytest展示函数参数。
        return self.client.request(
            "POST",
            "api/v1/users",
            json=payload,
            headers={"X-Zammad-Suppress-Notifications": "true"},
        )

    def read(self, user_id: int) -> HttpResult:
        """读取明确用户，不用搜索结果替代。"""
        return self.client.request("GET", f"api/v1/users/{user_id}")

    def update(self, user_id: int, payload: dict) -> HttpResult:
        """仅更新调用者明确提交的字段。"""
        __tracebackhide__ = True
        return self.client.request(
            "PUT",
            f"api/v1/users/{user_id}",
            json=payload,
            headers={"X-Zammad-Suppress-Notifications": "true"},
        )

    def delete(self, user_id: int) -> HttpResult:
        """单次删除已确认归属的临时用户，不重试。"""
        return self.client.request("DELETE", f"api/v1/users/{user_id}")
