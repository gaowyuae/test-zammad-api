"""M23/M24只读搜索场景，复用已核验基线而不自动造数或修复。"""

from copy import deepcopy
from pathlib import Path

import yaml

from api.tickets import TicketsAPI
from assertions.search import assert_ids, assert_saved, search_rows
from assertions.tickets import check
from support.config import ConfigError, _ConfigLoader

CASE_IDS = frozenset(f"TC_{value}" for value in range(109, 119))


def load_search_cases(path: Path | None = None) -> dict:
    """核对Excel十条身份、逻辑目标及分页参数，动态ID不写入YAML。"""
    path = path or Path(__file__).resolve().parents[1] / "data/cases/search.yaml"
    contracts = {
        "TC_109": ("BA1", "number", ["B05"]),
        "TC_110": ("BA1", "closed", ["B01", "B02", "B03", "B04"]),
        "TC_111": ("BC1", "number", []),
        "TC_112": ("BA1", "missing_number", []),
        "TC_113": ("BA1", "customer", ["B01", "B02", "B05", "B06", "B07", "B09"]),
        "TC_114": ("BA1", "owner_new", ["B06", "B07"]),
        "TC_115": ("Ø", "number", None),
        "TC_116": ("BA1", "title", ["B05"]),
        "TC_117": ("BA1", "number", ["B05"]),
        "TC_118": ("A1", "missing_id", None),
    }
    try:
        rows = yaml.load(path.read_text(encoding="utf-8"), Loader=_ConfigLoader)
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        for case_id, (actor, query_kind, codes) in contracts.items():
            expected = {"status_code": 200, "codes": codes}
            if case_id == "TC_115":
                expected = {"status_class": 4, "error_kind": "authentication"}
            elif case_id == "TC_118":
                expected = {"status_code": 404, "error_kind": "missing"}
            contract = {"actor": actor, "query_kind": query_kind, "expected": expected}
            if case_id in {"TC_109", "TC_111", "TC_115", "TC_116", "TC_117"}:
                contract["target"] = "B08" if case_id == "TC_111" else "B05"
            if case_id == "TC_113":
                contract["input"] = {"per_page": 2, "page": 1, "sort_by": "id", "order_by": "asc"}
            if case_id == "TC_117":
                contract["repetitions"] = 3
            if rows[case_id] != contract:
                raise ValueError
        return deepcopy(rows)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise ConfigError("搜索场景数据不符合原始用例") from None


class SearchCase:
    """持有本条独立会话和数据库快照，正式请求不轮询、不写业务数据。"""

    def __init__(self, clients, db, context, mapping, data, recorder) -> None:
        """使用批次预检后的长期映射，按用例实际身份选择独立客户端。"""
        self.apis = {name: TicketsAPI(client) for name, client in clients.items()}
        self.db, self.context, self.recorder = db, context, recorder
        self.mapping, self.data = mapping, deepcopy(data)
        self.identity, self.definition = context.search_identity, context.search_definition
        self.api = self.apis[data["actor"]]
        self.tickets = mapping["tickets"]
        self.stored = self.snapshot("search-before")

    def snapshot(self, label: str) -> dict[int, dict]:
        """保存专用组完整DB字段，用于精确集合及保存值比较。"""
        ids = self.db.group_ticket_ids(self.identity["group_id"])
        rows = {ticket_id: self.db.ticket(ticket_id) for ticket_id in ids}
        self.recorder.record(label, rows)
        check(set(rows) == {row["id"] for row in self.tickets.values()}, "专用组基线集合漂移")
        check(all(isinstance(row, dict) for row in rows.values()), "基线快照缺少工单")
        return rows

    def query(self) -> str:
        """只替换明确逻辑映射，不解释YAML表达式。"""
        kind = self.data["query_kind"]
        if kind == "number":
            return f"number:{self.tickets[self.data['target']]['number']}"
        if kind == "closed":
            return f"state_id:{self.identity['states']['closed']}"
        if kind == "customer":
            return f"customer_id:{self.identity['users']['BC1']}"
        if kind == "owner_new":
            return (
                f"owner_id:{self.identity['users']['BA1']} AND "
                f"state_id:{self.identity['states']['new']}"
            )
        if kind == "title":
            return f"title:{self.definition['title_keyword']}"
        if kind == "missing_number":
            number = str(int(self.context.run.run_id.removeprefix("RUN_"), 16))
            check(not self.db.ticket_number_exists(number), "无命中编号实际已存在")
            self.recorder.record("missing-number", {"number": number, "exists": False})
            return f"number:{number}"
        raise ConfigError("当前场景不是搜索查询") from None

    def request(self, *, page: int = 1, per_page: int = 100):
        """一次GET保留原始响应；分页参数之外不调整用例查询。"""
        params = {"query": self.query(), "page": page, "per_page": per_page}
        if "input" in self.data:
            params.update({key: self.data["input"][key] for key in ("sort_by", "order_by")})
        return self.api.search(params)

    def assert_result(self, result, *, ordered: bool = False) -> list[dict]:
        """核对完整目标集合及每项DB字段，而不是只取搜索首项。"""
        rows = search_rows(result)
        expected = sorted(self.tickets[code]["id"] for code in self.data["expected"]["codes"])
        assert_ids(rows, expected, ordered=ordered)
        assert_saved(rows, self.stored)
        return rows

    def paginated(self) -> list[dict]:
        """按原用例逐页至空页；异常不停页时有界失败而不无限请求。"""
        merged = []
        # 专用组只有九张，最多十页已覆盖每页仅一张的异常响应并留出空页。
        for page in range(1, len(self.tickets) + 2):
            rows = search_rows(self.request(page=page, per_page=self.data["input"]["per_page"]))
            check(len(rows) <= 2, "每页搜索结果超过per_page")
            self.recorder.record("search-page", {"page": page, "ids": [row["id"] for row in rows]})
            merged.extend(rows)
            if not rows:
                expected = sorted(
                    self.tickets[code]["id"] for code in self.data["expected"]["codes"]
                )
                assert_ids(merged, expected, ordered=True)
                assert_saved(merged, self.stored)
                return merged
        raise AssertionError("分页未在专用基线范围内返回空页") from None

    def forbidden(self, code: str) -> list[str]:
        """目标标题和文章正文供权限场景验证不回显。"""
        row = self.tickets[code]
        return [row["title"], *(article["body"] for article in row["articles"])]

    def finish(self) -> None:
        """只读场景不删除长期基线，结束确认既有证据完整。"""
        self.recorder.record("cleanup", {"result": "not-required", "scope": "read-only-baseline"})
        self.recorder.ensure_complete()
