"""投标编写代理（核心算法 A18 大纲生成、A19 章节写作循环）。

- `plan_sections`：策划章节大纲（LLM 策划 + 确定性兜底 + 归一化）。
- `section_agent`：单个章节的「检索 → 写作 → 评审 → 修订」循环。

模型调用通过 `ModelCaller` 协议注入，测试可用 fake 隔离网络；默认使用
DeepSeek。评审失败降级为 0 分，写作/修订失败则向上抛 `BidWriteError`。
"""
from __future__ import annotations

from typing import Any, Protocol

from src.bid_writer.prompts import (
    build_planner_prompt,
    build_reviewer_prompt,
    build_reviser_prompt,
    build_writer_prompt,
)
from src.bid_writer.schema import (
    STRATEGY_GENERATE,
    STRATEGY_PLACEHOLDER,
    ReviewResult,
    SectionDraft,
    SectionPlan,
    choose_strategy,
    normalize_title,
)
from src.errors import BidWriteError
from src.extraction.extractor import DeepSeekModelCaller, extract_json


class ModelCaller(Protocol):
    """模型调用协议，与抽取阶段的 `ModelCaller` 保持一致。"""

    def generate(self, messages: list[dict], json_mode: bool) -> dict[str, Any]:
        """返回 `{"content", "finish_reason", "json_mode_used"}`。"""
        ...


# ---------------------------------------------------------------------------
# A18 大纲生成
# ---------------------------------------------------------------------------

def plan_sections(
    tender,
    profile,
    *,
    target_words: int,
    max_sections: int,
    caller: ModelCaller,
) -> list[SectionPlan]:
    """生成并归一化章节大纲（A18）。"""
    plans = _planner_llm(tender, profile, target_words, max_sections, caller)
    if not plans:
        plans = _fallback_outline(tender, target_words)
    return _normalize_outline(plans, tender, max_sections)


def _planner_llm(tender, profile, target_words, max_sections, caller) -> list[SectionPlan]:
    """LLM 策划（8.1.1），单条非法跳过，整体异常返回空列表。"""
    try:
        messages = build_planner_prompt(tender, profile, target_words, max_sections)
        raw = _generate_json(caller, messages)
    except Exception:  # noqa: BLE001 - 策划失败回退确定性大纲
        return []

    plans: list[SectionPlan] = []
    for i, item in enumerate(_as_list(raw.get("sections"))):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        plans.append(
            SectionPlan(
                id=f"section_{i + 1}",
                title=title,
                order=i + 1,
                strategy=item.get("strategy") or choose_strategy(title),
                target_words=_int_value(item.get("target_words"), target_words),
                scoring_factors=_as_str_list(item.get("scoring_factors")),
                requirements=str(item.get("requirements") or ""),
                retrieval_query=str(item.get("retrieval_query") or ""),
            )
        )
    return plans


def _fallback_outline(tender, target_words: int) -> list[SectionPlan]:
    """确定性兜底大纲（8.1.2），不依赖模型。"""
    plans: list[SectionPlan] = []

    construction_item = next(
        (i for i in tender.bid_format_items if "施工组织设计" in i.get("title", "")),
        None,
    )
    if construction_item:
        construction_factors = [
            f.get("name", "")
            for f in tender.evaluation_factors
            if "施工组织" in f.get("name", "")
        ]
        plans.append(
            _new_plan(
                "施工组织设计",
                STRATEGY_GENERATE,
                target_words,
                construction_factors,
                construction_item.get("content", ""),
            )
        )
        # 技术类评分因素子章节
        for factor in tender.evaluation_factors:
            name = factor.get("name", "")
            if (
                factor.get("category") == "技术"
                and factor.get("score") is not None
                and "施工组织设计" not in name
            ):
                plans.append(
                    _new_plan(name, STRATEGY_GENERATE, target_words, [name], "")
                )

    for item in tender.bid_format_items:
        title = item.get("title", "")
        if not title or "施工组织设计" in title:
            continue
        plans.append(
            _new_plan(
                title,
                choose_strategy(title),
                target_words,
                [],
                item.get("content", ""),
            )
        )

    quote_factors = [f.get("name", "") for f in tender.evaluation_factors if "报价" in f.get("name", "")]
    if quote_factors:
        plans.append(_new_plan("投标报价", STRATEGY_PLACEHOLDER, target_words, quote_factors, ""))

    other_factors = [
        f.get("name", "")
        for f in tender.evaluation_factors
        if f.get("category") == "其他" and f.get("score") is not None
    ]
    if other_factors:
        plans.append(_new_plan("其他评分因素", STRATEGY_GENERATE, target_words, other_factors, ""))

    return _dedup_by_title(plans)


def _new_plan(
    title: str,
    strategy: str,
    target_words: int,
    scoring_factors: list[str],
    requirements: str,
) -> SectionPlan:
    return SectionPlan(
        id="",
        title=title,
        order=0,
        strategy=strategy,
        target_words=target_words,
        scoring_factors=list(scoring_factors),
        requirements=requirements,
    )


def _normalize_outline(
    plans: list[SectionPlan], tender, max_sections: int
) -> list[SectionPlan]:
    """大纲归一化（8.1.4）：去重、补格式、映射评分、排序截断。"""
    plans = _dedup_by_title(plans)

    # 格式覆盖：缺失的格式条目追加占位章节
    for item in tender.bid_format_items:
        title = item.get("title", "")
        if not title:
            continue
        if _title_covered(plans, title):
            continue
        plans.append(
            _new_plan(title, STRATEGY_PLACEHOLDER, 0, [], item.get("content", ""))
        )
        plans[-1].order = 10**6

    _map_scoring_factors(plans, tender)
    return _order_and_limit(plans, max_sections)


