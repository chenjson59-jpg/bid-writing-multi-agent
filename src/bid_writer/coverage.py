"""三层覆盖计算（T-012，AGENTS.md 5.2）：计划 / 正文 / 证据。

纯函数、零模型调用：
- 计划覆盖：评分因素是否分配给章节（精确名称匹配，与历史行为一致）；
- 正文覆盖：非占位章节在剔除占位符后，正文确实出现因素名；
- 证据覆盖：正文命中章节同时具备同 doc_id 的检索片段与合法范围内的引用标记。

三层构成严格漏斗：evidence ⇒ body ⇒ plan。
"""
from __future__ import annotations

from typing import Any

from src.bid_writer.rules import _chunk_doc_id, _citation_markers
from src.bid_writer.schema import (
    PLACEHOLDER_RE,
    STRATEGY_PLACEHOLDER,
    SectionDraft,
    normalize_title,
)

# 缺口层级的固定顺序，保证 gaps 输出稳定
_LAYER_PLAN = "plan"
_LAYER_BODY = "body"
_LAYER_EVIDENCE = "evidence"


def compute_evaluation_coverage(
    tender, drafts: list[SectionDraft], *, doc_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """计算逐因素三层覆盖明细与报告级漏斗汇总。"""
    entries: list[dict[str, Any]] = []
    for factor in tender.evaluation_factors:
        name = str(factor.get("name") or "")
        candidates = [
            draft for draft in drafts if name and name in draft.plan.scoring_factors
        ]
        plan_ids = [draft.plan.id for draft in candidates]
        plan_covered = bool(plan_ids)

        body_drafts = [
            draft
            for draft in candidates
            if draft.plan.strategy != STRATEGY_PLACEHOLDER
            and _body_mentions(draft.markdown, name)
        ]
        body_ids = [draft.plan.id for draft in body_drafts]
        body_covered = plan_covered and bool(body_drafts)

        evidence_drafts = [
            draft for draft in body_drafts if _has_valid_evidence(draft, doc_id)
        ]
        evidence_ids = [draft.plan.id for draft in evidence_drafts]
        evidence_covered = body_covered and bool(evidence_drafts)

        entries.append(
            {
                "name": name,
                "score": factor.get("score"),
                "category": factor.get("category"),
                # covered 保留为计划层别名，兼容补写链路与旧消费方
                "covered": plan_covered,
                "plan_covered": plan_covered,
                "body_covered": body_covered,
                "evidence_covered": evidence_covered,
                "section_ids": plan_ids,
                "body_section_ids": body_ids,
                "evidence_section_ids": evidence_ids,
                "gaps": _gaps(plan_covered, body_covered, evidence_covered),
            }
        )

    summary = {
        layer: {
            "total": len(entries),
            "covered": sum(1 for e in entries if e[f"{layer}_covered"]),
            "gaps": [e["name"] for e in entries if not e[f"{layer}_covered"]],
        }
        for layer in (_LAYER_PLAN, _LAYER_BODY, _LAYER_EVIDENCE)
    }
    return entries, summary


def _gaps(plan_covered: bool, body_covered: bool, evidence_covered: bool) -> list[str]:
    """按漏斗顺序返回该因素尚未达到的层级。"""
    if not plan_covered:
        return [_LAYER_PLAN, _LAYER_BODY, _LAYER_EVIDENCE]
    if not body_covered:
        return [_LAYER_BODY, _LAYER_EVIDENCE]
    if not evidence_covered:
        return [_LAYER_EVIDENCE]
    return []


def _body_mentions(markdown: str, factor_name: str) -> bool:
    """剔除占位符后做去空白子串匹配：占位符内的因素名不算正文响应。"""
    body = PLACEHOLDER_RE.sub("", markdown)
    return normalize_title(factor_name) in normalize_title(body)


def _has_valid_evidence(draft: SectionDraft, doc_id: str) -> bool:
    """证据覆盖：至少一条同 doc_id 片段，且正文有落在片段数范围内的引用标记。"""
    if not doc_id or not draft.retrieved:
        return False
    same_doc = any(_chunk_doc_id(chunk) == doc_id for chunk in draft.retrieved)
    if not same_doc:
        return False
    limit = len(draft.retrieved)
    return any(1 <= int(marker.strip("[]")) <= limit for marker in _citation_markers(draft.markdown))
