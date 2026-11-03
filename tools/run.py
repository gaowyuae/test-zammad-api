"""本地与 Jenkins 共用的单进程业务运行入口。"""

import argparse
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from tools.metadata import write_metadata

ROOT = Path(__file__).resolve().parents[1]
CONFIG_OPTIONS = (
    ("env", "config", "local.yaml"),
    ("run", "run-config", "run.local.yaml"),
    ("db", "db-config", "db.local.yaml"),
    ("creation", "creation-config", "creation.local.yaml"),
    ("assignment", "assignment-config", "assignment.local.yaml"),
    ("update_access", "update-access-config", "update_access.local.yaml"),
    ("search", "search-config", "search.local.yaml"),
)


class _Parser(argparse.ArgumentParser):
    """只输出安全参数错误，不回显用户输入。"""

    def error(self, message: str) -> None:
        """拒绝未知参数并返回固定错误。"""
        self.exit(2, "运行失败：命令行参数无效。\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """解析本地与 Jenkins 共用的配置路径和用例范围。"""
    parser = _Parser(description="Zammad 首版已实现功能检查；pilot 为 P0 五条")
    for _, option, filename in CONFIG_OPTIONS:
        parser.add_argument(f"--{option}", type=Path, default=ROOT / "config" / filename)
    parser.add_argument("--suite", choices=("functional", "pilot"), default="functional")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def _write_environment(results: Path, metadata: dict, suite: str) -> None:
    """在 Allure 中展示非敏感的版本与配置摘要。"""
    results.mkdir()
    properties = {
        "git_commit": metadata["git_commit"] or "unavailable",
        "git_dirty": str(metadata["git_dirty"]),
        "framework_sources_sha256": metadata["framework_sources_sha256"],
        "suite": suite,
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


def _pytest_args(configs: dict[str, Path], suite: str, output: Path) -> list[str]:
    """构造功能用例参数，将所有执行产物限制在本轮目录。"""
    # 自定义参数尚未注册时，等号避免外部配置路径被当作收集起点。
    config_args = [
        f"--zammad-{option}={configs[key].resolve()}" for key, option, _ in CONFIG_OPTIONS
    ]
    return [
        str(ROOT / "tests/functional"),
        "-m",
        suite,
        *config_args,
        "--alluredir",
        str(output / "allure-results"),
        "--junitxml",
        str(output / "junit.xml"),
        "--basetemp",
        str(output / "pytest-temp"),
        "-o",
        f"cache_dir={output / 'pytest-cache'}",
    ]


def main(argv: list[str] | None = None) -> int:
    """创建全新结果目录，再交由同一 pytest 前置和清理路径执行。"""
    args = _parse_args(argv)
    configs = {key: getattr(args, option.replace("-", "_")) for key, option, _ in CONFIG_OPTIONS}
    output = (args.output or ROOT / "artifacts" / f"run-{uuid4().hex}").resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("运行失败：需要一个可写且不存在的结果目录。", file=sys.stderr)
        return 2
    try:
        metadata = write_metadata(ROOT, output, configs, args.suite)
        _write_environment(output / "allure-results", metadata, args.suite)
    except Exception:
        print("运行失败：版本或配置元数据无法保存。", file=sys.stderr)
        return 2
    return int(pytest.main(_pytest_args(configs, args.suite, output)))


if __name__ == "__main__":
    raise SystemExit(main())
