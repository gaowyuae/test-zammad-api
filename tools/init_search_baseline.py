"""显式初始化或只读核对长期搜索基线，不收集业务TC。"""

import argparse
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from clients.http import HttpClient
from clients.identity import Identity
from db.config import load_db_config
from db.queries import Queries
from fixtures.retained_roles import check_retained_roles
from fixtures.search_baseline import BaselineError, SearchBaseline, load_mapping, preflight
from reporting.evidence import EvidenceRecorder
from support.config import load_config
from support.run_config import load_run_config
from support.runtime import EnvironmentRun
from support.search_config import ALIASES, load_definition, load_search_config
from tools.metadata import _hash_file, write_metadata

ROOT = Path(__file__).resolve().parents[1]


class FileEvidence(EvidenceRecorder):
    """独立入口即时保存已脱敏JSON，复用原有容量与缺证规则。"""

    def __init__(self, output: Path) -> None:
        """保存全新结果目录，不改变pytest的Allure证据路径。"""
        super().__init__()
        self.output = output
        self.sequence = 0

    def _attach(self, name: str, body: str, size: int) -> bool:
        """原子独占创建证据文件，写失败显式标记缺证。"""
        self.sequence += 1
        try:
            with (self.output / f"{self.sequence:04}-{name}.json").open(
                "x", encoding="utf-8"
            ) as stream:
                stream.write(body)
        except OSError:
            self.incomplete = True
            return False
        self.total_bytes += size
        return True


class _Parser(argparse.ArgumentParser):
    """参数错误不回显潜在秘密输入。"""

    def error(self, message: str) -> None:
        """使用固定诊断退出，不展示原始参数。"""
        self.exit(2, "基线入口参数无效。\n")


def main(argv: list[str] | None = None) -> int:
    """显式选择初始化，默认只读校验，持锁覆盖全部业务操作。"""
    parser = _Parser(description="M22搜索基线；默认check不创建或修复数据")
    parser.add_argument("--mode", choices=("init", "check"), default="check")
    parser.add_argument("--config", type=Path, default=ROOT / "config/local.yaml")
    parser.add_argument("--run-config", type=Path, default=ROOT / "config/run.local.yaml")
    parser.add_argument("--db-config", type=Path, default=ROOT / "config/db.local.yaml")
    parser.add_argument("--search-config", type=Path, default=ROOT / "config/search.local.yaml")
    parser.add_argument("--definition", type=Path, default=ROOT / "data/baseline/search.yaml")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    output = (args.output or ROOT / "artifacts" / f"search-{uuid4().hex}").resolve()
    try:
        settings = load_config(args.config)
        run_config = load_run_config(args.run_config)
        db_config = load_db_config(args.db_config)
        search = load_search_config(args.search_config)
        definition = load_definition(args.definition)
        if run_config.state_dir.resolve().is_relative_to(ROOT):
            raise ValueError("共享目录不能位于工作区")
        identities = {
            alias: Identity.from_env(alias, getattr(search, f"{alias.lower()}_token_env"))
            for alias in ALIASES
        }
        identities["M"] = Identity.from_env("M", run_config.maintenance_token_env)
        if len({identity.token for identity in identities.values()}) != 5:
            raise ValueError("基线身份凭据必须独立")
        output.mkdir(parents=True, exist_ok=False)
        recorder = FileEvidence(output)
        metadata = write_metadata(
            ROOT,
            output,
            {
                "env": args.config,
                "run": args.run_config,
                "db": args.db_config,
                "search": args.search_config,
            },
            f"search-baseline-{args.mode}",
        )
        hashes = {
            **metadata["config_sha256"],
            "search": _hash_file(args.search_config),
            "definition": _hash_file(args.definition),
        }
        if metadata["framework_sources_sha256"] is None or any(
            hashes[key] is None for key in ("env", "run", "db", "search", "definition")
        ):
            raise ValueError("基线来源摘要不完整")
    except Exception:
        print("基线前置配置、凭据或输出目录无效；未执行业务写入。", file=sys.stderr)
        return 2
    summary = {
        "env_id": settings.env_id,
        "mode": args.mode,
        "status": "failed",
        "version": definition["version"],
        "definition_sha256": definition["sha256"],
        "business_tc_count": 0,
        "framework_sources_sha256": metadata["framework_sources_sha256"],
        "config_sha256": hashes,
        "git_commit": metadata["git_commit"],
    }
    try:
        with EnvironmentRun(run_config.state_dir, settings.env_id) as run:
            summary["run_id"] = run.run_id
            path = run_config.state_dir / settings.env_id / "search-baseline.json"
            # 在任何API请求前拒绝误触发初始化或缺失映射。
            if args.mode == "init" and path.exists():
                raise ValueError("映射已存在")
            mapping = (
                load_mapping(path, settings.env_id, definition) if args.mode == "check" else None
            )
            with ExitStack() as stack:
                db = stack.enter_context(Queries(db_config, recorder))
                check_retained_roles(run, db)
                clients = {
                    alias: stack.enter_context(HttpClient(settings, identity, recorder))
                    for alias, identity in identities.items()
                }
                identity = preflight(clients, search, run_config, definition, db)
                baseline = SearchBaseline(clients, db, run, recorder, identity, definition)
                if args.mode == "init":
                    mapping = baseline.initialize(path, settings.env_id)
                else:
                    baseline.validate(mapping)
                recorder.ensure_complete()
                summary.update(
                    status="passed",
                    mapping_run_id=mapping["run_id"],
                    ticket_count=9,
                    article_count=13,
                )
            recorder.record("search-run-summary", {**summary, "resources": run.resources})
            recorder.ensure_complete()
    except Exception as error:
        summary["status"] = "failed"
        summary["reason"] = (
            str(error)
            if isinstance(error, BaselineError)
            else (
                "基线前置、数据、索引、证据或清理核查失败；请查看脱敏证据及共享运行清单，不自动修复。"
            )
        )
    try:
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        print("基线结果无法保存，不能报告成功。", file=sys.stderr)
        return 1
    print(f"搜索基线{args.mode}：{summary['status']}；业务TC新增执行数0。")
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
