"""安装演示数据（P0-2）：把 examples/demo 的合成数据装进 upload/ 运行时目录。

做的事情：
1. 用本地解析器解析 `examples/demo/tender_demo.pdf`（无需任何付费 API）；
2. 写入预置的 `extraction.json`（人工整理的抽取结果，doc_id 按实际 PDF 重写）；
3. 按 `outline.json` + `sections/*.md` 组装 `bid.md`、`bid.docx`、`report.json`、
   `outline.json`，覆盖报告由 `build_report` 真实计算，与正文保持一致；
4. 可选：把 `bidder_profile.demo.json` 写入投标人资料库（数据库不可用时自动跳过）。

用法：
    uv run python scripts/install_demo_data.py
    uv run python scripts/install_demo_data.py --skip-profile
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEMO_DIR = REPO_ROOT / "examples" / "demo"
DEMO_PDF = DEMO_DIR / "tender_demo.pdf"
ARTIFACTS = DEMO_DIR / "artifacts"
SECTIONS_DIR = ARTIFACTS / "sections"
PROFILE_JSON = DEMO_DIR / "bidder_profile.demo.json"

# 预置章节的评审分数（演示数据，非模型实时评审）
_SCORE_BEFORE = 76
_SCORE_AFTER = 82


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _install_extraction(doc_id: str, parsed_dir: Path) -> None:
    """写入预置抽取结果，doc_id 与预置元信息按实际情况回填。"""
    data = json.loads((ARTIFACTS / "extraction.json").read_text(encoding="utf-8"))
    data["doc_id"] = doc_id
    data.setdefault("extraction_meta", {})
    data["extraction_meta"]["source_pdf"] = "examples/demo/tender_demo.pdf"
    _write_json(parsed_dir / doc_id / "extraction.json", data)


def _build_drafts(outline: list[dict]) -> list:
    """按 outline.json 与 sections/*.md 构造章节草稿列表。"""
    from src.bid_writer.schema import (
        STRATEGY_PLACEHOLDER,
        ReviewResult,
        SectionDraft,
        SectionPlan,
    )

    files = sorted(SECTIONS_DIR.glob("*.md"))
    if len(files) != len(outline):
        raise RuntimeError(
            f"sections 数量（{len(files)}）与 outline 章节数（{len(outline)}）不一致"
        )

    drafts: list[SectionDraft] = []
    for item, section_file in zip(sorted(outline, key=lambda x: x["order"]), files):
        plan = SectionPlan(**item)
        markdown = section_file.read_text(encoding="utf-8").strip()
        if plan.strategy == STRATEGY_PLACEHOLDER:
            review = ReviewResult(score=100)
            drafts.append(SectionDraft(plan, markdown, review, 100, 100, 0, []))
        else:
            review = ReviewResult(score=_SCORE_AFTER)
            drafts.append(
                SectionDraft(
                    plan, markdown, review, _SCORE_BEFORE, _SCORE_AFTER, 1, []
                )
            )
    return drafts


def _save_profile(profile: dict) -> str:
    """写入资料库，返回状态描述（不抛异常）。"""
    import asyncio

    from src.db.repository import save_profile
    from src.db.session import init_db

    async def _run() -> bool:
        await init_db()
        return await save_profile(profile["company_name"], profile) is not None

    try:
        saved = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - 数据库不可用时演示数据仍可用
        return f"跳过（{type(exc).__name__}：{exc}）"
    return "已写入" if saved else "跳过（数据库未配置）"


def main() -> None:
    parser = argparse.ArgumentParser(description="安装演示数据到 upload/ 运行时目录")
    parser.add_argument(
        "--skip-profile", action="store_true", help="不写入投标人资料库"
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from src.bid_writer.assembly import assemble_markdown, build_report
    from src.bid_writer.docx_export import markdown_to_docx
    from src.bid_writer.schema import BidderProfile
    from src.pipeline import BIDS_DIR, load_extraction, run_parse

    if not DEMO_PDF.is_file():
        raise SystemExit(
            f"缺少演示 PDF：{DEMO_PDF}\n"
            "请先执行：uv run python scripts/make_demo_tender.py"
        )

    # 1. 解析（本地解析器，无外部依赖）
    doc = run_parse(DEMO_PDF, parser="local")
    doc_id = doc.doc_id
    print(f"[1/4] 解析完成：doc_id={doc_id}，{doc.pages} 页，{len(doc.sections)} 章")

    # 2. 预置抽取结果
    _install_extraction(doc_id, Path(doc.artifacts_dir).parent)
    tender = load_extraction(doc_id)
    print(
        f"[2/4] 抽取结果就绪：{tender.project_name}，"
        f"评分因素 {len(tender.evaluation_factors)} 项，格式条目 {len(tender.bid_format_items)} 项"
    )

    # 3. 组装编写产物（覆盖报告由代码真实计算）
    profile_raw = json.loads(PROFILE_JSON.read_text(encoding="utf-8"))
    profile = BidderProfile.from_dict(profile_raw)
    outline = json.loads((ARTIFACTS / "outline.json").read_text(encoding="utf-8"))
    drafts = _build_drafts(outline)

    now = datetime.now()
    markdown = assemble_markdown(
        tender.project_name, profile.company_name, drafts, now.strftime("%Y年%m月%d日")
    )
    report = build_report(
        doc_id, tender.project_name, profile.company_name, drafts, tender, profile, now
    )

    output_dir = BIDS_DIR / f"{doc_id}-{now.strftime('%Y%m%d-%H%M%S')}"
    for old in BIDS_DIR.glob(f"{doc_id}-*"):
        if old.is_dir():
            shutil.rmtree(old)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "bid.md").write_text(markdown, encoding="utf-8")
    _write_json(output_dir / "outline.json", [d.plan.to_dict() for d in drafts])
    try:
        markdown_to_docx(markdown, output_dir / "bid.docx")
        report.docx_path = str(output_dir / "bid.docx")
    except Exception as exc:  # noqa: BLE001 - Word 导出失败不阻断演示
        report.warnings.append(f"Word 导出失败：{exc}")

    report.output_dir = str(output_dir)
    report.markdown_path = str(output_dir / "bid.md")
    report.outline_path = str(output_dir / "outline.json")
    report.report_path = str(output_dir / "report.json")
    _write_json(output_dir / "report.json", report.to_dict())

    summary = report.summary()
    print(
        f"[3/4] 编写产物就绪：{summary['total_sections']} 章 / {summary['total_words']} 字，"
        f"格式覆盖 {summary['format_items_covered']}/{summary['format_items_total']}，"
        f"评分覆盖 {summary['evaluation_factors_covered']}/{summary['evaluation_factors_total']}，"
        f"占位符 {summary['placeholder_count']} 个"
    )
    print(f"      → {output_dir}")

    # 4. 资料库
    if args.skip_profile:
        print("[4/4] 资料库：按参数跳过")
    else:
        print(f"[4/4] 资料库：{_save_profile(profile_raw)}")

    print("完成。打开前端「标书历史」或文档库即可看到演示数据。")


if __name__ == "__main__":
    main()
