"""投标编写编排（planner → 章节扇出 → 汇总）。

按章节并发执行 `section_agent`，线程池大小取 `settings.bid_writer_max_concurrency`。
检索在扇出前串行完成（共享检索器自带互斥锁），占位章节跳过检索。

进度回调（T-032）：`report` 为可选注入，None 时归一化到模块级 `_NOOP_REPORT`，
缺省路径零行为漂移；章节完成消息只允许在 `as_completed` 消费循环（提交方主线程）
聚合，worker 线程内不打任何进度消息。
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.bid_writer.agents import ModelCaller, section_agent
from src.bid_writer.schema import (
    STRATEGY_PLACEHOLDER,
    SectionDraft,
    SectionPlan,
    WriteOptions,
)

Retriever = Callable[[str, int], list[dict[str, Any]]]
ProgressReport = Callable[[str], None]


def _noop_report(_message: str) -> None:
    """`report=None` 时的空操作回调（模块级常量，避免内联闭包）。"""


_NOOP_REPORT: ProgressReport = _noop_report


def run_write_pipeline(
    plans: list[SectionPlan],
    tender,
    profile,
    options: WriteOptions,
    retrieve: Retriever,
    caller: ModelCaller,
    *,
    max_concurrency: int,
    schema: dict | None = None,
    report: ProgressReport | None = None,
) -> list[SectionDraft]:
    """对每个章节并发执行写作循环，返回按 `order` 排序的草稿列表。

    schema 透传给 section_agent；report 非空时在主线程聚合发送
    「章节检索」与「章节完成 i/N（含 R 次修订）」进度消息。
    """
    report = report or _NOOP_REPORT
    report("章节检索")
    retrieved = {
        plan.id: (
            []
            if plan.strategy == STRATEGY_PLACEHOLDER
            else retrieve(plan.retrieval_query or plan.title, options.top_k)
        )
        for plan in plans
    }

    drafts: list[SectionDraft] = []
    workers = max(1, max_concurrency)
    total = len(plans)
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                section_agent,
                plan,
                tender,
                profile,
                retrieved[plan.id],
                min_score=options.min_score,
                max_revisions=options.max_revisions,
                caller=caller,
                schema=schema,
            ): plan
            for plan in plans
        }
        for future in as_completed(futures):
            draft = future.result()
            completed += 1
            if draft.revisions:
                report(f"章节完成 {completed}/{total}（含 {draft.revisions} 次修订）")
            else:
                report(f"章节完成 {completed}/{total}")
            drafts.append(draft)

    drafts.sort(key=lambda d: d.plan.order)
    return drafts