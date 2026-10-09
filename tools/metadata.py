"""生成本轮最小执行元数据，不连接业务服务或读取凭据环境变量。"""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

SOURCE_DIRS = (
    "clients",
    "api",
    "db",
    "fixtures",
    "assertions",
    "support",
    "reporting",
    "tests",
    "tools",
    "data",
)
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".runtime",
    ".tmp",
    ".downloads",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "artifacts",
    "qa-output",
    "docs",
    "privateconfig",
    "config",
}


def _git(root: Path, *args: str) -> str | None:
    """只读取 Git 输出；外部进程失败不传播命令、路径或错误文本。"""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _hash_file(path: Path) -> str | None:
    """对配置原始字节计算摘要，不解释或保存配置内容。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _source_hash(root: Path, configs: dict[str, Path]) -> str | None:
    """仅汇总明确的项目来源；该摘要只辅助诊断，不替代 Git 验收。"""
    root = root.resolve()
    excluded = {path.resolve() for path in configs.values()}
    paths = {root / name for name in ("conftest.py", "pyproject.toml", "Jenkinsfile")}
    paths.update(root.glob("requirements*.txt"))
    paths.update((root / "config").glob("*.example.yaml"))
    try:
        for name in SOURCE_DIRS:
            directory = root / name
            if directory.is_symlink() or not directory.resolve().is_relative_to(root):
                continue
            for current, dirs, files in os.walk(directory):
                # 提前剪枝，避免进入产物、环境目录和外部目录链接。
                dirs[:] = [
                    item
                    for item in dirs
                    if item not in EXCLUDED_DIRS
                    and not item.startswith(".")
                    and not (Path(current) / item).is_symlink()
                    and (Path(current) / item).resolve().is_relative_to(root)
                ]
                paths.update(
                    Path(current) / file
                    for file in files
                    if Path(file).suffix in {".py", ".yaml", ".toml"}
                    and not file.startswith(".")
                    and ".private." not in file
                    and ".local." not in file
                    and file not in {"local.yaml", "secrets.yaml", "credentials.yaml"}
                )
        digest = hashlib.sha256()
        for path in sorted(paths):
            resolved = path.resolve()
            if path.is_symlink() or resolved in excluded or not resolved.is_relative_to(root):
                continue
            if not path.is_file():
                continue
            # 相对文件名参与摘要以区分重命名；元数据中只保留最终摘要。
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()
    except OSError:
        return None


def write_metadata(root: Path, output: Path, configs: dict[str, Path], suite: str) -> dict:
    """返回并保存本轮最小证据，保存失败以固定安全错误阻止继续执行。"""
    started_at = datetime.now(UTC).isoformat()
    commit = _git(root, "rev-parse", "HEAD")
    if commit is None or not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit):
        commit = None
    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    build_number = os.environ.get("BUILD_NUMBER", "")
    metadata = {
        "schema_version": 1,
        "python": sys.version,
        "suite": suite,
        "git_commit": commit,
        "git_dirty": bool(status) if status is not None else None,
        "config_sha256": {
            key: _hash_file(configs[key]) if key in configs else None
            for key in ("env", "run", "db", "creation", "assignment", "update_access", "search")
        },
        "framework_sources_sha256": _source_hash(root, configs),
        "started_at": started_at,
        "build_number": build_number if re.fullmatch(r"[0-9]+", build_number) else None,
    }
    try:
        (output / "run-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        raise RuntimeError("运行元数据保存失败。") from None
    return metadata
