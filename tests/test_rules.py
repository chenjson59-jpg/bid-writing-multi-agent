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


# ---- 事实口径一致性（T-029 FACT_MISMATCH）----

from src.bid_writer.facts import (
    FACT_BUDGET,
    FACT_DEPOSIT,
    FACT_DURATION,
    FACT_PROJECT_NAME,
    FACT_VALIDITY,
    Fact,
    FactLedger,
    build_fact_ledger,
)
from src.bid_writer.rules import FACT_MISMATCH, check_fact_consistency


def _fact(fact_type: str, value: str, unit: str = "", label: str = "x") -> Fact:
    return Fact(
        fact_type=fact_type, label=label, value=value, unit=unit,
        raw_text=label, source="test", confidence=1.0,
    )


def test_ledger_none_skips_fact_check():
    result = check_section_rules("工期 200 天。", _plan(), [], doc_id=DOC_ID)
    assert result.passed is True
    assert FACT_MISMATCH not in _error_codes(result)


def test_empty_ledger_skips_fact_check():
    result = check_section_rules(
        "工期 200 天。", _plan(), [], doc_id=DOC_ID, ledger=FactLedger()
    )
    assert result.passed is True


def test_matching_duration_passes():
    ledger = FactLedger(facts=[_fact(FACT_DURATION, "180", "天", label="工期")])
    result = check_section_rules(
        "计划工期 180 日历天，满足招标要求。", _plan(), [], doc_id=DOC_ID, ledger=ledger
    )
    assert result.passed is True
    assert FACT_MISMATCH not in _error_codes(result)


def test_conflicting_duration_is_mismatch():
    ledger = FactLedger(facts=[_fact(FACT_DURATION, "180", "天", label="工期")])
    result = check_section_rules(
        "本项目工期 200 日历天。", _plan(), [], doc_id=DOC_ID, ledger=ledger
    )
    assert FACT_MISMATCH in _error_codes(result)
    assert result.passed is False
    message = next(e["message"] for e in result.errors if e["code"] == FACT_MISMATCH)
    assert "200" in message and "180" in message


def test_cross_unit_duration_not_judged():
    # 事实表为月、正文为天：含业务假设的换算不做，跳过不判错
    ledger = FactLedger(facts=[_fact(FACT_DURATION, "6", "月", label="服务期限")])
    result = check_section_rules(
        "服务期 180 天。", _plan(), [], doc_id=DOC_ID, ledger=ledger
    )
    assert FACT_MISMATCH not in _error_codes(result)


def test_amount_wanyuan_and_yuan_normalized():
    ledger = FactLedger(facts=[_fact(FACT_DEPOSIT, "50000.00", "元", label="投标保证金")])
    ok = check_section_rules(
        "投标保证金 5 万元，已按要求缴纳。", _plan(), [], doc_id=DOC_ID, ledger=ledger
    )
    # 金额归一后等值：无 FACT_MISMATCH（QUOTE_FORBIDDEN 是另一条规则，不在本用例断言范围）
    assert FACT_MISMATCH not in _error_codes(ok)

    bad = check_section_rules(
        "投标保证金 6 万元。", _plan(), [], doc_id=DOC_ID, ledger=ledger
    )
    assert FACT_MISMATCH in _error_codes(bad)


def test_placeholder_window_skips_fact_check():
    ledger = FactLedger(facts=[_fact(FACT_DURATION, "180", "天", label="工期")])
    result = check_section_rules(
        "工期：【待填写：工期】日历天，待人工补充。",
        _plan(), [], doc_id=DOC_ID, ledger=ledger,
    )
    assert FACT_MISMATCH not in _error_codes(result)


def test_project_name_mismatch_and_short_name_alias():
    ledger = FactLedger(facts=[_fact(FACT_PROJECT_NAME, "智慧校园建设项目", label="项目名称")])

    # 括号注释被截断后与事实等值；真子串简称（7/9 长度、互为子串）容忍通过
    alias = check_section_rules(
        "项目名称：智慧校园建设项目（以下简称本项目）。",
        _plan(), [], doc_id=DOC_ID, ledger=ledger,
    )
    assert FACT_MISMATCH not in _error_codes(alias)
    short = check_section_rules(
        "项目名称：校园建设项目。",
        _plan(), [], doc_id=DOC_ID, ledger=ledger,
    )
    assert FACT_MISMATCH not in _error_codes(short)

    wrong = check_section_rules(
        "项目名称：智慧城市管理平台。", _plan(), [], doc_id=DOC_ID, ledger=ledger
    )
    assert FACT_MISMATCH in _error_codes(wrong)


def test_unknown_fact_type_in_text_not_judged():
    # 事实表只有工期，正文出现的保证金数字没有对应事实 → 不判错
    ledger = FactLedger(facts=[_fact(FACT_DURATION, "180", "天", label="工期")])
    result = check_section_rules(
        "投标保证金 5 万元。", _plan(), [], doc_id=DOC_ID, ledger=ledger
    )
    assert FACT_MISMATCH not in _error_codes(result)


def test_any_matching_fact_among_conflicts_passes():
    ledger = FactLedger(
        facts=[
            _fact(FACT_DURATION, "180", "天", label="工期"),
            _fact(FACT_DURATION, "200", "天", label="工期"),
        ]
    )
    result = check_section_rules(
        "工期 200 天。", _plan(), [], doc_id=DOC_ID, ledger=ledger
    )
    assert FACT_MISMATCH not in _error_codes(result)


def test_check_fact_consistency_with_built_ledger():
    tender = type(
        "T",
        (),
        {
            "project_name": "智慧校园建设项目",
            "tender_no": "",
            "budget_amount": "",
            "contract_highlights": ["计划工期 180 日历天。"],
            "technical_requirements": [],
            "service_requirements": [],
            "preliminary_review": [],
            "qualifications": [],
        },
    )()
    ledger = build_fact_ledger(tender)
    errors = check_fact_consistency("正文承诺工期 200 天完工。", ledger)
    assert [e["code"] for e in errors] == [FACT_MISMATCH]
    assert check_fact_consistency("正文承诺工期 180 日历天。", ledger) == []


def test_validity_conflict_reported():
    ledger = FactLedger(facts=[_fact(FACT_VALIDITY, "90", "天", label="投标有效期")])
    result = check_section_rules(
        "投标有效期为 60 天。", _plan(), [], doc_id=DOC_ID, ledger=ledger
    )
    assert FACT_MISMATCH in _error_codes(result)


def test_budget_fact_used():
    ledger = FactLedger(facts=[_fact(FACT_BUDGET, "1205000.00", "元", label="最高限价")])
    result = check_section_rules(
        "本项目最高限价 100 万元，投标报价不得超限价。",
        _plan(), [], doc_id=DOC_ID, ledger=ledger,
    )
    assert FACT_MISMATCH in _error_codes(result)
