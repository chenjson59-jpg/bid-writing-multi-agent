"""持久化层测试：任务历史与投标人资料（SQLite 文件库）。"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db import Base, repository
from src.db.session import set_session_factory


@pytest_asyncio.fixture
async def db(tmp_path):
    """把会话工厂切到 SQLite 文件库，测试后还原。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_session_factory(async_sessionmaker(engine, expire_on_commit=False))
    yield
    await engine.dispose()
    set_session_factory(None)


@pytest.mark.asyncio
async def test_upsert_and_finish_task(db):
    await repository.upsert_task(
        job_id="job-1",
        kind="parse",
        doc_id="0123456789abcdef",
        source_name="招标文件.pdf",
        status="pending",
        options={"extract": True},
    )
    tasks = await repository.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"
    assert tasks[0]["doc_id"] == "0123456789abcdef"

    await repository.finish_task("job-1", "succeeded", summary={"pages": 10})
    tasks = await repository.list_tasks()
    assert tasks[0]["status"] == "succeeded"
    assert tasks[0]["summary"] == {"pages": 10}
    assert tasks[0]["finished_at"] is not None


@pytest.mark.asyncio
async def test_upsert_is_idempotent(db):
    await repository.upsert_task(
        job_id="j", kind="bid", doc_id=None, source_name=None, status="pending"
    )
    await repository.upsert_task(
        job_id="j", kind="bid", doc_id=None, source_name=None, status="running"
    )
    tasks = await repository.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["status"] == "running"


@pytest.mark.asyncio
async def test_save_and_list_profile(db):
    await repository.save_profile(
        "示例工程有限公司", {"legal_representative": "张三"}
    )
    profiles = await repository.list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["company_name"] == "示例工程有限公司"
    assert profiles[0]["data"]["legal_representative"] == "张三"


@pytest.mark.asyncio
async def test_save_profile_overwrites_same_company(db):
    """同一公司名重复录入只保留一条，避免资料库被编写任务刷成重复记录。"""
    await repository.save_profile("示例公司", {"address": "旧地址"})
    await repository.save_profile("示例公司", {"address": "新地址"})
    profiles = await repository.list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["data"]["address"] == "新地址"


@pytest.mark.asyncio
async def test_update_and_delete_profile(db):
    profile_id = await repository.save_profile("示例公司", {"address": "北京"})
    assert profile_id is not None

    assert await repository.update_profile(profile_id, "示例公司", {"address": "上海"}) is True
    profiles = await repository.list_profiles()
    assert profiles[0]["data"]["address"] == "上海"

    assert await repository.delete_profile(profile_id) is True
    assert await repository.list_profiles() == []
    assert await repository.delete_profile(profile_id) is False
