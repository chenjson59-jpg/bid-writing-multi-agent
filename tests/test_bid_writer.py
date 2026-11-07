"""投标编写测试（A18–A20）：大纲生成、章节循环、覆盖报告。"""
from __future__ import annotations

from datetime import datetime

from src.bid_writer import agents, assembly
from src.bid_writer.agents import plan_sections, section_agent
from src.bid_writer.schema import (
    BidderProfile,
    BidReport,
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
    assert choose_strategy("投标函") == "llm_fill"
    assert choose_strategy("授权委托书") == "llm_fill"
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


# ---- T-017 检索降级标志 ----


def _single_draft():
    return [
        SectionDraft(
            SectionPlan(
                id="s1", title="施工组织设计", order=1, strategy="generate",
                scoring_factors=["施工组织设计"],
            ),
            "正文内容",
            ReviewResult(score=80),
            80,
            80,
            0,
        )
    ]


def test_build_report_retrieval_degraded_flag():
    tender = make_tender()
    report = assembly.build_report(
        tender.doc_id, tender.project_name, "公司", _single_draft(),
        tender, BidderProfile(company_name="公司"), datetime(2026, 1, 1),
        retrieval_degraded=True,
    )
    assert report.retrieval_degraded is True
    assert report.summary()["retrieval_degraded"] is True


def test_build_report_retrieval_degraded_defaults_false_and_legacy_load():
    tender = make_tender()
    report = assembly.build_report(
        tender.doc_id, tender.project_name, "公司", _single_draft(),
        tender, BidderProfile(company_name="公司"), datetime(2026, 1, 1),
    )
    assert report.retrieval_degraded is False
    assert report.summary()["retrieval_degraded"] is False
    # 旧产物 report.json 无该键时必须兼容加载
    payload = {k: v for k, v in report.to_dict().items() if k != "retrieval_degraded"}
    assert BidReport(**payload).retrieval_degraded is False


# ---- T-013 确定性规则检查接入 ----


def _retrieved(doc_id: str = "0123456789abcdef") -> list[dict]:
    return [
        {"id": f"{doc_id}:0000", "text": "片段一", "metadata": {"doc_id": doc_id}},
        {"id": f"{doc_id}:0001", "text": "片段二", "metadata": {"doc_id": doc_id}},
    ]


def test_section_agent_attaches_rule_check_clean():
    plan = SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate")
    caller = ScriptCaller(
        [
            (False, "依据原文[1][2]展开的正文"),
            (True, '{"score": 80, "issues": [], "suggestions": []}'),
        ]
    )
    draft = section_agent(
        plan, make_tender(), BidderProfile(), _retrieved(),
        min_score=75, max_revisions=0, caller=caller,
    )

    assert draft.rule_check is not None
    assert draft.rule_check.passed is True
    assert draft.rule_check.citation_markers == ["[1]", "[2]"]
    # 规则结果与 LLM 语义评审分开存放
    assert draft.review.score == 80
    assert draft.to_dict()["rule_check"]["passed"] is True


def test_section_agent_flags_citation_violation():
    plan = SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate")
    caller = ScriptCaller(
        [
            (False, "只有两条片段却引用[5]"),
            (True, '{"score": 90, "issues": [], "suggestions": []}'),
        ]
    )
    draft = section_agent(
        plan, make_tender(), BidderProfile(), _retrieved(),
        min_score=75, max_revisions=0, caller=caller,
    )

    codes = [e["code"] for e in draft.rule_check.errors]
    assert "CITE_OUT_OF_RANGE" in codes
    # 规则错误不改变 LLM 评分流程与修订行为
    assert draft.score_after == 90


def test_section_agent_placeholder_branch_also_checked():
    plan = SectionPlan(id="s2", title="投标报价", order=2, strategy="placeholder")
    draft = section_agent(
        plan, make_tender(), BidderProfile(), [],
        min_score=75, max_revisions=1, caller=ScriptCaller([]),
    )

    assert draft.rule_check is not None
    assert draft.rule_check.passed is True
    assert draft.rule_check.placeholders == ["【待填写：投标报价】"]


def test_section_draft_rule_check_defaults_none_and_serializes():
    draft = _draft("第一章", "正文")
    assert draft.rule_check is None
    assert draft.to_dict()["rule_check"] is None


def test_build_report_aggregates_rule_checks_separate_from_warnings():
    tender = make_tender()
    good = SectionDraft(
        SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate",
                    scoring_factors=["施工组织设计"]),
        "依据[1]撰写",
        ReviewResult(score=80), 80, 80, 0,
        retrieved=_retrieved(),
    )
    bad = SectionDraft(
        SectionPlan(id="s2", title="项目管理机构", order=2, strategy="generate"),
        "无检索却引用[1]，且写 ¥999",
        ReviewResult(score=70), 70, 70, 0,
        retrieved=[],
    )
    report = assembly.build_report(
        tender.doc_id, tender.project_name, "公司", [good, bad],
        tender, BidderProfile(company_name="公司"), datetime(2026, 1, 1),
    )

    assert len(report.rule_checks) == 2
    assert report.rule_error_count >= 1
    section_rules = {s["id"]: s["rule_check"] for s in report.sections}
    assert section_rules["s1"]["passed"] is True
    assert section_rules["s2"]["passed"] is False
    codes = {e["code"] for e in section_rules["s2"]["errors"]}
    assert {"CITE_EMPTY", "QUOTE_FORBIDDEN"} <= codes
    # 规则结果独立成字段，不混入既有 warnings（warnings 只含未覆盖评分因素）
    assert all("CITE_" not in w and "QUOTE_" not in w for w in report.warnings)
    assert report.summary()["rule_error_count"] == report.rule_error_count


