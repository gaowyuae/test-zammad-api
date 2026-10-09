"""M13 的数据前置、附件证据及文章修改保护。"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import yaml

from assertions.tickets import check
from fixtures.creation_access import _actor_user, _pure_role
from fixtures.messages import MessageCase
from fixtures.preflight import PreflightError, list_rows, preflight, read_json
from fixtures.ticket import CleanupError, valid_id
from support.config import ConfigError, _ConfigLoader
from support.run_config import RunConfig

ROOT = Path(__file__).resolve().parents[1]
CASE_IDS = frozenset(f"TC_{number:03}" for number in range(92, 97))
EXPECTED = {
    "TC_092": {"status_code": 422, "error": "Need at least an 'article body' field."},
    "TC_093": {"status_code": 201, "filename": "qa.txt", "size": 2},
    "TC_094": {"status_code": 403, "error": "Not authorized"},
    "TC_095": {"status_code": 200, "body_unchanged": True},
    "TC_096": {
        "status_code": 403,
        "error": "Not authorized (you can only delete your own notes)!",
    },
}


def _validate_case(case_id: str, scene: object) -> None:
    """严格限制原表请求形状，缺失正文不可被配置悄悄补齐。"""
    fields = {"actor", "article", "expected"}
    fields |= {"TC_095": {"replacement"}, "TC_096": {"author"}}.get(case_id, set())
    if not isinstance(scene, dict) or set(scene) != fields:
        raise ValueError
    if scene["actor"] != ("C2" if case_id == "TC_094" else "A1"):
        raise ValueError
    expected = scene["expected"]
    if (
        not isinstance(expected, dict)
        or expected != EXPECTED[case_id]
        or any(type(expected[key]) is not type(value) for key, value in EXPECTED[case_id].items())
    ):
        raise ValueError
    if case_id == "TC_096" and scene["author"] != "A2":
        raise ValueError
    if case_id == "TC_095" and (
        not isinstance(scene["replacement"], str) or not scene["replacement"]
    ):
        raise ValueError
    article = scene["article"]
    fixed = {"type": "note", "sender": "Agent", "internal": case_id not in {"TC_093", "TC_094"}}
    if case_id != "TC_092":
        fixed["content_type"] = "text/plain"
    if case_id in {"TC_093", "TC_094"}:
        fixed["attachments"] = [{"filename": "qa.txt", "data": "UUE=", "mime-type": "text/plain"}]
    required = set(fixed) | ({"body"} if case_id != "TC_092" else set())
    if (
        not isinstance(article, dict)
        or set(article) != required
        or any(article[key] != value for key, value in fixed.items())
        or type(article["internal"]) is not bool
        or ("body" in required and (not isinstance(article["body"], str) or not article["body"]))
    ):
        raise ValueError


def load_article_check_case(case_id: str) -> tuple[dict, dict]:
    """加载五个明确场景并隔离基线工单和文章输入。"""
    try:
        rows = yaml.load(
            (ROOT / "data/cases/ticket_article_checks.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        scenario = rows[case_id]
        _validate_case(case_id, scenario)
        data = yaml.load(
            (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(data, dict) or not isinstance(data.get("article"), dict):
            raise ValueError
        data.update(case_id=case_id, state_name="open", expected=deepcopy(scenario["expected"]))
        return deepcopy(data), deepcopy(scenario)
    except (KeyError, OSError, ValueError, TypeError, yaml.YAMLError):
        raise ConfigError("文章保护场景数据缺失或结构无效") from None


def _check_import_mode(maintenance) -> None:
    """完整读取设置且只接受明确关闭的普通模式。"""
    settings = read_json(maintenance, "api/v1/settings")
    if not isinstance(settings, list) or not all(
        isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"]
        for row in settings
    ):
        raise PreflightError("设置列表结构无法确认") from None
    imports = [row for row in settings if row["name"] == "import_mode"]
    if (
        len(imports) != 1
        or not isinstance(imports[0].get("state_current"), dict)
        or imports[0]["state_current"].get("value") is not False
    ):
        raise PreflightError("无法确认 import_mode 已关闭") from None


def article_check_preflight(
    agent,
    maintenance,
    actor,
    peer,
    config: RunConfig,
    customer_login: str,
    peer_login: str,
    data: dict,
    case_id: str,
) -> dict:
    """写入前确认实际执行人、同组作者及客户隔离，禁止无效Token代替权限拒绝。"""
    if case_id not in CASE_IDS:
        raise PreflightError("不支持的文章保护场景") from None
    mapping = preflight(agent, maintenance, config, data)
    users = list_rows(maintenance, "api/v1/users")
    roles = list_rows(maintenance, "api/v1/roles")
    user = _actor_user(actor, users, customer_login)
    if case_id == "TC_094":
        _pure_role(user, roles, "ticket.customer")
        if user["id"] in {
            mapping["agent_id"],
            mapping["maintenance_id"],
            mapping["customer_id"],
        } or (user.get("organization_id") or user.get("organization_ids")):
            raise PreflightError("C2必须为无组织共享的独立客户") from None
    else:
        _pure_role(user, roles, "ticket.agent")
        if user["id"] != mapping["agent_id"]:
            raise PreflightError("文章被测请求必须由A1发出") from None
    read_json(actor, "api/v1/ticket_create")
    if case_id == "TC_096":
        if peer is None:
            raise PreflightError("缺少A2独立Token") from None
        author = _actor_user(peer, users, peer_login)
        groups = _pure_role(author, roles, "ticket.agent")
        if author["id"] in {
            mapping["agent_id"],
            mapping["maintenance_id"],
            mapping["customer_id"],
        } or ("full" not in groups.get(str(mapping["group_id"]), set())):
            raise PreflightError("A2必须为独立且具有G1 full权限的普通客服") from None
        read_json(peer, "api/v1/ticket_create")
        mapping["peer_id"] = author["id"]
    if case_id == "TC_095":
        _check_import_mode(maintenance)
    mapping.update(actor_id=user["id"], creator_id=mapping["maintenance_id"])
    return mapping


def payload_for(case, scenario: dict) -> dict:
    """仅为实际存在的正文加入RUN，092保持字段缺失。"""
    payload = deepcopy(scenario["article"])
    payload["ticket_id"] = case.ticket_id
    if "body" in payload:
        payload["body"] += f"；RUN={case.marker}"
    return payload


def assert_download(result, recorder, label: str, *, denied: bool = False) -> None:
    """成功失败均先保存字节摘要，下载比较原字节，拒绝响应不得夹带文件。"""
    content = result.response.content
    recorder.record(
        label,
        {
            "status": result.response.status_code,
            "size": len(content),
            "sha256": sha256(content).hexdigest(),
            "byte_equal": content == b"QA",
            "contains_file_bytes": b"QA" in content,
        },
    )
    if denied:
        check(result.response.status_code == 403, "附件越权状态码不符")
        value = result.get_json()
        check(
            isinstance(value, dict) and value.get("error") == "Not authorized", "附件拒绝原因不符"
        )
        check(b"QA" not in content, "拒绝响应泄露附件字节")
    else:
        check(result.response.status_code == 200, "授权附件下载失败")
        check(content == b"QA", "附件下载字节不符")


def assert_attachment(case, article: object, article_id: int) -> int:
    """核对当前文章唯一附件的API元数据及数据库精确关联。"""
    check(isinstance(article, dict), "附件文章结构不符")
    check(
        article.get("id") == article_id and article.get("ticket_id") == case.ticket_id,
        "附件文章关联不符",
    )
    rows = article.get("attachments")
    check(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict), "附件数量不符")
    attachment = rows[0]
    check(valid_id(attachment.get("id")), "附件ID无效")
    check(attachment.get("filename") == "qa.txt", "附件文件名不符")
    # Store的size来自varchar；兼容整数表示，但不接受布尔或浮点隐式相等。
    size = attachment.get("size")
    check(type(size) in (str, int) and size in ("2", 2), "附件大小不符")
    attachment_id = attachment["id"]
    case.attachments.add((case.ticket_id, article_id, attachment_id))
    stored = case.db.attachments(article_id)
    case.recorder.record("attachment-link", {"article_id": article_id, "attachments": stored})
    check(
        stored
        == [{"id": attachment_id, "article_id": article_id, "filename": "qa.txt", "size": 2}],
        "附件数据库关联不符",
    )
    return attachment_id


class ArticleCheckCase(MessageCase):
    """仅操作当前工单的已知文章，清理后另核验附件关联无残留。"""

    def __init__(self, agent, maintenance, run, mapping, data, recorder, db) -> None:
        """初始文章归属锚点和追加文章登记分别保存。"""
        super().__init__(agent, maintenance, run, mapping, data, recorder, db)
        self.known_article_ids: set[int] = set()
        self.attachments: set[tuple[int, int, int]] = set()
        self.need_attachment_check = self.case_id in {"TC_093", "TC_094"}
        self.peer_api = None

    def post_article(self, api, payload: dict):
        """成功创建后立即登记文章和所有可识别附件，不等业务断言。"""
        result = super().post_article(api, payload)
        value = result.get_json()
        if not self.write_unknown and result.response.status_code == 201:
            self.known_article_ids.add(value["id"])
            rows = value.get("attachments", [])
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and valid_id(row.get("id")):
                        self.attachments.add((self.ticket_id, value["id"], row["id"]))
        return result

    def _write_intent(self, article_id: int, method: str, payload: dict | None) -> None:
        """写前检查当前工单及已确认文章ID，并在请求前保留未知状态。"""
        if self.write_unknown or self.key is None or not valid_id(self.ticket_id):
            raise RuntimeError("当前工单状态不允许修改文章") from None
        if not valid_id(article_id) or article_id not in self.known_article_ids | set(
            self.article_ids
        ):
            raise ValueError("只能操作当前工单已知文章") from None
        self.recorder.ensure_complete()
        self.recorder.record(
            "article-mutation-intent",
            {
                "ticket_id": self.ticket_id,
                "article_id": article_id,
                "method": method,
                "payload": payload,
            },
        )
        self.write_unknown = True

    def update_article(self, api, article_id: int, payload: dict):
        """只允许提交正文修改，矛盾成功及网络中断均保留未知保护。"""
        if (
            not isinstance(payload, dict)
            or set(payload) != {"body"}
            or not isinstance(payload["body"], str)
        ):
            raise ValueError("文章更新只能提交body") from None
        self._write_intent(article_id, "PUT", payload)
        result = api.update_article(article_id, payload)
        status = result.response.status_code
        if 400 <= status < 500:
            self.write_unknown = False
        elif status == 200:
            value = result.get_json()
            self.write_unknown = not (
                isinstance(value, dict)
                and value.get("id") == article_id
                and value.get("ticket_id") == self.ticket_id
            )
        return result

    def delete_article(self, api, article_id: int):
        """明确拒绝或已完成删除可安全清理，业务层仍须严格断言403。"""
        self._write_intent(article_id, "DELETE", None)
        result = api.delete_article(article_id)
        status = result.response.status_code
        self.write_unknown = not (status in {200, 204} or 400 <= status < 500)
        return result

    def cleanup(self) -> None:
        """先完成已有安全归属清理，再核对本轮文章的附件数据库关联。"""
        super().cleanup()
        if not self.need_attachment_check or self.key is None:
            return
        try:
            ids = (
                set(self.article_ids)
                | self.known_article_ids
                | {item[1] for item in self.attachments}
            )
            for article_id in sorted(ids):
                rows = self.db.attachments(article_id)
                self.recorder.record(
                    "attachment-cleanup", {"article_id": article_id, "attachments": rows}
                )
                if rows != []:
                    raise CleanupError("工单清理后仍有附件关联残留")
        except Exception:
            self.run.attention(self.key, "cleanup_failed")
            raise CleanupError("附件清理证据缺失或关联残留，需人工核查") from None
