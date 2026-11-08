"""编写阶段 Golden Data 离线评测 runner（T-022）。

在不调用 ``src.pipeline.run_bid_writer``（其硬构造 ChromaRetriever 并写 upload/）的
前提下，直接组合生产函数 ``plan_sections → run_write_pipeline → build_report``，
以脚本化假模型与 Fake 检索驱动完整编写链，输出可重复的冻结基线指标。

硬约束：零网络、零真实 LLM/Embedding、不碰 chroma_db/、不写 upload/、
max_concurrency=1、夹具缺失或耗尽显式抛错。
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.harness.writing_metrics import (
    ARM_A,
    ARM_B,
    ARMS,
    ab_delta,
    build_writing_report,
    compare_metrics,
    project_volatile,
    summarize_report,
)
from src.bid_writer.agents import plan_sections
from src.bid_writer.assembly import build_report
from src.bid_writer.graph import run_write_pipeline
from src.bid_writer.profile_schema import infer_profile_schema
from src.bid_writer.schema import PROFILE_FIELD_LABELS, BidderProfile, WriteOptions
from src.bid_writer.usage import UsageMeter
from src.extraction.schema import TenderDocument

# 固定时钟：信封不含真实时间戳，保证同结果序列化字节一致
_FROZEN_CLOCK = datetime(2026, 1, 1)
_TITLE_RE = re.compile(r"^章节标题：(.+?)\s*$", re.MULTILINE)

# (内部角色键, system 消息识别字样)；顺序敏感，策划/编写字样不可互相包含
_ROLE_MARKERS: tuple[tuple[str, str], ...] = (
    ("planner", "编写策划助手"),
    ("reviewer", "评审助手"),
    ("reviser", "修订助手"),
    ("writer", "文件编写助手"),
)
_JSON_MODE_ROLES = frozenset({"planner", "reviewer"})

_ARM_MAX_REVISIONS = {ARM_A: 0, ARM_B: 1}


class WritingCaller:
    """脚本化 ModelCaller：按「角色 + 章节标题」键控消费夹具队列。

    夹具键：``planner`` 或 ``writer:<标题>`` / ``reviewer:<标题>`` /
    ``reviser:<标题>``。键缺失、队列耗尽、角色无法识别、标题解析失败或
    json_mode 与角色不符时一律显式抛错，不做静默兜底。
    """

    def __init__(self, fixtures: dict[str, list[dict[str, Any]]]) -> None:
        self._queues: dict[str, list[dict[str, Any]]] = {
            key: list(responses) for key, responses in fixtures.items()
        }
        self.consumption: dict[str, int] = {}

    def detect_role(self, messages: list[dict[str, str]]) -> str:
        system_content = messages[0]["content"] if messages else ""
        for role, marker in _ROLE_MARKERS:
            if marker in system_content:
                return role
        raise RuntimeError(f"无法从 system 提示词识别调用角色：{system_content[:40]!r}")

    @staticmethod
    def _title_of(messages: list[dict[str, str]]) -> str:
        user_content = messages[-1]["content"] if messages else ""
        match = _TITLE_RE.search(user_content)
        if not match:
            raise KeyError("无法从提示词首行解析「章节标题：...」，夹具键控失败")
        return match.group(1).strip()

    def generate(self, messages: list[dict[str, str]], json_mode: bool) -> dict[str, Any]:
        role = self.detect_role(messages)
        expects_json = role in _JSON_MODE_ROLES
        if json_mode != expects_json:
            raise TypeError(
                f"{role} 角色要求 json_mode={expects_json}，实际收到 {json_mode}"
            )
        if role == "planner":
            key = "planner"
        else:
            key = f"{role}:{self._title_of(messages)}"

        queue = self._queues.get(key)
        if not queue:
            raise KeyError(f"Writing 夹具缺失或已耗尽：{key}")
        self.consumption[key] = self.consumption.get(key, 0) + 1
        return dict(queue.pop(0))


def assert_planner_consumed(caller: WritingCaller) -> None:
    """planner 异常会被生产 _planner_llm 静默吞掉并回退确定性大纲，这里显式守门。"""
    consumed = caller.consumption.get("planner", 0)
    if consumed != 1:
        raise RuntimeError(
            f"planner 夹具实际消费 {consumed} 次（期望恰好 1 次），"
            "疑似 planner 调用异常被生产代码静默兜底为确定性大纲"
        )


class ScriptedRetriever:
    """按章节标题键控的 Fake 检索回调，签名与 ChromaRetriever.search 兼容。"""

    def __init__(self, chunks_by_title: dict[str, list[dict[str, Any]]]) -> None:
        self._chunks = {
            title: list(chunks) for title, chunks in chunks_by_title.items()
        }

    def __call__(self, query: str, top_k: int) -> list[dict[str, Any]]:
        if query not in self._chunks:
            raise KeyError(f"检索夹具缺失章节：{query}（top_k={top_k}）")
        return copy.deepcopy(self._chunks[query])


@dataclass
class WritingSample:
    """一份编写评测样本的全部离线输入与冻结期望。"""

    name: str
    tender: TenderDocument
    profile: BidderProfile
    fixtures: dict[str, list[dict[str, Any]]]
    retrieval: dict[str, list[dict[str, Any]]]
    config: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    root: Path | None = None


def load_writing_sample(sample_dir: Path) -> WritingSample:
    """从 Golden 样本目录加载 writing/ 数据集，缺文件或 JSON 非法时显式报错。"""
    sample_dir = Path(sample_dir)
    name = sample_dir.parent.name

    def _load(filename: str, *, required: bool) -> Any:
        path = sample_dir / filename
        if not path.is_file():
            if required:
                raise ValueError(f"编写样本 {name} 缺少必需文件：writing/{filename}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"编写样本 {name} 的 writing/{filename} JSON 非法：{exc}") from exc

    extraction_path = sample_dir.parent / "expected_extraction.json"
    if not extraction_path.is_file():
        raise ValueError(f"编写样本 {name} 缺少已冻结的 expected_extraction.json")
    tender_data = json.loads(extraction_path.read_text(encoding="utf-8"))
    tender = TenderDocument(**{k: v for k, v in tender_data.items()})

    fixtures = _load("fixtures.json", required=True)
    retrieval = _load("retrieval.json", required=True)
    profile_raw = _load("profile.json", required=False) or {}
    config = _load("config.json", required=False) or {}
    expected = _load("expected_writing_metrics.json", required=True)
    if not isinstance(fixtures, dict) or not isinstance(retrieval, dict):
        raise TypeError(f"编写样本 {name} 的 fixtures/retrieval 必须是 JSON 对象")

    placeholder_expectation = expected.get("expected_placeholders") or {}
    present = set(placeholder_expectation.get("present") or [])
    absent = set(placeholder_expectation.get("absent") or [])
    overlap = present & absent
    if overlap:
        raise ValueError(f"编写样本 {name} 占位符 present/absent 相交：{sorted(overlap)}")

    return WritingSample(
        name=name,
        tender=tender,
        profile=BidderProfile.from_dict(profile_raw),
        fixtures=fixtures,
        retrieval=retrieval,
        config=config,
        expected=expected,
        root=sample_dir,
    )


def _write_options(arm: str) -> WriteOptions:
    return WriteOptions(
        max_revisions=_ARM_MAX_REVISIONS[arm],
        min_score=75,
        max_sections=0,
        export_word=False,
        target_words=1200,
        top_k=6,
    )


def execute_arm(
    sample: WritingSample,
    arm: str,
    *,
    caller_factory: Callable[[dict[str, list[dict[str, Any]]]], WritingCaller] = WritingCaller,
) -> dict[str, Any]:
    """对单个样本跑一个 A/B 臂，返回未剔除易变字段的完整指标。"""
    if arm not in ARMS:
        raise ValueError(f"未知评测臂：{arm}")
    options = _write_options(arm)
    tender_dict = sample.tender.to_dict()
    schema = infer_profile_schema(tender_dict, PROFILE_FIELD_LABELS)

    raw_caller = caller_factory(sample.fixtures)
    meter = UsageMeter(price_input=0.0, price_output=0.0)
    caller = meter.wrap(raw_caller)

    plans = plan_sections(
        sample.tender,
        sample.profile,
        target_words=options.target_words,
        max_sections=options.max_sections,
        caller=caller,
        schema=schema,
    )
    assert_planner_consumed(raw_caller)

    drafts = run_write_pipeline(
        plans,
        sample.tender,
        sample.profile,
        options,
        ScriptedRetriever(sample.retrieval),
        caller,
        max_concurrency=1,
        schema=schema,
    )

    report = build_report(
        sample.tender.doc_id,
        sample.tender.project_name,
        sample.profile.company_name,
        drafts,
        sample.tender,
        sample.profile,
        _FROZEN_CLOCK,
        schema=schema,
        retrieval_degraded=bool(sample.config.get("retrieval_degraded", False)),
    )
    report.llm_usage = meter.summary()

    placeholder_expectation = sample.expected.get("expected_placeholders") or {}
    return summarize_report(
        report.to_dict(),
        placeholder_expectation.get("present") or [],
        placeholder_expectation.get("absent") or [],
    )


def run_sample(sample: WritingSample) -> dict[str, Any]:
    """跑两臂并与人工冻结期望比较，计算 A/B 差值。"""
    arms: dict[str, dict[str, Any]] = {}
    projected_metrics: dict[str, dict[str, Any]] = {}
    expected_arms = sample.expected.get("arms") or {}
    for arm in ARMS:
        actual = execute_arm(sample, arm)
        projected = project_volatile(actual)
        projected_metrics[arm] = projected
        diffs = compare_metrics(projected, expected_arms.get(arm, {}))
        arms[arm] = {"passed": not diffs, "diffs": diffs, "metrics": projected}

    return {
        "name": sample.name,
        "expected_placeholders": sample.expected.get("expected_placeholders") or {
            "present": [],
            "absent": [],
        },
        "arms": arms,
        "ab_delta": ab_delta(projected_metrics[ARM_A], projected_metrics[ARM_B]),
    }


def discover_sample_dirs(datasets_dir: Path) -> list[Path]:
    return sorted(
        child / "writing"
        for child in sorted(datasets_dir.iterdir())
        if child.is_dir() and (child / "writing" / "fixtures.json").is_file()
    )


def run_all(datasets_dir: Path | None = None) -> list[dict[str, Any]]:
    """加载全部含 writing/ 子目录的 Golden 样本并顺序执行，不产生仓库文件残留。"""
    datasets_dir = datasets_dir or (
        Path(__file__).resolve().parents[1] / "datasets" / "golden"
    )
    results = []
    for sample_dir in discover_sample_dirs(datasets_dir):
        results.append(run_sample(load_writing_sample(sample_dir)))
    if not results:
        raise ValueError(f"未在 {datasets_dir} 发现任何 writing/ 编写评测样本")
    return results


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_summary(results: list[dict[str, Any]]) -> str:
    lines = [
        "编写评测指标（T-022；A=max_revisions=0 单轮直写，B=max_revisions=1 评审修订闭环）：",
        (
            f"{'样本':<16}{'臂':<18}{'调用':>4}{'token':>7}{'修订':>4}"
            f"{'计划/正文/证据覆盖':>22}{'引用有效率':>12}{'规则错误':>8}"
        ),
    ]
    for result in results:
        for arm in ARMS:
            metrics = result["arms"][arm]["metrics"]
            coverage = metrics["coverage"]
            coverage_text = (
                f"{coverage['plan']['covered']}/{coverage['body']['covered']}/"
                f"{coverage['evidence']['covered']}"
            )
            lines.append(
                f"{result['name']:<16}{arm:<18}"
                f"{metrics['llm_usage'].get('calls', 0):>4}"
                f"{metrics['llm_usage'].get('total_tokens', 0):>7}"
                f"{metrics['total_revisions']:>4}{coverage_text:>22}"
                f"{_percent(metrics['citation']['citation_valid_rate']):>12}"
                f"{metrics['rules']['rule_total_errors']:>8}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="编写阶段 Golden Data 离线评测（T-022）")
    parser.add_argument("--out", type=Path, default=None, help="可选：把 JSON 报告写入该路径")
    args = parser.parse_args(argv)

    results = run_all()
    all_passed = True
    for result in results:
        for arm in ARMS:
            arm_result = result["arms"][arm]
            if arm_result["passed"]:
                print(f"[PASS] {result['name']} {arm}")
            else:
                all_passed = False
                print(f"[FAIL] {result['name']} {arm}")
                for diff in arm_result["diffs"]:
                    print(f"       - {diff}")
    passed = sum(
        1
        for result in results
        if all(result["arms"][arm]["passed"] for arm in ARMS)
    )
    print(f"\n汇总：{passed}/{len(results)} 份样本两臂全部匹配")
    print()
    print(format_summary(results))

    if args.out is not None:
        args.out.write_text(
            json.dumps(build_writing_report(results), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())