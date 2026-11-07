"""三层覆盖（计划/正文/证据）纯函数测试（T-012）：全部离线，不依赖模型。"""
from __future__ import annotations

from src.bid_writer.coverage import compute_evaluation_coverage
from src.bid_writer.schema import (
    ReviewResult,
    SectionDraft,
    SectionPlan,
)

DOC_ID = "0123456789abcdef"


class _Tender:
    def __init__(self, factors):
        self.evaluation_factors = factors


def _factor(name: str, category: str = "技术", score: int = 10) -> dict:
    return {"name": name, "category": category, "score": score}


def _chunk(index: int, *, doc_id: str = DOC_ID) -> dict:
    return {
        "id": f"{doc_id}:{index:04d}",
        "text": f"片段{index}",
        "metadata": {"doc_id": doc_id},
    }


def _draft(
    section_id: str,
    title: str,
    factors: list[str],
    markdown: str,
    *,
    strategy: str = "generate",
    retrieved: list[dict] | None = None,
) -> SectionDraft:
    plan = SectionPlan(
        id=section_id, title=title, order=1, strategy=strategy, scoring_factors=factors
    )
    return SectionDraft(
        plan=plan,
        markdown=markdown,
        review=ReviewResult(score=80),
        score_before=80,
        score_after=80,
        revisions=0,
        retrieved=retrieved or [],
    )


def _entry(entries: list[dict], name: str) -> dict:
    return next(e for e in entries if e["name"] == name)


# ---- 计划覆盖 ----


def test_unassigned_factor_misses_all_layers():
    tender = _Tender([_factor("技术方案")])
    entries, summary = compute_evaluation_coverage(
        tender, [_draft("s1", "其他章", ["别的因素"], "正文")], doc_id=DOC_ID
    )

    entry = entries[0]
    assert entry["covered"] is False
    assert entry["plan_covered"] is False
    assert entry["body_covered"] is False
    assert entry["evidence_covered"] is False
    assert entry["gaps"] == ["plan", "body", "evidence"]
    assert entry["section_ids"] == []
    assert summary["plan"]["gaps"] == ["技术方案"]


def test_covered_alias_equals_plan_covered():
    tender = _Tender([_factor("技术方案")])
    drafts = [_draft("s1", "技术方案", ["技术方案"], "正文未提及因素名称")]
    entries, _ = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    entry = entries[0]
    assert entry["covered"] == entry["plan_covered"] is True
    assert entry["body_covered"] is False


# ---- 正文覆盖 ----


def test_plan_coverage_without_body_response():
    tender = _Tender([_factor("施工组织设计")])
    drafts = [_draft("s1", "施工组织设计", ["施工组织设计"], "泛泛而谈的正文，没有因素名。")]
    entries, _ = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    entry = _entry(entries, "施工组织设计")
    assert entry["plan_covered"] is True
    assert entry["body_covered"] is False
    assert entry["evidence_covered"] is False
    assert "body" in entry["gaps"]


def test_body_coverage_uses_normalized_substring():
    tender = _Tender([_factor("施工组织设计")])
    # 因素名被空白拆开，normalize_title 去空白后应命中
    drafts = [_draft("s1", "章节", ["施工组织设计"], "本方案的施工 组织设计安排如下。")]
    entries, _ = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    entry = _entry(entries, "施工组织设计")
    assert entry["body_covered"] is True
    assert entry["body_section_ids"] == ["s1"]
    assert entry["evidence_covered"] is False


def test_placeholder_section_not_body_covered():
    tender = _Tender([_factor("投标报价", category="报价", score=30)])
    drafts = [
        _draft(
            "s1",
            "投标报价",
            ["投标报价"],
            "【待填写：投标报价】",
            strategy="placeholder",
        )
    ]
    entries, _ = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    entry = _entry(entries, "投标报价")
    assert entry["plan_covered"] is True
    assert entry["body_covered"] is False
    assert entry["body_section_ids"] == []


def test_llm_fill_strategy_counts_as_body_response():
    tender = _Tender([_factor("投标函承诺")])
    drafts = [
        _draft(
            "s1", "投标函", ["投标函承诺"], "投标函承诺：完全响应招标文件要求。",
            strategy="llm_fill",
        )
    ]
    entries, _ = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    assert _entry(entries, "投标函承诺")["body_covered"] is True


