"""T-041 库内历史高敏数据物理清洗的离线测试（方案 A）。

覆盖：
- src/db/profile_sanitize.py 纯函数（白名单裁剪、options.profile 剥离、API 视图收敛）；
- SQLite 临时库上的只读计划与幂等清洗（不连真实 PostgreSQL）；
- GET /api/tasks 响应不再回显 options.profile（路由层剥离，仓储 get_task 不受影响）。

约定：无真实网络/LLM；数据库用 tmp_path 下 SQLite 文件库，夹具模式与 test_db.py 一致。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api import routes
from src.db import Base, repository
from src.db.profile_sanitize import (
    PROFILE_WHITELIST_KEYS,
    apply_cleanup,
    collect_cleanup_plan,
    sanitize_profile_data,
    sanitize_task_for_api,
    strip_task_options,
)
from src.db.session import set_session_factory

WHITELIST = (
    "company_name",
    "legal_representative",
    "registered_capital",
    "established_date",
    "address",
    "qualifications",
    "certifications",
)
REMOVED_KEYS = ("similar_projects", "personnel", "financials", "contact", "notes", "unknown_field")


def legacy_profile() -> dict:
    return {
        "company_name": "旧资料公司",
        "legal_representative": "李四",
        "registered_capital": "500 万元",
        "established_date": "2008-05-05",
        "address": "旧地址 8 号",
        "qualifications": ["资质甲"],
        "certifications": ["证书乙"],
        "similar_projects": [{"project_name": "历史项目 A", "contract_amount": "9999 万元"}],
        "personnel": [{"name": "王五", "position": "项目经理"}],
        "financials": {"report_period": "2024 年度", "net_profit": "100 万元"},
        "contact": {"phone": "13800000000", "email": "demo@example.com"},
        "notes": ["历史备注"],
    }


# ---- 纯函数 ----


def test_whitelist_keys_match_schema_single_source_of_truth():
    from src.bid_writer.schema import PROFILE_FIELD_LABELS

    assert PROFILE_WHITELIST_KEYS == tuple(PROFILE_FIELD_LABELS.keys())
    assert PROFILE_WHITELIST_KEYS == WHITELIST


def test_sanitize_profile_data_keeps_only_whitelist_and_values():
    raw = legacy_profile()
    cleaned = sanitize_profile_data(raw)
    assert sorted(cleaned.keys()) == sorted(WHITELIST)
    for key in REMOVED_KEYS:
        assert key not in cleaned
    # 白名单值原样保留，结构不被改写
    assert cleaned["qualifications"] == ["资质甲"]
    assert cleaned["company_name"] == "旧资料公司"
    # 入参不被原地修改
    assert "contact" in raw


def test_sanitize_profile_data_edge_inputs():
    assert sanitize_profile_data({}) == {}
    assert sanitize_profile_data(None) == {}  # type: ignore[arg-type]
    assert sanitize_profile_data("not-a-dict") == {}  # type: ignore[arg-type]
    # 只有移出键时清洗结果为空 dict
    assert sanitize_profile_data({"contact": {"phone": "x"}, "notes": []}) == {}


def test_strip_task_options_removes_profile_and_keeps_other_options():
    options = {
        "max_revisions": 1,
        "max_sections": 0,
        "min_score": 0.7,
        "export_word": True,
        "profile": legacy_profile(),
    }
    stripped = strip_task_options(options)
    assert "profile" not in stripped
    assert stripped["max_revisions"] == 1
    assert stripped["export_word"] is True
    # 入参不被原地修改
    assert "profile" in options
    # 无 profile 时返回内容相等的新 dict，不报错
    again = strip_task_options(stripped)
    assert again == stripped
    assert again is not stripped


def test_sanitize_task_for_api_defensive():
    item = {
        "job_id": "job-1",
        "kind": "bid",
        "options": {"max_revisions": 1, "profile": legacy_profile()},
    }
    view = sanitize_task_for_api(item)
    assert "profile" not in view["options"]
    assert view["job_id"] == "job-1"
    # 缺 options / options 为 None 不抛异常
    assert sanitize_task_for_api({"job_id": "job-2"}) == {"job_id": "job-2"}
    broken = sanitize_task_for_api({"job_id": "job-3", "options": None})
    assert broken["options"] is None


# ---- SQLite 临时库上的异步清洗 ----


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't041.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_session_factory(async_sessionmaker(engine, expire_on_commit=False))
    yield
    await engine.dispose()
    set_session_factory(None)


async def _seed() -> None:
    await repository.save_profile("旧资料公司", legacy_profile())
    await repository.upsert_task(
        job_id="job-bid-legacy",
        kind="bid",
        doc_id="doc-1",
        source_name="demo.pdf",
        status="succeeded",
        options={"max_revisions": 1, "profile": legacy_profile()},
    )
    await repository.upsert_task(
        job_id="job-bid-clean",
        kind="bid",
        doc_id="doc-1",
        source_name="demo.pdf",
        status="succeeded",
        options={"max_revisions": 0},
    )
    await repository.upsert_task(
        job_id="job-parse-1",
        kind="parse",
        doc_id="doc-1",
        source_name="demo.pdf",
        status="succeeded",
        options={"parser": "auto"},
    )


@pytest.mark.asyncio
async def test_collect_plan_is_read_only(db):
    await _seed()
    plan = await collect_cleanup_plan()
    assert plan.profile_ids == [1]
    assert plan.task_job_ids == ["job-bid-legacy"]
    assert plan.profile_changed_count == 1
    assert plan.task_changed_count == 1
    # 计划扫描不得改库
    profiles = await repository.list_profiles()
    assert "contact" in profiles[0]["data"]
    tasks = {t["job_id"]: t for t in await repository.list_tasks()}
    assert "profile" in tasks["job-bid-legacy"]["options"]


@pytest.mark.asyncio
async def test_apply_cleanup_mutates_both_tables_and_preserves_other_options(db):
    await _seed()
    report = await apply_cleanup()
    assert report.profiles_changed == 1
    assert report.tasks_changed == 1
    assert report.profile_ids == [1]
    assert report.task_job_ids == ["job-bid-legacy"]

    profiles = await repository.list_profiles()
    assert sorted(profiles[0]["data"].keys()) == sorted(WHITELIST)
    assert "contact" not in profiles[0]["data"]

    tasks = {t["job_id"]: t for t in await repository.list_tasks()}
    assert "profile" not in tasks["job-bid-legacy"]["options"]
    assert tasks["job-bid-legacy"]["options"]["max_revisions"] == 1
    # 本来就干净的行不动
    assert tasks["job-bid-clean"]["options"] == {"max_revisions": 0}
    assert tasks["job-parse-1"]["options"] == {"parser": "auto"}


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_second_run_zero_changes(db):
    await _seed()
    first = await apply_cleanup()
    assert first.profiles_changed == 1 and first.tasks_changed == 1
    second = await apply_cleanup()
    assert second.profiles_changed == 0
    assert second.tasks_changed == 0
    plan = await collect_cleanup_plan()
    assert plan.profile_changed_count == 0
    assert plan.task_changed_count == 0


# ---- 接口层：/tasks 不回显 profile ----


def test_tasks_endpoint_strips_profile(client, monkeypatch):
    async def fake_list_tasks(limit: int = 100, offset: int = 0):
        return [
            {
                "job_id": "job-bid-legacy",
                "kind": "bid",
                "doc_id": "doc-1",
                "source_name": "demo.pdf",
                "status": "succeeded",
                "options": {"max_revisions": 1, "profile": legacy_profile()},
                "summary": None,
                "error": None,
                "created_at": None,
                "finished_at": None,
            },
            {
                "job_id": "job-parse-1",
                "kind": "parse",
                "doc_id": "doc-1",
                "source_name": "demo.pdf",
                "status": "succeeded",
                "options": {"parser": "auto"},
                "summary": None,
                "error": None,
                "created_at": None,
                "finished_at": None,
            },
        ]

    monkeypatch.setattr(routes, "list_tasks", fake_list_tasks)
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    items = {item["job_id"]: item for item in resp.json()}
    assert "profile" not in items["job-bid-legacy"]["options"]
    assert items["job-bid-legacy"]["options"]["max_revisions"] == 1
    assert items["job-parse-1"]["options"] == {"parser": "auto"}
    # 全响应文本不得带出高敏针
    body = resp.text
    assert "13800000000" not in body
    assert "demo@example.com" not in body