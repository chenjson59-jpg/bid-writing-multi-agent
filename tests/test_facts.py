"""全局事实表测试（T-029）：全部离线、纯函数，不依赖模型与文件系统。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.bid_writer.facts import (
    FACT_BUDGET,
    FACT_DEPOSIT,
    FACT_DURATION,
    FACT_PROJECT_NAME,
    FACT_TENDER_NO,
    FACT_VALIDITY,
    Fact,
    FactLedger,
    build_fact_ledger,
)


def _tender(**kwargs) -> SimpleNamespace:
    base = {
        "doc_id": "doc1",
        "project_name": "",
        "tender_no": "",
        "budget_amount": "",
        "contract_highlights": [],
        "technical_requirements": [],
        "service_requirements": [],
        "preliminary_review": [],
        "qualifications": [],
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _values(ledger: FactLedger, fact_type: str) -> list[tuple[str, str]]:
    return [(f.value, f.unit) for f in ledger.by_type(fact_type)]


# ---- 空与边界 ----


def test_empty_tender_builds_empty_ledger():
    ledger = build_fact_ledger(_tender())
    assert ledger.is_empty is True
    assert ledger.to_dict()["facts"] == []


def test_budget_amount_without_number_is_skipped():
    ledger = build_fact_ledger(_tender(budget_amount="以招标文件正式公布为准"))
    assert ledger.by_type(FACT_BUDGET) == []


def test_unknown_tender_shape_does_not_raise():
    # 鸭子类型：缺失属性的对象按空值处理，不抛 AttributeError
    ledger = build_fact_ledger(SimpleNamespace(doc_id="x"))
    assert ledger.is_empty is True


# ---- 扁平结构化字段（confidence=1.0）----


def test_flat_budget_amount_wanyuan_normalized_to_yuan():
    ledger = build_fact_ledger(_tender(budget_amount="437.677万元"))
    facts = ledger.by_type(FACT_BUDGET)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.value == "4376770.00"
    assert fact.unit == "元"
    assert fact.confidence == 1.0
    assert fact.source == "basic.budget_amount"
    assert fact.reviewed is False


def test_flat_budget_amount_yuan_kept():
    ledger = build_fact_ledger(_tender(budget_amount="5000元"))
    assert _values(ledger, FACT_BUDGET) == [("5000.00", "元")]


def test_flat_project_name_and_tender_no():
    ledger = build_fact_ledger(
        _tender(project_name="智慧校园建设项目", tender_no="ZC-2026-001")
    )
    name = ledger.by_type(FACT_PROJECT_NAME)
    no = ledger.by_type(FACT_TENDER_NO)
    assert [f.value for f in name] == ["智慧校园建设项目"]
    assert [f.value for f in no] == ["ZC-2026-001"]
    assert all(f.unit == "" and f.confidence == 1.0 for f in name + no)


# ---- 文本规则抽取（confidence=0.8）----


def test_duration_calendar_day_normalized_to_day():
    ledger = build_fact_ledger(
        _tender(contract_highlights=["计划工期 180 日历天，具体以合同约定为准。"])
    )
    assert _values(ledger, FACT_DURATION) == [("180", "天")]
    fact = ledger.by_type(FACT_DURATION)[0]
    assert fact.label == "工期"
    assert fact.confidence == 0.8
    assert fact.source == "contract_highlights[0]"


def test_workday_and_month_keep_own_units():
    ledger = build_fact_ledger(
        _tender(
            contract_highlights=[
                "工期 60 工作日完成安装。",
                "服务期限 12 个月。",
            ]
        )
    )
    pairs = _values(ledger, FACT_DURATION)
    assert ("60", "工作日") in pairs
    assert ("12", "月") in pairs


def test_deposit_wanyuan_normalized():
    ledger = build_fact_ledger(
        _tender(contract_highlights=["投标保证金：5万元，须从基本账户转出。"])
    )
    assert _values(ledger, FACT_DEPOSIT) == [("50000.00", "元")]


def test_validity_in_days():
    ledger = build_fact_ledger(
        _tender(preliminary_review=["投标有效期为 90 天。"])
    )
    assert _values(ledger, FACT_VALIDITY) == [("90", "天")]


def test_budget_anchors_longest_first():
    # 「最高限价」必须作为整体锚点命中，不被「限价」之类短词干扰
    ledger = build_fact_ledger(
        _tender(technical_requirements=["本项目最高限价 120.5 万元。"])
    )
    assert _values(ledger, FACT_BUDGET) == [("1205000.00", "元")]


def test_qualification_requirement_scanned():
    ledger = build_fact_ledger(
        _tender(
            qualifications=[
                {"requirement": "投标保证金 2 万元，到账截止时间另行通知。"}
            ]
        )
    )
    assert _values(ledger, FACT_DEPOSIT) == [("20000.00", "元")]


def test_window_does_not_cross_sentence():
    # 锚点与数字被句号隔断，不得跨句拼接出虚假事实
    ledger = build_fact_ledger(
        _tender(contract_highlights=["投标保证金按招标文件执行。金额为 3 万元。"])
    )
    assert ledger.by_type(FACT_DEPOSIT) == []


def test_duplicate_fact_deduped_keeping_first_source():
    ledger = build_fact_ledger(
        _tender(
            contract_highlights=["工期 180 日历天。"],
            technical_requirements=["总工期 180 天。"],
        )
    )
    facts = ledger.by_type(FACT_DURATION)
    assert len(facts) == 1
    assert facts[0].source == "contract_highlights[0]"


def test_conflicting_facts_are_all_kept():
    ledger = build_fact_ledger(
        _tender(contract_highlights=["工期 180 日历天。", "工期要求 200 天。"])
    )
    values = [f.value for f in ledger.by_type(FACT_DURATION)]
    assert sorted(values) == ["180", "200"]


def test_placeholder_window_creates_no_fact():
    ledger = build_fact_ledger(
        _tender(contract_highlights=["工期【待填写：工期】日历天。"])
    )
    assert ledger.by_type(FACT_DURATION) == []


# ---- 序列化 ----


def test_to_dict_from_dict_roundtrip():
    ledger = build_fact_ledger(
        _tender(
            project_name="智慧校园建设项目",
            contract_highlights=["工期 180 日历天。", "投标保证金 5 万元。"],
        )
    )
    payload = ledger.to_dict()
    restored = FactLedger.from_dict(payload)
    assert [(f.fact_type, f.value, f.unit) for f in restored.facts] == [
        (f.fact_type, f.value, f.unit) for f in ledger.facts
    ]


def test_from_dict_tolerates_none_and_garbage():
    assert FactLedger.from_dict(None).is_empty is True
    assert FactLedger.from_dict({"facts": "x"}).is_empty is True
    assert FactLedger.from_dict({"facts": [{"fact_type": "duration"}]}).facts == [
        Fact(fact_type="duration", label="", value="", unit="", raw_text="", source="", confidence=0.0)
    ]


def test_raw_text_truncated():
    long_line = "投标保证金" + "很" * 300 + " 5 万元"
    ledger = build_fact_ledger(_tender(contract_highlights=[long_line]))
    # 窗口限制下锚点与数字距离超过 12 字时本就不命中；构造可命中场景验证截断保护
    fact_ledger = FactLedger.from_dict(
        {"facts": [{"fact_type": "duration", "raw_text": "x" * 500}]}
    )
    assert len(fact_ledger.facts[0].raw_text) <= 200
    assert isinstance(ledger, FactLedger)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
