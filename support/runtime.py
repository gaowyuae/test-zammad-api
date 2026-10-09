"""用主机文件锁和持续清单保护单环境的工单测试。"""

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from types import TracebackType
from typing import BinaryIO
from uuid import uuid4

REASONS = frozenset(
    {"create_unknown", "ownership_unconfirmed", "cleanup_failed", "persistence_failed"}
)
SUPPORTED_CASES = frozenset(
    {
        "TC_023",
        "TC_025",
        "TC_026",
        "TC_027",
        "TC_028",
        "TC_029",
        "TC_030",
        "TC_031",
        "TC_032",
        "TC_033",
        "TC_034",
        "TC_035",
        "TC_036",
        "TC_037",
        "TC_058",
        "TC_059",
        "TC_060",
        "TC_061",
        "TC_062",
        "TC_063",
        "TC_064",
        "TC_065",
        "TC_066",
        "TC_067",
        "TC_068",
        "TC_069",
        "TC_070",
        "TC_071",
        "TC_072",
        "TC_087",
        "TC_088",
        "TC_089",
        "TC_090",
        "TC_091",
        "TC_092",
        "TC_093",
        "TC_094",
        "TC_095",
        "TC_096",
        "TC_143",
        "TC_144",
        "TC_190",
    }
)
NEGATIVE_CREATION_CASES = frozenset(
    {"TC_025", "TC_026", "TC_027", "TC_030", "TC_031", "TC_032", "TC_033", "TC_034", "TC_037"}
)
FINISHED = frozenset({"cleaned", "absent", "retained"})
ROLE_CASES = frozenset({"TC_145", "TC_146", "TC_147", "TC_148"})
ROLE_RETENTION = "m18-inactive-empty-unassigned-v1"
CONFIG_CASES = {
    "template": frozenset({"TC_159", "TC_160", "TC_161", "TC_162"}),
    "text_module": frozenset({"TC_171", "TC_172", "TC_173", "TC_174"}),
    "macro": frozenset({"TC_187", "TC_188", "TC_189", "TC_190"}),
}
USER_CASES = frozenset(
    {"TC_137", "TC_138", "TC_139", "TC_140", "TC_141", "TC_142", "TC_143", "TC_144"}
)
TOKEN_CASES = frozenset({"TC_141", "TC_143", "TC_144"})


def _valid_token(resource: dict, run_id: str) -> bool:
    """Token清单只记录非敏感名称、ID及归属，绝不存Token值。"""
    if resource.keys() != {
        "type",
        "case_id",
        "marker",
        "user_id",
        "name",
        "token_id",
        "state",
        "reason",
    }:
        return False
    token_id, state, reason = resource["token_id"], resource["state"], resource["reason"]
    return (
        resource["case_id"] in TOKEN_CASES
        and _safe_marker(resource["marker"], run_id)
        and _safe_marker(resource["name"], run_id)
        and _positive_id(resource["user_id"])
        and (token_id is None or _positive_id(token_id))
        and state in ("intent", "created", "cleaned", "attention", "absent")
        and (reason is None or isinstance(reason, str) and reason in REASONS)
        and (state not in ("created", "cleaned") or token_id is not None)
        and (state not in ("intent", "absent") or token_id is None)
        and (state != "attention" or reason is not None)
        and (state != "absent" or reason is None)
    )


def user_login(run_id: str, case_id: str) -> str:
    """将十六进制RUN数字可逆映射为g～p，避免CTI将随机串识别为电话。"""
    return f"{run_id}_{case_id}@example.invalid".lower().translate(
        str.maketrans("0123456789", "ghijklmnop")
    )


def _valid_user(resource: dict, run_id: str) -> bool:
    """只接受本项目临时用户的非敏感定位信息和明确状态。"""
    if resource.keys() != {"type", "case_id", "marker", "login", "user_id", "state", "reason"}:
        return False
    user_id, state, reason = resource["user_id"], resource["state"], resource["reason"]
    login = resource["login"]
    return (
        resource["case_id"] in USER_CASES
        and _safe_marker(resource["marker"], run_id)
        and isinstance(login, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1,160}@example\.invalid", login) is not None
        # 兼容已有清单；新用户使用无数字的可逆 RUN 派生登录名。
        and (run_id.lower() in login.lower() or login == user_login(run_id, resource["case_id"]))
        and (user_id is None or _positive_id(user_id))
        and state in ("intent", "created", "cleaned", "attention", "absent")
        and (reason is None or isinstance(reason, str) and reason in REASONS)
        and (state not in ("created", "cleaned") or user_id is not None)
        and (state != "intent" or user_id is None)
        and (state != "attention" or reason is not None)
        and (
            state != "absent"
            or resource["case_id"] == "TC_138"
            and user_id is None
            and reason is None
        )
    )


