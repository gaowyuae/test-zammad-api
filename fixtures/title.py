"""标题输入保真及无标题孤儿工单的保守清理核查。"""

from pathlib import Path

import yaml

from fixtures.pilot import PilotCase
from fixtures.ticket import CleanupError
from support.config import ConfigError, _ConfigLoader

ROOT = Path(__file__).resolve().parents[1]
NEGATIVE_CASES = frozenset({"TC_026", "TC_027", "TC_037"})


def load_title_case(case_id: str) -> tuple[dict, dict]:
    """合并固定七条场景；缺失字段保持缺失，不解释表达式。"""
    try:
        rows = yaml.load(
            (ROOT / "data/cases/ticket_titles.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(rows, dict) or set(rows) != {
            "TC_026",
            "TC_027",
            "TC_028",
            "TC_029",
            "TC_035",
            "TC_036",
            "TC_037",
        }:
            raise ValueError
        scenario = rows[case_id]
        if not isinstance(scenario, dict) or not isinstance(scenario.get("expected"), dict):
            raise ValueError
        fields = {"expected"} if case_id == "TC_026" else {"title", "expected"}
        if case_id == "TC_029":
            fields.add("article")
        if set(scenario) != fields:
            raise ValueError
        data = yaml.load(
            (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(data, dict) or not isinstance(data.get("article"), dict):
            raise ValueError
        data.update(case_id=case_id, expected=scenario["expected"])
        data.pop("title", None)
        if "title" in scenario:
            if not isinstance(scenario["title"], str):
                raise ValueError
            data["title"] = scenario["title"]
        if case_id == "TC_029":
            article = scenario["article"]
            if (
                not isinstance(article, dict)
                or set(article) != {"subject", "body"}
                or not all(isinstance(value, str) for value in article.values())
            ):
                raise ValueError
            data["article"].update(subject=article["subject"], body=article["body"])
        return data, scenario
    except (KeyError, OSError, ValueError, TypeError, yaml.YAMLError, ConfigError):
        raise ConfigError("标题场景数据文件缺失或结构无效") from None


class TitleCase(PilotCase):
    """精确保留标题，额外检查可能未附带文章的异常工单。"""

    def __init__(self, agent, maintenance, run, mapping, data, recorder, db) -> None:
        """正文保持 RUN 标记，标题不追加任何内容。"""
        super().__init__(agent, maintenance, run, mapping, data, recorder, db, mark_title=False)
        self.creation_baseline: list[int] | None = None

    def scoped_ticket_ids(self) -> list[int]:
        """只查当前专用组、客户和创建人的工单 ID。"""
        return self.db.ticket_ids_for_creator(
            self.mapping["group_id"], self.mapping["customer_id"], self.creator_id
        )

    def create(self):
        """负向创建在发请求前保存范围集合，查库失败则不写入。"""
        if self.case_id in NEGATIVE_CASES:
            self.creation_baseline = self.scoped_ticket_ids()
        return super().create()

    def cleanup(self) -> None:
        """明确拒绝仍需排除孤儿工单；未知写入保持父类的人工处理边界。"""
        if (
            self.key is not None
            and self.case_id in NEGATIVE_CASES
            and self.ticket_id is None
            and not self.write_unknown
            and self.create_status is not None
            and 400 <= self.create_status < 500
        ):
            problem = None
            try:
                if (
                    self.creation_baseline is None
                    or self.scoped_ticket_ids() != self.creation_baseline
                ):
                    problem = "专用范围工单集合改变，不能确认负向请求无残留"
            except Exception:
                problem = "负向清理的数据库证据缺失"
            if problem:
                self.run.attention(self.key, "ownership_unconfirmed")
                raise CleanupError(problem) from None
        super().cleanup()
