"""字段级抽取评测指标（T-021）。

以 schema 感知方式把投影后的 TenderDocument 字典按声明字段展平、配对与计数，
产出 Precision/Recall/F1 与未溯源字段率。全部为纯函数：不做语义或模糊匹配、
不访问网络、不读取环境变量，同一输入结果可重复。

口径要点：
- 标量空值（None/""/[]/{}）视为缺位，双方皆空不进入统计；
- 字符串列表按 multiset 比较，重复元素按出现次数配对；
- 对象列表按身份键配对（重复键按出现次序第 k 个配第 k 个），再逐叶子比较；
- 值不匹配对称计 1 个 FP 与 1 个 FN；
- 未溯源字段率复用生产侧 ``ground_check``，不另立规则，避免口径漂移。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from src.extraction.extractor import ground_check

SCHEMA_VERSION = 1

# 顶层标量字段，与 extractor._GROUND_SCALAR_FIELDS 的键保持一致
SCALAR_FIELDS: tuple[str, ...] = (
    "project_name",
    "tender_no",
    "budget_amount",
    "bid_deadline",
    "bid_open_place",
)

# 字符串列表字段：元素即比较单元，按 multiset 比较
STRING_LIST_FIELDS: tuple[str, ...] = (
    "preliminary_review",
    "contract_highlights",
    "technical_requirements",
    "service_requirements",
)

# 对象列表字段：(字段名, 身份键, ((叶子名, 叶子类型), ...))，叶子类型为 scalar/str_list
OBJECT_LIST_FIELDS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "evaluation_factors",
        "name",
        (
            ("category", "scalar"),
            ("name", "scalar"),
            ("score", "scalar"),
            ("source_quote", "scalar"),
        ),
    ),
    (
        "score_table",
        "factor",
        (
            ("factor", "scalar"),
            ("score", "scalar"),
            ("comment", "scalar"),
        ),
    ),
    (
        "qualifications",
        "requirement",
        (
            ("category", "scalar"),
            ("requirement", "scalar"),
            ("source_quote", "scalar"),
            ("thresholds", "str_list"),
        ),
    ),
    (
        "bid_format_items",
        "title",
        (
            ("title", "scalar"),
            ("content", "scalar"),
        ),
    ),
)


@dataclass
class FieldCounts:
    """字段比较的 TP/FP/FN 累计与逐条差异明细。"""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list)

    def add_issue(
        self, category: str, path: str, actual: Any, expected: Any
    ) -> None:
        self.issues.append(
            {
                "category": category,
                "path": path,
                "actual": actual,
                "expected": expected,
            }
        )


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _ratio(numerator: int, denominator: int) -> float | None:
    """分母为 0 时返回 None，由展示层决定如何渲染。"""
    return None if denominator == 0 else numerator / denominator


def precision(counts: FieldCounts) -> float | None:
    return _ratio(counts.tp, counts.tp + counts.fp)


def recall(counts: FieldCounts) -> float | None:
    return _ratio(counts.tp, counts.tp + counts.fn)


def f1(counts: FieldCounts) -> float | None:
    return _ratio(2 * counts.tp, 2 * counts.tp + counts.fp + counts.fn)


def _compare_scalar(
    path: str, actual: Any, expected: Any, counts: FieldCounts
) -> None:
    actual_empty = _is_empty(actual)
    expected_empty = _is_empty(expected)
    if actual_empty and expected_empty:
        return
    if expected_empty:
        counts.fp += 1
        counts.add_issue("spurious_field", path, actual, expected)
    elif actual_empty:
        counts.fn += 1
        counts.add_issue("missing_field", path, actual, expected)
    elif actual != expected:
        counts.fp += 1
        counts.fn += 1
        counts.add_issue("value_mismatch", path, actual, expected)
    else:
        counts.tp += 1


def _compare_string_list(
    path: str, actual: Any, expected: Any, counts: FieldCounts
) -> None:
    if not isinstance(actual, list) or not isinstance(expected, list):
        counts.fp += 1
        counts.fn += 1
        counts.add_issue("type_mismatch", path, actual, expected)
        return
    actual_counter = Counter(actual)
    expected_counter = Counter(expected)
    overlap = actual_counter & expected_counter
    counts.tp += sum(overlap.values())
    for value, number in (actual_counter - overlap).items():
        counts.fp += number
        for _ in range(number):
            counts.add_issue("element_spurious", path, value, None)
    for value, number in (expected_counter - overlap).items():
        counts.fn += number
        for _ in range(number):
            counts.add_issue("element_missing", path, None, value)


def _identity(item: Any, id_key: str) -> str:
    if isinstance(item, dict):
        return str(item.get(id_key) or "")
    return ""


def _compare_object_list(
    path: str,
    actual: Any,
    expected: Any,
    id_key: str,
    leaves: tuple[tuple[str, str], ...],
    counts: FieldCounts,
) -> None:
    if not isinstance(actual, list) or not isinstance(expected, list):
        counts.fp += 1
        counts.fn += 1
        counts.add_issue("type_mismatch", path, actual, expected)
        return

    used = [False] * len(actual)
    for expected_item in expected:
        expected_id = _identity(expected_item, id_key)
        match_index = next(
            (
                index
                for index, actual_item in enumerate(actual)
                if not used[index]
                and _identity(actual_item, id_key) == expected_id
            ),
            None,
        )
        item_path = f"{path}[{id_key}={expected_id}]"
        if match_index is None:
            counts.fn += 1
            counts.add_issue(
                "element_missing", item_path, None, expected_item
            )
            continue
        used[match_index] = True
        actual_item = actual[match_index]
        if not isinstance(actual_item, dict) or not isinstance(
            expected_item, dict
        ):
            counts.fp += 1
            counts.fn += 1
            counts.add_issue(
                "type_mismatch", item_path, actual_item, expected_item
            )
            continue
        for leaf, kind in leaves:
            leaf_path = f"{item_path}.{leaf}"
            if kind == "scalar":
                _compare_scalar(
                    leaf_path,
                    actual_item.get(leaf),
                    expected_item.get(leaf),
                    counts,
                )
            else:
                _compare_string_list(
                    leaf_path,
                    actual_item.get(leaf, []),
                    expected_item.get(leaf, []),
                    counts,
                )

    for index, actual_item in enumerate(actual):
        if used[index]:
            continue
        counts.fp += 1
        counts.add_issue(
            "element_spurious",
            f"{path}[{id_key}={_identity(actual_item, id_key)}]",
            actual_item,
            None,
        )


def compare_fields(
    actual: dict[str, Any], expected: dict[str, Any]
) -> FieldCounts:
    """按 schema 声明字段比较两份投影后的 TenderDocument 字典。"""
    counts = FieldCounts()
    for key in SCALAR_FIELDS:
        _compare_scalar(key, actual.get(key), expected.get(key), counts)
    for key in STRING_LIST_FIELDS:
        _compare_string_list(
            key, actual.get(key, []), expected.get(key, []), counts
        )
    for key, id_key, leaves in OBJECT_LIST_FIELDS:
        _compare_object_list(
            key,
            actual.get(key, []),
            expected.get(key, []),
            id_key,
            leaves,
            counts,
        )
    return counts


def grounding_stats(
    actual: dict[str, Any], full_text: str
) -> dict[str, Any]:
    """计算未溯源字段率，证据单元范围与生产侧 ``ground_check`` 完全一致。

    分母 = 非空基础标量（5 个）+ 含非空 source_quote 的评分因素/资格条件；
    空 quote 的因素/资格不参与检查，仅计入 ``empty_quote_units`` 单独披露。
    """
    ungrounded_fields = ground_check(actual, full_text)

    evidence_units = 0
    empty_quote_units = 0
    for key in SCALAR_FIELDS:
        if not _is_empty(actual.get(key)):
            evidence_units += 1
    for container in ("evaluation_factors", "qualifications"):
        for item in actual.get(container, []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("source_quote"):
                evidence_units += 1
            else:
                empty_quote_units += 1

    ungrounded = len(ungrounded_fields)
    return {
        "evidence_units": evidence_units,
        "ungrounded": ungrounded,
        "ungrounded_rate": _ratio(ungrounded, evidence_units),
        "ungrounded_fields": ungrounded_fields,
        "empty_quote_units": empty_quote_units,
    }


def evaluate_sample(
    name: str,
    actual: dict[str, Any],
    expected: dict[str, Any],
    full_text: str,
) -> dict[str, Any]:
    """单样本字段指标 + 溯源指标的完整结果。"""
    counts = compare_fields(actual, expected)
    return {
        "name": name,
        "counts": {
            "tp": counts.tp,
            "fp": counts.fp,
            "fn": counts.fn,
        },
        "precision": precision(counts),
        "recall": recall(counts),
        "f1": f1(counts),
        "issues": counts.issues,
        "grounding": grounding_stats(actual, full_text),
    }


def _mean_or_none(values: list[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def aggregate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """跨样本聚合：micro 按字段量加权，macro 按样本等权平均。"""
    tp = sum(sample["counts"]["tp"] for sample in samples)
    fp = sum(sample["counts"]["fp"] for sample in samples)
    fn = sum(sample["counts"]["fn"] for sample in samples)
    micro_counts = FieldCounts(tp=tp, fp=fp, fn=fn)

    evidence = sum(
        sample["grounding"]["evidence_units"] for sample in samples
    )
    ungrounded = sum(
        sample["grounding"]["ungrounded"] for sample in samples
    )
    return {
        "samples": len(samples),
        "micro": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision(micro_counts),
            "recall": recall(micro_counts),
            "f1": f1(micro_counts),
        },
        "macro": {
            "precision": _mean_or_none(
                [sample["precision"] for sample in samples]
            ),
            "recall": _mean_or_none(
                [sample["recall"] for sample in samples]
            ),
            "f1": _mean_or_none([sample["f1"] for sample in samples]),
        },
        "grounding": {
            "micro": {
                "evidence_units": evidence,
                "ungrounded": ungrounded,
                "ungrounded_rate": _ratio(ungrounded, evidence),
            },
            "macro": {
                "ungrounded_rate": _mean_or_none(
                    [
                        sample["grounding"]["ungrounded_rate"]
                        for sample in samples
                    ]
                )
            },
        },
    }

def build_report(results: list[Any]) -> dict[str, Any]:
    """组装带版本号的 JSON 报告信封（鸭子类型，避免与 runner 循环导入）。"""
    samples: list[dict[str, Any]] = []
    metric_reports: list[dict[str, Any]] = []
    for result in results:
        samples.append(
            {
                "name": result.name,
                "passed": result.passed,
                "diffs": result.diffs,
                "field_metrics": result.metrics,
                "actual": result.actual,
            }
        )
        metric_reports.append(result.metrics)
    return {
        "schema_version": SCHEMA_VERSION,
        "samples": samples,
        "aggregate": aggregate(metric_reports),
    }
