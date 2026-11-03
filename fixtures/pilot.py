"""P0 的明确拒绝、未知写入和数据库清理核查。"""

from pathlib import Path

import yaml

from fixtures.ticket import CleanupError, TicketCase, load_case
from support.config import _ConfigLoader


class PilotCase(TicketCase):
    """复用已验收工单生命周期，仅补齐 P0 所需的数据库证据。"""

    def __init__(
        self, agent, maintenance, run, mapping, data, recorder, db, *, mark_title: bool = True
    ) -> None:
        """保存当前用例连接及写入结果状态，不跨用例共享。"""
        super().__init__(agent, maintenance, run, mapping, data, recorder, mark_title=mark_title)
        self.db = db
        self.write_unknown = False
        self.create_status = None

    def create(self):
        """创建过程中断时保留未知状态，禁止随后删除与请求竞争。"""
        self.write_unknown = True
        result = super().create()
        self.create_status = result.response.status_code
        self.write_unknown = False
        return result

    def update(self, api, payload: dict):
        """明确区分请求结束和网络中断，不重试写入。"""
        self.write_unknown = True
        result = api.update(self.ticket_id, payload)
        self.write_unknown = False
        return result

    def cleanup(self) -> None:
        """先确认写入已结束，再按归属删除或核实明确拒绝无残留。"""
        if self.key is None:
            return
        if self.write_unknown:
            self.run.attention(self.key, "create_unknown")
            raise CleanupError("写入结果未知，禁止冲突清理，需人工核查") from None
        if self.ticket_id is None:
            if not 400 <= self.create_status < 500:
                self.run.attention(self.key, "create_unknown")
                raise CleanupError("创建结果未知，已保留清单") from None
            if self.db.by_marker(self.marker) != {"tickets": [], "articles": []}:
                self.run.attention(self.key, "ownership_unconfirmed")
                raise CleanupError("负向请求存在异常残留，保留证据待人工核查") from None
            self.run.absent(self.key)
            self.recorder.record("negative-cleanup", {"result": "absent", "marker": self.marker})
            return
        super().cleanup()
        # API 404 不代替数据库证据；发现关联残留时重新标为需人工处理。
        try:
            remains = self.db.ticket(self.ticket_id) is not None or self.db.articles(self.ticket_id)
        except Exception:
            self.run.attention(self.key, "cleanup_failed")
            raise CleanupError("API 删除后的数据库证据缺失，需人工核查") from None
        if remains:
            self.run.attention(self.key, "cleanup_failed")
            raise CleanupError("API 删除后仍有数据库残留") from None


def load_pilot_case(case_id: str) -> tuple[dict, dict]:
    """将仓库维护的 P0 场景覆盖合入基础工单输入。"""
    path = Path(__file__).resolve().parents[1] / "data/cases/ticket_pilot.yaml"
    scenario = yaml.load(path.read_text(encoding="utf-8"), Loader=_ConfigLoader)[case_id]
    data = load_case()
    data.update(
        case_id=case_id,
        title=scenario["title"],
        state_name=scenario["state_name"],
        expected=scenario["expected"],
    )
    return data, scenario