def _dedup_by_title(plans: list[SectionPlan]) -> list[SectionPlan]:
    """规范化标题去重，保留第一条（8.1.4.1）。"""
    seen: set[str] = set()
    result: list[SectionPlan] = []
    for plan in plans:
        key = normalize_title(plan.title)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(plan)
    return result


def _title_covered(plans: list[SectionPlan], title: str) -> bool:
    """检查标题是否已被某章节标题互相包含（8.1.4.3）。"""
    key = normalize_title(title)
    if not key:
        return True
    return any(key in normalize_title(p.title) or normalize_title(p.title) in key for p in plans)


def _map_scoring_factors(plans: list[SectionPlan], tender) -> None:
    """评分因素映射（8.1.4.4），未归属的因素留待覆盖报告标记。"""
    for factor in tender.evaluation_factors:
        name = factor.get("name", "")
        if not name:
            continue
        if any(name in p.scoring_factors for p in plans):
            continue

        matched = False
        for plan in plans:
            if normalize_title(name) in normalize_title(plan.title):
                plan.scoring_factors.append(name)
                matched = True
                break
        if matched:
            continue

        if factor.get("score") is None:
            continue
        for keyword in _category_keywords(factor.get("category", "其他")):
            target = next((p for p in plans if keyword in p.title), None)
            if target is not None:
                target.scoring_factors.append(name)
                break


def _category_keywords(category: str) -> tuple[str, ...]:
    return {
        "技术": ("施工组织设计", "技术"),
        "商务": ("项目管理机构", "商务"),
        "报价": ("投标报价", "报价"),
    }.get(category, ("其他评分", "其他资料", "其他"))


def _order_and_limit(plans: list[SectionPlan], max_sections: int) -> list[SectionPlan]:
    """排序与截断（8.1.4.5）：优先保留 generate，再按 order 重排重编号。"""
    plans = sorted(plans, key=lambda p: p.order)
    if max_sections > 0 and len(plans) > max_sections:
        generate = [p for p in plans if p.strategy == STRATEGY_GENERATE]
        others = [p for p in plans if p.strategy != STRATEGY_GENERATE]
        kept = (generate + others)[:max_sections]
        kept.sort(key=lambda p: p.order)
        plans = kept
    return _renumber(plans)


def _renumber(plans: list[SectionPlan]) -> list[SectionPlan]:
    for i, plan in enumerate(plans, 1):
        plan.id = f"section_{i}"
        plan.order = i
    return plans


# ---------------------------------------------------------------------------
# A19 章节写作循环
# ---------------------------------------------------------------------------

def section_agent(
    plan: SectionPlan,
    tender,
    profile,
    retrieved: list[dict[str, Any]],
    *,
    min_score: int,
    max_revisions: int,
    caller: ModelCaller,
) -> SectionDraft:
    """单个章节的写作—评审—修订循环（8.2.1）。"""
    if plan.strategy == STRATEGY_PLACEHOLDER:
        markdown = placeholder_section(plan)
        review = ReviewResult(score=100)
        return SectionDraft(plan, markdown, review, 100, 100, 0, retrieved)

    draft = _writer(plan, tender, profile, retrieved, caller)
    review = _reviewer(plan, draft, caller)
    score_before = review.score

    best_draft, best_review = draft, review
    revisions = 0
    while best_review.score < min_score and revisions < max_revisions:
        revised = _reviser(plan, best_draft, best_review, caller)
        revised_review = _reviewer(plan, revised, caller)
        revisions += 1
        if revised_review.score >= best_review.score:
            best_draft, best_review = revised, revised_review

    return SectionDraft(
        plan=plan,
        markdown=best_draft,
        review=best_review,
        score_before=score_before,
        score_after=best_review.score,
        revisions=revisions,
        retrieved=retrieved,
    )


def placeholder_section(plan: SectionPlan) -> str:
    """占位章节正文模板，不调用模型（8.2.1）。标题由汇总层统一添加。"""
    return f"【待填写：{plan.title}】\n"


def _writer(plan, tender, profile, retrieved, caller) -> str:
    messages = build_writer_prompt(plan, tender, profile, retrieved)
    return _generate_text(caller, messages)


def _reviewer(plan, draft: str, caller) -> ReviewResult:
    """评审失败不抛异常，返回 0 分（8.2.2）。"""
    messages = build_reviewer_prompt(plan, draft)
    raw = _generate_json(caller, messages)
    return ReviewResult(
        score=_int_value(raw.get("score"), 0),
        covered_points=_as_str_list(raw.get("covered_points")),
        issues=_as_str_list(raw.get("issues")),
        suggestions=_as_str_list(raw.get("suggestions")),
        placeholder_count=_int_value(raw.get("placeholder_count"), 0),
    )


def _reviser(plan, draft: str, review, caller) -> str:
    messages = build_reviser_prompt(plan, draft, review)
    return _generate_text(caller, messages)


# ---------------------------------------------------------------------------
# 模型调用与工具
# ---------------------------------------------------------------------------

def default_caller() -> ModelCaller:
    """默认模型调用实现（DeepSeek）。"""
    return DeepSeekModelCaller()


def _generate_text(caller: ModelCaller, messages: list[dict]) -> str:
    result = caller.generate(messages, json_mode=False)
    content = (result.get("content") or "").strip()
    if not content:
        raise BidWriteError("模型返回空内容")
    return content


def _generate_json(caller: ModelCaller, messages: list[dict]) -> dict[str, Any]:
    result = caller.generate(messages, json_mode=True)
    content = (result.get("content") or "").strip()
    if not content:
        return {}
    try:
        parsed = extract_json(content)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001 - 评审解析失败按 0 分处理
        return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _as_str_list(value: Any) -> list[str]:
    return [str(v) for v in _as_list(value) if v is not None]


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