def _positive_id(value: object) -> bool:
    """严格判断 ID，避免将布尔值当作整数。"""
    return type(value) is int and value > 0


def _safe_marker(value: object, run_id: str) -> bool:
    """只接受含本轮 RUN 的非敏感定位标记。"""
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value) is not None
        and run_id in value
    )


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    """拒绝清单中的重复键，防止后项掩盖残留状态。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("清单存在重复键")
        result[key] = value
    return result


def _valid_resource(resource: object, run_id: str) -> bool:
    """校验固定工单清单格式和状态所需的实际 ID。"""
    if isinstance(resource, dict) and resource.get("type") == "search_baseline":
        return _valid_baseline(resource, run_id)
    if isinstance(resource, dict) and resource.get("type") == "user":
        return _valid_user(resource, run_id)
    if isinstance(resource, dict) and resource.get("type") == "token":
        return _valid_token(resource, run_id)
    if isinstance(resource, dict) and resource.get("type") == "role":
        return _valid_role(resource, run_id)
    if isinstance(resource, dict) and resource.get("type") in CONFIG_CASES:
        return _valid_config_resource(resource, run_id)
    if not isinstance(resource, dict) or resource.keys() != {
        "type",
        "case_id",
        "marker",
        "ticket_id",
        "article_ids",
        "state",
        "reason",
    }:
        return False
    ticket_id = resource["ticket_id"]
    article_ids = resource["article_ids"]
    state = resource["state"]
    reason = resource["reason"]
    return (
        resource["type"] == "ticket"
        and resource["case_id"] in SUPPORTED_CASES
        and _safe_marker(resource["marker"], run_id)
        and (ticket_id is None or _positive_id(ticket_id))
        and isinstance(article_ids, list)
        and all(_positive_id(value) for value in article_ids)
        and len(article_ids) == len(set(article_ids))
        and state in ("intent", "created", "cleaned", "attention", "absent")
        and (reason is None or isinstance(reason, str) and reason in REASONS)
        and (state not in ("created", "cleaned") or ticket_id is not None)
        and (state != "intent" or ticket_id is None and not article_ids)
        and (state != "attention" or reason is not None)
        and (
            state != "absent"
            or resource["case_id"] in NEGATIVE_CREATION_CASES
            and ticket_id is None
            and not article_ids
            and reason is None
        )
    )


def _valid_baseline(resource: dict, run_id: str) -> bool:
    """长期基线独立登记真实关系，不能套用业务TC或角色例外。"""
    if set(resource) != {
        "type",
        "marker",
        "name",
        "group_id",
        "customer_id",
        "owner_id",
        "creator_id",
        "ticket_id",
        "article_ids",
        "state",
        "reason",
        "mapping_name",
    }:
        return False
    state, ticket_id, ids = resource["state"], resource["ticket_id"], resource["article_ids"]
    return (
        _safe_marker(resource["marker"], run_id)
        and isinstance(resource["name"], str)
        and 0 < len(resource["name"]) <= 250
        and all(
            _positive_id(resource[key])
            for key in ("group_id", "customer_id", "owner_id", "creator_id")
        )
        and (ticket_id is None or _positive_id(ticket_id))
        and isinstance(ids, list)
        and all(_positive_id(value) for value in ids)
        and len(ids) == len(set(ids))
        and state in {"intent", "created", "cleaned", "attention", "retained"}
        and (state not in {"created", "cleaned", "retained"} or ticket_id is not None)
        and (state != "intent" or ticket_id is None and not ids)
        and (resource["reason"] is None or resource["reason"] in REASONS)
        and (state != "attention" or resource["reason"] is not None)
        and (state not in {"cleaned", "retained"} or resource["reason"] is None)
        and resource["mapping_name"] == ("search-baseline.json" if state == "retained" else None)
    )


def _valid_role(resource: dict, run_id: str) -> bool:
    """仅角色允许获准保留终态，记录具体策略和创建归属。"""
    if set(resource) != {
        "type",
        "case_id",
        "marker",
        "name",
        "creator_id",
        "role_id",
        "state",
        "reason",
        "policy",
    }:
        return False
    state, role_id = resource["state"], resource["role_id"]
    name, case_id = resource["name"], resource["case_id"]
    return (
        case_id in ROLE_CASES
        and _safe_marker(resource["marker"], run_id)
        and isinstance(name, str)
        and len(name) <= 200
        and (
            (case_id == "TC_146" and name == "")
            or (case_id == "TC_147" and name == "agent")
            or (case_id in {"TC_145", "TC_148"} and run_id in name)
        )
        and _positive_id(resource["creator_id"])
        and (role_id is None or _positive_id(role_id))
        and state in {"intent", "created", "absent", "attention", "retained"}
        and (state not in {"created", "retained"} or role_id is not None)
        and (state not in {"intent", "absent"} or role_id is None)
        and (state != "absent" or case_id != "TC_145")
        and resource["policy"] == ROLE_RETENTION
        and (resource["reason"] is None or resource["reason"] in REASONS)
        and (state != "attention" or resource["reason"] is not None)
        and (state not in {"absent", "retained"} or resource["reason"] is None)
    )


def _valid_config_resource(resource: dict, run_id: str) -> bool:
    """仅已实现的三类配置使用同一定位字段。"""
    if set(resource) != {
        "type",
        "case_id",
        "marker",
        "name",
        "creator_id",
        "object_id",
        "state",
        "reason",
    }:
        return False
    kind, case_id = resource["type"], resource["case_id"]
    state, object_id, name = resource["state"], resource["object_id"], resource["name"]
    return (
        kind in CONFIG_CASES
        and case_id in CONFIG_CASES[kind]
        and _safe_marker(resource["marker"], run_id)
        and isinstance(name, str)
        and len(name) <= 200
        and (name == " " if case_id == "TC_160" else run_id in name)
        and _positive_id(resource["creator_id"])
        and (object_id is None or _positive_id(object_id))
        and state in {"intent", "created", "absent", "cleaned", "attention"}
        and (state not in {"created", "cleaned"} or object_id is not None)
        and (state not in {"intent", "absent"} or object_id is None)
        and (state != "absent" or case_id in {"TC_160", "TC_173", "TC_189"})
        and (resource["reason"] is None or resource["reason"] in REASONS)
        and (state != "attention" or resource["reason"] is not None)
        and (state not in {"cleaned", "absent"} or resource["reason"] is None)
    )


class EnvironmentRun:
    """持锁期间登记本轮工单，未解决残留阻塞后续运行。"""

    def __init__(self, state_dir: Path, env_id: str) -> None:
        """校验目录与环境代号，不在初始化时写入文件。"""
        if not isinstance(env_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", env_id):
            raise ValueError("env_id 必须为非空安全代号") from None
        if not isinstance(state_dir, Path) or not state_dir.is_absolute():
            raise ValueError("共享状态目录必须为绝对目录路径") from None
        self.run_id = f"RUN_{uuid4().hex}"
        self._directory = state_dir / env_id
        self.path = self._directory / f"run-{self.run_id}.json"
        self._record = {
            "env_id": env_id,
            "run_id": self.run_id,
            "status": "running",
            "resources": [],
        }
        self._lock: BinaryIO | None = None
        self._active = False
        self._entered = False
        self._retained_roles = []

    @property
    def retained_roles(self) -> list[dict]:
        """提供历史获准保留角色，业务准备前只读核查其状态。"""
        return copy.deepcopy(self._retained_roles)

    @property
    def resources(self) -> list[dict]:
        """返回独立副本，避免调用方绕过持久化修改资源。"""
        return copy.deepcopy(self._record["resources"])

    def _acquire(self) -> None:
        """获取非阻塞系统锁，保留锁文件以避免 inode 替换竞争。"""
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            self._lock = (self._directory / "environment.lock").open("a+b")
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt

                # Windows 可以锁定 EOF 之后的字节，不写锁文件以免竞争写入。
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if self._lock is not None:
                self._lock.close()
                self._lock = None
            raise RuntimeError("环境锁已被占用或共享状态目录不可访问") from None

    def _release(self) -> None:
        """关闭句柄释放系统锁，即使业务失败也保留锁文件。"""
        lock, self._lock = self._lock, None
        self._active = False
        if lock is not None:
            try:
                lock.close()
            except OSError:
                raise RuntimeError("环境锁释放失败") from None

    def _check_previous(self) -> None:
        """验证同环境所有清单，拒绝未知格式和未恢复运行。"""
        try:
            # iterdir 保留目录枚举错误；glob 在部分平台会静默忽略权限失败。
            for path in self._directory.iterdir():
                if not path.match("run-*.json"):
                    continue
                record = json.loads(
                    path.read_text(encoding="utf-8"), object_pairs_hook=_json_object
                )
                if not self._valid_record(record, path):
                    raise ValueError("清单格式无效")
                if record["status"] not in ("clean", "clean_with_retained", "recovered"):
                    raise RuntimeError("存在未处理残留运行，必须先人工恢复") from None
                self._retained_roles.extend(
                    item
                    for item in record["resources"]
                    if item["type"] == "role" and item["state"] == "retained"
                )
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            raise RuntimeError("历史运行清单损坏或格式未知，必须先人工核查") from None

    def _valid_record(self, record: object, path: Path) -> bool:
        """校验记录结构与 clean 声明，人工恢复只接受明确 recovered 状态。"""
        if not isinstance(record, dict) or record.keys() != {
            "env_id",
            "run_id",
            "status",
            "resources",
        }:
            return False
        run_id = record["run_id"]
        resources = record["resources"]
        return (
            record["env_id"] == self._record["env_id"]
            and isinstance(run_id, str)
            and re.fullmatch(r"RUN_[0-9a-f]{32}", run_id) is not None
            and path.name == f"run-{run_id}.json"
            and record["status"]
            in ("running", "attention", "clean", "clean_with_retained", "recovered")
            and isinstance(resources, list)
            and all(_valid_resource(item, run_id) for item in resources)
            and len({item["marker"] for item in resources}) == len(resources)
            and (
                record["status"] not in {"clean", "clean_with_retained"}
                or all(item["state"] in FINISHED for item in resources)
            )
            and (
                record["status"] != "clean"
                or not any(item["state"] == "retained" for item in resources)
            )
            and (
                record["status"] != "clean_with_retained"
                or any(item["state"] == "retained" for item in resources)
            )
        )

    def _save(self) -> None:
        """同目录临时写入并刷盘后原子替换，不回显系统异常。"""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._directory, suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(self._record, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError):
            raise RuntimeError("运行清单持久化失败，必须停止业务操作并人工核查") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def __enter__(self) -> "EnvironmentRun":
        """先获取锁并检查残留，再创建本轮运行清单。"""
        if self._entered:
            raise RuntimeError("运行上下文不能重复进入") from None
        self._entered = True
        self._acquire()
        try:
            self._check_previous()
            self._save()
            self._active = True
        except BaseException as original:
            try:
                self._release()
            except RuntimeError as error:
                raise BaseExceptionGroup("运行准备及锁释放失败", [original, error]) from None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """落盘最终清理状态并释放锁，同时保留业务和关闭失败。"""
        errors = []
        self._record["status"] = (
            "clean" if all(item["state"] in FINISHED for item in self.resources) else "attention"
        )
        if self._record["status"] == "clean" and any(
            item["state"] == "retained" for item in self.resources
        ):
            self._record["status"] = "clean_with_retained"
        try:
            self._save()
        except RuntimeError as error:
            errors.append(error)
        finally:
            try:
                self._release()
            except RuntimeError as error:
                errors.append(error)
        if errors:
            if exc is not None:
                errors.insert(0, exc)
            raise BaseExceptionGroup("运行结束存在失败", errors) from None
        return False

    def _resource(self, key: int) -> dict:
        """只允许持锁期间访问本轮已登记的资源序号。"""
        if not self._active:
            raise RuntimeError("运行上下文未持有环境锁") from None
        if type(key) is not int or not 0 <= key < len(self._record["resources"]):
            raise ValueError("资源序号无效") from None
        return self._record["resources"][key]

    def intent(self, case_id: str, marker: str) -> int:
        """创建请求发出前持久化唯一工单意图并返回资源序号。"""
        if not self._active:
            raise RuntimeError("运行上下文未持有环境锁") from None
        if case_id not in SUPPORTED_CASES or not _safe_marker(marker, self.run_id):
            raise ValueError("用例或本轮资源标记无效") from None
        if any(item["marker"] == marker for item in self.resources):
            raise ValueError("本轮资源标记重复") from None
        key = len(self._record["resources"])
        self._record["resources"].append(
            {
                "type": "ticket",
                "case_id": case_id,
                "marker": marker,
                "ticket_id": None,
                "article_ids": [],
                "state": "intent",
                "reason": None,
            }
        )
        self._save()
        return key

    def created(self, key: int, ticket_id: int, article_ids: list[int]) -> None:
        """创建成功后持久化工单和已知文章 ID，不改写既有 ID。"""
        resource = self._resource(key)
        if resource["type"] not in {"ticket", "search_baseline"}:
            raise RuntimeError("该资源不是工单") from None
        if (
            not _positive_id(ticket_id)
            or not isinstance(article_ids, list)
            or not all(_positive_id(value) for value in article_ids)
            or len(article_ids) != len(set(article_ids))
        ):
            raise ValueError("工单或文章 ID 无效") from None
        if resource["state"] != "intent":
            raise RuntimeError("资源已经记录创建结果") from None
        resource.update(ticket_id=ticket_id, article_ids=list(article_ids), state="created")
        self._save()

    def add_articles(self, key: int, article_ids: list[int]) -> None:
        """删除前将回读文章合并落盘，保留工单 ID、资源状态和原因。"""
        resource = self._resource(key)
        if (
            resource["type"] not in {"ticket", "search_baseline"}
            or resource["ticket_id"] is None
            or resource["state"] not in ("created", "attention")
        ):
            raise RuntimeError("只能向已知且未清理的工单补充文章 ID") from None
        if not isinstance(article_ids, list) or not all(
            _positive_id(value) for value in article_ids
        ):
            raise ValueError("文章 ID 必须为正整数列表") from None
        resource["article_ids"] = list(dict.fromkeys([*resource["article_ids"], *article_ids]))
        self._save()

    def cleaned(self, key: int) -> None:
        """调用方确认归属及删除结果后持久化清理完成。"""
        resource = self._resource(key)
        if resource["type"] == "role" or resource["state"] == "retained":
            raise RuntimeError("长期保留对象不能记为已删除") from None
        id_field = {
            "ticket": "ticket_id",
            "user": "user_id",
            "token": "token_id",
            "template": "object_id",
            "text_module": "object_id",
            "macro": "object_id",
            "search_baseline": "ticket_id",
        }[resource["type"]]
        if resource[id_field] is None:
            raise RuntimeError("未知创建结果不能标为已清理") from None
        resource.update(state="cleaned", reason=None)
        self._save()

    def absent(self, key: int) -> None:
        """负向请求明确结束且调用方以数据库证实无记录后，登记无残留。"""
        resource = self._resource(key)
        if (
            resource["type"] != "ticket"
            or resource["case_id"] not in NEGATIVE_CREATION_CASES
            or resource["state"] != "intent"
            or resource["ticket_id"] is not None
        ):
            raise RuntimeError("只有明确拒绝且未创建的负向意图可登记无残留") from None
        resource.update(state="absent", reason=None)
        self._save()

    def user_intent(self, case_id: str, marker: str, login: str) -> int:
        """先登记用户创建意图，仅保存本轮合成登录名，不接受密码。"""
        resource = dict(
            type="user",
            case_id=case_id,
            marker=marker,
            login=login,
            user_id=None,
            state="intent",
            reason=None,
        )
        if not self._active:
            raise RuntimeError("运行上下文未持有环境锁") from None
        if not _valid_user(resource, self.run_id):
            raise ValueError("用户资源定位信息无效") from None
        if any(item["marker"] == marker for item in self.resources):
            raise ValueError("本轮资源标记重复") from None
        key = len(self._record["resources"])
        self._record["resources"].append(resource)
        self._save()
        return key

    def user_created(self, key: int, user_id: int) -> None:
        """及时保存实际用户 ID，拒绝覆盖已有结果或跨类型登记。"""
        resource = self._resource(key)
        if resource["type"] != "user" or resource["state"] != "intent":
            raise RuntimeError("用户资源已经记录结果或类型错误") from None
        if not _positive_id(user_id):
            raise ValueError("用户 ID 无效") from None
        resource.update(user_id=user_id, state="created")
        self._save()

    def user_absent(self, key: int) -> None:
        """仅在重复创建请求已明确拒绝且核查无新增后登记无残留。"""
        resource = self._resource(key)
        if (
            resource["type"] != "user"
            or resource["case_id"] != "TC_138"
            or resource["state"] != "intent"
            or resource["user_id"] is not None
        ):
            raise RuntimeError("只有已核实拒绝的重复创建意图可登记无残留") from None
        resource.update(state="absent", reason=None)
        self._save()

    def attention(self, key: int, reason: str) -> None:
        """用固定原因登记需要人工核查的资源，不接受原始异常文本。"""
        resource = self._resource(key)
        if not isinstance(reason, str) or reason not in REASONS:
            raise ValueError("资源失败原因必须为约定代号") from None
        resource.update(state="attention", reason=reason)
        self._save()

    def baseline_intent(
        self,
        marker: str,
        name: str,
        group_id: int,
        customer_id: int,
        owner_id: int,
        creator_id: int,
    ) -> int:
        """基线请求前保存归属关系，尚未完整提交时按半成品管理。"""
        resource = dict(
            type="search_baseline",
            marker=marker,
            name=name,
            group_id=group_id,
            customer_id=customer_id,
            owner_id=owner_id,
            creator_id=creator_id,
            ticket_id=None,
            article_ids=[],
            state="intent",
            reason=None,
            mapping_name=None,
        )
        if not self._active or not _valid_baseline(resource, self.run_id):
            raise ValueError("基线创建意图或运行上下文无效") from None
        if any(row["marker"] == marker for row in self.resources):
            raise ValueError("基线标记重复") from None
        key = len(self._record["resources"])
        self._record["resources"].append(resource)
        self._save()
        return key

    def baseline_created(self, key: int, ticket_id: int, article_ids: list[int]) -> None:
        """明确基线类型后登记服务端真实ID。"""
        if self._resource(key)["type"] != "search_baseline":
            raise RuntimeError("不是基线资源") from None
        self.created(key, ticket_id, article_ids)

    def baseline_retained(self, keys: list[int], mapping_path: Path) -> None:
        """九张十三篇完整映射落盘并匹配后，一次提交长期保留状态。"""
        valid = False
        try:
            rows = [self._resource(key) for key in keys]
            mapping = json.loads(
                mapping_path.read_text(encoding="utf-8"), object_pairs_hook=_json_object
            )
            tickets = mapping["tickets"]
            valid = (
                len(keys) == 9
                and len(set(keys)) == 9
                and mapping_path.resolve() == (self._directory / "search-baseline.json").resolve()
                and mapping["run_id"] == self.run_id
                and set(tickets) == {f"B{i:02d}" for i in range(1, 10)}
                and all(
                    row["type"] == "search_baseline" and row["state"] == "created" for row in rows
                )
                and len({row["ticket_id"] for row in rows}) == 9
                and sum(len(row["article_ids"]) for row in rows) == 13
                and len({aid for row in rows for aid in row["article_ids"]}) == 13
                and {(row["ticket_id"], tuple(sorted(row["article_ids"]))) for row in rows}
                == {(row["id"], tuple(sorted(row["article_ids"]))) for row in tickets.values()}
            )
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            valid = False
        if not valid:
            raise RuntimeError("基线映射未完整保存或与清单不一致") from None
        for row in rows:
            row.update(state="retained", reason=None, mapping_name="search-baseline.json")
        self._save()

    def config_intent(
        self, kind: str, case_id: str, marker: str, name: str, creator_id: int
    ) -> int:
        """配置创建前持久化意图和实际准备身份，不保存正文。"""
        resource = dict(
            type=kind,
            case_id=case_id,
            marker=marker,
            name=name,
            creator_id=creator_id,
            object_id=None,
            state="intent",
            reason=None,
        )
        if not self._active:
            raise RuntimeError("运行上下文未持有环境锁") from None
        if not _valid_config_resource(resource, self.run_id) or any(
            item["marker"] == marker for item in self.resources
        ):
            raise ValueError("配置对象类型、编号或本轮定位无效") from None
        key = len(self._record["resources"])
        self._record["resources"].append(resource)
        self._save()
        return key

    def config_created(self, key: int, object_id: int) -> None:
        """确认本轮新对象归属后登记ID，拒绝跨类型或重复登记。"""
        resource = self._resource(key)
        if (
            resource["type"] not in CONFIG_CASES
            or resource["state"] != "intent"
            or not _positive_id(object_id)
        ):
            raise RuntimeError("配置资源不能登记创建结果") from None
        resource.update(object_id=object_id, state="created")
        self._save()

    def config_absent(self, key: int) -> None:
        """仅已证实未新增的明确负向创建可登记不存在。"""
        resource = self._resource(key)
        if (
            resource["type"] not in CONFIG_CASES
            or resource["case_id"] not in {"TC_160", "TC_173", "TC_189"}
            or resource["state"] != "intent"
            or resource["object_id"] is not None
        ):
            raise RuntimeError("配置资源不能登记不存在") from None
        resource.update(state="absent", reason=None)
        self._save()

    def role_intent(self, case_id: str, marker: str, name: str, creator_id: int) -> int:
        """创建前登记本轮角色意图，不复用既有角色冒充创建。"""
        resource = dict(
            type="role",
            case_id=case_id,
            marker=marker,
            name=name,
            creator_id=creator_id,
            role_id=None,
            state="intent",
            reason=None,
            policy=ROLE_RETENTION,
        )
        if not self._active:
            raise RuntimeError("运行上下文未持有环境锁") from None
        if not _valid_role(resource, self.run_id) or any(
            item["marker"] == marker for item in self.resources
        ):
            raise ValueError("角色资源定位信息无效或重复") from None
        key = len(self._record["resources"])
        self._record["resources"].append(resource)
        self._save()
        return key

    def role_created(self, key: int, role_id: int) -> None:
        """确认全新角色归属后登记实际ID，不能覆盖已有结果。"""
        resource = self._resource(key)
        if resource["type"] != "role" or resource["state"] != "intent" or not _positive_id(role_id):
            raise RuntimeError("角色资源不能登记创建结果") from None
        resource.update(role_id=role_id, state="created")
        self._save()

    def role_absent(self, key: int) -> None:
        """负向请求结束且确认没有新角色后登记不存在。"""
        resource = self._resource(key)
        if (
            resource["type"] != "role"
            or resource["case_id"] == "TC_145"
            or resource["state"] != "intent"
        ):
            raise RuntimeError("只有已核实未创建的负向角色可登记不存在") from None
        resource.update(state="absent", reason=None)
        self._save()

    def role_retained(self, key: int) -> None:
        """调用方核实停用、无默认注册、无权限组成员后登记保留例外。"""
        resource = self._resource(key)
        if (
            resource["type"] != "role"
            or resource["state"] != "created"
            or resource["role_id"] is None
        ):
            raise RuntimeError("未知或未确认的角色不能标为获准保留") from None
        resource.update(state="retained", reason=None)
        self._save()

    def token_intent(self, case_id: str, marker: str, user_id: int, name: str) -> int:
        """签发及临时授权开始前登记Token生命周期意图。"""
        resource = dict(
            type="token",
            case_id=case_id,
            marker=marker,
            user_id=user_id,
            name=name,
            token_id=None,
            state="intent",
            reason=None,
        )
        if not self._active:
            raise RuntimeError("运行上下文未持有环境锁") from None
        if not _valid_token(resource, self.run_id) or any(
            item["marker"] == marker for item in self.resources
        ):
            raise ValueError("Token资源定位信息无效或重复") from None
        key = len(self._record["resources"])
        self._record["resources"].append(resource)
        self._save()
        return key

    def token_created(self, key: int, token_id: int) -> None:
        """取得当前用户Token清单中的唯一ID后记录，不存凭据。"""
        resource = self._resource(key)
        if resource["type"] != "token" or resource["state"] != "intent":
            raise RuntimeError("Token资源状态不允许重复登记") from None
        if not _positive_id(token_id):
            raise ValueError("Token ID无效") from None
        resource.update(token_id=token_id, state="created")
        self._save()

    def token_absent(self, key: int) -> None:
        """仅在请求已结束且当前用户列表核实无本次Token时登记不存在。"""
        resource = self._resource(key)
        if resource["type"] != "token" or resource["state"] != "intent":
            raise RuntimeError("Token资源状态不能登记不存在") from None
        resource.update(state="absent", reason=None)
        self._save()
