"""任务可观测性：结构化事件日志、阶段计时与指标聚合（P1-9）。

每个任务一份 JSON Lines 日志（`<LOG_DIR>/jobs/<job_id>.jsonl`），记录阶段开始 /
结束（含耗时）与终态异常。任务在 `job_scope()` 内执行时，`stage()` 调用会被自动
计入该任务的轨迹，因此流水线内部无需感知 job_id。

日志按任务隔离，失败后可用 `job_id` 直接定位到阶段与异常：接口侧提供
`GET /api/jobs/{job_id}/trace`，指标侧提供 `GET /api/metrics`。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import settings

logger = logging.getLogger("bidding.observability")

_JOB_ID: ContextVar[str] = ContextVar("job_id", default="")
_KIND: ContextVar[str] = ContextVar("job_kind", default="")
_TRACE: ContextVar[JobTrace | None] = ContextVar("job_trace", default=None)


def jobs_log_dir() -> Path:
    """任务日志目录，随 `settings.log_dir` 变化（测试中可重定向）。"""
    return Path(settings.log_dir) / "jobs"


def current_job_id() -> str:
    """当前任务 id；不在任务上下文内时为空串。"""
    return _JOB_ID.get()


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class JobTrace:
    """单任务的阶段与终态记录，线程安全；写日志失败不影响主任务。"""

    def __init__(self, job_id: str, kind: str) -> None:
        self.job_id = job_id
        self.kind = kind
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._stage: str | None = None
        self._stage_started = self._started
        self._closed = False
        self._write("start")

    @property
    def closed(self) -> bool:
        return self._closed

    def stage(self, name: str) -> None:
        """切换到新阶段：先结算上一阶段耗时，同名阶段重复上报会被忽略。"""
        with self._lock:
            if self._closed or not name or name == self._stage:
                return
            self._close_stage()
            self._stage = name
            self._stage_started = time.monotonic()
            self._write("stage_start", stage=name)

    def finish(self, status: str, exc: BaseException | None = None) -> None:
        """记录终态；重复调用只生效一次。"""
        with self._lock:
            if self._closed:
                return
            failed_stage = self._stage
            self._close_stage()
            fields: dict[str, Any] = {
                "status": status,
                "duration_ms": self._elapsed(self._started),
            }
            if failed_stage:
                fields["stage"] = failed_stage
            if exc is not None:
                fields["error"] = str(exc)
                fields["error_type"] = type(exc).__name__
            self._write("end", **fields)
            self._closed = True

    def _close_stage(self) -> None:
        if self._stage is None:
            return
        self._write(
            "stage_done",
            stage=self._stage,
            duration_ms=self._elapsed(self._stage_started),
        )
        self._stage = None

    @staticmethod
    def _elapsed(start: float) -> int:
        return int((time.monotonic() - start) * 1000)

    def _write(self, event: str, **fields: Any) -> None:
        record = {"ts": _iso_now(), "job_id": self.job_id, "kind": self.kind, "event": event}
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False)
        # 标准日志里同样带上 job_id，便于 grep；结构化明细在 JSONL 文件里
        logger.info(line)
        try:
            directory = jobs_log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / f"{self.job_id}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.warning("任务日志写入失败 job=%s: %s", self.job_id, exc)


def stage(name: str) -> None:
    """记录阶段（无任务上下文时为空操作），供流水线内部打点。"""
    trace = _TRACE.get()
    if trace is not None:
        trace.stage(name)


@contextmanager
def job_scope(job_id: str, kind: str) -> Iterator[JobTrace]:
    """绑定任务上下文并开启轨迹；异常与未显式收尾都按失败记录。"""
    trace = JobTrace(job_id, kind)
    tokens = (_JOB_ID.set(job_id), _KIND.set(kind), _TRACE.set(trace))
    try:
        yield trace
    except BaseException as exc:  # 记录真实异常后再向外抛，交给调用方处理
        trace.finish("failed", exc)
        raise
    finally:
        # 正常收尾与异常收尾都未生效时（进程退出等）兜底标记失败
        trace.finish("failed", RuntimeError("任务未正常结束"))
        _TRACE.reset(tokens[2])
        _KIND.reset(tokens[1])
        _JOB_ID.reset(tokens[0])


def _read_records(job_id: str) -> list[dict[str, Any]]:
    """读取某任务的日志记录；不存在或损坏时返回空列表。"""
    if not job_id or not job_id.isalnum():
        return []
    path = jobs_log_dir() / f"{job_id}.jsonl"
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(data)
    except OSError:
        return []
    return records


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """把事件序列还原为「状态 + 当前/失败阶段 + 各阶段耗时」。"""
    if not records:
        return {}
    stages: list[dict[str, Any]] = []
    current: str | None = None
    end: dict[str, Any] | None = None
    for record in records:
        event = record.get("event")
        if event == "stage_start":
            current = record.get("stage")
        elif event == "stage_done":
            stages.append(
                {
                    "name": record.get("stage"),
                    "duration_ms": int(record.get("duration_ms") or 0),
                }
            )
            current = None
        elif event == "end":
            end = record

    summary: dict[str, Any] = {
        "job_id": records[0].get("job_id"),
        "kind": records[0].get("kind"),
        "status": str(end.get("status")) if end else "running",
        "stage": (end.get("stage") if end else current) or None,
        "started_at": records[0].get("ts"),
        "finished_at": end.get("ts") if end else None,
        "duration_ms": end.get("duration_ms") if end else None,
        "stages": stages,
        "error": end.get("error") if end else None,
        "error_type": end.get("error_type") if end else None,
    }
    return summary


def job_trace(job_id: str) -> dict[str, Any] | None:
    """按 job_id 返回轨迹摘要；无日志（任务未执行或日志已清理）返回 None。"""
    summary = _summarize(_read_records(job_id))
    return summary or None


def latest_stage(job_id: str) -> str | None:
    """当前或最后阶段，供任务查询接口展示。"""
    trace = job_trace(job_id)
    return trace.get("stage") if trace else None


def metrics(limit: int = 500) -> dict[str, Any]:
    """聚合最近 `limit` 个任务的终态、耗时与失败原因。"""
    directory = jobs_log_dir()
    try:
        files = sorted(
            directory.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
    except OSError:
        files = []

    by_status: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    durations: dict[str, list[int]] = {}
    failures: list[dict[str, Any]] = []

    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        records = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(data)
        summary = _summarize(records)
        if not summary:
            continue
        status = str(summary["status"])
        kind = str(summary.get("kind") or "unknown")
        by_status[status] += 1
        by_kind[kind] += 1
        if summary.get("duration_ms") is not None:
            durations.setdefault(kind, []).append(int(summary["duration_ms"]))
        if status == "failed":
            failures.append(
                {
                    "job_id": summary["job_id"],
                    "kind": kind,
                    "stage": summary.get("stage"),
                    "error_type": summary.get("error_type"),
                    "error": summary.get("error"),
                    "finished_at": summary.get("finished_at"),
                }
            )

    failures.sort(key=lambda item: item.get("finished_at") or "", reverse=True)
    avg_duration = {
        kind: int(sum(values) / len(values)) for kind, values in durations.items() if values
    }
    return {
        "jobs_total": sum(by_status.values()),
        "by_status": dict(by_status),
        "by_kind": dict(by_kind),
        "avg_duration_ms": avg_duration,
        "recent_failures": failures[:10],
    }


__all__ = [
    "JobTrace",
    "current_job_id",
    "job_scope",
    "job_trace",
    "jobs_log_dir",
    "latest_stage",
    "metrics",
    "stage",
]
