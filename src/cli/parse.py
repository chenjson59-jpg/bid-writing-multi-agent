"""parse 子命令：解析 PDF 为 `ParsedDocument`。"""
from __future__ import annotations

import json

from src.pipeline import run_parse


def register(subparsers) -> None:
    p = subparsers.add_parser("parse", help="解析 PDF 为 ParsedDocument")
    p.add_argument("pdf_path", help="源 PDF 路径")
    p.add_argument("--parser", choices=["auto", "cloud", "local"], default="auto")
    p.add_argument("--strict-parser", action="store_true", help="云解析失败时直接报错")
    p.add_argument("--force", action="store_true", help="忽略已有产物重新解析")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    doc = run_parse(
        args.pdf_path,
        parser=args.parser,
        force=args.force,
        strict_parser=args.strict_parser,
    )
    summary = {
        "doc_id": doc.doc_id,
        "doc_type": doc.doc_type,
        "parser": doc.parser,
        "pages": doc.pages,
        "sections": len(doc.sections),
        "source_name": doc.source_name,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"doc_id: {summary['doc_id']}  类型: {summary['doc_type']}  "
            f"解析器: {summary['parser']}  页数: {summary['pages']}  章节: {summary['sections']}"
        )
    return 0
