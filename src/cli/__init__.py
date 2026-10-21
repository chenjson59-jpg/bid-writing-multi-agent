"""命令行入口：doctor / parse / extract / ingest / inspect 子命令。

与接口层复用同一套 `run_parse` / `run_extract` / `run_ingest`，仅负责参数
解析、控制台输出与进程退出码映射。`write` 子命令随 M4 投标编写阶段补齐。
"""
from __future__ import annotations

import argparse
import sys

from src.errors import BiddingError

from . import doctor, extract, ingest, inspect, parse


def build_parser() -> argparse.ArgumentParser:
    """构建顶层解析器并注册全部子命令。"""
    parser = argparse.ArgumentParser(
        prog="src.cli",
        description="招标文件多智能体系统命令行",
    )
    subparsers = parser.add_subparsers(dest="command")
    doctor.register(subparsers)
    parse.register(subparsers)
    extract.register(subparsers)
    ingest.register(subparsers)
    inspect.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    """解析参数并分发到子命令，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    try:
        return handler(args)
    except BiddingError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130
