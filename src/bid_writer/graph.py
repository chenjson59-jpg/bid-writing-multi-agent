"""投标编写编排（planner → 章节扇出 → 汇总）。

按章节并发执行 `section_agent`，线程池大小取 `settings.bid_writer_max_concurrency`。
检索在扇出前串行完成（共享检索器自带互斥锁），占位章节跳过检索。
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


def run_write_pipeline(
    plans: list[SectionPlan],
    tender,
    profile,
    options: WriteOptions,
    retrieve: Retriever,
    caller: ModelCaller,
    *,
    max_concurrency: int,
) -> list[SectionDraft]:
    """对每个章节并发执行写作循环，返回按 `order` 排序的草稿列表。"""
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
            ): plan
            for plan in plans
        }
        for future in as_completed(futures):
            drafts.append(future.result())

    drafts.sort(key=lambda d: d.plan.order)
    return drafts
