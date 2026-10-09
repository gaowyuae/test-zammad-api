"""状态更新的独立数据、动态字典及最小请求构造。"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from fixtures.pilot import PilotCase
from fixtures.preflight import PreflightError, list_rows, preflight, read_json, unique
from support.config import ConfigError, _ConfigLoader
from support.run_config import RunConfig

ROOT = Path(__file__).resolve().parents[1]
CASE_IDS = frozenset({"TC_069", "TC_070", "TC_071"})


def load_state_case(case_id: str) -> tuple[dict, dict]:
    """深拷贝 TC_023 并限制为三条已批准的状态场景。"""
    try:
        rows = yaml.load(
            (ROOT / "data/cases/ticket_state.yaml").read_text(encoding="utf-8"),
            Loader=_ConfigLoader,
        )
        if not isinstance(rows, dict) or set(rows) != CASE_IDS:
            raise ValueError
        scenario = rows[case_id]
        if scenario != {
            "actor": "A1",
            "expected": {"status_code": 422 if case_id == "TC_069" else 200},
        }:
            raise ValueError
        data = deepcopy(
            yaml.load(
                (ROOT / "data/cases/ticket_create.yaml").read_text(encoding="utf-8"),
                Loader=_ConfigLoader,
            )
        )
        if not isinstance(data, dict) or not isinstance(data.get("article"), dict):
            raise ValueError
        data.update(
            case_id=case_id,
            expected=deepcopy(scenario["expected"]),
            state_name="pending reminder" if case_id == "TC_071" else "open",
        )
        return data, deepcopy(scenario)
    except (KeyError, OSError, ValueError, TypeError, yaml.YAMLError):
        raise ConfigError("状态场景数据缺失或结构无效") from None


def state_preflight(agent, maintenance, config: RunConfig, data: dict, case_id: str) -> dict:
    """写入前确认 A1 能力、启用状态及合法而不同的新优先级。"""
    if case_id not in CASE_IDS:
        raise PreflightError("不支持的状态场景") from None
    mapping = preflight(agent, maintenance, config, data)
    read_json(agent, "api/v1/ticket_create")
    states = list_rows(maintenance, "api/v1/ticket_states")
    if case_id == "TC_069":
        invalid_id = max(row["id"] for row in states) + 1000
        result = maintenance.request("GET", f"api/v1/ticket_states/{invalid_id}")
        if result.response.status_code != 404:
            raise PreflightError("无法确认非法状态确实不存在") from None
        priority = unique(list_rows(maintenance, "api/v1/ticket_priorities"), "name", "3 high")
        if priority["id"] == mapping["priority_id"]:
            raise PreflightError("目标优先级必须合法且不同于初始值") from None
        mapping.update(invalid_state_id=invalid_id, target_priority_id=priority["id"])
    else:
        target = unique(states, "name", "pending reminder" if case_id == "TC_070" else "open")
        if target["id"] == mapping["state_id"]:
            raise PreflightError("起止状态必须不同") from None
        mapping["target_state_id"] = target["id"]
    return mapping


class StateCase(PilotCase):
    """沿用写入及清理保护，071自行准备挂起工单。"""

    def __init__(self, agent, maintenance, run, mapping, data, recorder, db) -> None:
        """按运行时 UTC 整分钟生成未来24小时时刻。"""
        super().__init__(agent, maintenance, run, mapping, data, recorder, db)
        self.pending_at = (
            datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(hours=24)
        ).isoformat()
        if self.case_id == "TC_071":
            self.payload["pending_time"] = self.pending_at


def update_payload(case: StateCase) -> dict:
    """只构造原用例提交字段，071绝不主动补空挂起时间。"""
    if case.case_id == "TC_069":
        return {
            "state_id": case.mapping["invalid_state_id"],
            "priority_id": case.mapping["target_priority_id"],
        }
    payload = {"state_id": case.mapping["target_state_id"]}
    if case.case_id == "TC_070":
        payload["pending_time"] = case.pending_at
    return payload
