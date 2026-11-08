"""编写阶段离线评测指标（T-022）。

全部为纯函数：基于生产 ``BidReport.to_dict()`` 的结果做汇总与比率计算，不联网、
不读环境、不实现第二套业务规则。覆盖率、引用规则与占位符口径分别复用生产
``coverage.py``、``rules.py`` 与 ``extract_placeholders`` 的产出。

离线无语义 ground truth，本模块不提供「幻觉检测」：
- 规则违规数直接汇总生产规则码（CITE_* / QUOTE_FORBIDDEN）；
- 无依据信号（ungrounded_signals）只陈述「正文已响应但缺有效证据」这一确定性事实。
"""
from __future__ import annotations

import json
from typing import Any

from src.bid_writer.rules import (
    CITE_DOC_MISMATCH,
    CITE_EMPTY,
    CITE_OUT_OF_RANGE,
    QUOTE_FORBIDDEN,
)

SCHEMA_VERSION = 1
KIND = "writing_eval"

ARM_A = "A_single_pass"
ARM_B = "B_review_revise"
ARMS: tuple[str, ...] = (ARM_A, ARM_B)

# 规则码顺序固定，保证 by_code 键集合稳定
RULE_CODES: tuple[str, ...] = (
    CITE_EMPTY,
    CITE_OUT_OF_RANGE,
    CITE_DOC_MISMATCH,
    QUOTE_FORBIDDEN,
)

# 易变字段：wall-clock 只在终端实时展示，不进信封、不参与冻结比较
VOLATILE_PATHS: tuple[tuple[str, ...], ...] = (("llm_usage", "duration_ms"),)

# A/B 差值只对显式声明的整数字段做 B−A，比率类字段并排比较、不做差值
DELTA_PATHS: tuple[tuple[str, ...], ...] = (
    ("llm_usage", "calls"),
    ("llm_usage", "prompt_tokens"),
    ("llm_usage", "completion_tokens"),
    ("llm_usage", "total_tokens"),
    ("total_revisions",),
    ("rules", "rule_total_errors"),
    ("rules", "rule_error_sections"),
    ("ungrounded_signal_count",),
    ("placeholders", "tp"),
    ("placeholders", "fp"),
    ("placeholders", "fn"),
    ("coverage", "plan", "covered"),
    ("coverage", "body", "covered"),
    ("coverage", "evidence", "covered"),
)


def _ratio(numerator: int, denominator: int) -> float | None:
    """分母为 0 时返回 None，由展示层渲染为 n/a。"""
    return None if denominator == 0 else numerator / denominator


def _f1(tp: int, fp: int, fn: int) -> float | None:
    denominator = 2 * tp + fp + fn
    return None if denominator == 0 else 2 * tp / denominator


def placeholder_metrics(
    actual: list[str],
    expected_present: list[str],
    expected_absent: list[str],
) -> dict[str, Any]:
    """占位符准确率：以数据集显式声明的应出现/禁现集合为期望。"""
    present = set(expected_present)
    absent = set(expected_absent)
    overlap = present & absent
    if overlap:
        raise ValueError(f"expected_present 与 expected_absent 必须互不相交：{sorted(overlap)}")

    actual_set = set(actual)
    tp = len(actual_set & present)
    fn = len(present - actual_set)
    fp = len(actual_set - present)
    return {
        "actual": sorted(actual_set),
        "expected_present": sorted(present),
        "expected_absent": sorted(absent),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": _f1(tp, fp, fn),
        "forbidden_hits": sorted(actual_set & absent),
    }


