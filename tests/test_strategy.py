"""章节策略改名与旧值归一化测试（T-015）。"""
from __future__ import annotations

import json

from src.bid_writer.agents import plan_sections
from src.bid_writer.schema import (
    STRATEGY_GENERATE,
    STRATEGY_LLM_FILL,
    STRATEGY_PLACEHOLDER,
    BidderProfile,
    SectionPlan,
    choose_strategy,
    normalize_strategy,
)
from src.bid_writer.sections import INDEX_NAME, load_drafts, section_infos, sections_dir
from src.extraction.schema import TenderDocument


class JsonCaller:
    """返回固定 JSON 文本的 Fake ModelCaller。"""

    def __init__(self, content: str) -> None:
        self.content = content

    def generate(self, messages, json_mode):
        return {
            "content": self.content,
            "finish_reason": "stop",
            "json_mode_used": json_mode,
        }


def make_tender() -> TenderDocument:
    return TenderDocument(
        doc_id="0123456789abcdef",
        doc_type="tender_file",
        project_name="示例项目",
        evaluation_factors=[
            {"name": "施工组织设计", "category": "技术", "score": 40},
        ],
        bid_format_items=[],
    )


# ---- normalize_strategy ----

def test_normalize_strategy_maps_known_values():
    assert normalize_strategy("generate") == STRATEGY_GENERATE
    assert normalize_strategy("llm_fill") == STRATEGY_LLM_FILL
    assert normalize_strategy("placeholder") == STRATEGY_PLACEHOLDER


def test_normalize_strategy_legacy_fill_template_maps_to_llm_fill():
    assert normalize_strategy("fill_template") == STRATEGY_LLM_FILL


def test_normalize_strategy_empty_and_unknown_fall_back_to_placeholder():
    assert normalize_strategy(None) == STRATEGY_PLACEHOLDER
    assert normalize_strategy("") == STRATEGY_PLACEHOLDER
    assert normalize_strategy("totally-unknown") == STRATEGY_PLACEHOLDER


def test_normalize_strategy_respects_explicit_fallback():
    assert normalize_strategy("totally-unknown", fallback=STRATEGY_GENERATE) == STRATEGY_GENERATE


# ---- choose_strategy ----

def test_choose_strategy_returns_llm_fill_for_formal_letters():
    assert choose_strategy("投标函") == STRATEGY_LLM_FILL
    assert choose_strategy("授权委托书") == STRATEGY_LLM_FILL


# ---- SectionPlan 构造期归一化（覆盖所有磁盘还原入口）----

def test_section_plan_normalizes_legacy_value_in_post_init():
    plan = SectionPlan(id="s1", title="投标函", order=1, strategy="fill_template")
    assert plan.strategy == STRATEGY_LLM_FILL


def test_section_plan_unknown_value_becomes_placeholder():
    plan = SectionPlan(id="s1", title="投标函", order=1, strategy="totally-unknown")
    assert plan.strategy == STRATEGY_PLACEHOLDER


# ---- Planner LLM 输出兼容 ----

def test_planner_normalizes_legacy_strategy_from_llm():
    content = json.dumps(
        {"sections": [{"title": "投标函", "strategy": "fill_template"}]},
        ensure_ascii=False,
    )
    plans = plan_sections(
        make_tender(),
        BidderProfile(),
        target_words=1200,
        max_sections=0,
        caller=JsonCaller(content),
    )
    target = next(p for p in plans if p.title == "投标函")
    assert target.strategy == STRATEGY_LLM_FILL


def test_planner_unknown_strategy_falls_back_to_title_classifier():
    content = json.dumps(
        {"sections": [{"title": "投标函", "strategy": "totally-unknown"}]},
        ensure_ascii=False,
    )
    plans = plan_sections(
        make_tender(),
        BidderProfile(),
        target_words=1200,
        max_sections=0,
        caller=JsonCaller(content),
    )
    target = next(p for p in plans if p.title == "投标函")
    assert target.strategy == STRATEGY_LLM_FILL


# ---- 磁盘旧产物（sections/index.json）兼容 ----

def _legacy_bid_dir(root) -> object:
    bid_dir = root / "0123456789abcdef-20260101-000000"
    sdir = sections_dir(bid_dir)
    sdir.mkdir(parents=True)
    entry = {
        "id": "section_1",
        "title": "投标函",
        "order": 1,
        "filename": "01-section_1.md",
        "strategy": "fill_template",
        "locked": False,
        "score_before": 80,
        "score_after": 85,
        "revisions": 0,
        "plan": {
            "id": "section_1",
            "title": "投标函",
            "order": 1,
            "strategy": "fill_template",
            "target_words": 100,
            "scoring_factors": [],
            "requirements": "",
            "retrieval_query": "",
        },
    }
    (sdir / INDEX_NAME).write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
    (sdir / "01-section_1.md").write_text("投标函正文", encoding="utf-8")
    return bid_dir


def test_load_drafts_normalizes_legacy_strategy(tmp_path):
    bid_dir = _legacy_bid_dir(tmp_path)
    drafts = load_drafts(bid_dir)
    assert len(drafts) == 1
    assert drafts[0].plan.strategy == STRATEGY_LLM_FILL


def test_section_infos_normalizes_legacy_strategy(tmp_path):
    bid_dir = _legacy_bid_dir(tmp_path)
    infos = section_infos(bid_dir)
    assert infos[0]["strategy"] == STRATEGY_LLM_FILL
