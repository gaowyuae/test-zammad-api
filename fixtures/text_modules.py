"""M20文本模块的可见正文核对、权限隔离及独立资源清理。"""

import json
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import yaml

from api.text_modules import TextModulesAPI
from assertions.tickets import check
from fixtures.config_checks import assert_ownership, new_object_id, pure_actor
from fixtures.preflight import PreflightError
from fixtures.ticket import CleanupError, valid_id
from fixtures.users import preflight_users
from support.config import ConfigError, _ConfigLoader

CASE_IDS = frozenset({"TC_171", "TC_172", "TC_173", "TC_174"})


class _VisibleText(HTMLParser):
    """仅提取普通HTML可见字符，不依赖text2html生成的具体标签。"""

    def __init__(self) -> None:
        """保留正文数据和块级换行，不执行HTML内容。"""
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        """忽略脚本样式文本并保留显式换行。"""
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag == "br" and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """块结束保留换行，避免连接两个本来分开的段落。"""
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"div", "p", "li"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        """收集可见文本，实体已经由HTMLParser解码。"""
        if not self.hidden:
            self.parts.append(data)


def visible_text(value: str) -> str:
    """将服务端HTML转换回可见正文，忽略包装标签的尾部换行。"""
    check(isinstance(value, str), "文本模块正文不是字符串")
    parser = _VisibleText()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts).strip()


def load_text_module_cases(path: Path | None = None) -> dict:
    """固定四条原始输入，note在运行时按明确长度生成。"""
    path = path or Path(__file__).resolve().parents[1] / "data/cases/text_modules.yaml"
    try:
        rows = yaml.load(path.read_text(encoding="utf-8"), Loader=_ConfigLoader)
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        for case_id, data in rows.items():
            fields = {"actor", "name_template", "input", "expected"}
            if case_id in {"TC_171", "TC_174"}:
                expected_input = {
                    "keywords": "qahello",
                    "content": "您好，已受理您的问题。",
                    "active": True,
                    "group_ids": [],
                }
            else:
                fields.add("note_length")
                expected_input = {"content": "有效正文", "active": True}
                if type(data["note_length"]) is not int or data["note_length"] != (
                    250 if case_id == "TC_172" else 251
                ):
                    raise ValueError
            expected = {
                "TC_171": {"status_code": 200},
                "TC_172": {"status_code": 201},
                "TC_173": {"status_class": 4, "error_kind": "note_length"},
                "TC_174": {"status_class": 4, "error_kind": "permission"},
            }[case_id]
            if (
                set(data) != fields
                or data["actor"] != ("C1" if case_id == "TC_174" else "AD")
                or data["name_template"] != "{run_id}_{case_id}_text_module"
                or data["input"] != expected_input
                or data["input"]["active"] is not True
                or data["expected"] != expected
            ):
                raise ValueError
        return deepcopy(rows)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise ConfigError("文本模块场景数据不符合原始输入") from None


def preflight_text_modules(ad, maintenance, customer, context, db) -> dict:
    """核实AD/M以及仅174所需C1的真实Customer角色及可用scope。"""
    db.check_text_module_access()
    mapping = preflight_users(ad, maintenance, context, db)
    if customer is not None:
        mapping["customer_id"] = pure_actor(
            customer, maintenance, context.config.customer_login, "Customer", db
        )
    return mapping


def assert_text(value: object, expected: dict, object_id: int, creator_id: int) -> None:
    """普通字段严格比较，HTML正文按实际可见内容比较。"""
    __tracebackhide__ = True
    fields = {
        "id": object_id,
        "created_by_id": creator_id,
        **{key: val for key, val in expected.items() if key not in {"content", "group_ids"}},
    }
    check(
        isinstance(value, dict)
        and all(key in value and value[key] == val for key, val in fields.items()),
        "文本模块名称、备注或属性保存不符",
    )
    check(value.get("active") is expected["active"], "文本模块启用状态不符")
    check(visible_text(value.get("content")) == expected["content"], "文本模块可见正文改变")


def assert_no_content_leak(result, original: str) -> None:
    """扫描原始字节、JSON解码及嵌套HTML可见文本，禁止正文泄露。"""
    __tracebackhide__ = True
    value = result.get_json()
    candidates = [
        result.response.content.decode("utf-8", errors="replace"),
        json.dumps(value, ensure_ascii=False),
    ]

    def collect(item) -> None:
        """递归遍历解码值，检测正文被改名或嵌套后的泄露。"""
        if isinstance(item, str):
            candidates.extend((item, visible_text(item)))
        elif isinstance(item, dict):
            for key, child in item.items():
                check(
                    str(key).casefold() != "content" or child in (None, "", [], {}),
                    "客户拒绝响应包含非空正文内容",
                )
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)

    collect(value)
    normalized = "".join(original.split())
    check(
        not any(normalized in "".join(candidate.split()) for candidate in candidates),
        "客户拒绝响应泄露文本模块正文",
    )


