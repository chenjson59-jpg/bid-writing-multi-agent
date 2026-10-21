"""持久化操作：任务历史与投标人资料的读写（尽力而为，失败不阻断主流程）。

数据库是增强能力而非主链路依赖：写库失败仅记日志，解析/编写不受影响。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.db.models import BidderProfile, TaskHistory
from src.db.session import get_session_factory

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _run_async(coro) -> None:
    """在无事件循环的线程里运行协程；已有事件循环则交给后台线程。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    threading.Thread(target=asyncio.run, args=(coro,), daemon=True).start()


def parse_summary(result: dict[str, Any]) -> dict[str, Any]:
    """从解析任务结果提取历史摘要。"""
    return {
        "doc_id": result.get("doc_id"),
        "doc_type": result.get("doc_type"),
        "pages": result.get("pages"),
        "sections": result.get("sections"),
    }


def bid_summary(bid: dict[str, Any]) -> dict[str, Any]:
    """从编写任务结果提取历史摘要。"""
    return {
        "project_name": bid.get("project_name"),
        "bidder": bid.get("bidder"),
        "total_sections": bid.get("total_sections"),
        "total_words": bid.get("total_words"),
    }


async def upsert_task(
    *,
    job_id: str,
    kind: str,
    doc_id: str | None,
    source_name: str | None,
    status: str,
    options: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """按 job_id 幂等写入任务记录。"""
    try:
        async with get_session_factory()() as session:
            task = await session.get(TaskHistory, job_id)
            if task is None:
                session.add(
                    TaskHistory(
                        job_id=job_id,
                        kind=kind,
                        doc_id=doc_id,
                        source_name=source_name,
                        status=status,
                        options=options or {},
                        summary=summary,
                        error=error,
                        created_at=_now(),
                    )
                )
            else:
                task.status = status
                if summary is not None:
                    task.summary = summary
                if error is not None:
                    task.error = error
                if status in ("succeeded", "failed"):
                    task.finished_at = _now()
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - 持久化失败不阻断主流程
        logger.warning("任务历史写入失败: %s", exc)


async def finish_task(
    job_id: str,
    status: str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """任务进入终态时更新记录。"""
    try:
        async with get_session_factory()() as session:
            task = await session.get(TaskHistory, job_id)
            if task is None:
                return
            task.status = status
            task.finished_at = _now()
            if summary is not None:
                task.summary = summary
            if error is not None:
                task.error = error
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - 持久化失败不阻断主流程
        logger.warning("任务历史更新失败: %s", exc)


async def save_profile(company_name: str, data: dict[str, Any]) -> None:
    """保存一份投标人资料。"""
    try:
        async with get_session_factory()() as session:
            session.add(
                BidderProfile(company_name=company_name, data=data, created_at=_now())
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - 持久化失败不阻断主流程
        logger.warning("投标人资料保存失败: %s", exc)


def _task_to_dict(task: TaskHistory) -> dict[str, Any]:
    return {
        "job_id": task.job_id,
        "kind": task.kind,
        "doc_id": task.doc_id,
        "source_name": task.source_name,
        "status": task.status,
        "options": task.options,
        "summary": task.summary,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def _profile_to_dict(profile: BidderProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "company_name": profile.company_name,
        "data": profile.data,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }


async def list_tasks(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """按创建时间倒序返回任务历史。"""
    try:
        async with get_session_factory()() as session:
            result = await session.execute(
                select(TaskHistory)
                .order_by(TaskHistory.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [_task_to_dict(t) for t in result.scalars()]
    except Exception as exc:  # noqa: BLE001 - 查询失败返回空，不阻断
        logger.warning("任务历史查询失败: %s", exc)
        return []


async def list_profiles(limit: int = 100) -> list[dict[str, Any]]:
    """按录入倒序返回投标人资料。"""
    try:
        async with get_session_factory()() as session:
            result = await session.execute(
                select(BidderProfile).order_by(BidderProfile.id.desc()).limit(limit)
            )
            return [_profile_to_dict(p) for p in result.scalars()]
    except Exception as exc:  # noqa: BLE001 - 查询失败返回空，不阻断
        logger.warning("投标人资料查询失败: %s", exc)
        return []


# ---- 同步包装：供 Celery worker / 线程池 / 同步端点调用 ----

def upsert_task_sync(**kwargs: Any) -> None:
    _run_async(upsert_task(**kwargs))


def finish_task_sync(
    job_id: str,
    status: str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    _run_async(finish_task(job_id, status, summary, error))


def save_profile_sync(company_name: str, data: dict[str, Any]) -> None:
    _run_async(save_profile(company_name, data))