def test_build_report_recomputes_rules_for_restored_drafts():
    # 旧产物还原的草稿没有 rule_check，build_report 基于磁盘正文与 retrieved 重算补齐
    tender = make_tender()
    restored = SectionDraft(
        SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate",
                    scoring_factors=["施工组织设计"]),
        "越界引用[8]",
        ReviewResult(score=80), 80, 80, 0,
        retrieved=_retrieved(),
    )
    assert restored.rule_check is None

    report = assembly.build_report(
        tender.doc_id, tender.project_name, "公司", [restored],
        tender, BidderProfile(company_name="公司"), datetime(2026, 1, 1),
    )

    assert report.rule_checks[0]["passed"] is False
    assert report.rule_error_count == 1


# ---- T-012 三层覆盖集成 ----


def test_build_report_three_layer_coverage():
    tender = make_tender()
    doc_id = tender.doc_id
    chunk = {"id": f"{doc_id}:0000", "text": "片段", "metadata": {"doc_id": doc_id}}
    drafts = [
        # 证据覆盖：正文含因素名 + 合法引用 + 同 doc_id 片段
        SectionDraft(
            SectionPlan(id="s1", title="施工组织设计", order=1, strategy="generate",
                        scoring_factors=["施工组织设计"]),
            "施工组织设计依据[1]展开。",
            ReviewResult(score=80), 80, 80, 0, retrieved=[chunk],
        ),
        # 仅正文覆盖：有因素名无引用
        SectionDraft(
            SectionPlan(id="s2", title="企业业绩", order=2, strategy="generate",
                        scoring_factors=["企业业绩"]),
            "企业业绩情况说明。",
            ReviewResult(score=80), 80, 80, 0, retrieved=[],
        ),
        # 仅计划覆盖：占位章节
        SectionDraft(
            SectionPlan(id="s3", title="投标报价", order=3, strategy="placeholder",
                        scoring_factors=["投标报价"]),
            "【待填写：投标报价】",
            ReviewResult(score=100), 100, 100, 0, retrieved=[],
        ),
    ]
    report = assembly.build_report(
        doc_id, tender.project_name, "公司", drafts,
        tender, BidderProfile(company_name="公司"), datetime(2026, 1, 1),
    )

    by_name = {e["name"]: e for e in report.evaluation_coverage}
    assert by_name["施工组织设计"]["evidence_covered"] is True
    assert by_name["施工组织设计"]["gaps"] == []
    assert by_name["企业业绩"]["plan_covered"] is True
    assert by_name["企业业绩"]["body_covered"] is True
    assert by_name["企业业绩"]["evidence_covered"] is False
    assert by_name["投标报价"]["plan_covered"] is True
    assert by_name["投标报价"]["body_covered"] is False
    assert by_name["其他因素"]["plan_covered"] is False
    # 兼容字段 covered 仍表示计划层
    assert all(e["covered"] == e["plan_covered"] for e in report.evaluation_coverage)

    summary = report.coverage_summary
    assert summary["plan"] == {"total": 4, "covered": 3, "gaps": ["其他因素"]}
    assert summary["body"]["covered"] == 2
    assert summary["evidence"]["covered"] == 1
    # 漏斗单调
    assert summary["plan"]["covered"] >= summary["body"]["covered"] >= summary["evidence"]["covered"]

    bid_summary = report.summary()
    assert bid_summary["evaluation_factors_covered"] == 3
    assert bid_summary["body_factors_covered"] == 2
    assert bid_summary["evidence_factors_covered"] == 1
    # 警告仍以计划层为准：只有完全未分配的因素产生警告
    assert any("其他因素" in w for w in report.warnings)
    assert not any("投标报价" in w for w in report.warnings)


