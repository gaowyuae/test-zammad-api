"""TC_092～096：正文必填、附件隔离及已发送备注保护。"""

import allure
import pytest

from assertions.messages import (
    ARTICLE_FIELDS,
    assert_article,
    assert_posted,
    assert_ticket_stable,
    check_snapshot,
    prepare,
    snapshot,
)
from assertions.tickets import check
from fixtures.article_checks import assert_attachment, assert_download, payload_for

pytestmark = [pytest.mark.functional, pytest.mark.database]


def assert_error(result, expected: dict) -> None:
    """只接受已核实的确切业务错误，其他4xx不能替代。"""
    check(result.response.status_code == expected["status_code"], "文章拒绝状态码不符")
    value = result.get_json()
    check(isinstance(value, dict) and value.get("error") == expected["error"], "文章拒绝原因不符")


def create_note(case, scenario: dict, db, api, author_id: int) -> tuple[dict, int, object]:
    """由指定真实身份创建并核对文章实际作者，保留完整前后快照。"""
    before = prepare(case, db)
    payload = payload_for(case, scenario)
    result = case.post_article(api, payload)
    after = snapshot(case, db, "article-check-created")
    check_snapshot(after, case.ticket_id)
    article_id = assert_posted(
        case, result, payload, after, author_id, "Agent", payload["internal"]
    )
    assert_ticket_stable(before, after, 2)
    for key in ("articles", "db_articles"):
        check(
            [row for row in after[key] if row["id"] != article_id] == before[key],
            "追加改变了初始文章",
        )
    return after, article_id, result


def prepare_attachment(case, scenario: dict, db, api, author_id: int) -> tuple[dict, int, int]:
    """核对附件POST、单条回读、列表和DB归属后再使用完整三元组。"""
    before, article_id, result = create_note(case, scenario, db, api, author_id)
    attachment_id = assert_attachment(case, result.get_json(), article_id)
    single = case.maintenance.article(article_id)
    check(single.response.status_code == 200, "附件文章单条回读失败")
    expanded = next(row for row in before["articles"] if row["id"] == article_id)
    for value in (single.get_json(), expanded):
        check(assert_attachment(case, value, article_id) == attachment_id, "附件ID回读不一致")
    return before, article_id, attachment_id


@allure.title("TC_092 缺失body且无附件时拒绝追加文章")
def test_tc_092(article_check_case) -> None:
    """保持原始缺失字段，验证422及完整API/DB快照不变。"""
    case, scenario, db, api = article_check_case
    with allure.step("M准备独立工单并保存文章基线"):
        before = prepare(case, db)
    payload = payload_for(case, scenario)
    check("body" not in payload and "attachments" not in payload, "缺失字段被意外补齐")
    result = case.post_article(api, payload)
    after = snapshot(case, db, "article-check-after")
    with allure.step("核对必填错误及工单文章无变化"):
        assert_error(result, scenario["expected"])
        check_snapshot(after, case.ticket_id)
        check(after == before, "缺失正文请求改变了工单或文章")
        case.recorder.record("assertions", {"result": "passed", "ticket_id": case.ticket_id})


@allure.title("TC_093 Base64附件关联本次文章且下载字节恰为QA")
def test_tc_093(article_check_case) -> None:
    """A1直接上传固定2字节附件，并以同一身份下载完整原字节。"""
    case, scenario, db, api = article_check_case
    with allure.step("A1创建公开文章并验证附件实际归属"):
        before, article_id, attachment_id = prepare_attachment(
            case, scenario, db, api, case.mapping["actor_id"]
        )
    with allure.step("授权下载原始字节并保存长度和SHA256证据"):
        result = api.attachment(case.ticket_id, article_id, attachment_id)
        assert_download(result, case.recorder, "attachment-download")
    after = snapshot(case, db, "article-check-after")
    check(after == before, "附件下载改变了工单或文章")
    case.recorder.record(
        "assertions", {"result": "passed", "article_id": article_id, "attachment_id": attachment_id}
    )


