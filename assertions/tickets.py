"""分派及权限更新共用的工单字段与只读响应核查。"""

FIELDS = (
    "id",
    "number",
    "title",
    "customer_id",
    "group_id",
    "owner_id",
    "state_id",
    "priority_id",
    "created_by_id",
    "article_count",
)


def check(condition: bool, message: str) -> None:
    """仅回显固定诊断，不暴露完整业务响应。"""
    if not condition:
        raise AssertionError(message) from None


def ticket_value(result) -> dict:
    """响应必须成功且包含明确空 pending_time。"""
    check(result.response.status_code == 200, "工单请求状态码不符")
    ticket = result.get_json()
    check(isinstance(ticket, dict), "工单响应结构不符")
    check("pending_time" in ticket and ticket["pending_time"] is None, "pending_time 未明确为空")
    return ticket


def assert_fields(ticket: dict, expected: dict) -> None:
    """逐字段核对更新结果，缺失字段也必须失败。"""
    check(isinstance(ticket, dict), "工单数据缺失")
    for field in FIELDS:
        check(field in ticket and ticket[field] == expected[field], f"工单 {field} 不一致")


def article_rows(api, ticket_id: int) -> list[dict]:
    """取得初始唯一文章，不用空列表掩盖读失败。"""
    result = api.articles(ticket_id)
    check(result.response.status_code == 200, "文章回读失败")
    rows = result.get_json()
    check(
        isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict),
        "初始文章数量或结构不符",
    )
    return rows
