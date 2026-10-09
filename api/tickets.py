"""TC_023 所需工单及文章接口，保留原始响应。"""

from clients.http import HttpClient, HttpResult


class TicketsAPI:
    """使用显式身份客户端操作工单资源。"""

    def __init__(self, client: HttpClient) -> None:
        """保存调用方选择的身份客户端。"""
        self.client = client

    def create(self, payload: dict) -> HttpResult:
        """发送一次创建请求，不补齐或改写输入字段。"""
        return self.client.request(
            "POST",
            "api/v1/tickets",
            json=payload,
            headers={"X-Zammad-Suppress-Notifications": "true"},
        )

    def read(self, ticket_id: int) -> HttpResult:
        """读取明确工单 ID。"""
        return self.client.request("GET", f"api/v1/tickets/{ticket_id}")

    def update(self, ticket_id: int, payload: dict) -> HttpResult:
        """只提交调用方指定的更新字段，不补齐缺失字段。"""
        return self.client.request(
            "PUT",
            f"api/v1/tickets/{ticket_id}",
            json=payload,
            headers={"X-Zammad-Suppress-Notifications": "true"},
        )

    def mass_macro(self, macro_id: int, ticket_ids: list[int]) -> HttpResult:
        """一次提交整个宏执行集合，由服务端决定事务结果。"""
        return self.client.request(
            "POST",
            "api/v1/tickets/mass_macro",
            json={"macro_id": macro_id, "ticket_ids": ticket_ids},
            headers={"X-Zammad-Suppress-Notifications": "true"},
        )

    def search(self, params: dict) -> HttpResult:
        """显式查询参数传入搜索接口，不轮询或自动取第一项。"""
        return self.client.request("GET", "api/v1/tickets/search", params=params)

    def articles(self, ticket_id: int) -> HttpResult:
        """读取目标工单文章并展开类型和发送方名称。"""
        return self.client.request(
            "GET", f"api/v1/ticket_articles/by_ticket/{ticket_id}", params={"expand": "true"}
        )

    def create_article(self, payload: dict) -> HttpResult:
        """使用显式调用身份追加文章，保留故意伪造的输入供服务端校验。"""
        return self.client.request(
            "POST",
            "api/v1/ticket_articles",
            json=payload,
            headers={"X-Zammad-Suppress-Notifications": "true"},
        )

    def article(self, article_id: int) -> HttpResult:
        """按明确文章 ID 核对清理后的接口状态。"""
        return self.client.request("GET", f"api/v1/ticket_articles/{article_id}")

    def delete(self, ticket_id: int) -> HttpResult:
        """删除调用者已核实归属的工单，不自动重试。"""
        return self.client.request("DELETE", f"api/v1/tickets/{ticket_id}")

    def update_article(self, article_id: int, payload: dict) -> HttpResult:
        """仅提交调用方明确指定的文章更新字段。"""
        return self.client.request("PUT", f"api/v1/ticket_articles/{article_id}", json=payload)

    def delete_article(self, article_id: int) -> HttpResult:
        """使用所选身份单次删除明确文章，不重试。"""
        return self.client.request("DELETE", f"api/v1/ticket_articles/{article_id}")

    def attachment(self, ticket_id: int, article_id: int, attachment_id: int) -> HttpResult:
        """按完整三元组下载，保留原始响应字节供内容校验。"""
        return self.client.request(
            "GET", f"api/v1/ticket_attachment/{ticket_id}/{article_id}/{attachment_id}"
        )