def rule_violations(rule_checks: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总生产规则检查结果：错误章节数、错误总条数、按规则码计数。"""
    by_code = {code: 0 for code in RULE_CODES}
    total_errors = 0
    error_sections = 0
    for check in rule_checks:
        errors = check.get("errors") or []
        if errors:
            error_sections += 1
        total_errors += len(errors)
        for error in errors:
            code = error.get("code")
            if code in by_code:
                by_code[code] += 1
    return {
        "rule_error_sections": error_sections,
        "rule_total_errors": total_errors,
        "rule_errors_by_code": by_code,
    }


def citation_validity(rule_checks: list[dict[str, Any]]) -> dict[str, Any]:
    """章节级引用有效率：含引用标记的章节为分母，无 CITE_* 错误者为分子。"""
    marked = [c for c in rule_checks if c.get("citation_markers")]
    valid = [
        c
        for c in marked
        if not any(
            str(e.get("code", "")).startswith("CITE_")
            for e in (c.get("errors") or [])
        )
    ]
    marked_count = len(marked)
    valid_count = len(valid)
    return {
        "sections_with_markers": marked_count,
        "valid_sections": valid_count,
        "invalid_sections": marked_count - valid_count,
        "citation_valid_rate": _ratio(valid_count, marked_count),
    }


def ungrounded_signals(evaluation_coverage: list[dict[str, Any]]) -> dict[str, Any]:
    """无依据信号：正文已覆盖但证据层未覆盖的评分因素（确定性代理指标，非幻觉检测）。"""
    names = sorted(
        entry["name"]
        for entry in evaluation_coverage
        if entry.get("body_covered") and not entry.get("evidence_covered")
    )
    return {"count": len(names), "names": names}


def coverage_metrics(coverage_summary: dict[str, Any]) -> dict[str, Any]:
    """把生产三层覆盖漏斗汇总为带比率的稳定结构，口径完全沿用 coverage.py。"""
    result: dict[str, Any] = {}
    for layer in ("plan", "body", "evidence"):
        entry = coverage_summary.get(layer) or {}
        total = int(entry.get("total", 0))
        covered = int(entry.get("covered", 0))
        result[layer] = {
            "total": total,
            "covered": covered,
            "gaps": list(entry.get("gaps") or []),
            "rate": _ratio(covered, total),
        }
    return result


def _compact_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for section in sections:
        rule_check = section.get("rule_check") or {}
        compact.append(
            {
                "id": section.get("id"),
                "title": section.get("title"),
                "strategy": section.get("strategy"),
                "revisions": section.get("revisions"),
                "score_before": section.get("score_before"),
                "score_after": section.get("score_after"),
                "retrieved_count": section.get("retrieved_count"),
                "rule_codes": [e.get("code") for e in (rule_check.get("errors") or [])],
                "placeholders": list(rule_check.get("placeholders") or []),
            }
        )
    return compact


def summarize_report(
    report: dict[str, Any],
    expected_present: list[str],
    expected_absent: list[str],
) -> dict[str, Any]:
    """把生产 BidReport 字典投影为编写评测指标字典（仍含 duration_ms，由投影层剔除）。"""
    sections = report.get("sections") or []
    llm_sections = [s for s in sections if s.get("strategy") != "placeholder"]
    scores = [s.get("score_after") for s in llm_sections if s.get("score_after") is not None]
    placeholders = placeholder_metrics(
        report.get("placeholders") or [], expected_present, expected_absent
    )
    signals = ungrounded_signals(report.get("evaluation_coverage") or [])
    metrics = {
        "sections": _compact_sections(sections),
        "coverage": coverage_metrics(report.get("coverage_summary") or {}),
        "citation": citation_validity(report.get("rule_checks") or []),
        "rules": rule_violations(report.get("rule_checks") or []),
        "ungrounded_signals": signals,
        "ungrounded_signal_count": signals["count"],
        "placeholders": placeholders,
        "llm_usage": dict(report.get("llm_usage") or {}),
        "total_revisions": sum(int(s.get("revisions") or 0) for s in sections),
        "avg_score_after": (
            None if not scores else round(sum(scores) / len(scores), 4)
        ),
        "retrieval_degraded": bool(report.get("retrieval_degraded", False)),
    }
    return metrics


def _get_path(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def project_volatile(metrics: dict[str, Any]) -> dict[str, Any]:
    """剔除易变字段后的稳定投影，实际与期望走同一投影。"""
    projected = json_safe_copy(metrics)
    for path in VOLATILE_PATHS:
        current: Any = projected
        for key in path[:-1]:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if isinstance(current, dict) and path[-1] in current:
            del current[path[-1]]
    return projected


def json_safe_copy(data: Any) -> Any:
    """JSON 往返深拷贝，保证投影不与原对象共享嵌套结构。"""
    return json.loads(json.dumps(data, ensure_ascii=False, sort_keys=True))


def compare_metrics(actual: Any, expected: Any, path: str = "") -> list[str]:
    """递归比较指标字典，输出字段路径级差异。"""
    if isinstance(expected, dict) or isinstance(actual, dict):
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            return [
                (
                    f"{path}: 类型不一致 actual={type(actual).__name__} "
                    f"expected={type(expected).__name__}"
                )
            ]
        diffs: list[str] = []
        for key in sorted(set(actual) | set(expected)):
            next_path = f"{path}.{key}" if path else key
            if key not in actual:
                diffs.append(f"{next_path}: 实际缺失（期望={expected[key]!r}）")
            elif key not in expected:
                diffs.append(f"{next_path}: 期望缺失（实际={actual[key]!r}）")
            else:
                diffs.extend(compare_metrics(actual[key], expected[key], next_path))
        return diffs
    if isinstance(expected, list) or isinstance(actual, list):
        if not isinstance(expected, list) or not isinstance(actual, list):
            return [
                (
                    f"{path}: 类型不一致 actual={type(actual).__name__} "
                    f"expected={type(expected).__name__}"
                )
            ]
        if len(actual) != len(expected):
            return [
                f"{path}: 列表长度不一致 actual={len(actual)} expected={len(expected)}"
            ]
        diffs = []
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected)):
            diffs.extend(
                compare_metrics(actual_item, expected_item, f"{path}[{index}]")
            )
        return diffs
    if actual != expected:
        return [f"{path}: actual={actual!r} expected={expected!r}"]
    return []


def ab_delta(
    metrics_a: dict[str, Any], metrics_b: dict[str, Any]
) -> dict[tuple[str, ...], int]:
    """对声明的整数字段计算 B−A；字段缺失按 0 处理，比率字段不参与。"""
    delta: dict[tuple[str, ...], int] = {}
    for path in DELTA_PATHS:
        value_a = _get_path(metrics_a, path) or 0
        value_b = _get_path(metrics_b, path) or 0
        delta[path] = int(value_b) - int(value_a)
    return delta


def _mean_or_none(values: list[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    return None if not valid else round(sum(valid) / len(valid), 4)


def aggregate(sample_results: list[dict[str, Any]]) -> dict[str, Any]:
    """跨样本聚合：整数指标求和（micro），比率指标按样本等权平均（macro）。"""
    output: dict[str, Any] = {}
    for arm in ARMS:
        arm_metrics = [result["arms"][arm]["metrics"] for result in sample_results]
        output[arm] = {
            "samples": len(arm_metrics),
            "calls": sum(m["llm_usage"].get("calls", 0) for m in arm_metrics),
            "prompt_tokens": sum(
                m["llm_usage"].get("prompt_tokens", 0) for m in arm_metrics
            ),
            "completion_tokens": sum(
                m["llm_usage"].get("completion_tokens", 0) for m in arm_metrics
            ),
            "total_tokens": sum(
                m["llm_usage"].get("total_tokens", 0) for m in arm_metrics
            ),
            "total_revisions": sum(m.get("total_revisions", 0) for m in arm_metrics),
            "rule_total_errors": sum(
                m["rules"]["rule_total_errors"] for m in arm_metrics
            ),
            "ungrounded_signal_count": sum(
                m["ungrounded_signal_count"] for m in arm_metrics
            ),
            "macro_rates": {
                "citation_valid_rate": _mean_or_none(
                    [m["citation"]["citation_valid_rate"] for m in arm_metrics]
                ),
                "plan_coverage_rate": _mean_or_none(
                    [m["coverage"]["plan"]["rate"] for m in arm_metrics]
                ),
                "body_coverage_rate": _mean_or_none(
                    [m["coverage"]["body"]["rate"] for m in arm_metrics]
                ),
                "evidence_coverage_rate": _mean_or_none(
                    [m["coverage"]["evidence"]["rate"] for m in arm_metrics]
                ),
                "placeholder_precision": _mean_or_none(
                    [m["placeholders"]["precision"] for m in arm_metrics]
                ),
                "placeholder_recall": _mean_or_none(
                    [m["placeholders"]["recall"] for m in arm_metrics]
                ),
                "placeholder_f1": _mean_or_none(
                    [m["placeholders"]["f1"] for m in arm_metrics]
                ),
            },
        }
    return output


def _delta_to_jsonable(delta: dict[tuple[str, ...], int]) -> dict[str, int]:
    return {".".join(path): value for path, value in sorted(delta.items())}


def build_writing_report(sample_results: list[dict[str, Any]]) -> dict[str, Any]:
    """组装编写评测信封（与抽取信封独立的版本家族）。"""
    samples = []
    for result in sample_results:
        samples.append(
            {
                "name": result["name"],
                "expected_placeholders": result["expected_placeholders"],
                "arms": {
                    arm: {
                        "passed": result["arms"][arm]["passed"],
                        "diffs": result["arms"][arm]["diffs"],
                        "metrics": result["arms"][arm]["metrics"],
                    }
                    for arm in ARMS
                },
                "ab_delta": _delta_to_jsonable(result["ab_delta"]),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "samples": samples,
        "aggregate": aggregate(sample_results),
    }