"""汇总与覆盖报告（docs/algorithms.md A20）。

把按顺序排列的章节草稿合并为 Markdown，并计算格式覆盖、评分覆盖、
占位符与缺失资料，产出 `BidReport`。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.bid_writer.coverage import compute_evaluation_coverage
from src.bid_writer.rules import check_section_rules
from src.bid_writer.schema import (
    BidReport,
    SectionDraft,
    extract_placeholders,
    normalize_title,
)


def _char_count(text: str) -> int:
    """字数统计：去除空白后的字符数（8.3.2）。"""
    return len(re.sub(r"\s+", "", text))


def _strip_leading_heading(text: str, title: str) -> str:
    """章节草稿可能自带开头的 `## 标题`（section_agent 产出和人工编辑都会带），
    合并时外层已追加 `## {title}` 前缀，这里只去掉草稿开头**与本章节标题一致**的那个 heading，
    避免误伤正文中 `### 一、编制说明...` 之类的小节标题。"""
    stripped = text.lstrip()
    # 只匹配标题为当前章节标题本身的 heading（允许 # ~ #### 任何层级）
    pattern = rf"^#{{1,4}}\s+{re.escape(title)}\s*\n+"
    return re.sub(pattern, "", stripped, count=1)


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
        f"## {s.plan.title}\n\n{_strip_leading_heading(s.markdown.strip(), s.plan.title)}"
        for s in sections
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
    schema: dict | None = None,
    retrieval_degraded: bool = False,
) -> BidReport:
    """计算覆盖报告（8.3.2–8.3.5）。`drafts` 需已按 order 排序。schema 透传给 missing_fields。"""
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

    # 评分覆盖（8.3.4，T-012 三层覆盖）：计划/正文/证据，纯确定性计算
    evaluation_coverage, coverage_summary = compute_evaluation_coverage(
        tender, drafts, doc_id=doc_id
    )
    for item in evaluation_coverage:
        # 未覆盖警告仍以计划层为准，保持既有语义
        if not item["plan_covered"] and item.get("score") is not None:
            warnings.append(f"评分因素「{item['name']}」未覆盖")

    # 占位符去重排序（8.4.1）
    placeholders: list[str] = []
    for draft in drafts:
        for placeholder in extract_placeholders(draft.markdown):
            if placeholder not in placeholders:
                placeholders.append(placeholder)
    placeholders.sort()

    # 确定性规则检查（T-013）：与 LLM 评审分开输出；旧产物还原的草稿无缓存结果时重算
    rule_checks: list[dict[str, Any]] = []
    rule_error_count = 0
    for section, draft in zip(sections, drafts):
        result = draft.rule_check or check_section_rules(
            draft.markdown, draft.plan, draft.retrieved, doc_id=doc_id
        )
        payload = result.to_dict()
        section["rule_check"] = payload
        rule_checks.append(payload)
        if not result.passed:
            rule_error_count += 1

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
        missing_profile_fields=profile.missing_fields(schema=schema),
        warnings=warnings,
        rule_checks=rule_checks,
        rule_error_count=rule_error_count,
        coverage_summary=coverage_summary,
        retrieval_degraded=retrieval_degraded,
    )
