"""T-021 字段级抽取评测指标测试：手写故障注入数据，离线、不依赖外部服务。"""
from __future__ import annotations

import json

import pytest

from eval.harness.metrics import (
    SCHEMA_VERSION,
    aggregate,
    build_report,
    compare_fields,
    evaluate_sample,
    f1,
    grounding_stats,
    precision,
    recall,
)
from eval.harness.runner import run_all

# ---------------------------------------------------------------------------
# 标量字段
# ---------------------------------------------------------------------------

def test_scalar_equal_is_tp():
    counts = compare_fields(
        {"project_name": "A项目"}, {"project_name": "A项目"}
    )
    assert (counts.tp, counts.fp, counts.fn) == (1, 0, 0)


def test_scalar_missing_is_fn():
    counts = compare_fields({"project_name": ""}, {"project_name": "A项目"})
    assert (counts.tp, counts.fp, counts.fn) == (0, 0, 1)
    assert counts.issues[0]["category"] == "missing_field"
    assert counts.issues[0]["path"] == "project_name"


def test_scalar_spurious_is_fp():
    counts = compare_fields({"tender_no": "X-1"}, {"tender_no": ""})
    assert (counts.tp, counts.fp, counts.fn) == (0, 1, 0)
    assert counts.issues[0]["category"] == "spurious_field"


def test_both_empty_is_not_counted():
    counts = compare_fields({"budget_amount": ""}, {"budget_amount": ""})
    assert (counts.tp, counts.fp, counts.fn) == (0, 0, 0)
    assert counts.issues == []


def test_scalar_value_mismatch_is_fp_and_fn():
    counts = compare_fields(
        {"project_name": "错误名"}, {"project_name": "正确名"}
    )
    assert (counts.tp, counts.fp, counts.fn) == (0, 1, 1)
    issue = counts.issues[0]
    assert issue["category"] == "value_mismatch"
    assert issue["actual"] == "错误名"
    assert issue["expected"] == "正确名"


def test_undeclared_keys_are_ignored_schema_aware():
    # 归一化产物只可能含 schema 声明字段；未声明键不参与指标，由精确匹配基线守门
    counts = compare_fields(
        {"project_name": "A", "unknown_key": "噪声"}, {"project_name": "A"}
    )
    assert (counts.tp, counts.fp, counts.fn) == (1, 0, 0)


# ---------------------------------------------------------------------------
# 字符串列表（multiset）
# ---------------------------------------------------------------------------

def test_string_list_multiset_overlap_spurious_missing():
    counts = compare_fields(
        {"technical_requirements": ["共同项", "多余项"]},
        {"technical_requirements": ["共同项", "缺失项"]},
    )
    assert (counts.tp, counts.fp, counts.fn) == (1, 1, 1)
    categories = {i["category"] for i in counts.issues}
    assert categories == {"element_spurious", "element_missing"}


def test_string_list_duplicates_count_with_multiplicity():
    counts = compare_fields(
        {"preliminary_review": ["x", "x", "y"]},
        {"preliminary_review": ["x", "z", "z"]},
    )
    # 交集 multiset = {x:1} → 1 TP；actual 剩 x、y → 2 FP；expected 剩 z、z → 2 FN
    assert (counts.tp, counts.fp, counts.fn) == (1, 2, 2)


def test_string_list_order_independent():
    counts = compare_fields(
        {"contract_highlights": ["b", "a"]},
        {"contract_highlights": ["a", "b"]},
    )
    assert (counts.tp, counts.fp, counts.fn) == (2, 0, 0)


def test_type_mismatch_is_flagged():
    counts = compare_fields(
        {"preliminary_review": "不是列表"},
        {"preliminary_review": ["a"]},
    )
    assert (counts.tp, counts.fp, counts.fn) == (0, 1, 1)
    assert counts.issues[0]["category"] == "type_mismatch"


# ---------------------------------------------------------------------------
# 对象列表（身份键配对 + 叶子比较）
# ---------------------------------------------------------------------------

