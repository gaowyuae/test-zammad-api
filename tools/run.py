"""本地与 Jenkins 共用的单进程业务运行入口。"""

import argparse
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from tools.metadata import write_metadata

ROOT = Path(__file__).resolve().parents[1]


class _Parser(argparse.ArgumentParser):
    """只输出安全参数错误，不回显用户输入。"""

    def error(self, message: str) -> None:
        """拒绝未知参数并返回固定错误。"""
        self.exit(2, "运行失败：命令行参数无效。\n")


def main(argv: list[str] | None = None) -> int:
    """创建全新结果目录，再交由同一 pytest 前置和清理路径执行。"""
    parser = _Parser(description="Zammad 首版已实现功能检查；pilot 为 P0 五条")
    parser.add_argument("--config", type=Path, default=ROOT / "config/local.yaml")
    parser.add_argument("--run-config", type=Path, default=ROOT / "config/run.local.yaml")
    parser.add_argument("--db-config", type=Path, default=ROOT / "config/db.local.yaml")
    parser.add_argument("--creation-config", type=Path, default=ROOT / "config/creation.local.yaml")
    parser.add_argument(
        "--assignment-config", type=Path, default=ROOT / "config/assignment.local.yaml"
    )
    parser.add_argument(
        "--update-access-config", type=Path, default=ROOT / "config/update_access.local.yaml"
    )
    parser.add_argument("--suite", choices=("functional", "pilot"), default="functional")
    parser.add_argument("--search-config", type=Path, default=ROOT / "config/search.local.yaml")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    output = (args.output or ROOT / "artifacts" / f"run-{uuid4().hex}").resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("运行失败：需要一个可写且不存在的结果目录。", file=sys.stderr)
        return 2
    try:
        metadata = write_metadata(
            ROOT,
            output,
            {
                "env": args.config,
                "run": args.run_config,
                "db": args.db_config,
                "creation": args.creation_config,
                "assignment": args.assignment_config,
                "update_access": args.update_access_config,
                "search": args.search_config,
            },
            args.suite,
        )
        results = output / "allure-results"
        results.mkdir()
        # Allure 中只展示稳定、非敏感的版本与配置摘要。
        properties = {
            "git_commit": metadata["git_commit"] or "unavailable",
            "git_dirty": str(metadata["git_dirty"]),
            "framework_sources_sha256": metadata["framework_sources_sha256"],
            "suite": args.suite,
        }
        properties.update(
            {
                f"config_{key}_sha256": value or "unavailable"
                for key, value in metadata["config_sha256"].items()
            }
        )
        (results / "environment.properties").write_text(
            "\n".join(f"{key}={value}" for key, value in properties.items()), encoding="utf-8"
        )
    except Exception:
        print("运行失败：版本或配置元数据无法保存。", file=sys.stderr)
        return 2
    return int(
        pytest.main(
            [
                str(ROOT / "tests/functional"),
                "-m",
                args.suite,
                # 自定义参数尚未注册时，避免 pytest 把外部配置路径当作收集起点。
                f"--zammad-config={args.config.resolve()}",
                f"--zammad-run-config={args.run_config.resolve()}",
                f"--zammad-db-config={args.db_config.resolve()}",
                f"--zammad-creation-config={args.creation_config.resolve()}",
                f"--zammad-assignment-config={args.assignment_config.resolve()}",
                f"--zammad-update-access-config={args.update_access_config.resolve()}",
                f"--zammad-search-config={args.search_config.resolve()}",
                "--alluredir",
                str(output / "allure-results"),
                "--junitxml",
                str(output / "junit.xml"),
                "--basetemp",
                str(output / "pytest-temp"),
                "-o",
                f"cache_dir={output / 'pytest-cache'}",
            ]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
