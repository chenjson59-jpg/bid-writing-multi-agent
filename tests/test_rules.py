"""确定性规则检查器测试（T-013）：全部离线，不依赖模型。"""
from __future__ import annotations

from src.bid_writer.rules import (
    CITE_DOC_MISMATCH,
    CITE_EMPTY,
    CITE_OUT_OF_RANGE,
    QUOTE_FORBIDDEN,
    check_section_rules,
)
from src.bid_writer.schema import SectionPlan

DOC_ID = "0123456789abcdef"


def _plan(title: str = "施工组织设计", strategy: str = "generate") -> SectionPlan:
    return SectionPlan(
        id="section_1", title=title, order=1, strategy=strategy,
        scoring_factors=[title],
    )


def _chunk(index: int, *, doc_id: str = DOC_ID, with_metadata: bool = True) -> dict:
    cid = f"{doc_id}:{index:04d}"
    return {
        "id": cid,
        "text": f"片段 {index} 正文",
        "metadata": {"doc_id": doc_id} if with_metadata else {},
    }


def _error_codes(result) -> list[str]:
    return [e["code"] for e in result.errors]


# ---- 引用规则 ----


def test_clean_section_passes():
    result = check_section_rules("纯技术方案正文，没有引用。", _plan(), [], doc_id=DOC_ID)

    assert result.passed is True
    assert result.errors == []
    assert result.citation_markers == []


def test_valid_citations_pass():
    retrieved = [_chunk(0), _chunk(1)]
    markdown = "依据原文[1]制定措施，并结合[2]细化流程。"

    result = check_section_rules(markdown, _plan(), retrieved, doc_id=DOC_ID)

    assert result.passed is True
    assert result.citation_markers == ["[1]", "[2]"]


def test_citation_without_retrieval_is_error():
    result = check_section_rules("无检索却输出引用[1][2]。", _plan(), [], doc_id=DOC_ID)

    assert result.passed is False
    assert _error_codes(result) == [CITE_EMPTY]
    assert "[1]" in result.errors[0]["message"]


def test_citation_out_of_range_is_error():
    retrieved = [_chunk(0), _chunk(1)]
    result = check_section_rules("引用越界[3]。", _plan(), retrieved, doc_id=DOC_ID)

    assert _error_codes(result) == [CITE_OUT_OF_RANGE]
    assert "3" in result.errors[0]["message"]


def test_citation_zero_is_out_of_range():
    result = check_section_rules("编号从 1 开始，[0]非法。", _plan(), [_chunk(0)], doc_id=DOC_ID)

    assert _error_codes(result) == [CITE_OUT_OF_RANGE]


def test_empty_and_out_of_range_both_reported():
    # 无检索时只报 CITE_EMPTY，不再重复报越界
    result = check_section_rules("[1][9]", _plan(), [], doc_id=DOC_ID)
    assert _error_codes(result) == [CITE_EMPTY]


def test_doc_id_mismatch_from_metadata():
    retrieved = [_chunk(0, doc_id="ffffffffffffffff")]
    result = check_section_rules("正文[1]。", _plan(), retrieved, doc_id=DOC_ID)

    assert _error_codes(result) == [CITE_DOC_MISMATCH]


def test_doc_id_mismatch_from_chunk_id_prefix():
    chunk = {"id": "ffffffffffffffff:0000", "text": "x", "metadata": {}}
    result = check_section_rules("正文[1]。", _plan(), [chunk], doc_id=DOC_ID)

    assert _error_codes(result) == [CITE_DOC_MISMATCH]


def test_matching_doc_id_passes_without_metadata():
    chunk = {"id": f"{DOC_ID}:0000", "text": "x", "metadata": {}}
    result = check_section_rules("正文[1]。", _plan(), [chunk], doc_id=DOC_ID)

    assert result.passed is True


def test_doc_id_check_skipped_when_unidentifiable():
    # 片段无 metadata 且 id 不是 doc_id:index 形态、调用方也未传 doc_id：不得臆造错误
    chunk = {"id": "weird", "text": "x"}
    result = check_section_rules("正文[1]。", _plan(), [chunk], doc_id="")

    assert result.passed is True


def test_duplicate_markers_dedup_and_sorted():
    retrieved = [_chunk(i) for i in range(3)]
    result = check_section_rules("[3][1][3][2]", _plan(), retrieved, doc_id=DOC_ID)

    assert result.citation_markers == ["[1]", "[2]", "[3]"]
    assert result.passed is True


def test_markdown_link_not_treated_as_citation():
    result = check_section_rules(
        "详见[招标文件要求](https://example.com/a)。", _plan(), [], doc_id=DOC_ID
    )

    assert result.citation_markers == []
    assert result.passed is True


# ---- 报价禁令 ----


def test_currency_symbol_violation():
    result = check_section_rules("投标总价为 ¥1200000 元。", _plan(), [], doc_id=DOC_ID)
    assert QUOTE_FORBIDDEN in _error_codes(result)


def test_money_unit_violations():
    for text in ("报价 120.5 万元", "该项费用合计 5000元"):
        result = check_section_rules(text, _plan(), [], doc_id=DOC_ID)
        assert QUOTE_FORBIDDEN in _error_codes(result), text


def test_discount_keyword_violation():
    result = check_section_rules("承诺让利 5% 作为优惠。", _plan(), [], doc_id=DOC_ID)
    assert QUOTE_FORBIDDEN in _error_codes(result)


def test_benign_numbers_not_flagged():
    text = "价格因素占 30 分；计划工期 2026 年；报价部分详见报价表，本表不填数值。"
    result = check_section_rules(text, _plan(), [], doc_id=DOC_ID)
    assert QUOTE_FORBIDDEN not in _error_codes(result)
    assert result.passed is True


# ---- 占位符与占位章节 ----


def test_placeholders_recorded_as_facts_not_errors():
    text = "公司名称【待填写：公司名称】，资质【待填写：企业资质】。"
    result = check_section_rules(text, _plan(), [], doc_id=DOC_ID)

    assert result.passed is True
    assert result.placeholders == ["【待填写：公司名称】", "【待填写：企业资质】"]


def test_placeholder_strategy_standard_text_passes():
    plan = _plan(title="投标报价", strategy="placeholder")
    result = check_section_rules("【待填写：投标报价】\n", plan, [], doc_id=DOC_ID)

    assert result.passed is True
    assert result.placeholders == ["【待填写：投标报价】"]
    assert result.section_id == "section_1"


def test_to_dict_is_json_friendly():
    result = check_section_rules("[1]", _plan(), [], doc_id=DOC_ID)
    data = result.to_dict()

    assert data["section_id"] == "section_1"
    assert data["passed"] is False
    assert data["errors"][0]["code"] == CITE_EMPTY
    assert isinstance(data["citation_markers"], list)
    assert isinstance(data["placeholders"], list)
