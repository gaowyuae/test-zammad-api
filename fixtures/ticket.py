"""当前用例工单的创建登记和安全清理。"""

from copy import deepcopy

import allure

from api.tickets import TicketsAPI
from clients.http import HttpClient, HttpResult
from reporting.evidence import EvidenceRecorder


class CleanupError(RuntimeError):
    """无法确认本次资源已安全清理。"""


def valid_id(value: object) -> bool:
    """拒绝布尔值和非正业务 ID。"""
    return type(value) is int and value > 0


class TicketCase:
    """只管理当前用例的工单，不查找或删除其他运行数据。"""

    def __init__(
        self,
        agent: HttpClient,
        maintenance: HttpClient,
        run,
        mapping: dict,
        data: dict,
        recorder: EvidenceRecorder,
        *,
        mark_title: bool = True,
    ) -> None:
        """构建独立数据副本和当前 RUN 标记。"""
        self.api = TicketsAPI(agent)
        self.maintenance = TicketsAPI(maintenance)
        self.run = run
        self.mapping = mapping
        self.recorder = recorder
        self.case_id = data["case_id"]
        self.creator_id = mapping.get("creator_id", mapping["agent_id"])
        self.mark_title = mark_title
        self.marker = f"{run.run_id}_{self.case_id}"
        self.payload = deepcopy(data)
        self.payload.pop("case_id")
        for name in ("state_name", "priority_name", "expected"):
            self.payload.pop(name, None)
        if mark_title:
            self.payload["title"] += f" {self.marker}"
        self.payload["article"]["body"] += f"；RUN={self.marker}"
        self.payload.update(
            customer_id=mapping["customer_id"],
            group_id=mapping["group_id"],
            owner_id=mapping["agent_id"],
        )
        for field in ("state_id", "priority_id"):
            if field in mapping:
                self.payload[field] = mapping[field]
        self.key: int | None = None
        self.ticket_id: int | None = None
        self.article_ids: list[int] = []

    def create(self) -> HttpResult:
        """先持久化创建意图，再发送一次并登记已返回 ID。"""
        if self.key is not None:
            raise RuntimeError("同一用例不能重复提交创建请求") from None
        self.recorder.ensure_complete()
        self.key = self.run.intent(self.case_id, self.marker)
        result = self.api.create(self.payload)
        value = result.get_json()
        if isinstance(value, dict) and valid_id(value.get("id")):
            self.ticket_id = value["id"]
            ids = value.get("article_ids", [])
            if isinstance(ids, list) and all(valid_id(item) for item in ids):
                self.article_ids = list(ids)
            self.run.created(self.key, self.ticket_id, self.article_ids)
        return result

    def cleanup_customer_ids(self) -> set[int]:
        """默认只允许本用例客户；冒用场景可显式列出双方，仍须核对RUN和作者。"""
        return {self.mapping["customer_id"]}

    def cleanup_group_ids(self) -> set[int]:
        """默认限定原组；转组用例可显式声明起止组，保留其他归属核验。"""
        return {self.mapping["group_id"]}

    def cleanup(self) -> None:
        """先保全快照，确认归属，删除并验证工单及已知文章不可读。"""
        if self.key is None:
            return
        if self.ticket_id is None:
            self.run.attention(self.key, "create_unknown")
            raise CleanupError("创建结果未知，已保留清单，需人工核查") from None
        reason = "ownership_unconfirmed"
        try:
            with allure.step(f"{self.case_id} 清理前确认归属并保全快照"):
                result = self.maintenance.read(self.ticket_id)
                value = result.get_json()
                expected = {
                    "id": self.ticket_id,
                    "created_by_id": self.creator_id,
                }
                if self.mark_title:
                    expected["title"] = self.payload["title"]
                if result.response.status_code != 200 or not isinstance(value, dict):
                    raise CleanupError("无法确认清理对象归属")
                if any(value.get(key) != item for key, item in expected.items()):
                    raise CleanupError("清理对象归属不一致")
                if value.get("customer_id") not in self.cleanup_customer_ids():
                    raise CleanupError("清理客户不属于本用例范围")
                if value.get("group_id") not in self.cleanup_group_ids():
                    raise CleanupError("清理组不属于本用例范围")
                articles = self.maintenance.articles(self.ticket_id)
                rows = articles.get_json()
                if articles.response.status_code != 200 or not isinstance(rows, list):
                    raise CleanupError("无法取得清理前文章快照")
                if not all(
                    isinstance(row, dict)
                    and valid_id(row.get("id"))
                    and row.get("ticket_id") == self.ticket_id
                    for row in rows
                ):
                    raise CleanupError("文章归属无法确认")
                # 边界标题可能完全相同或缺失，必须以本轮初始正文和真实作者确认归属。
                initial = [
                    row
                    for row in rows
                    if row.get("body") == self.payload["article"]["body"]
                    and row.get("created_by_id") == self.creator_id
                ]
                if not initial or (
                    self.article_ids and not any(row["id"] in self.article_ids for row in initial)
                ):
                    raise CleanupError("初始文章 RUN 或创建身份不一致")
                self.article_ids = sorted(set(self.article_ids + [row["id"] for row in rows]))
                self.run.add_articles(self.key, self.article_ids)
                self.recorder.record(
                    f"{self.case_id}-pre-cleanup",
                    {
                        "ticket_id": self.ticket_id,
                        "article_ids": self.article_ids,
                        "ownership": "confirmed",
                    },
                )
            reason = "cleanup_failed"
            with allure.step(f"{self.case_id} 维护身份删除并验证清理"):
                deleted = self.maintenance.delete(self.ticket_id)
                if deleted.response.status_code != 200:
                    raise CleanupError("删除接口未成功")
                if self.maintenance.read(self.ticket_id).response.status_code != 404:
                    raise CleanupError("工单删除后仍可访问或验证失败")
                for article_id in self.article_ids:
                    if self.maintenance.article(article_id).response.status_code != 404:
                        raise CleanupError("文章清理后仍可访问或验证失败")
                self.run.cleaned(self.key)
                self.recorder.record(
                    f"{self.case_id}-cleanup", {"result": "cleaned", "ticket_id": self.ticket_id}
                )
        except Exception:
            self.run.attention(self.key, reason)
            self.recorder.record(
                f"{self.case_id}-cleanup", {"result": "attention", "reason": reason}
            )
            raise CleanupError("清理未完成，已保留残留清单") from None