@allure.title("TC_094 C2知道完整附件地址仍不能下载C1公开附件")
def test_tc_094(article_check_case) -> None:
    """M先证明附件真实存在且可下载，再验证独立C2明确403及无文件字节。"""
    case, scenario, db, api = article_check_case
    with allure.step("M准备C1公开附件并验证授权下载"):
        before, article_id, attachment_id = prepare_attachment(
            case, scenario, db, case.maintenance, case.mapping["maintenance_id"]
        )
        authorized = case.maintenance.attachment(case.ticket_id, article_id, attachment_id)
        assert_download(authorized, case.recorder, "attachment-authorized")
    with allure.step("C2使用完整工单文章附件三元组请求下载"):
        result = api.attachment(case.ticket_id, article_id, attachment_id)
        assert_download(result, case.recorder, "attachment-denied", denied=True)
    after = snapshot(case, db, "article-check-after")
    check(after == before, "越权附件访问改变了工单或文章")
    case.recorder.record(
        "assertions", {"result": "passed", "article_id": article_id, "attachment_id": attachment_id}
    )


@allure.title("TC_095 普通模式下A1更新自己备注返回200但原正文保持")
def test_tc_095(article_check_case) -> None:
    """前置已确认非导入模式，直接核对响应、单条回读及API/DB原正文。"""
    case, scenario, db, api = article_check_case
    with allure.step("A1实际创建自己的专用备注"):
        before, article_id, _ = create_note(case, scenario, db, api, case.mapping["actor_id"])
    original = next(row for row in before["articles"] if row["id"] == article_id)
    expected = {field: original[field] for field in ARTICLE_FIELDS}
    payload = {"body": f"{scenario['replacement']}；RUN={case.marker}"}
    check(payload["body"] != expected["body"], "替换正文必须不同于原正文")
    with allure.step("A1只提交body字段并核对已发送正文不可改写"):
        result = case.update_article(api, article_id, payload)
        after = snapshot(case, db, "article-check-after")
        single = case.maintenance.article(article_id)
        check(
            result.response.status_code == scenario["expected"]["status_code"], "文章更新状态码不符"
        )
        check(single.response.status_code == 200, "更新后文章回读失败")
        check_snapshot(after, case.ticket_id)
        assert_ticket_stable(before, after, 2)
        for value in (result.get_json(), single.get_json()):
            assert_article(value, expected)
        for key in ("articles", "db_articles"):
            targets = [row for row in after[key] if row["id"] == article_id]
            check(len(targets) == 1, "更新后原文章未唯一保留")
            assert_article(targets[0], expected)
            check(
                [row for row in after[key] if row["id"] != article_id]
                == [row for row in before[key] if row["id"] != article_id],
                "正文更新改变了其他文章",
            )
        case.recorder.record(
            "assertions", {"result": "passed", "article_id": article_id, "body_unchanged": True}
        )


@allure.title("TC_096 A1不能删除A2实际创建的备注")
def test_tc_096(article_check_case) -> None:
    """明确校验作者身份不同和精确403，文章及工单关联保持。"""
    case, scenario, db, api = article_check_case
    check(case.peer_api is not None, "缺少A2实际请求身份")
    check(case.mapping["peer_id"] != case.mapping["actor_id"], "A2与A1必须为不同作者")
    with allure.step("A2直接POST专用备注并验证真实created_by_id"):
        before, article_id, _ = create_note(
            case, scenario, db, case.peer_api, case.mapping["peer_id"]
        )
    with allure.step("A1尝试删除他人备注，文章和完整关联必须仍然存在"):
        result = case.delete_article(api, article_id)
        after = snapshot(case, db, "article-check-after")
        single = case.maintenance.article(article_id)
        assert_error(result, scenario["expected"])
        check(single.response.status_code == 200, "拒绝删除后文章不可读")
        check_snapshot(after, case.ticket_id)
        check(after == before, "拒绝删除后工单或文章发生变化")
        original = next(row for row in before["articles"] if row["id"] == article_id)
        assert_article(single.get_json(), {field: original[field] for field in ARTICLE_FIELDS})
        case.recorder.record("assertions", {"result": "passed", "article_id": article_id})