class TextModuleCase:
    """每条独立准备文本模块，原始正文和备注从不按实测放宽。"""

    def __init__(self, ad, maintenance, customer, context, mapping, data, recorder, db) -> None:
        """仅注入唯一名称和指定note长度，其他字段保持输入原样。"""
        self.admin, self.maintenance = TextModulesAPI(ad), TextModulesAPI(maintenance)
        self.actor = TextModulesAPI(customer if data["actor"] == "C1" else ad)
        self.run, self.mapping, self.data, self.recorder, self.db = (
            context.run,
            mapping,
            deepcopy(data),
            recorder,
            db,
        )
        self.case_id = data["case_id"]
        self.marker = f"{self.run.run_id}_{self.case_id}_text_module"
        self.name = self.marker
        self.creator_id = mapping["admin_id"]
        self.payload = {**deepcopy(data["input"]), "name": self.name}
        if "note_length" in data:
            self.payload["note"] = "A" * data["note_length"]
        self.key = self.object_id = None
        self.write_unknown = self.integrity_unknown = False
        self.baseline = []
        self.before = None

    def snapshot(self, label: str) -> dict:
        """维护详情和只读数据库同时取证，组关联不由默认空集合代替。"""
        result = self.maintenance.get(self.object_id)
        value = {
            "status": result.response.status_code,
            "api": result.get_json(),
            "db": self.db.text_module(self.object_id),
            "groups": self.db.text_module_groups(self.object_id),
            "ids": self.db.text_module_ids(),
        }
        self.recorder.record(label, value)
        return value

    def owned(self) -> dict:
        """本轮唯一名称、创建人和新ID必须在API与DB一致。"""
        if not valid_id(self.object_id) or self.object_id in self.baseline:
            raise CleanupError("文本模块不是本条新对象") from None
        value = self.snapshot("text-module-ownership")
        assert_ownership(value, self.object_id, self.name, self.creator_id)
        return value

    def create(self):
        """先登记后POST，251截断等异常新增也登记但保留业务失败。"""
        if self.key is not None:
            raise CleanupError("文本模块创建不可重复提交") from None
        self.baseline = self.db.text_module_ids()
        self.before = self.db.text_module_inventory()
        if self.db.text_modules_named(self.name):
            raise PreflightError("本轮文本模块名称已存在") from None
        self.recorder.ensure_complete()
        self.key = self.run.config_intent(
            "text_module", self.case_id, self.marker, self.name, self.creator_id
        )
        self.write_unknown = True
        result = self.admin.create(self.payload)
        self.write_unknown = result.response.status_code >= 500
        self.integrity_unknown = True
        after = self.db.text_module_ids()
        self.recorder.record(
            "text-module-create-inventory",
            {
                "before": self.baseline,
                "after": after,
                "status": result.response.status_code,
                "inventory": self.db.text_module_inventory(),
            },
        )
        if self.write_unknown:
            raise CleanupError("文本模块写入结果未知") from None
        self.object_id = new_object_id(result, self.baseline, after)
        if self.object_id is not None:
            self.owned()
            self.run.config_created(self.key, self.object_id)
        self.integrity_unknown = False
        return result

    def detail_as_actor(self):
        """正式被测请求仅GET本条详情，不发列表请求。"""
        return self.actor.get(self.object_id)

    def assert_saved(self, snapshot: dict) -> None:
        """API、DB正文和可选组关联必须同时满足原预期。"""
        __tracebackhide__ = True
        check(snapshot["status"] == 200, "维护身份读取文本模块失败")
        for value in (snapshot["api"], snapshot["db"]):
            assert_text(value, self.payload, self.object_id, self.creator_id)
        check(
            snapshot["api"].get("group_ids") == [] and snapshot["groups"] == [],
            "文本模块出现额外组限制",
        )

    def assert_unchanged_others(self) -> None:
        """排除本条配置后，原模块及组关联必须完整不变。"""
        check(
            self.db.text_module_inventory(exclude_id=self.object_id) == self.before,
            "原文本模块或组关联被修改",
        )
        expected = set(self.baseline) | ({self.object_id} if self.object_id is not None else set())
        check(set(self.db.text_module_ids()) == expected, "文本模块集合出现额外变化")

    def cleanup(self) -> None:
        """只删除本条已确认模块，DB对象及组关联消失才标cleaned。"""
        if self.key is None:
            return
        try:
            self.recorder.ensure_complete()
            if self.write_unknown or self.integrity_unknown:
                raise CleanupError("文本模块写入未知或归属不明")
            if self.object_id is None:
                if (
                    self.case_id != "TC_173"
                    or self.db.text_module_ids() != self.baseline
                    or self.db.text_module_inventory() != self.before
                ):
                    raise CleanupError("文本模块负向请求无法证明无新增")
                self.run.config_absent(self.key)
                return
            self.owned()
            self.recorder.ensure_complete()
            self.write_unknown = True
            result = self.maintenance.delete(self.object_id)
            self.write_unknown = result.response.status_code >= 500
            if (
                result.response.status_code != 200
                or self.maintenance.get(self.object_id).response.status_code != 404
                or self.db.text_module(self.object_id) is not None
                or self.db.text_module_groups(self.object_id)
            ):
                raise CleanupError("文本模块删除后对象或组关联未消失")
            if (
                self.db.text_module_ids() != self.baseline
                or self.db.text_module_inventory() != self.before
            ):
                raise CleanupError("文本模块清理后原集合不符")
            self.recorder.ensure_complete()
            self.run.cleaned(self.key)
            self.recorder.record("text-module-cleanup", {"id": self.object_id, "result": "cleaned"})
        except Exception:
            self.run.attention(
                self.key, "create_unknown" if self.write_unknown else "cleanup_failed"
            )
            raise CleanupError("文本模块清理未完成，已保留定位信息") from None
