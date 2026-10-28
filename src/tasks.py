"""后台任务定义。

接口层只入队并返回 job_id，实际执行在 Celery worker（或本地线程池）：
- bidding.parse_document：解析 + 结构化抽取 + 建立检索索引
- bidding.write_bid：多角色编写投标文件（M4 阶段补齐）

`run_parse_task` 是核心执行逻辑，`parse_document` 仅负责把它包装成 Celery 任务，
两种入口复用同一套进度上报，保证接口层与本地模式行为一致。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.celery_app import celery_app
from src.db.repository import bid_summary, finish_task_sync, parse_summary
from src.extraction.extractor import run_extraction
from src.observability import job_scope, stage
from src.pipeline import run_bid_writer, run_parse
from src.vectorstore.ingest import ingest_document

# A22：进度节流窗口与保留条数
_PROGRESS_THROTTLE = 0.5
_PROGRESS_KEEP = 50


class ProgressReporter:
    """进度上报：0.5 秒节流，仅保留最近 50 条，上报失败不阻断主任务。"""

    def __init__(self, sink: Callable[[list[str]], None]) -> None:
        self._sink = sink
        self._messages: list[str] = []
        self._last = 0.0

    def report(self, message: str) -> None:
        self._messages.append(message)
        # 进度文案即阶段名：同一条文案重复上报会被轨迹去重（P1-9）
        stage(message)
        now = time.monotonic()
        if now - self._last < _PROGRESS_THROTTLE:
            return
        self._last = now
        self._flush()

    def snapshot(self) -> list[str]:
        """最近 50 条消息，任务结束时写入最终结果。"""
        return self._messages[-_PROGRESS_KEEP:]

    def _flush(self) -> None:
        try:
            self._sink(self.snapshot())
        except Exception:  # noqa: BLE001 - 进度上报失败不影响主任务
            return


def _extraction_summary(extraction) -> dict[str, Any]:
    """抽取结果摘要，供任务结果返回。"""
    return {
        "project_name": extraction.project_name,
        "evaluation_factors": len(extraction.evaluation_factors),
        "bid_format_items": len(extraction.bid_format_items),
        "qualifications": len(extraction.qualifications),
    }


def run_parse_task(
    source_path: str,
    options: dict[str, Any],
    report: Callable[[str], None],
) -> dict[str, Any]:
    """解析 → 抽取 → 索引 的核心执行逻辑，`report` 为进度回调。"""
    parser = options.get("parser", "auto")
    force = options.get("force", False)

    report("解析文档")
    doc = run_parse(source_path, parser=parser, force=force)
    result = {
        "doc_id": doc.doc_id,
        "doc_type": doc.doc_type,
        "parser": doc.parser,
        "pages": doc.pages,
        "sections": len(doc.sections),
        "source_name": doc.source_name,
    }

    extraction = None
    if options.get("extract"):
        report("结构化抽取")
        extraction = run_extraction(doc, force=force)

    ingest = None
    if options.get("ingest"):
        report("建立检索索引")
        project_name = extraction.project_name if extraction else ""
        ingest = ingest_document(doc, project_name=project_name).to_dict()

    result["extraction"] = _extraction_summary(extraction) if extraction else None
    result["ingest"] = ingest
    return result


@celery_app.task(bind=True, name="bidding.parse_document")
def parse_document(self, source_path: str, options: dict[str, Any]) -> dict[str, Any]:
    """Celery 入口：把 `update_state` 包装成进度回调后执行核心逻辑。"""
    started_at = time.time()
    job_id = self.request.id

    def sink(messages: list[str]) -> None:
        self.update_state(
            state="PROGRESS",
            meta={"progress": messages, "started_at": started_at},
        )

    reporter = ProgressReporter(sink)
    with job_scope(job_id, "parse") as trace:
        try:
            result = run_parse_task(source_path, options, reporter.report)
            result["progress"] = reporter.snapshot()
            result["started_at"] = started_at
            trace.finish("succeeded")
            finish_task_sync(job_id, "succeeded", summary=parse_summary(result))
            return result
        except Exception as exc:
            trace.finish("failed", exc)
            finish_task_sync(job_id, "failed", error=str(exc))
            raise


def run_bid_task(
    doc_id: str,
    profile: dict[str, Any] | None,
    options: dict[str, Any] | None,
    report: Callable[[str], None],
) -> dict[str, Any]:
    """投标编写核心逻辑：规划 → 分章节写作 → 汇总，返回 bid 摘要。"""
    report("规划章节")
    result = run_bid_writer(doc_id, profile=profile, options=options)
    return {"bid": result.summary()}


@celery_app.task(bind=True, name="bidding.write_bid")
def write_bid(
    self,
    doc_id: str,
    profile: dict[str, Any] | None,
    options: dict[str, Any] | None,
) -> dict[str, Any]:
    """Celery 入口：把 `update_state` 包装成进度回调后执行投标编写。"""
    started_at = time.time()
    job_id = self.request.id

    def sink(messages: list[str]) -> None:
        self.update_state(
            state="PROGRESS",
            meta={"progress": messages, "started_at": started_at},
        )

    reporter = ProgressReporter(sink)
    with job_scope(job_id, "bid") as trace:
        try:
            result = run_bid_task(doc_id, profile, options, reporter.report)
            result["progress"] = reporter.snapshot()
            result["started_at"] = started_at
            trace.finish("succeeded")
            finish_task_sync(job_id, "succeeded", summary=bid_summary(result.get("bid") or {}))
            return result
        except Exception as exc:
            trace.finish("failed", exc)
            finish_task_sync(job_id, "failed", error=str(exc))
            raise


def run_supplement_task(
    bid_id: str,
    report: Callable[[str], None],
) -> dict[str, Any]:
    """未覆盖评分因素补写的核心逻辑（P1-1）。"""
    from src.bid_writer.supplement import run_supplement

    report("补写未覆盖评分因素")
    result = run_supplement(bid_id)
    return {"bid": result.summary()}


@celery_app.task(bind=True, name="bidding.supplement_bid")
def supplement_bid(self, bid_id: str) -> dict[str, Any]:
    """Celery 入口：在已有产物基础上补写未覆盖评分因素。"""
    started_at = time.time()
    job_id = self.request.id

    def sink(messages: list[str]) -> None:
        self.update_state(
            state="PROGRESS",
            meta={"progress": messages, "started_at": started_at},
        )

    reporter = ProgressReporter(sink)
    with job_scope(job_id, "supplement") as trace:
        try:
            result = run_supplement_task(bid_id, reporter.report)
            result["progress"] = reporter.snapshot()
            result["started_at"] = started_at
            trace.finish("succeeded")
            finish_task_sync(job_id, "succeeded", summary=bid_summary(result.get("bid") or {}))
            return result
        except Exception as exc:
            trace.finish("failed", exc)
            finish_task_sync(job_id, "failed", error=str(exc))
            raise


def run_section_rewrite_task(
    bid_id: str,
    section_ids: list[str] | None,
    report: Callable[[str], None],
) -> dict[str, Any]:
    """章节级重写核心逻辑（P1-2）：只重写指定章节，锁定章节跳过。"""
    from src.bid_writer.sections import rewrite_sections

    report("重写章节")
    result = rewrite_sections(bid_id, section_ids)
    return {"bid": result.summary()}


@celery_app.task(bind=True, name="bidding.rewrite_section")
def rewrite_section(
    self, bid_id: str, section_ids: list[str] | None = None
) -> dict[str, Any]:
    """Celery 入口：章节级重写（原地更新产物，不新建目录）。"""
    started_at = time.time()
    job_id = self.request.id

    def sink(messages: list[str]) -> None:
        self.update_state(
            state="PROGRESS",
            meta={"progress": messages, "started_at": started_at},
        )

    reporter = ProgressReporter(sink)
    with job_scope(job_id, "rewrite") as trace:
        try:
            result = run_section_rewrite_task(bid_id, section_ids, reporter.report)
            result["progress"] = reporter.snapshot()
            result["started_at"] = started_at
            trace.finish("succeeded")
            finish_task_sync(job_id, "succeeded", summary=bid_summary(result.get("bid") or {}))
            return result
        except Exception as exc:
            trace.finish("failed", exc)
            finish_task_sync(job_id, "failed", error=str(exc))
            raise
