"""搜索长期基线的显式初始化、只读验证和有限索引等待。"""

import json
import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from api.tickets import TicketsAPI
from fixtures.config_checks import pure_actor
from fixtures.preflight import list_rows, permissions, read_json, unique
from fixtures.ticket import valid_id
from support.runtime import _json_object
from support.search_config import ALIASES


class BaselineError(RuntimeError):
    """基线不完整或漂移，仅输出非敏感诊断。"""


def require(condition: bool, message: str) -> None:
    """对前置和基线错误使用可安全展示的固定消息。"""
    if not condition:
        raise BaselineError(message) from None


def save_mapping(path: Path, mapping: dict) -> None:
    """持环境锁时同目录刷盘后原子发布，绝不覆盖已存在映射。"""
    require(not path.exists(), "长期基线映射已存在，禁止覆盖或再次初始化")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(mapping, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        require(not path.exists(), "长期映射已存在，禁止覆盖")
        # 所有合法调用均持同一环境锁；Windows rename 也拒绝已有目标。
        os.rename(temporary, path)
    except (OSError, ValueError, TypeError):
        raise BaselineError("基线映射保存失败，停止并核查运行清单") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_mapping(path: Path, env_id: str, definition: dict) -> dict:
    """仅读取完整版本映射；缺失、重复键或结构错误不自动恢复。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object)
        require(
            isinstance(value, dict)
            and set(value)
            == {"env_id", "run_id", "version", "definition_sha256", "identity", "tickets"}
            and value["env_id"] == env_id
            and value["version"] == definition["version"]
            and value["definition_sha256"] == definition["sha256"]
            and isinstance(value["run_id"], str)
            and re.fullmatch(r"RUN_[0-9a-f]{32}", value["run_id"]) is not None
            and isinstance(value["tickets"], dict)
            and set(value["tickets"]) == {f"B{i:02}" for i in range(1, 10)},
            "基线映射版本或结构不符",
        )
        ticket_ids, article_ids = [], []
        for code, row in value["tickets"].items():
            require(
                isinstance(row, dict)
                and set(row) == {"id", "number", "title", "article_ids", "articles"}
                and valid_id(row["id"])
                and isinstance(row["number"], str)
                and row["number"].isdigit()
                and isinstance(row["title"], str)
                and value["run_id"] in row["title"]
                and isinstance(row["article_ids"], list)
                and all(valid_id(item) for item in row["article_ids"])
                and isinstance(row["articles"], list)
                and len(row["articles"]) == (5 if code == "B02" else 1)
                and row["article_ids"] == [article["id"] for article in row["articles"]],
                "基线对象映射不完整",
            )
            ticket_ids.append(row["id"])
            article_ids.extend(row["article_ids"])
        require(len(set(ticket_ids)) == 9 and len(set(article_ids)) == 13, "基线ID重复或数量不符")
        return value
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, RecursionError):
        raise BaselineError("基线映射缺失或损坏，禁止自动初始化或修复") from None


def preflight(clients: dict, search, run_config, definition: dict, db) -> dict:
    """核对固定账户、Token范围、有效组权限与无组织关系。"""
    db.check_search_access()
    db.check_permission_access()
    maintenance = clients["M"]
    roles = list_rows(maintenance, "api/v1/roles")
    group = unique(list_rows(maintenance, "api/v1/groups"), "name", search.group_name)
    m = read_json(maintenance, "api/v1/users/me")
    require(
        isinstance(m, dict)
        and m.get("login") == run_config.maintenance_login
        and m.get("active") is True
        and valid_id(m.get("id")),
        "维护身份不符",
    )
    m_names, m_groups = permissions(m, roles)
    require(
        {"admin", "ticket.agent"}.issubset(m_names)
        and "full" in m_groups.get(str(group["id"]), set()),
        "维护身份缺少GB准备清理权限",
    )
    mapping = {"group_id": group["id"], "maintenance_id": m["id"], "users": {}, "states": {}}
    for alias in ALIASES:
        role = "Agent" if alias.startswith("BA") else "Customer"
        login = getattr(search, f"{alias.lower()}_login")
        user_id = pure_actor(clients[alias], maintenance, login, role, db)
        user = read_json(clients[alias], "api/v1/users/me")
        stored = db.user(user_id)
        require(
            stored is not None and stored["login"] == login and stored["active"] is True,
            "基线账号持久化身份不符",
        )
        _, api_groups = permissions(user, roles)
        db_groups = {}
        for row in db.user_groups(user_id) + db.inherited_groups(user_id):
            db_groups.setdefault(str(row["group_id"]), set()).add(row["access"])
        api_groups = {key: grants for key, grants in api_groups.items() if grants}
        expected_groups = {str(group["id"]): {"full"}} if role == "Agent" else {}
        require(api_groups == db_groups == expected_groups, "基线身份具有额外组权限或缺少GB权限")
        require(
            not user.get("organization_id")
            and not user.get("organization_ids")
            and not stored["organization_id"]
            and not db.user_organizations(user_id),
            "基线身份出现组织关联，不允许自动修复",
        )
        mapping["users"][alias] = user_id
    require(len({m["id"], *mapping["users"].values()}) == 5, "基线四身份和维护身份必须独立")
    states = list_rows(maintenance, "api/v1/ticket_states")
    for name in ("new", "open", "closed"):
        mapping["states"][name] = unique(states, "name", name)["id"]
    mapping["priority_id"] = unique(
        list_rows(maintenance, "api/v1/ticket_priorities"), "name", definition["priority"]
    )["id"]
    return mapping


def wait_index(api, tickets: dict, *, budget: float = 60, interval: float = 2) -> None:
    """只确认九个编号已可见，不提前运行组合筛选、分页或客户权限场景。"""
    deadline = time.monotonic() + budget
    pending = dict(tickets)
    original = api.client.settings
    try:
        while pending:
            for code, row in list(pending.items()):
                remaining = deadline - time.monotonic()
                require(remaining > 0, "索引探测超时，原因未决；不能直接认定环境或产品问题")
                if original is not None:
                    api.client.settings = replace(
                        original,
                        connect_timeout=min(original.connect_timeout, remaining),
                        read_timeout=min(original.read_timeout, remaining),
                    )
                result = api.search({"query": f"number:{row['number']}", "per_page": 100})
                require(result.response.status_code == 200, "索引探测响应错误，停止等待并保留证据")
                values = result.get_json()
                require(
                    isinstance(values, list)
                    and all(isinstance(item, dict) and valid_id(item.get("id")) for item in values),
                    "索引探测结构错误",
                )
                require(
                    all(item["id"] == row["id"] for item in values) and len(values) <= 1,
                    "索引探测出现其他对象或重复结果，不按延迟处理",
                )
                if values:
                    del pending[code]
            if pending:
                remaining = deadline - time.monotonic()
                require(remaining > 0, "索引探测超时，原因未决；不能直接认定环境或产品问题")
                time.sleep(min(interval, remaining))
    finally:
        api.client.settings = original


class SearchBaseline:
    """管理一次九张工单初始化，完整后转为长期基线，检查阶段只读。"""

    def __init__(self, clients: dict, db, run, recorder, identity: dict, definition: dict) -> None:
        """保存显式身份、映射和当前运行，不创建业务对象。"""
        self.apis = {alias: TicketsAPI(client) for alias, client in clients.items()}
        self.db, self.run, self.recorder = db, run, recorder
        self.identity, self.definition = identity, definition
        self.entries = []

    def _article(self, marker: str, index: int, internal: bool) -> dict:
        """使用纯文本合成正文，不发送真实邮件。"""
        return {
            "body": f"{marker}_ARTICLE_{index}",
            "type": "note",
            "sender": "Agent",
            "content_type": "text/plain",
            "internal": internal,
        }

    def _expected(self, spec: dict, row: dict) -> dict:
        """由已确认定义与身份映射推导字段，不信任映射中的业务预期。"""
        return {
            "id": row["id"],
            "number": row["number"],
            "title": row["title"],
            "group_id": self.identity["group_id"],
            "customer_id": self.identity["users"][spec["customer"]],
            "owner_id": self.identity["users"][spec["owner"]],
            "state_id": self.identity["states"][spec["state"]],
            "priority_id": self.identity["priority_id"],
            "created_by_id": self.identity["maintenance_id"],
            "article_count": len(spec["articles"]),
        }

    def _create_ticket(self, spec: dict) -> dict:
        """先登记意图，再一次创建和追加；任何未知写入阻止冲突清理。"""
        marker = f"{self.run.run_id}_{spec['code']}"
        title = f"{self.definition['title_keyword'] if spec['code'] == 'B05' else 'SEARCH_BASELINE'} {marker}"
        provisional = {"id": 1, "number": "pending", "title": title}
        payload = self._expected(spec, provisional)
        for name in ("id", "number", "created_by_id", "article_count"):
            payload.pop(name)
        payload["article"] = self._article(marker, 1, False)
        self.recorder.ensure_complete()
        key = self.run.baseline_intent(
            marker,
            title,
            payload["group_id"],
            payload["customer_id"],
            payload["owner_id"],
            self.identity["maintenance_id"],
        )
        entry = {
            "key": key,
            "marker": marker,
            "payload": payload,
            "id": None,
            "unknown": True,
            "owned": False,
        }
        self.entries.append(entry)
        response = self.apis["M"].create(payload)
        value = response.get_json()
        require(
            response.response.status_code == 201
            and isinstance(value, dict)
            and valid_id(value.get("id")),
            "基线创建未明确成功，停止并保留清单",
        )
        require(
            value["id"] not in {item["id"] for item in self.entries if item is not entry},
            "基线创建返回重复ID",
        )
        entry["id"] = value["id"]
        require(isinstance(value.get("number"), str) and value["number"].isdigit(), "基线编号无效")
        ids = value.get("article_ids")
        require(isinstance(ids, list) and len(ids) == 1 and valid_id(ids[0]), "初始文章ID不完整")
        self.run.baseline_created(key, value["id"], ids)
        entry["unknown"] = False
        row = {
            "id": value["id"],
            "number": value["number"],
            "title": title,
            "article_ids": list(ids),
        }
        # 返回ID不证明归属；确认工单及初始文章属于本轮后才允许追加写入。
        self._confirm_owned(spec, row, marker)
        entry["owned"] = True
        for index, internal in enumerate(spec["articles"][1:], 2):
            entry["unknown"] = True
            result = self.apis["M"].create_article(
                {"ticket_id": row["id"], **self._article(marker, index, internal)}
            )
            added = result.get_json()
            require(
                result.response.status_code == 201
                and isinstance(added, dict)
                and valid_id(added.get("id"))
                and added["id"] not in row["article_ids"],
                "基线追加文章未明确成功",
            )
            entry["unknown"] = False
            expected_article = {
                "ticket_id": row["id"],
                "body": f"{marker}_ARTICLE_{index}",
                "internal": internal,
                "content_type": "text/plain",
                "created_by_id": self.identity["maintenance_id"],
            }
            require(
                all(added.get(name) == value for name, value in expected_article.items()),
                "基线追加响应归属或内容不符，禁止继续追加",
            )
            row["article_ids"].append(added["id"])
            self._confirm_owned(spec, row, marker)
            self.run.add_articles(key, row["article_ids"])
        articles = self.db.articles(row["id"])
        require(
            {item["id"] for item in articles} == set(row["article_ids"]), "基线文章保存集合不符"
        )
        row["articles"] = [
            next(item for item in articles if item["id"] == article_id)
            for article_id in row["article_ids"]
        ]
        return row

    def _confirm_owned(self, spec: dict, row: dict, marker: str) -> None:
        """任何后续写入前以API、DB和精确初始正文共同证明本轮归属。"""
        expected = self._expected(spec, row)
        expected["article_count"] = len(row["article_ids"])
        response = self.apis["M"].read(row["id"])
        current, stored = response.get_json(), self.db.ticket(row["id"])
        require(
            response.response.status_code == 200
            and isinstance(current, dict)
            and isinstance(stored, dict)
            and all(
                current.get(key) == value and stored.get(key) == value
                for key, value in expected.items()
            ),
            "基线追加前无法确认工单归属或持久化字段",
        )
        response = self.apis["M"].articles(row["id"])
        current, stored = response.get_json(), self.db.articles(row["id"])
        require(
            response.response.status_code == 200
            and isinstance(current, list)
            and all(isinstance(item, dict) for item in current)
            and len(current) == len(stored) == len(row["article_ids"])
            and {item.get("id") for item in current}
            == {item["id"] for item in stored}
            == set(row["article_ids"]),
            "基线追加前文章关联集合不符",
        )
        for index, article_id in enumerate(row["article_ids"], 1):
            actual = next(item for item in current if item["id"] == article_id)
            persisted = next(item for item in stored if item["id"] == article_id)
            expected_article = {
                "id": article_id,
                "ticket_id": row["id"],
                "body": f"{marker}_ARTICLE_{index}",
                "internal": spec["articles"][index - 1],
                "content_type": "text/plain",
                "created_by_id": self.identity["maintenance_id"],
            }
            require(
                all(
                    actual.get(key) == value and persisted.get(key) == value
                    for key, value in expected_article.items()
                )
                and actual.get("type") == "note"
                and actual.get("sender") == "Agent"
                and actual.get("type_id") == persisted.get("type_id")
                and actual.get("sender_id") == persisted.get("sender_id"),
                "基线初始或追加文章归属及内容不符",
            )

    def validate(self, mapping: dict, *, index: bool = True) -> None:
        """API和DB逐项核验固定关系、正文和可见性，发现漂移只报错。"""
        require(mapping["identity"] == self.identity, "基线身份或字典映射发生变化")
        rows = mapping["tickets"]
        require(
            set(self.db.group_ticket_ids(self.identity["group_id"]))
            == {row["id"] for row in rows.values()},
            "GB工单集合漂移",
        )
        for spec in self.definition["tickets"]:
            row = rows[spec["code"]]
            expected = self._expected(spec, row)
            database = self.db.ticket(row["id"])
            require(
                database is not None
                and all(database.get(key) == value for key, value in expected.items()),
                "基线工单DB字段漂移",
            )
            response = self.apis["BA1"].read(row["id"])
            current = response.get_json()
            require(
                response.response.status_code == 200
                and isinstance(current, dict)
                and all(current.get(key) == value for key, value in expected.items()),
                "基线工单API字段漂移",
            )
            db_articles = self.db.articles(row["id"])
            require(
                sorted(db_articles, key=lambda item: item["id"])
                == sorted(row["articles"], key=lambda item: item["id"]),
                "基线文章DB内容或关联漂移",
            )
            result = self.apis["BA1"].articles(row["id"])
            articles = result.get_json()
            require(
                result.response.status_code == 200
                and isinstance(articles, list)
                and all(isinstance(item, dict) for item in articles)
                and len(articles) == len(row["article_ids"])
                and {item.get("id") for item in articles} == set(row["article_ids"]),
                "基线客服文章集合不符",
            )
            for position, (article_id, internal) in enumerate(
                zip(row["article_ids"], spec["articles"], strict=True), 1
            ):
                actual = next(item for item in articles if item["id"] == article_id)
                db_article = next(item for item in db_articles if item["id"] == article_id)
                required = {
                    "id": article_id,
                    "ticket_id": row["id"],
                    "body": f"{mapping['run_id']}_{spec['code']}_ARTICLE_{position}",
                    "internal": internal,
                    "content_type": "text/plain",
                    "created_by_id": self.identity["maintenance_id"],
                }
                require(
                    all(
                        actual.get(key) == value and db_article.get(key) == value
                        for key, value in required.items()
                    )
                    and actual.get("type") == "note"
                    and actual.get("sender") == "Agent"
                    and actual.get("type_id") == db_article.get("type_id")
                    and actual.get("sender_id") == db_article.get("sender_id"),
                    "基线文章内容、作者或可见性错误",
                )
        b02 = rows["B02"]
        result = self.apis["BC1"].articles(b02["id"])
        public = result.get_json()
        require(
            result.response.status_code == 200
            and isinstance(public, list)
            and all(isinstance(item, dict) and item.get("internal") is False for item in public)
            and len(public) == 2
            and {item.get("id") for item in public} == set(b02["article_ids"][:2]),
            "B02客户公开文章集合错误",
        )
        for actual in public:
            expected = next(item for item in b02["articles"] if item["id"] == actual["id"])
            require(
                all(
                    actual.get(key) == expected.get(key)
                    for key in (
                        "body",
                        "ticket_id",
                        "internal",
                        "created_by_id",
                        "type_id",
                        "sender_id",
                        "content_type",
                    )
                ),
                "B02客户公开文章内容不符",
            )
        if index:
            wait_index(self.apis["BA1"], rows)
        self.recorder.record(
            "search-baseline-checked",
            {
                "version": mapping["version"],
                "ticket_count": 9,
                "article_count": 13,
                "result": "valid",
            },
        )
        self.recorder.ensure_complete()

    def initialize(self, path: Path, env_id: str) -> dict:
        """只初始化一次，完成全部验证后原子保存并登记长期保留。"""
        require(not path.exists(), "长期基线已存在，禁止再次初始化")
        require(
            not self.db.group_ticket_ids(self.identity["group_id"]),
            "GB已有工单，禁止将既有数据接管或覆盖",
        )
        mapping = {
            "env_id": env_id,
            "run_id": self.run.run_id,
            "version": self.definition["version"],
            "definition_sha256": self.definition["sha256"],
            "identity": self.identity,
            "tickets": {},
        }
        try:
            for spec in self.definition["tickets"]:
                mapping["tickets"][spec["code"]] = self._create_ticket(spec)
            self.validate(mapping)
            save_mapping(path, mapping)
            self.run.baseline_retained([entry["key"] for entry in self.entries], path)
            return mapping
        except Exception as error:
            self.recorder.record(
                "search-initialization-failed",
                {
                    "reason": str(error)
                    if isinstance(error, BaselineError)
                    else "接口、数据库或清单操作异常",
                    "mapping_published": path.exists(),
                },
            )
            # 映射已发布时，禁止删除可能已完整建成的长期基线。
            if path.exists():
                raise BaselineError("基线提交未完整结束，保留映射和清单，需人工核查") from None
            self.cleanup()
            raise BaselineError("基线初始化失败；已清理可确认对象，详见证据及运行清单") from None

    def cleanup(self) -> None:
        """逐个清理本次明确归属对象，未知写入保留并继续独立对象。"""
        failed = False
        for entry in reversed(self.entries):
            key = entry["key"]
            if entry["unknown"] or entry["id"] is None:
                self.run.attention(key, "create_unknown")
                failed = True
                continue
            if not entry["owned"]:
                self.run.attention(key, "ownership_unconfirmed")
                failed = True
                continue
            try:
                response = self.apis["M"].read(entry["id"])
                value = response.get_json()
                expected = {
                    name: entry["payload"][name]
                    for name in ("title", "group_id", "customer_id", "owner_id")
                }
                expected.update(id=entry["id"], created_by_id=self.identity["maintenance_id"])
                require(
                    response.response.status_code == 200
                    and isinstance(value, dict)
                    and all(value.get(name) == item for name, item in expected.items()),
                    "基线清理归属无法确认",
                )
                articles = self.db.articles(entry["id"])
                require(
                    bool(articles)
                    and all(
                        item["created_by_id"] == self.identity["maintenance_id"]
                        and item["body"].startswith(entry["marker"] + "_ARTICLE_")
                        for item in articles
                    ),
                    "基线清理文章归属无法确认",
                )
                self.run.add_articles(key, [item["id"] for item in articles])
                result = self.apis["M"].delete(entry["id"])
                require(
                    result.response.status_code == 200
                    and self.apis["M"].read(entry["id"]).response.status_code == 404
                    and self.db.ticket(entry["id"]) is None
                    and not self.db.articles(entry["id"]),
                    "基线清理未完成",
                )
                self.run.cleaned(key)
            except Exception:
                self.run.attention(key, "cleanup_failed")
                failed = True
        require(not failed, "基线初始化失败且存在需人工处理的清单，不得自动重建")
