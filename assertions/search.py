"""搜索复用的精确集合、保存字段和负向响应检查。"""

import json

from assertions.tickets import FIELDS, check
from fixtures.ticket import valid_id


def search_rows(result) -> list[dict]:
    """只接受200的工单列表，拒绝重复和缺失ID。"""
    check(result.response.status_code == 200, "搜索状态码不是200")
    rows = result.get_json()
    check(
        isinstance(rows, list)
        and all(isinstance(row, dict) and valid_id(row.get("id")) for row in rows),
        "搜索结果不是有效工单列表",
    )
    check(len({row["id"] for row in rows}) == len(rows), "搜索结果存在重复工单")
    return rows


def assert_ids(rows: list[dict], expected: list[int], *, ordered: bool = False) -> None:
    """完整集合不能多项、漏项或重复，分页另要求稳定顺序。"""
    actual = [row["id"] for row in rows]
    check(len(actual) == len(set(actual)), "搜索集合重复")
    check(sorted(actual) == sorted(expected), "搜索完整集合与预期不符")
    if ordered:
        check(actual == expected, "搜索分页顺序不是ID升序")


def assert_saved(rows: list[dict], stored: dict[int, dict]) -> None:
    """搜索返回的核心业务字段逐项与查询前DB快照一致。"""
    for row in rows:
        expected = stored.get(row["id"])
        check(isinstance(expected, dict), "搜索返回基线以外的工单")
        for field in FIELDS:
            check(field in row and row[field] == expected[field], f"搜索字段{field}与保存值不符")


def assert_no_leak(value: object, forbidden: list[str]) -> None:
    """检查JSON解码后的全部返回值，避免转义正文绕过泄露断言。"""
    text = json.dumps(value, ensure_ascii=False)
    check(not any(word and word in text for word in forbidden), "负向响应泄露目标工单内容")


def assert_error(result, kind: str) -> dict:
    """仅允许明确错误字段，认证错误与目标工单不存在分别核查。"""
    status = result.response.status_code
    check(400 <= status < 500, "负向请求未返回4xx")
    value = result.get_json()
    check(
        isinstance(value, dict)
        and set(value).issubset({"error", "error_human"})
        and isinstance(value.get("error"), str)
        and all(isinstance(item, str) for item in value.values()),
        "错误响应结构包含业务信息或缺少原因",
    )
    error = value["error"].casefold()
    if kind == "authentication":
        matched = any(
            word in error
            for word in ("not authorized", "authentication", "authorization", "unauthorized")
        )
    else:
        matched = (
            kind == "missing"
            and status == 404
            and "ticket" in error
            and any(word in error for word in ("find", "not found", "does not exist"))
        )
    check(matched, "负向错误原因不符合用例")
    return value