def test_bid_report_loading_old_data_without_coverage_fields():
    # 旧 report.json 没有 coverage_summary / rule_checks 等新字段，依赖 dataclass 默认值加载
    from src.bid_writer.schema import BidReport

    old_data = {
        "doc_id": "doc",
        "project_name": "项目",
        "bidder": "公司",
        "generated_at": "2026-01-01T00:00:00",
        "total_sections": 1,
        "total_words": 10,
        "sections": [],
        "format_coverage": [],
        "evaluation_coverage": [
            {"name": "技术方案", "score": 10, "category": "技术",
             "covered": True, "section_ids": ["s1"]}
        ],
        "placeholders": [],
        "missing_profile_fields": [],
        "warnings": [],
    }
    report = BidReport(**old_data)

    assert report.coverage_summary == {}
    assert report.rule_checks == []
    assert report.rule_error_count == 0
    summary = report.summary()
    assert summary["body_factors_covered"] == 0
    assert summary["evidence_factors_covered"] == 0
    assert summary["evaluation_factors_covered"] == 1


# ---- T-014 Reviser 完整上下文与修订上限 ----


class _RecordingCaller:
    """记录每次调用的消息，用于断言 Reviser 实际收到的上下文。"""

    def __init__(self, script: list[tuple[bool, str]]):
        self.script = list(script)
        self.messages: list[list] = []
        self.json_modes: list[bool] = []

    def generate(self, messages, json_mode):
        self.messages.append(messages)
        self.json_modes.append(json_mode)
        content = self.script.pop(0)[1] if self.script else ""
        return {"content": content, "finish_reason": "stop", "json_mode_used": json_mode}


def _revision_plan() -> SectionPlan:
    return SectionPlan(
        id="s1", title="施工组织设计", order=1, strategy="generate",
        scoring_factors=["施工组织设计"],
    )


def _revision_retrieved() -> list[dict]:
    return [
        {
            "id": "0123456789abcdef:0000",
            "text": "仅限本测试出现的检索证据原文 XYZ-777",
            "metadata": {"doc_id": "0123456789abcdef"},
        }
    ]


def test_reviser_receives_full_context_and_respects_cap():
    low = '{"score": 60, "issues": ["针对性不足"], "suggestions": ["补充"]}'
    caller = _RecordingCaller(
        [
            (False, "初稿"),
            (True, low),
            (False, "修订稿一"),
            (True, low),
            (False, "修订稿二"),
            (True, low),
        ]
    )
    profile = BidderProfile(company_name="上下文测试公司")
    draft = section_agent(
        _revision_plan(), make_tender(project_name="上下文测试项目"), profile,
        _revision_retrieved(),
        min_score=75, max_revisions=2, caller=caller,
    )

    # 上限锁定：两轮修订后即使仍不达标也停止，共 1 次写作 + 2 轮（修订+复评）
    assert draft.revisions == 2
    assert len(caller.messages) == 6
    # 第 3、5 次调用是 Reviser（非 JSON 模式，且不是首次 Writer）
    reviser_texts = [
        caller.messages[i][1]["content"]
        for i in (2, 4)
        if caller.json_modes[i] is False
    ]
    assert len(reviser_texts) == 2
    for content in reviser_texts:
        # 四类上下文全部进入修订提示词
        assert "仅限本测试出现的检索证据原文 XYZ-777" in content
        assert "[1]" in content
        assert "上下文测试公司" in content
        assert "上下文测试项目" in content
        assert "施工组织设计" in content
        assert "初稿" in content or "修订稿一" in content


def test_max_revisions_zero_skips_reviser():
    caller = _RecordingCaller(
        [(False, "初稿"), (True, '{"score": 50, "issues": ["差"], "suggestions": []}')]
    )
    draft = section_agent(
        _revision_plan(), make_tender(), BidderProfile(), _revision_retrieved(),
        min_score=75, max_revisions=0, caller=caller,
    )

    assert draft.revisions == 0
    assert len(caller.messages) == 2  # 仅 Writer + Reviewer
    assert draft.markdown == "初稿"
