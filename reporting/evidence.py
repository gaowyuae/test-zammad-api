"""将脱敏 JSON 即时写入 Allure，并显式报告缺证。"""

import json
import re

import allure
import allure_commons

from support.redaction import Redactor, content_summary


def _attachment_available() -> bool:
    """确认当前 Allure 同时有附件接收和文件保存监听器。"""
    hooks = allure_commons.plugin_manager.hook
    return bool(hooks.attach_data.get_hookimpls() and hooks.report_attached_data.get_hookimpls())


class EvidenceError(RuntimeError):
    """证据不可完整取得的安全异常。"""


class EvidenceRecorder:
    """以简单字节计数限制当前整轮的 Allure 证据。"""

    def __init__(
        self,
        redactor: Redactor | None = None,
        *,
        attachment_limit: int = 1024 * 1024,
        run_limit: int = 50 * 1024 * 1024,
    ) -> None:
        """初始化脱敏器与单附件、整轮预算。"""
        if (
            type(attachment_limit) is not int
            or type(run_limit) is not int
            or min(attachment_limit, run_limit) <= 0
        ):
            raise EvidenceError("证据容量配置无效") from None
        self.redactor = redactor if redactor is not None else Redactor()
        self.attachment_limit = attachment_limit
        self.run_limit = run_limit
        self.total_bytes = 0
        self.incomplete = False

    def _attach(self, name: str, body: str, size: int) -> bool:
        """立即写入安全附件，失败时标记缺证。"""
        if not _attachment_available():
            self.incomplete = True
            return False
        try:
            allure.attach(body, name=name, attachment_type=allure.attachment_type.JSON)
        except Exception:
            self.incomplete = True
            return False
        self.total_bytes += size
        return True

    def record(self, name: str, data: object) -> bool:
        """立即记录脱敏数据；容量越界返回缺证标志。"""
        # FileEvidence 会将名称用于文件名，必须保留路径安全限制。
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            self.incomplete = True
            raise EvidenceError("证据名称无效") from None
        try:
            body = json.dumps(self.redactor.redact(data), ensure_ascii=True, allow_nan=False)
        except Exception:
            self.incomplete = True
            return False
        # ensure_ascii 保证字符数就是落盘字节数，正常附件无需再复制为 bytes。
        size = len(body)
        safe_name = self.redactor.redact(name)
        if size <= self.attachment_limit and self.total_bytes + size <= self.run_limit:
            return self._attach(safe_name, body, size)
        self._record_summary(safe_name, body)
        return False

    def _record_summary(self, name: str, body: str) -> None:
        """标记超限缺证，并在预算允许时保存脱敏正文的安全摘要。"""
        self.incomplete = True
        summary = json.dumps(
            {
                "incomplete": True,
                "reason": "evidence-budget",
                **content_summary(body.encode("ascii")),
            }
        )
        size = len(summary)
        if size <= self.attachment_limit and self.total_bytes + size <= self.run_limit:
            self._attach(name, summary, size)

    def ensure_complete(self) -> None:
        """在整轮结束检查容量或保存失败造成的缺证。"""
        if self.incomplete:
            raise EvidenceError("测试证据不完整") from None


class CaseEvidence:
    """共享整轮预算，前置仅记录摘要，附件带 TC 编号与序号。"""

    def __init__(self, recorder: EvidenceRecorder, case_id: str = "TC_023") -> None:
        """使用同一脱敏器和预算，不另建证据缓冲。"""
        self.target = recorder
        self.redactor = recorder.redactor
        self.summary_only = True
        self.sequence = 0
        self.case_id = case_id

    def record(self, name: str, data: object) -> bool:
        """避免前置用户列表的无关资料进入附件。"""
        self.sequence += 1
        if self.summary_only and isinstance(data, dict):
            allowed = {"identity", "method", "url", "params", "status", "elapsed", "size", "error"}
            data = {key: value for key, value in data.items() if key in allowed}
        return self.target.record(f"{self.case_id}-{name}-{self.sequence}", data)

    def ensure_complete(self) -> None:
        """检查同一整轮取证预算是否完整。"""
        self.target.ensure_complete()
