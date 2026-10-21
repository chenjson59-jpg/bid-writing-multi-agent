"""投标编写测试（A18–A20）：大纲生成、章节循环、覆盖报告。"""
from __future__ import annotations

from datetime import datetime

from src.bid_writer import agents, assembly
from src.bid_writer.agents import plan_sections, section_agent
from src.bid_writer.schema import (
    BidderProfile,
    ReviewResult,
    SectionDraft,
    SectionPlan,
    choose_strategy,
)
from src.extraction.schema import TenderDocument


class ScriptCaller:
    """按脚本顺序返回预设内容，记录每次调用是否 JSON 模式。"""

    def __init__(self, script: list[tuple[bool, str]]):
        self.script = list(script)
        self.calls: list[bool] = []

    def generate(self, messages, json_mode):
        self.calls.append(json_mode)
        if self.script:
            _, content = self.script.pop(0)
            return {"content": content, "finish_reason": "stop", "json_mode_used": json_mode}
        return {"content": "", "finish_reason": "stop", "json_mode_used": json_mode}


def make_tender(**overrides) -> TenderDocument:
    data = {
        "doc_id": "0123456789abcdef",
        "doc_type": "tender_file",
        "project_name": "示例项目",
        "tender_no": "示例编号",
        "evaluation_factors": [
            {"name": "施工组织设计", "category": "技术", "score": 40},
            {"name": "投标报价", "category": "报价", "score": 30},
            {"name": "企业业绩", "category": "商务", "score": 20},
            {"name": "其他因素", "category": "其他", "score": 10},
        ],
        "bid_format_items": [
            {"title": "投标函", "content": ""},
            {"title": "施工组织设计", "content": ""},
        ],
    }
    data.update(overrides)
    return TenderDocument(**data)


# ---- 章节策略与资料 ----

def test_choose_strategy():
    assert choose_strategy("施工组织设计") == "generate"
    assert choose_strategy("项目管理机构") == "generate"
    assert choose_strategy("投标函") == "fill_template"
    assert choose_strategy("授权委托书") == "fill_template"
    assert choose_strategy("其他内容") == "placeholder"


def test_profile_missing_fields():
    profile = BidderProfile(company_name="公司", legal_representative="张三")
    missing = profile.missing_fields()
    assert "公司名称" not in missing
    assert "法定代表人" not in missing
    assert "注册资本" in missing
    assert "联系方式" in missing


# ---- A18 大纲生成 ----

def test_fallback_outline_contains_expected_sections():
    tender = make_tender()
    plans = agents._fallback_outline(tender, 1200)
    titles = [p.title for p in plans]
    assert "施工组织设计" in titles
    assert "投标函" in titles
    assert "投标报价" in titles
    assert "其他评分因素" in titles


def test_plan_sections_falls_back_when_planner_fails():
    tender = make_tender()
    caller = ScriptCaller([(True, "不是 JSON")])
    plans = plan_sections(
        tender, BidderProfile(), target_words=1200, max_sections=0, caller=caller
    )
    assert len(plans) >= 1


def test_normalize_outline_adds_missing_format_item():
    tender = make_tender(bid_format_items=[{"title": "投标函"}, {"title": "授权委托书"}])
    plans = [SectionPlan(id="section_1", title="投标函", order=1)]
    result = agents._normalize_outline(plans, tender, max_sections=0)
    titles = [p.title for p in result]
    assert "授权委托书" in titles


def test_normalize_outline_maps_scoring_to_title():
    tender = make_tender()
    plans = [SectionPlan(id="section_1", title="施工组织设计", order=1)]
    result = agents._normalize_outline(plans, tender, max_sections=0)
    construction = next(p for p in result if "施工组织设计" in p.title)
    assert "施工组织设计" in construction.scoring_factors


def test_order_and_limit_keeps_generate_first():
    plans = [
        SectionPlan(id="a", title="占位章", order=1, strategy="placeholder"),
        SectionPlan(id="b", title="技术章", order=2, strategy="generate"),
        SectionPlan(id="c", title="报价章", order=3, strategy="placeholder"),
    ]
    result = agents._order_and_limit(plans, max_sections=2)
    # generate 章节优先保留，最终按 order 重排：占位章(1)、技术章(2)，报价章被截断
    assert [p.title for p in result] == ["占位章", "技术章"]
    assert result[0].order == 1
    assert result[1].order == 2


# ---- A19 章节写作循环 ----