def test_object_list_pairs_by_identity_key_regardless_of_order():
    actual = {"evaluation_factors": [
        {"name": "B", "score": 20.0},
        {"name": "A", "score": 10.0},
    ]}
    expected = {"evaluation_factors": [
        {"name": "A", "score": 10.0},
        {"name": "B", "score": 20.0},
    ]}
    counts = compare_fields(actual, expected)
    assert (counts.tp, counts.fp, counts.fn) == (4, 0, 0)


def test_object_list_leaf_mismatch_path_uses_identity_key():
    counts = compare_fields(
        {"evaluation_factors": [{"name": "价格", "score": 20.0}]},
        {"evaluation_factors": [{"name": "价格", "score": 30.0}]},
    )
    assert (counts.tp, counts.fp, counts.fn) == (1, 1, 1)
    issue = next(i for i in counts.issues if i["category"] == "value_mismatch")
    assert issue["path"] == "evaluation_factors[name=价格].score"


def test_object_list_element_missing_and_spurious():
    counts = compare_fields(
        {"qualifications": [
            {"requirement": "多余资格", "category": "其他"},
        ]},
        {"qualifications": [
            {"requirement": "期望资格", "category": "资质"},
        ]},
    )
    assert (counts.tp, counts.fp, counts.fn) == (0, 1, 1)
    categories = {i["category"] for i in counts.issues}
    assert categories == {"element_spurious", "element_missing"}


def test_duplicate_identity_key_pairs_in_order():
    actual = {"evaluation_factors": [
        {"name": "X", "score": 20.0},
        {"name": "X", "score": 10.0},
    ]}
    expected = {"evaluation_factors": [
        {"name": "X", "score": 10.0},
        {"name": "X", "score": 20.0},
    ]}
    counts = compare_fields(actual, expected)
    # 同名键按出现次序第 k 个配第 k 个：两个 score 均不匹配，两个 name 命中
    assert (counts.tp, counts.fp, counts.fn) == (2, 2, 2)
    score_issues = [i for i in counts.issues if i["path"].endswith(".score")]
    assert len(score_issues) == 2


def test_nested_thresholds_multiset():
    counts = compare_fields(
        {"qualifications": [{
            "requirement": "人员",
            "category": "人员",
            "source_quote": "",
            "thresholds": ["PMP", "多余"],
        }]},
        {"qualifications": [{
            "requirement": "人员",
            "category": "人员",
            "source_quote": "",
            "thresholds": ["PMP", "缺失"],
        }]},
    )
    # requirement/category 两个 TP；thresholds 1 TP + 1 FP + 1 FN；空 quote 双空不计
    assert (counts.tp, counts.fp, counts.fn) == (3, 1, 1)


# ---------------------------------------------------------------------------
# 比率口径与零分母
# ---------------------------------------------------------------------------

def test_ratios_and_zero_denominator():
    counts = compare_fields(
        {"project_name": "A"}, {"project_name": "A"}
    )
    assert precision(counts) == 1.0
    assert recall(counts) == 1.0
    assert f1(counts) == 1.0

    empty = compare_fields({}, {})
    assert precision(empty) is None
    assert recall(empty) is None
    assert f1(empty) is None


def test_micro_and_macro_aggregation():
    # A：1 TP + 1 FP → P=0.5, R=1.0；原文含实际值 → 全部可溯源
    report_a = evaluate_sample(
        "a",
        {"project_name": "A", "tender_no": "X"},
        {"project_name": "A", "tender_no": ""},
        "全文包含 A 与 X 两个值",
    )
    # B：2 TP + 2 FN → P=1.0, R=0.5
    report_b = evaluate_sample(
        "b",
        {"project_name": "B", "tender_no": "T",
         "budget_amount": "", "bid_deadline": ""},
        {"project_name": "B", "tender_no": "T",
         "budget_amount": "M", "bid_deadline": "D"},
        "全文包含 B 与 T 两个值",
    )
    agg = aggregate([report_a, report_b])
    assert agg["micro"]["tp"] == 3
    assert agg["micro"]["fp"] == 1
    assert agg["micro"]["fn"] == 2
    assert agg["micro"]["precision"] == pytest.approx(0.75)
    assert agg["micro"]["recall"] == pytest.approx(0.6)
    assert agg["macro"]["precision"] == pytest.approx(0.75)
    assert agg["macro"]["recall"] == pytest.approx(0.75)
    assert agg["macro"]["f1"] == pytest.approx(
        (2 * 0.5 * 1.0 / 1.5 + 2 * 1.0 * 0.5 / 1.5) / 2
    )


