"""write 子命令：生成投标文件草稿与覆盖报告。"""
from __future__ import annotations

import json
from pathlib import Path

from src.pipeline import run_bid_writer


def register(subparsers) -> None:
    p = subparsers.add_parser("write", help="生成投标文件草稿")
    p.add_argument("doc_id", help="已抽取文档的 doc_id")
    p.add_argument("--profile", help="投标人资料 JSON 文件路径")
    p.add_argument("--max-revisions", type=int, default=None, help="修订次数上限")
    p.add_argument("--min-score", type=int, default=None, help="评审阈值")
    p.add_argument("--max-sections", type=int, default=None, help="章节数量上限")
    p.add_argument("--no-word", action="store_true", help="不导出 Word")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    profile: dict = {}
    if args.profile:
        profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))

    options = {
        "max_revisions": args.max_revisions,
        "min_score": args.min_score,
        "max_sections": args.max_sections,
        "export_word": not args.no_word,
    }

    report = run_bid_writer(args.doc_id, profile=profile, options=options)
    summary = report.summary()

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"章节: {summary['total_sections']}  字数: {summary['total_words']}  "
            f"格式覆盖: {summary['format_items_covered']}/{summary['format_items_total']}  "
            f"评分覆盖: {summary['evaluation_factors_covered']}/{summary['evaluation_factors_total']}  "
            f"占位符: {summary['placeholder_count']}"
        )
        print(f"产物目录: {summary['output_dir']}")
    return 0
