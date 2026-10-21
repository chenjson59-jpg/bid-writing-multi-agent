"""汇总与覆盖报告（核心算法 A20）。

把按顺序排列的章节草稿合并为 Markdown，并计算格式覆盖、评分覆盖、
占位符与缺失资料，产出 `BidReport`。
"""
from __future__ import annotations

import re
from datetime import datetime

from src.bid_writer.schema import (
    BidReport,
    SectionDraft,
    extract_placeholders,
    normalize_title,
)


def _char_count(text: str) -> int:
    """字数统计：去除空白后的字符数（8.3.2）。"""
    return len(re.sub(r"\s+", "", text))


def assemble_markdown(
    project_name: str,
    bidder: str,
    sections: list[SectionDraft],
    date_text: str,
) -> str:
    """合并章节为 Markdown（8.3.1）。"""
    company = bidder or "【待填写：公司名称】"
    toc = "\n".join(f"{i}. {s.plan.title}" for i, s in enumerate(sections, 1))
    body = "\n\n".join(
        f"## {s.plan.title}\n\n{s.markdown.strip()}" for s in sections
    )
    return (
        f"# {project_name or '【待填写：项目名称】'}\n\n"
        f"**投标人：{company}**\n"
        f"**日期：{date_text}**\n\n"
        f"---\n\n"
        f"## 目录\n{toc}\n\n"
        f"{body}\n"
    )


def build_report(
    doc_id: str,
    project_name: str,
    bidder: str,
    drafts: list[SectionDraft],
    tender,
    profile,
    generated_at: datetime,
) -> BidReport:
    """计算覆盖报告（8.3.2–8.3.5）。`drafts` 需已按 order 排序。"""
    warnings: list[str] = []

    # 章节统计（8.3.2）
    sections = [d.to_dict() for d in drafts]
    total_words = sum(_char_count(d.markdown) for d in drafts)

    # 格式覆盖（8.3.3）
    format_coverage = []
    for item in tender.bid_format_items:
        title = item.get("title", "")
        key = normalize_title(title)
        hit = next(
            (
                d
                for d in drafts
                if key
                and (key in normalize_title(d.plan.title) or normalize_title(d.plan.title) in key)
            ),
            None,
        )
        format_coverage.append(
            {
                "title": title,
                "section_id": hit.plan.id if hit else None,
                "section_title": hit.plan.title if hit else None,
                "strategy": hit.plan.strategy if hit else None,
            }
        )

    # 评分覆盖（8.3.4）：只精确名称匹配
    evaluation_coverage = []
    for factor in tender.evaluation_factors:
        name = factor.get("name", "")
        section_ids = [d.plan.id for d in drafts if name in d.plan.scoring_factors]
        covered = bool(section_ids)
        evaluation_coverage.append(
            {
                "name": name,
                "score": factor.get("score"),
                "category": factor.get("category"),
                "covered": covered,
                "section_ids": section_ids,
            }
        )
        if not covered and factor.get("score") is not None:
            warnings.append(f"评分因素「{name}」未覆盖")

    # 占位符去重排序（8.4.1）
    placeholders: list[str] = []
    for draft in drafts:
        for placeholder in extract_placeholders(draft.markdown):
            if placeholder not in placeholders:
                placeholders.append(placeholder)
    placeholders.sort()

    return BidReport(
        doc_id=doc_id,
        project_name=project_name,
        bidder=bidder,
        generated_at=generated_at.isoformat(),
        total_sections=len(drafts),
        total_words=total_words,
        sections=sections,
        format_coverage=format_coverage,
        evaluation_coverage=evaluation_coverage,
        placeholders=placeholders,
        missing_profile_fields=profile.missing_fields(),
        warnings=warnings,
    )