# ---------------------------------------------------------------------------
# 未溯源字段率
# ---------------------------------------------------------------------------

def test_grounding_stats_scope_and_rate():
    actual = {
        "project_name": "虚构项目",       # 原文命中 → 已溯源
        "tender_no": "",                  # 空值不进分母
        "budget_amount": "999万元",       # 原文不存在 → 未溯源
        "evaluation_factors": [
            {"name": "价格", "source_quote": "价格因素 30 分"},  # 命中
            {"name": "赠品", "source_quote": ""},                # 空 quote 不进分母
        ],
        "qualifications": [
            {"requirement": "执照", "source_quote": "供应商须具有营业执照。",
             "thresholds": []},
        ],
    }
    full_text = "本项目为虚构项目，价格因素 30 分。供应商须具有营业执照。"
    stats = grounding_stats(actual, full_text)
    # 2 个非空标量 + 1 个带 quote 因素 + 1 个带 quote 资格 = 4
    assert stats["evidence_units"] == 4
    assert stats["ungrounded"] == 1
    assert stats["ungrounded_rate"] == pytest.approx(0.25)
    assert stats["empty_quote_units"] == 1
    assert stats["ungrounded_fields"] == ["预算金额"]


def test_grounding_zero_evidence_returns_null_rate():
    stats = grounding_stats(
        {"project_name": "", "evaluation_factors": [], "qualifications": []},
        "任意原文",
    )
    assert stats["evidence_units"] == 0
    assert stats["ungrounded_rate"] is None


# ---------------------------------------------------------------------------
# Golden Data 锁定值（防篡改期望文件）与报告确定性
# ---------------------------------------------------------------------------

def test_golden_locked_metrics():
    results = run_all()
    by_name = {r.name: r for r in results}
    expected_tp = {
        "01_engineering": 47,
        "02_goods": 25,
        "03_service": 31,
    }
    for name, tp in expected_tp.items():
        metrics = by_name[name].metrics
        assert metrics["counts"] == {"tp": tp, "fp": 0, "fn": 0}
        assert metrics["precision"] == 1.0
        assert metrics["recall"] == 1.0
        assert metrics["f1"] == 1.0

    grounding = {name: by_name[name].metrics["grounding"] for name in expected_tp}
    assert grounding["01_engineering"]["evidence_units"] == 13
    assert grounding["01_engineering"]["ungrounded"] == 0
    assert grounding["02_goods"]["evidence_units"] == 9
    assert grounding["02_goods"]["ungrounded"] == 2
    assert grounding["02_goods"]["ungrounded_rate"] == pytest.approx(2 / 9)
    assert grounding["03_service"]["evidence_units"] == 8
    assert grounding["03_service"]["ungrounded"] == 1
    assert grounding["03_service"]["ungrounded_rate"] == pytest.approx(0.125)

    # 重算溯源结果必须与生产 meta 中冻结的 ungrounded_fields 一致
    for result in results:
        assert result.metrics["grounding"]["ungrounded_fields"] == (
            result.actual["extraction_meta"]["ungrounded_fields"]
        )

    report = build_report(results)
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["aggregate"]["micro"]["tp"] == 103
    assert report["aggregate"]["grounding"]["micro"]["ungrounded_rate"] == (
        pytest.approx(0.1)
    )
    assert report["aggregate"]["grounding"]["macro"]["ungrounded_rate"] == (
        pytest.approx((0.0 + 2 / 9 + 0.125) / 3)
    )
    # 同一结果两次序列化字节一致（确定性报告）
    assert json.dumps(report, ensure_ascii=False, sort_keys=True) == (
        json.dumps(build_report(results), ensure_ascii=False, sort_keys=True)
    )
