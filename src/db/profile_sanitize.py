"""T-041 历史高敏数据物理清洗（方案 A：数据库层收敛）。

设计要点见 docs/specs/2026-11-12-t041-historical-sensitive-data-cleanup.md：

- 白名单 7 键的唯一事实源是 src.bid_writer.schema.PROFILE_FIELD_LABELS，本模块不复制键表；
- 纯函数无 IO，可直接单元测试；异步函数经会话工厂操作 SQLite/PostgreSQL；
- task_history.options 的 profile 子键整体移除（重试快照非业务主数据），bidder_profile
  行仅裁剪 data 键保留低敏白名单；
- 路由层剥离函数与仓储层隔离：retry 的 get_task 仍读库内完整 options，不被接口收敛截断。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from src.bid_writer.schema import PROFILE_FIELD_LABELS
from src.db.models import BidderProfile, TaskHistory
from src.db.session import get_session_factory

logger = logging.getLogger(__name__)

# 单一事实源：与 T-040 表单/prompt 白名单同源，禁止在本模块另立键表副本
PROFILE_WHITELIST_KEYS: tuple[str, ...] = tuple(PROFILE_FIELD_LABELS.keys())


def sanitize_profile_data(data: Any) -> dict[str, Any]:
    """裁剪投标人资料：仅保留白名单键（含原值），其余键（含 notes/未知键）丢弃。"""
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in PROFILE_WHITELIST_KEYS if key in data}


def strip_task_options(options: Any) -> dict[str, Any]:
    """移除任务 options 中的 profile 快照；不原地修改入参，其余选项原样保留。"""
    if not isinstance(options, dict):
        return {}
    stripped = dict(options)
    stripped.pop("profile", None)
    return stripped


def sanitize_task_for_api(item: dict[str, Any]) -> dict[str, Any]:
    """接口视图收敛：仅剥离 options.profile，供 GET /api/tasks 使用。"""
    view = dict(item)
    if "options" in view:
        options = view["options"]
        view["options"] = strip_task_options(options) if isinstance(options, dict) else options
    return view


@dataclass
class CleanupPlan:
    """只读清洗计划：列出将被变更的行与计数，不产生任何写入。"""

    profile_ids: list[int] = field(default_factory=list)
    task_job_ids: list[str] = field(default_factory=list)
    profile_changed_count: int = 0
    task_changed_count: int = 0


@dataclass
class CleanupReport:
    """apply 实际变更报告；二次执行时两个 changed 计数应为 0（幂等）。"""

    profiles_changed: int = 0
    tasks_changed: int = 0
    profile_ids: list[int] = field(default_factory=list)
    task_job_ids: list[str] = field(default_factory=list)


async def collect_cleanup_plan() -> CleanupPlan:
    """扫描两表，比较清洗前后差异，返回待变更清单（只读）。"""
    plan = CleanupPlan()
    async with get_session_factory()() as session:
        profiles = (await session.execute(select(BidderProfile))).scalars().all()
        for profile in profiles:
            current = profile.data or {}
            if sanitize_profile_data(current) != current:
                plan.profile_ids.append(profile.id)

        tasks = (await session.execute(select(TaskHistory))).scalars().all()
        for task in tasks:
            options = task.options
            if isinstance(options, dict) and "profile" in options:
                plan.task_job_ids.append(task.job_id)

    plan.profile_changed_count = len(plan.profile_ids)
    plan.task_changed_count = len(plan.task_job_ids)
    return plan


async def apply_cleanup() -> CleanupReport:
    """单事务执行清洗；已干净的行不产生 UPDATE，重复执行零变更。"""
    report = CleanupReport()
    async with get_session_factory()() as session:
        profiles = (await session.execute(select(BidderProfile))).scalars().all()
        for profile in profiles:
            current = profile.data or {}
            cleaned = sanitize_profile_data(current)
            if cleaned != current:
                profile.data = cleaned
                report.profile_ids.append(profile.id)
                report.profiles_changed += 1

        tasks = (await session.execute(select(TaskHistory))).scalars().all()
        for task in tasks:
            options = task.options
            if isinstance(options, dict) and "profile" in options:
                task.options = strip_task_options(options)
                report.task_job_ids.append(task.job_id)
                report.tasks_changed += 1

        await session.commit()
    logger.info(
        "T-041 清洗完成：profiles_changed=%s tasks_changed=%s",
        report.profiles_changed,
        report.tasks_changed,
    )
    return report