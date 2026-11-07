"""Golden Data 离线评测 runner（T-020 精确匹配 + T-021 字段级指标）。

对每份样本跑完整 `run_extraction`（脚本化模型，零网络），把实际产物与人工核定的
期望产物做稳定投影后的精确比较，并在同一底座上叠加字段级 Precision/Recall/F1
与未溯源字段率。精确匹配仍是回归基线与退出码依据，指标为增强能力，不设门禁。
"""
from __future__ import annotations

import argparse
import json
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eval.harness.loader import (
    GoldenCaller,
    GoldenSample,
    load_all,
    markdown_to_parsed_doc,
)
from eval.harness.metrics import build_report, evaluate_sample
from src.extraction.extractor import run_extraction

# 易变/环境相关元数据：每次运行可能不同，不参与基线比较
VOLATILE_META_KEYS = {"elapsed_seconds", "cache_hit", "model", "content_sha256"}


@dataclass
class EvalResult:
    """单样本评测结果：精确匹配结论 + 字段级指标。"""

    name: str
    passed: bool
    diffs: list[str] = field(default_factory=list)
    actual: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


def project_document(doc: dict[str, Any]) -> dict[str, Any]:
    """剔除易变键后的稳定投影，实际产物与期望文件走同一投影。"""
    data = dict(doc)
    meta = {
        key: value
        for key, value in (data.get("extraction_meta") or {}).items()
        if key not in VOLATILE_META_KEYS
    }
    data["extraction_meta"] = meta
    return data


def _diff(path: str, actual: Any, expected: Any) -> list[str]:
    """递归比较，输出字段路径级差异。"""
    if isinstance(expected, dict) or isinstance(actual, dict):
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            return [f"{path}: 类型不一致 actual={type(actual).__name__} expected={type(expected).__name__}"]
        diffs: list[str] = []
        for key in sorted(set(actual) | set(expected)):
            next_path = f"{path}.{key}" if path else key
            if key not in actual:
                diffs.append(f"{next_path}: 实际缺失（期望={expected[key]!r}）")
            elif key not in expected:
                diffs.append(f"{next_path}: 期望缺失（实际={actual[key]!r}）")
            else:
                diffs.extend(_diff(next_path, actual[key], expected[key]))
        return diffs
    if isinstance(expected, list) or isinstance(actual, list):
        if not isinstance(expected, list) or not isinstance(actual, list):
            return [f"{path}: 类型不一致 actual={type(actual).__name__} expected={type(expected).__name__}"]
        if len(actual) != len(expected):
            return [f"{path}: 列表长度不一致 actual={len(actual)} expected={len(expected)}"]
        diffs = []
        for index, (a, e) in enumerate(zip(actual, expected)):
            diffs.extend(_diff(f"{path}[{index}]", a, e))
        return diffs
    if actual != expected:
        return [f"{path}: actual={actual!r} expected={expected!r}"]
    return []


def run_sample(sample: GoldenSample, work_dir: Path) -> EvalResult:
    """在指定工作目录（通常是临时目录）运行单样本并比较期望产物。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    doc = markdown_to_parsed_doc(sample.markdown, sample.meta)
    caller = GoldenCaller(sample.fixtures)
    output_dir = work_dir / sample.name
    result_doc = run_extraction(doc, caller=caller, output_dir=output_dir, force=True)

    expected_path = sample.root / "expected_extraction.json"
    expected = project_document(json.loads(expected_path.read_text(encoding="utf-8")))
    actual = project_document(result_doc.to_dict())
    diffs = _diff("", actual, expected)
    metrics = evaluate_sample(sample.name, actual, expected, sample.markdown)
    return EvalResult(
        name=sample.name,
        passed=not diffs,
        diffs=diffs,
        actual=actual,
        metrics=metrics,
    )


def run_all(datasets_dir: Path | None = None) -> list[EvalResult]:
    """在单个临时目录内顺序跑完全部样本，不写 upload/ 与 chroma_db/。"""
    samples = load_all(datasets_dir)
    results: list[EvalResult] = []
    with tempfile.TemporaryDirectory(prefix="golden_eval_") as tmp:
        for sample in samples:
            results.append(run_sample(sample, Path(tmp)))
    return results


def _display_width(text: str) -> int:
    """终端显示宽度：全角/宽字符计 2 列，其余计 1 列。"""
    return sum(
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        for char in text
    )


def _pad(text: str, width: int) -> str:
    return text + " " * max(width - _display_width(text), 1)


def _ratio_text(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _percent_text(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_metrics_section(
    results: list[EvalResult], aggregate: dict[str, Any]
) -> str:
    """渲染终端字段指标表（micro 按字段量加权，macro 按样本等权）。"""
    headers = ("样本", "TP", "FP", "FN", "Precision", "Recall", "F1",
               "证据单元", "未溯源", "未溯源率")
    widths = (18, 5, 4, 4, 10, 8, 8, 9, 7, 9)
    lines = [
        "抽取字段指标（T-021；micro 按字段量加权，macro 按样本等权）：",
        "".join(_pad(header, width) for header, width in zip(headers, widths)),
    ]
    for result in results:
        metrics = result.metrics
        counts = metrics["counts"]
        grounding = metrics["grounding"]
        row = (
            result.name,
            str(counts["tp"]),
            str(counts["fp"]),
            str(counts["fn"]),
            _ratio_text(metrics["precision"]),
            _ratio_text(metrics["recall"]),
            _ratio_text(metrics["f1"]),
            str(grounding["evidence_units"]),
            str(grounding["ungrounded"]),
            _percent_text(grounding["ungrounded_rate"]),
        )
        lines.append("".join(_pad(cell, width) for cell, width in zip(row, widths)))

    micro = aggregate["micro"]
    micro_grounding = aggregate["grounding"]["micro"]
    macro = aggregate["macro"]
    macro_grounding = aggregate["grounding"]["macro"]
    micro_row = (
        "micro",
        str(micro["tp"]), str(micro["fp"]), str(micro["fn"]),
        _ratio_text(micro["precision"]), _ratio_text(micro["recall"]),
        _ratio_text(micro["f1"]),
        str(micro_grounding["evidence_units"]),
        str(micro_grounding["ungrounded"]),
        _percent_text(micro_grounding["ungrounded_rate"]),
    )
    macro_row = (
        "macro", "-", "-", "-",
        _ratio_text(macro["precision"]), _ratio_text(macro["recall"]),
        _ratio_text(macro["f1"]), "-", "-",
        _percent_text(macro_grounding["ungrounded_rate"]),
    )
    lines.append("".join(_pad(cell, width) for cell, width in zip(micro_row, widths)))
    lines.append("".join(_pad(cell, width) for cell, width in zip(macro_row, widths)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden Data 离线评测（T-020/T-021）")
    parser.add_argument("--out", type=Path, default=None, help="可选：把 JSON 报告写入该路径")
    args = parser.parse_args(argv)

    results = run_all()
    for result in results:
        if result.passed:
            print(f"[PASS] {result.name}")
        else:
            print(f"[FAIL] {result.name}")
            for diff in result.diffs:
                print(f"       - {diff}")
    passed = sum(1 for r in results if r.passed)
    print(f"\n汇总：{passed}/{len(results)} 份样本匹配")

    report = build_report(results)
    print()
    print(format_metrics_section(results, report["aggregate"]))

    if args.out is not None:
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())