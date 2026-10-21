"""extract 子命令：对已解析文档执行结构化抽取。"""
from __future__ import annotations

import json

from src.pipeline import run_extract


def register(subparsers) -> None:
    p = subparsers.add_parser("extract", help="对已解析文档执行结构化抽取")
    p.add_argument("target", help="PDF 路径或已有 doc_id")
    p.add_argument("--parser", choices=["auto", "cloud", "local"], default="auto")
    p.add_argument("--strict-parser", action="store_true", help="云解析失败时直接报错")
    p.add_argument("--force", action="store_true", help="忽略已有产物重新抽取")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    result = run_extract(
        args.target,
        parser=args.parser,
        force=args.force,
        strict_parser=args.strict_parser,
    )
    summary = {
        "doc_id": result.doc_id,
        "project_name": result.project_name,
        "evaluation_factors": len(result.evaluation_factors),
        "bid_format_items": len(result.bid_format_items),
        "qualifications": len(result.qualifications),
        "ungrounded_fields": result.extraction_meta.get("ungrounded_fields", []),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"doc_id: {summary['doc_id']}  项目名称: {summary['project_name']}")
        print(
            f"评分因素: {summary['evaluation_factors']}  "
            f"投标格式: {summary['bid_format_items']}  "
            f"资格条件: {summary['qualifications']}"
        )
    return 0