def test_section_agent_placeholder_no_model_call():
    plan = SectionPlan(id="s1", title="投标函", order=1, strategy="placeholder")
    caller = ScriptCaller([])
    draft = section_agent(
        plan, make_tender(), BidderProfile(), [], min_score=75, max_revisions=1, caller=caller
    )
    assert draft.markdown == "【待填写：投标函】\n"
    assert draft.score_after == 100
    assert caller.calls == []


def test_section_agent_no_revision_when_score_ok():
    plan = SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate")
    caller = ScriptCaller(
        [
            (False, "这是正文"),
            (True, '{"score": 80, "issues": [], "suggestions": []}'),
        ]
    )
    draft = section_agent(
        plan, make_tender(), BidderProfile(), [], min_score=75, max_revisions=1, caller=caller
    )
    assert draft.markdown == "这是正文"
    assert draft.revisions == 0
    assert draft.score_after == 80


def test_section_agent_revises_and_adopts():
    plan = SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate")
    caller = ScriptCaller(
        [
            (False, "初稿"),
            (True, '{"score": 60, "issues": ["不够详细"], "suggestions": ["补充"]}'),
            (False, "修订稿"),
            (True, '{"score": 85, "issues": [], "suggestions": []}'),
        ]
    )
    draft = section_agent(
        plan, make_tender(), BidderProfile(), [], min_score=75, max_revisions=1, caller=caller
    )
    assert draft.markdown == "修订稿"
    assert draft.revisions == 1
    assert draft.score_after == 85


def test_section_agent_keeps_best_when_revision_worse():
    plan = SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate")
    caller = ScriptCaller(
        [
            (False, "初稿"),
            (True, '{"score": 60, "issues": [], "suggestions": []}'),
            (False, "更差稿"),
            (True, '{"score": 40, "issues": [], "suggestions": []}'),
        ]
    )
    draft = section_agent(
        plan, make_tender(), BidderProfile(), [], min_score=75, max_revisions=1, caller=caller
    )
    assert draft.markdown == "初稿"
    assert draft.revisions == 1
    assert draft.score_after == 60


def test_section_agent_review_failure_returns_zero():
    plan = SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate")
    caller = ScriptCaller(
        [
            (False, "正文"),
            (True, "不是 JSON"),
        ]
    )
    draft = section_agent(
        plan, make_tender(), BidderProfile(), [], min_score=75, max_revisions=0, caller=caller
    )
    assert draft.score_after == 0


# ---- A20 覆盖报告 ----

def _draft(title, markdown, strategy="generate", scoring_factors=None, score=80):
    plan = SectionPlan(
        id="s1", title=title, order=1, strategy=strategy, scoring_factors=scoring_factors or []
    )
    return SectionDraft(plan, markdown, ReviewResult(score=score), score, score, 0)


def test_assemble_markdown_structure():
    sections = [_draft("第一章", "正文一")]
    markdown = assembly.assemble_markdown("项目", "公司", sections, "2026年01月01日")
    assert "# 项目" in markdown
    assert "**投标人：公司**" in markdown
    assert "## 第一章" in markdown
    assert "正文一" in markdown


def test_assemble_markdown_empty_company_placeholder():
    sections = [_draft("第一章", "正文一")]
    markdown = assembly.assemble_markdown("项目", "", sections, "2026年01月01日")
    assert "【待填写：公司名称】" in markdown


def test_build_report_coverage_and_placeholders():
    tender = make_tender()
    drafts = [
        SectionDraft(
            SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate", scoring_factors=["施工组织设计"]),
            "正文【待填写：法定代表人】",
            ReviewResult(score=80),
            80,
            80,
            0,
        )
    ]
    profile = BidderProfile(company_name="公司")
    report = assembly.build_report(
        "doc", "项目", "公司", drafts, tender, profile, datetime(2026, 1, 1, 12, 0, 0)
    )

    format_coverage = {c["title"]: c for c in report.format_coverage}
    assert format_coverage["施工组织设计"]["section_id"] == "s1"
    assert format_coverage["投标函"]["section_id"] is None

    evaluation = {c["name"]: c for c in report.evaluation_coverage}
    assert evaluation["施工组织设计"]["covered"] is True
    assert evaluation["投标报价"]["covered"] is False

    assert "法定代表人" in report.missing_profile_fields
    assert "【待填写：法定代表人】" in report.placeholders
    assert any("投标报价" in w for w in report.warnings)
