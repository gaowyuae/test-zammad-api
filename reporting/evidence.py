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
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            self.incomplete = True
            raise EvidenceError("证据名称无效") from None
        try:
            body = json.dumps(self.redactor.redact(data), ensure_ascii=True, allow_nan=False)
            encoded = body.encode("utf-8")
        except Exception:
            self.incomplete = True
            return False
        safe_name = str(self.redactor.redact(name))
        if (
            len(encoded) <= self.attachment_limit
            and self.total_bytes + len(encoded) <= self.run_limit
        ):
            return self._attach(safe_name, body, len(encoded))
        self.incomplete = True
        summary = json.dumps(
            {"incomplete": True, "reason": "evidence-budget", **content_summary(encoded)}
        )
        size = len(summary.encode("utf-8"))
        if size <= self.attachment_limit and self.total_bytes + size <= self.run_limit:
            self._attach(safe_name, summary, size)
        return False

    def ensure_complete(self) -> None:
        """在整轮结束检查容量或保存失败造成的缺证。"""
        if self.incomplete:
            raise EvidenceError("测试证据不完整") from None
