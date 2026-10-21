"""数据库会话与初始化（懒加载，便于测试替换为 SQLite）。"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings
from src.db.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url, echo=False, pool_pre_ping=True
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """测试用：替换会话工厂（如指向 SQLite 内存库）。"""
    global _session_factory
    _session_factory = factory


async def init_db() -> None:
    """建表（幂等）。第一期仅两张表，无迁移历史，用 create_all 即可。"""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
