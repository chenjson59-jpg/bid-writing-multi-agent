"""ingest 子命令：解析 + 抽取 + 建立检索索引。"""
from __future__ import annotations

import json

from src.extraction.extractor import run_extraction
from src.pipeline import run_parse
from src.vectorstore.ingest import ingest_document


def register(subparsers) -> None:
    p = subparsers.add_parser("ingest", help="解析 + 抽取 + 建立检索索引")
    p.add_argument("pdf_path", help="源 PDF 路径")
    p.add_argument("--parser", choices=["auto", "cloud", "local"], default="auto")
    p.add_argument("--strict-parser", action="store_true", help="云解析失败时直接报错")
    p.add_argument("--force", action="store_true", help="忽略已有产物重新执行")
    p.add_argument("--no-extract", action="store_true", help="跳过结构化抽取")
    p.add_argument("--no-ingest", action="store_true", help="跳过向量入库")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    doc = run_parse(
        args.pdf_path,
        parser=args.parser,
        force=args.force,
        strict_parser=args.strict_parser,
    )

    project_name = ""
    if not args.no_extract:
        project_name = run_extraction(doc, force=args.force).project_name

    summary = None
    if not args.no_ingest:
        summary = ingest_document(doc, project_name=project_name)

    result = {
        "doc_id": doc.doc_id,
        "pages": doc.pages,
        "sections": len(doc.sections),
    }
    if summary is not None:
        result["ingest"] = summary.to_dict()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"doc_id: {result['doc_id']}  页数: {result['pages']}  章节: {result['sections']}")
        if summary is not None:
            print(
                f"入库: 新增 {summary.added}  更新 {summary.updated}  "
                f"删除 {summary.deleted}  未变 {summary.unchanged}  总计 {summary.total}"
            )
    return 0
