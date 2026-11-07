"""Prompt 回归：JSON 输出示例必须真实包含在提示词文本中，且 format 不报错。"""

from src.bid_writer.prompts import (
    _PLANNER_SYSTEM,
    _REVIEWER_SYSTEM,
    build_reviser_prompt,
)
from src.bid_writer.schema import BidderProfile, ReviewResult, SectionPlan
from src.extraction.schema import TenderDocument


def test_planner_prompt_contains_json_example_and_formats():
    rendered = _PLANNER_SYSTEM.format(max_sections=8)
    assert '"sections"' in rendered
    assert '{"title": "章节标题"' in rendered
    assert "不超过 8 个" in rendered


def test_reviewer_prompt_contains_json_example():
    assert '"covered_points"' in _REVIEWER_SYSTEM
    assert '"placeholder_count"' in _REVIEWER_SYSTEM


# ---- T-014 Reviser 完整上下文 ----


def _reviser_fixtures(retrieved=None):
    tender = TenderDocument(
        doc_id="0123456789abcdef",
        doc_type="tender_file",
        project_name="示范工程",
        tender_no="ZB-2026-001",
        evaluation_factors=[{"name": "施工组织设计", "category": "技术", "score": 40}],
    )
    profile = BidderProfile(company_name="示范建工有限公司")
    plan = SectionPlan(
        id="section_1",
        title="施工组织设计",
        order=1,
        strategy="generate",
        target_words=1200,
        scoring_factors=["施工组织设计"],
        requirements="按评分因素完整响应",
    )
    review = ReviewResult(score=60, issues=["针对性不足"], suggestions=["补充工艺细节"])
    return plan, tender, profile, review, retrieved or []


def test_reviser_prompt_contains_full_context():
    plan, tender, profile, review, retrieved = _reviser_fixtures(
        retrieved=[{"id": "0123456789abcdef:0000", "text": "关键工艺要求原文片段", "metadata": {}}]
    )
    messages = build_reviser_prompt(
        plan, "这是当前草稿。", review, tender, profile, retrieved
    )
    user = messages[1]["content"]

    # 章节计划与评分因素
    assert "施工组织设计" in user
    assert "按评分因素完整响应" in user
    # 招标摘要与投标人资料
    assert "示范工程" in user
    assert "ZB-2026-001" in user
    assert "示范建工有限公司" in user
    # 检索证据（编号与原文）
    assert "[1]" in user
    assert "关键工艺要求原文片段" in user
    # 当前草稿与评审意见
    assert "这是当前草稿。" in user
    assert "针对性不足" in user
    assert "补充工艺细节" in user


def test_reviser_prompt_empty_retrieval_is_explicit():
    plan, tender, profile, review, retrieved = _reviser_fixtures(retrieved=[])
    messages = build_reviser_prompt(plan, "草稿", review, tender, profile, retrieved)

    assert "（无检索结果）" in messages[1]["content"]


def test_reviser_prompt_system_keeps_hard_rules():
    plan, tender, profile, review, retrieved = _reviser_fixtures()
    system = build_reviser_prompt(plan, "草稿", review, tender, profile, retrieved)[0]["content"]

    assert "不得编造" in system
    assert "报价" in system
