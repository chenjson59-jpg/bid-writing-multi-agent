"""Prompt 回归：JSON 输出示例必须真实包含在提示词文本中，且 format 不报错。"""

from src.bid_writer.prompts import (
    _PLANNER_SYSTEM,
    _REVIEWER_SYSTEM,
    build_planner_prompt,
    build_reviser_prompt,
    build_writer_prompt,
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


# ---- T-040 prompt 外发白名单收敛（7 字段，移出字段与 notes 一律不外发）----

_WHITELIST_VALUES = {
    "company_name": "白名单测试公司有限公司T040",
    "legal_representative": "白名单法人孙八T040",
    "registered_capital": "9000万元T040",
    "established_date": "2001-03-05",
    "address": "白名单路99号T040",
    "qualifications": ["白名单资质甲级T040"],
    "certifications": ["白名单认证ISO777T040"],
}

# 移出字段的醒目具体值：任何一个出现在 prompt 文本里都意味着高敏数据外发
_SECRET_VALUES = [
    "阿尔法保密供水工程T040",
    "保密人员钱七T040",
    "年营收99亿T040保密",
    "保密联系人周九T040",
    "13900007777",
    "保密补充说明备注T040",
]

# 移出字段与 notes 的完整中文标签（不用单字，避免与系统规则里的“人员/业绩/财务”混淆）
_REMOVED_LABELS = ["类似项目业绩", "项目人员", "财务数据", "联系方式", "补充说明"]


def _whitelist_profile() -> BidderProfile:
    return BidderProfile(
        **_WHITELIST_VALUES,
        similar_projects=[{"name": "阿尔法保密供水工程T040", "amount": "保密金额T040"}],
        personnel=[{"name": "保密人员钱七T040", "role": "项目经理"}],
        financials={"2024": "年营收99亿T040保密"},
        contact={"person": "保密联系人周九T040", "phone": "13900007777"},
        notes=["保密补充说明备注T040"],
    )


def _profile_prompt_texts(profile: BidderProfile) -> list[str]:
    tender = TenderDocument(
        doc_id="0123456789abcdef",
        doc_type="tender_file",
        project_name="白名单示范工程T040",
        tender_no="ZB-2026-T040",
        evaluation_factors=[{"name": "施工组织设计", "category": "技术", "score": 40}],
    )
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
    planner = build_planner_prompt(tender, profile, target_words=1200, max_sections=8)
    writer = build_writer_prompt(plan, tender, profile, retrieved=[])
    reviser = build_reviser_prompt(plan, "当前草稿T040", review, tender, profile, retrieved=[])
    return [
        "\n".join(message["content"] for message in planner),
        "\n".join(message["content"] for message in writer),
        "\n".join(message["content"] for message in reviser),
    ]


def test_prompt_whitelist_keeps_seven_fields_and_drops_sensitive_ones():
    """三类 prompt 只外发 7 个白名单字段；移出 4 字段与 notes 的值和标签均不出现。"""
    texts = _profile_prompt_texts(_whitelist_profile())
    assert len(texts) == 3
    for text in texts:
        # 白名单 7 字段的具体值必须正常外发
        for value in _WHITELIST_VALUES.values():
            if isinstance(value, list):
                for item in value:
                    assert item in text
            else:
                assert value in text
        # 移出字段与 notes 的具体值一律不得出现
        for secret in _SECRET_VALUES:
            assert secret not in text
        # 移出字段与 notes 的完整中文标签不得出现
        for label in _REMOVED_LABELS:
            assert label not in text


def test_prompt_with_only_legacy_fields_renders_empty_placeholders():
    """旧资料仅含移出字段时不抛异常：白名单行以（空）占位，旧值与标签不外发。"""
    legacy_only = BidderProfile(
        similar_projects=[{"name": "阿尔法保密供水工程T040"}],
        personnel=[{"name": "保密人员钱七T040"}],
        financials={"2024": "年营收99亿T040保密"},
        contact={"phone": "13900007777"},
        notes=["保密补充说明备注T040"],
    )
    texts = _profile_prompt_texts(legacy_only)
    for text in texts:
        assert "（空）" in text
        for secret in _SECRET_VALUES:
            assert secret not in text
        for label in _REMOVED_LABELS:
            assert label not in text
