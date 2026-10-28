"""未覆盖评分因素补写（P1-1）。

流程：读取已有产物 → 从 `bid.md` / `outline.json` / `report.json` 还原章节 →
为每个未覆盖评分因素新增一个生成型章节 → 合并后写出**新产物**（不覆盖原产物），
两份 `report.json` 可直接对比覆盖率。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.bid_writer.agents import default_caller, section_agent
from src.bid_writer.assembly import assemble_markdown, build_report
from src.bid_writer.docx_export import markdown_to_docx
from src.bid_writer.schema import (
    STRATEGY_GENERATE,
    BidderProfile,
    ReviewResult,
    SectionDraft,
    SectionPlan,
    WriteOptions,
)
from src.bid_writer.usage import default_meter
from src.errors import BidWriteError
from src.pipeline import BIDS_DIR, load_extraction

_HEADING_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_TOC_TITLE = "目录"


def _split_sections(markdown: str) -> list[tuple[str, str]]:
    """按 `## 标题` 拆分 `bid.md`，跳过「目录」章，返回 `(标题, 正文)`。"""
    matches = list(_HEADING_RE.finditer(markdown))
    parts: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        if title == _TOC_TITLE:
            continue
        parts.append((title, markdown[match.end() : end].strip()))
    return parts


def _load_report(bid_id: str) -> tuple[dict[str, Any], Path]:
    bid_dir = BIDS_DIR / bid_id
    report_path = bid_dir / "report.json"
    if not report_path.is_file():
        raise BidWriteError(f"产物不存在或是旧版本：{bid_id}")
    return json.loads(report_path.read_text(encoding="utf-8")), bid_dir


def restore_drafts(bid_id: str) -> tuple[list[SectionDraft], dict[str, Any], Path]:
    """从磁盘产物还原章节草稿（标题按 `outline.json` 顺序对齐）。"""
    report_data, bid_dir = _load_report(bid_id)
    outline_path = bid_dir / "outline.json"
    markdown_path = bid_dir / "bid.md"
    for path in (outline_path, markdown_path):
        if not path.is_file():
            raise BidWriteError(f"产物缺少 {path.name}，无法补写：{bid_id}")

    outline = json.loads(outline_path.read_text(encoding="utf-8"))
    bodies = _split_sections(markdown_path.read_text(encoding="utf-8"))
    if len(bodies) != len(outline):
        raise BidWriteError(
            f"产物结构与大纲不一致（正文 {len(bodies)} 章 / 大纲 {len(outline)} 章），请重新生成后再补写"
        )

    stats = {item.get("id"): item for item in report_data.get("sections", [])}
    drafts: list[SectionDraft] = []
    for plan_data, (_title, body) in zip(outline, bodies):
        plan = SectionPlan(**plan_data)
        stat = stats.get(plan.id, {})
        score_before = int(stat.get("score_before") or 0)
        score_after = int(stat.get("score_after") or 0)
        drafts.append(
            SectionDraft(
                plan=plan,
                markdown=body,
                review=ReviewResult(score=score_after),
                score_before=score_before,
                score_after=score_after,
                revisions=int(stat.get("revisions") or 0),
                retrieved=[],
            )
        )
    return drafts, report_data, bid_dir


def _load_profile(company_name: str) -> BidderProfile:
    """按公司名从资料库取回资料；数据库不可用时退化为仅含公司名，避免阻断补写。"""
    from src.db.repository import list_profiles
    from src.db.session import init_db

    async def _run() -> list[dict[str, Any]]:
        await init_db()
        return await list_profiles(limit=100)

    try:
        items = asyncio.run(_run())
    except Exception:  # noqa: BLE001 - 资料库不可用时仍可补写
        items = []
    for item in items:
        data = item.get("data")
        if item.get("company_name") == company_name and isinstance(data, dict):
            return BidderProfile.from_dict(data)
    return BidderProfile(company_name=company_name)


def _uncovered_factors(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        factor
        for factor in report_data.get("evaluation_coverage", [])
        if not factor.get("covered") and factor.get("score") is not None
    ]


def _search(retriever, query: str, top_k: int) -> list[dict[str, Any]]:
    """检索失败（索引缺失等）时返回空列表，不阻断补写。"""
    if retriever is None:
        return []
    try:
        return retriever.search(query, top_k)
    except Exception:  # noqa: BLE001 - 演示环境可能没有索引
        return []


def run_supplement(bid_id: str, *, caller=None, options: dict | None = None):
    """为未覆盖评分因素补写章节并写出新产物，返回新报告。"""
    drafts, report_data, _bid_dir = restore_drafts(bid_id)
    factors = _uncovered_factors(report_data)
    if not factors:
        raise BidWriteError("该产物已覆盖全部评分因素，无需补写")

    tender = load_extraction(report_data["doc_id"])
    profile = _load_profile(report_data.get("bidder") or "")
    write_options = WriteOptions.from_dict(options)
    meter = default_meter()
    metered = meter.wrap(caller or default_caller())

    retriever = None
    try:
        from src.vectorstore.ingest import collection_name
        from src.vectorstore.retrieval import ChromaRetriever

        retriever = ChromaRetriever(collection_name(tender.doc_type), doc_id=tender.doc_id)
    except Exception:  # noqa: BLE001 - 无索引时按无检索处理
        retriever = None

    base_order = max((d.plan.order for d in drafts), default=0)
    new_drafts: list[SectionDraft] = []
    for index, factor in enumerate(factors, start=1):
        name = str(factor.get("name") or "")
        plan = SectionPlan(
            id=f"section_supplement_{index}",
            title=f"补充：{name}",
            order=base_order + index,
            strategy=STRATEGY_GENERATE,
            target_words=write_options.target_words,
            scoring_factors=[name],
            requirements=str(factor.get("source_quote") or f"补充说明{name}"),
            retrieval_query=f"{name} {factor.get('category') or ''}".strip(),
        )
        draft = section_agent(
            plan,
            tender,
            profile,
            _search(retriever, plan.retrieval_query, write_options.top_k),
            min_score=write_options.min_score,
            max_revisions=write_options.max_revisions,
            caller=metered,
        )
        new_drafts.append(draft)

    all_drafts = drafts + new_drafts
    now = datetime.now()
    markdown = assemble_markdown(
        tender.project_name, profile.company_name, all_drafts, now.strftime("%Y年%m月%d日")
    )
    report = build_report(
        tender.doc_id,
        tender.project_name,
        profile.company_name,
        all_drafts,
        tender,
        profile,
        now,
    )
    report.supplement_of = bid_id
    report.warnings.append(
        f"本产物在原产物 {bid_id} 基础上补写：{'、'.join(d.plan.title for d in new_drafts)}"
    )

    output_dir = BIDS_DIR / f"{tender.doc_id}-{now.strftime('%Y%m%d-%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = output_dir / "bid.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    outline_path = output_dir / "outline.json"
    outline_path.write_text(
        json.dumps([p.plan.to_dict() for p in all_drafts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    try:
        docx_path = output_dir / "bid.docx"
        markdown_to_docx(markdown, docx_path)
        report.docx_path = str(docx_path)
    except Exception as exc:  # noqa: BLE001 - Word 导出失败不阻断补写
        report.warnings.append(f"Word 导出失败：{exc}")

    report.llm_usage = meter.summary()
    report.output_dir = str(output_dir)
    report.markdown_path = str(markdown_path)
    report.outline_path = str(outline_path)
    report_path = output_dir / "report.json"
    report.report_path = str(report_path)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def supplement_plan(bid_id: str) -> list[str]:
    """只读预检：返回待补写的评分因素名称，供接口在入队前给出提示。"""
    report_data, _bid_dir = _load_report(bid_id)
    return [str(factor.get("name") or "") for factor in _uncovered_factors(report_data)]


__all__ = ["restore_drafts", "run_supplement", "supplement_plan"]
