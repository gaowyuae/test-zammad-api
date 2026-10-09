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
CASE_IDS = frozenset(f"TC_{number:03}" for number in range(87, 92))
ACTORS = {"TC_087": "A1", "TC_088": "C1", "TC_089": "C1", "TC_090": "C1", "TC_091": "C2"}


def load_message_case(case_id: str) -> tuple[dict, dict]:
    """加载已确认的五条消息场景并独立复制建单前置。"""
    try:
        rows = yaml.load(
            (ROOT / "data/cases/ticket_messages.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        scenario = rows[case_id]
        fields = {"actor", "expected"}
        if case_id in {"TC_087", "TC_088", "TC_091"}:
            fields.add("article")
        if not isinstance(scenario, dict) or set(scenario) != fields:
            raise ValueError
        expected = scenario["expected"]
        if scenario["actor"] != ACTORS[case_id] or not isinstance(expected, dict):
            raise ValueError
        expected_status = (
            201 if case_id in {"TC_087", "TC_088"} else 200 if case_id == "TC_089" else 403
        )
        if (
            type(expected.get("status_code")) is not int
            or expected["status_code"] != expected_status
        ):
            raise ValueError
        expected_fields = {"status_code", "error"}
        if case_id in {"TC_087", "TC_088"}:
            expected_fields = {"status_code", "sender", "internal"}
            if (
                expected.get("sender") != ("Agent" if case_id == "TC_087" else "Customer")
                or expected.get("internal") is not False
            ):
                raise ValueError
        elif case_id == "TC_089":
            expected_fields = {"status_code", "public_count", "internal_count"}
            if expected.get("public_count") != 2 or expected.get("internal_count") != 3:
                raise ValueError
        elif expected.get("error") != "Not authorized":
            raise ValueError
        if set(expected) != expected_fields:
            raise ValueError
        if "article" in fields:
            article = scenario["article"]
            if (
                not isinstance(article, dict)
                or set(article) != {"type", "sender", "content_type", "body", "internal"}
                or not all(
                    isinstance(article[key], str) and article[key]
                    for key in ("type", "sender", "content_type", "body")
                )
                or type(article["internal"]) is not bool
            ):
                raise ValueError
            if (
                article["type"],
                article["sender"],
                article["content_type"],
                article["internal"],
            ) != {
                "TC_087": ("note", "Agent", "text/plain", False),
                "TC_088": ("web", "Agent", "text/plain", True),
                "TC_091": ("web", "Customer", "text/plain", False),
            }[case_id]:
                raise ValueError
        data = deepcopy(
            yaml.load(
                (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
                Loader=_ConfigLoader,
            )
        )
        if not isinstance(data, dict) or not isinstance(data.get("article"), dict):
            raise ValueError
        data.update(case_id=case_id, state_name="open", expected=deepcopy(expected))
        return data, deepcopy(scenario)
    except (KeyError, OSError, ValueError, TypeError, yaml.YAMLError):
        raise ConfigError("消息场景数据缺失或结构无效") from None


def message_preflight(
    agent, maintenance, actor, config: RunConfig, customer_login: str, data: dict, case_id: str
) -> dict:
    """写入前核对真实执行身份、客户隔离和有效Token范围。"""
    if case_id not in CASE_IDS:
        raise PreflightError("不支持的消息场景") from None
    mapping = preflight(agent, maintenance, config, data)
    users = list_rows(maintenance, "api/v1/users")
    roles = list_rows(maintenance, "api/v1/roles")
    user = _actor_user(actor, users, customer_login)
    if case_id == "TC_087":
        _pure_role(user, roles, "ticket.agent")
        if user["id"] != mapping["agent_id"]:
            raise PreflightError("备注必须由A1实际创建") from None
    else:
        _pure_role(user, roles, "ticket.customer")
        if user.get("organization_id") or user.get("organization_ids"):
            raise PreflightError("消息场景客户不能有组织共享") from None
        same_customer = user["id"] == mapping["customer_id"]
        if same_customer != (case_id != "TC_091"):
            raise PreflightError("消息请求客户与目标工单归属不符") from None
    read_json(actor, "api/v1/ticket_create")
    mapping.update(
        actor_id=user["id"],
        creator_id=mapping["maintenance_id"],
    )
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