# ---- 证据覆盖 ----


def test_evidence_covered_when_cited_with_valid_same_doc_chunk():
    tender = _Tender([_factor("施工组织设计")])
    drafts = [
        _draft(
            "s1",
            "施工组织设计",
            ["施工组织设计"],
            "施工组织设计依据原文[1]展开。",
            retrieved=[_chunk(0)],
        )
    ]
    entries, summary = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    entry = _entry(entries, "施工组织设计")
    assert entry["body_covered"] is True
    assert entry["evidence_covered"] is True
    assert entry["evidence_section_ids"] == ["s1"]
    assert entry["gaps"] == []
    assert summary["evidence"]["covered"] == 1


def test_evidence_fails_without_retrieval_even_with_marker():
    tender = _Tender([_factor("施工组织设计")])
    drafts = [
        _draft("s1", "施工组织设计", ["施工组织设计"], "施工组织设计见[1]。", retrieved=[])
    ]
    entries, _ = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    entry = _entry(entries, "施工组织设计")
    assert entry["body_covered"] is True
    assert entry["evidence_covered"] is False
    assert "evidence" in entry["gaps"]


def test_evidence_fails_with_doc_id_mismatch():
    tender = _Tender([_factor("施工组织设计")])
    drafts = [
        _draft(
            "s1",
            "施工组织设计",
            ["施工组织设计"],
            "施工组织设计依据[1]。",
            retrieved=[_chunk(0, doc_id="ffffffffffffffff")],
        )
    ]
    entries, _ = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    assert _entry(entries, "施工组织设计")["evidence_covered"] is False


def test_evidence_fails_with_out_of_range_marker():
    tender = _Tender([_factor("施工组织设计")])
    drafts = [
        _draft(
            "s1",
            "施工组织设计",
            ["施工组织设计"],
            "施工组织设计依据[5]。",
            retrieved=[_chunk(0)],
        )
    ]
    entries, _ = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    assert _entry(entries, "施工组织设计")["evidence_covered"] is False


def test_evidence_fails_without_marker_even_with_chunks():
    tender = _Tender([_factor("施工组织设计")])
    drafts = [
        _draft(
            "s1",
            "施工组织设计",
            ["施工组织设计"],
            "施工组织设计的完整说明。",
            retrieved=[_chunk(0)],
        )
    ]
    entries, _ = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    entry = _entry(entries, "施工组织设计")
    assert entry["body_covered"] is True
    assert entry["evidence_covered"] is False


# ---- 汇总与漏斗 ----


def test_summary_funnel_and_gap_lists():
    tender = _Tender(
        [
            _factor("技术方案"),       # 证据覆盖
            _factor("企业业绩", "商务", 20),  # 仅正文覆盖
            _factor("投标报价", "报价", 30),  # 仅计划覆盖（占位）
            _factor("遗漏因素", "其他", 5),   # 未分配
        ]
    )
    drafts = [
        _draft(
            "s1", "技术方案", ["技术方案"], "技术方案依据[1]。", retrieved=[_chunk(0)]
        ),
        _draft("s2", "企业业绩", ["企业业绩"], "企业业绩说明充分。"),
        _draft(
            "s3",
            "投标报价",
            ["投标报价"],
            "【待填写：投标报价】",
            strategy="placeholder",
        ),
    ]
    entries, summary = compute_evaluation_coverage(tender, drafts, doc_id=DOC_ID)

    assert summary["plan"] == {"total": 4, "covered": 3, "gaps": ["遗漏因素"]}
    assert summary["body"]["covered"] == 2
    assert set(summary["body"]["gaps"]) == {"投标报价", "遗漏因素"}
    assert summary["evidence"]["covered"] == 1
    assert set(summary["evidence"]["gaps"]) == {"企业业绩", "投标报价", "遗漏因素"}
    # 漏斗单调
    assert summary["plan"]["covered"] >= summary["body"]["covered"] >= summary["evidence"]["covered"]
    # 逐因素 gaps 与汇总自洽
    by_name = {e["name"]: e for e in entries}
    assert by_name["技术方案"]["gaps"] == []
    assert by_name["企业业绩"]["gaps"] == ["evidence"]
    assert by_name["投标报价"]["gaps"] == ["body", "evidence"]
    assert by_name["遗漏因素"]["gaps"] == ["plan", "body", "evidence"]
