"""消息场景数据、执行身份核对及追加文章的写入保护。"""

from copy import deepcopy
from pathlib import Path

import yaml

from fixtures.creation_access import _actor_user, _pure_role
from fixtures.pilot import PilotCase
from fixtures.preflight import PreflightError, list_rows, preflight, read_json
from fixtures.ticket import valid_id
from support.config import ConfigError, _ConfigLoader
from support.run_config import RunConfig

ROOT = Path(__file__).resolve().parents[1]


def load_message_case(case_id: str) -> tuple[dict, dict]:
    """按编号读取消息场景，并合并独立的建单前置。"""
    try:
        rows = yaml.load(
            (ROOT / "data/cases/ticket_messages.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        scenario = rows[case_id]
        data = yaml.load(
            (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        data.update(case_id=case_id, state_name="open", expected=deepcopy(scenario["expected"]))
        return data, scenario
    except (KeyError, OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        raise ConfigError("消息场景数据缺失或结构无效") from None


def message_preflight(
    agent, maintenance, actor, config: RunConfig, customer_login: str, data: dict, case_id: str
) -> dict:
    """写入前核对真实执行身份、客户隔离和有效Token范围。"""
    mapping = preflight(agent, maintenance, config, data)
    actor_id = mapping["agent_id"]
    # A1 与 C1 的角色及组织隔离已由基础前置核实，只补充实际客户身份和 C2。
    if case_id != "TC_087":
        user = _actor_user(actor, list_rows(maintenance, "api/v1/users"), customer_login)
        actor_id = user["id"]
        if case_id == "TC_091":
            _pure_role(user, list_rows(maintenance, "api/v1/roles"), "ticket.customer")
            if user.get("organization_id") or user.get("organization_ids"):
                raise PreflightError("消息场景客户不能有组织共享") from None
        same_customer = user["id"] == mapping["customer_id"]
        if same_customer != (case_id != "TC_091"):
            raise PreflightError("消息请求客户与目标工单归属不符") from None
    read_json(actor, "api/v1/ticket_create")
    mapping.update(actor_id=actor_id, creator_id=mapping["maintenance_id"])
    return mapping


class MessageCase(PilotCase):
    """追加文章仍属于当前独立工单，未知写入不得与清理竞争。"""

    def post_article(self, api, payload: dict):
        """单次写入并立即登记已确认文章，保留初始文章归属锚点。"""
        if self.write_unknown or self.key is None or not valid_id(self.ticket_id):
            raise RuntimeError("当前工单状态不允许追加文章") from None
        if payload.get("ticket_id") != self.ticket_id:
            raise ValueError("追加文章必须属于当前工单") from None
        self.recorder.ensure_complete()
        self.recorder.record(
            "article-write-intent", {"ticket_id": self.ticket_id, "body": payload.get("body")}
        )
        self.write_unknown = True
        result = api.create_article(payload)
        value = result.get_json()
        confirmed = (
            result.response.status_code == 201
            and isinstance(value, dict)
            and valid_id(value.get("id"))
            and value.get("ticket_id") == self.ticket_id
        )
        if confirmed:
            self.run.add_articles(self.key, [value["id"]])
        # 5xx及矛盾的成功响应仍可能留下未知对象，不能马上删除与服务端竞争。
        self.write_unknown = not (confirmed or 400 <= result.response.status_code < 500)
        return result
