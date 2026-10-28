"""数据库会话与初始化（懒加载，便于测试替换为 SQLite）。

事件循环边界约定：
- FastAPI 主循环用全局 `_engine` / `_session_factory`；
- worker 线程（local 后端、Celery）里的同步写库走 `run_isolated()`，
  每次新建 engine、用完即释放，**不复用主循环的连接**——
  asyncpg 连接绑定在创建它的事件循环上，跨循环复用会导致退出时清理
  卡死（进程假死）并抛出 `coroutine 'Connection._cancel' was never awaited`。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Coroutine
from contextvars import ContextVar
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings
from src.db.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_isolated_factory: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "db_isolated_factory", default=None
)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url, echo=False, pool_pre_ping=True
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    isolated = _isolated_factory.get()
    if isolated is not None:
        return isolated
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def set_session_factory(factory: async_sessionmaker[AsyncSession] | None) -> None:
    """测试用：替换会话工厂（如指向 SQLite 内存库）。"""
    global _session_factory
    _session_factory = factory


async def init_db() -> None:
    """建表（幂等）。第一期仅两张表，无迁移历史，用 create_all 即可。"""
    if not settings.database_url:
        return
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def run_isolated(coro: Coroutine[Any, Any, Any]) -> Any:
    """在独立事件循环与独立 engine 中执行协程（同步写库路径）。

    返回协程结果；数据库未配置时直接关闭协程并返回 None。已有事件循环时
    交给后台线程执行（无法回传结果）。
    """

    def _run() -> Any:
        if not settings.database_url:
            coro.close()
            return None
        try:
            return asyncio.run(_runner())
        except Exception as exc:  # noqa: BLE001 - 持久化失败不阻断主流程
            logger.warning("数据库操作失败（忽略）: %s", exc)
            return None

    async def _runner() -> Any:
        engine = create_async_engine(settings.database_url, echo=False)
        token = _isolated_factory.set(
            async_sessionmaker(engine, expire_on_commit=False)
        )
        try:
            return await coro
        finally:
            _isolated_factory.reset(token)
            await engine.dispose()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run()
    threading.Thread(target=_run, daemon=True).start()
    return None
