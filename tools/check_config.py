"""通过固定摘要检查非敏感配置，不建立外部连接。"""

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from support.config import ConfigError, load_config


class _Parser(argparse.ArgumentParser):
    """避免参数解析错误回显输入内容。"""

    def error(self, message: str) -> NoReturn:
        """忽略原始诊断并以固定摘要返回参数错误。"""
        self.exit(2, "配置检查失败：命令行参数无效。\n")


def main(argv: list[str] | None = None) -> int:
    """解析配置路径并通过退出码返回离线检查结果。"""
    parser = _Parser(description="离线检查项目非敏感配置")
    parser.add_argument("--config", type=Path, default=Path("config/local.example.yaml"))
    args = parser.parse_args(argv)
    try:
        load_config(args.config)
    except ConfigError as error:
        print(f"配置检查失败：{error}", file=sys.stderr)
        return 2
    print("配置检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
