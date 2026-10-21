"""任务分发：生成 job_id、入队、排队深度控制与任务视图查询。

支持两种后端（`settings.task_backend`）：
- celery：先写任务标记到 Redis，再入队；查询走 `AsyncResult` + 任务标记；
- local：内存任务表 + 线程池，重启即丢失，仅用于测试与无队列环境。

两种后端对外返回统一的「任务视图」字典，时间字段一律为 ISO 8601。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from api.jobs import map_state
from src.celery_app import celery_app
from src.config import settings
from src.tasks import ProgressReporter, run_parse_task

_JOB_KEY = "bidding:job:{job_id}"
_QUEUE_NAME = "bidding"


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _split_result(result: dict[str, Any]) -> dict[str, Any]:
    """把任务返回的扁平结果拆成任务视图里的 result / extraction / ingest。"""
    return {
        "result": {
            "doc_id": result["doc_id"],
            "doc_type": result["doc_type"],
            "parser": result["parser"],
            "pages": result["pages"],
            "sections": result["sections"],
            "source_name": result["source_name"],
        },
        "extraction": result.get("extraction"),
        "ingest": result.get("ingest"),
    }


class CeleryBackend:
    """Celery + Redis：任务标记存 Redis，状态与进度走结果后端。"""

    def __init__(self) -> None:
        self._redis: Any = None

    def _r(self):
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(
                settings.redis_url, decode_responses=True
            )
        return self._redis

    def submit(
        self, job_id: str, source_path: str, options: dict, meta: dict
    ) -> None:
        from src.tasks import parse_document

        # 先写任务标记（供 pending 阶段查询元数据），再入队
        self._r().set(
            _JOB_KEY.format(job_id=job_id),
            json.dumps(meta),
            ex=settings.celery_result_expires,
        )
        parse_document.apply_async(args=(source_path, options), task_id=job_id)

    def get_job(self, job_id: str) -> dict | None:
        raw = self._r().get(_JOB_KEY.format(job_id=job_id))
        if raw is None:
            return None
        meta = json.loads(raw)

        from celery.result import AsyncResult

        result = AsyncResult(job_id, app=celery_app)
        status = map_state(result.state)
        view = {
            **meta,
            "status": status,
            "started_at": None,
            "finished_at": None,
            "progress": [],
            "result": None,
            "extraction": None,
            "ingest": None,
            "bid": None,
            "error": None,
            "error_type": None,
        }

        if status == "running":
            info = result.info if isinstance(result.info, dict) else {}
            view["progress"] = info.get("progress", [])
            view["started_at"] = _iso(info.get("started_at"))
        elif status == "succeeded" and isinstance(result.result, dict):
            split = _split_result(result.result)
            view.update(split)
            view["progress"] = result.result.get("progress", [])
            view["started_at"] = _iso(result.result.get("started_at"))
            view["finished_at"] = _iso(result.result.get("finished_at"))
        elif status == "failed":
            view["error"] = str(result.info)
            view["error_type"] = type(result.info).__name__

        return view

    def available(self) -> bool:
        try:
            return bool(self._r().ping())
        except Exception:  # noqa: BLE001 - Redis 不可用即视为队列不可用
            return False

    def queue_depth(self) -> int:
        try:
            return int(self._r().llen(_QUEUE_NAME))
        except Exception:  # noqa: BLE001 - 查询失败按 0 处理
            return 0


class LocalBackend:
    """内存任务表 + 线程池（无 Redis 环境）。"""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=settings.api_max_concurrent_parses,
            thread_name_prefix="parse",
        )

    def submit(
        self, job_id: str, source_path: str, options: dict, meta: dict
    ) -> None:
        with self._lock:
            self._jobs[job_id] = {
                **meta,
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "progress": [],
                "result": None,
                "extraction": None,
                "ingest": None,
                "bid": None,
                "error": None,
                "error_type": None,
            }
        self._executor.submit(self._run, job_id, source_path, options)

    def _run(self, job_id: str, source_path: str, options: dict) -> None:
        started_at = time.time()
        with self._lock:
            self._jobs[job_id]["status"] = "running"
            self._jobs[job_id]["started_at"] = started_at

        def sink(messages: list[str]) -> None:
            with self._lock:
                self._jobs[job_id]["progress"] = messages

        reporter = ProgressReporter(sink)
        try:
            result = run_parse_task(source_path, options, reporter.report)
            with self._lock:
                self._jobs[job_id].update(
                    status="succeeded",
                    finished_at=time.time(),
                    progress=reporter.snapshot(),
                    **_split_result(result),
                )
        except Exception as exc:  # noqa: BLE001 - 任务失败需落到任务视图
            with self._lock:
                self._jobs[job_id].update(
                    status="failed",
                    finished_at=time.time(),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            view = dict(job)
        view["created_at"] = _iso(view.get("created_at"))
        view["started_at"] = _iso(view.get("started_at"))
        view["finished_at"] = _iso(view.get("finished_at"))
        return view

    def available(self) -> bool:
        return True

    def queue_depth(self) -> int:
        with self._lock:
            return sum(
                1
                for job in self._jobs.values()
                if job["status"] in ("pending", "running")
            )


_backend: CeleryBackend | LocalBackend | None = None


def _get_backend() -> CeleryBackend | LocalBackend:
    global _backend
    if _backend is None:
        _backend = (
            CeleryBackend() if settings.task_backend == "celery" else LocalBackend()
        )
    return _backend


def submit_parse(
    source_path: str, options: dict[str, Any], doc_id: str, source_name: str
) -> str:
    """提交解析任务，返回 job_id。"""
    job_id = uuid.uuid4().hex
    meta = {
        "job_id": job_id,
        "kind": "parse",
        "source_name": source_name,
        "doc_id": doc_id,
        "options": options,
        "created_at": time.time(),
    }
    _get_backend().submit(job_id, source_path, options, meta)
    return job_id


def get_job(job_id: str) -> dict | None:
    return _get_backend().get_job(job_id)


def queue_available() -> bool:
    return _get_backend().available()


def queue_depth() -> int:
    return _get_backend().queue_depth()
