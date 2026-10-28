"""任务可观测性（P1-9）：阶段计时、失败定位与指标聚合。"""
from __future__ import annotations

import pytest

from src import observability
from src.config import settings


@pytest.fixture
def log_env(tmp_path, monkeypatch):
    """把任务日志目录重定向到临时目录，避免污染仓库 logs/。"""
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    return tmp_path


def test_trace_records_stage_durations(log_env):
    with observability.job_scope("job1", "bid") as trace:
        observability.stage("规划章节")
        trace.stage("章节写作")
        observability.stage("章节写作")  # 同名阶段重复上报只记一次
        trace.stage("合并与导出")
        trace.finish("succeeded")

    summary = observability.job_trace("job1")
    assert summary is not None
    assert summary["status"] == "succeeded"
    assert summary["kind"] == "bid"
    assert [s["name"] for s in summary["stages"]] == ["规划章节", "章节写作", "合并与导出"]
    assert all(s["duration_ms"] >= 0 for s in summary["stages"])
    assert summary["duration_ms"] is not None
    assert (log_env / "jobs" / "job1.jsonl").is_file()


def test_failed_trace_exposes_stage_and_error(log_env):
    with observability.job_scope("job2", "parse") as trace:
        observability.stage("解析文档")
        observability.stage("结构化抽取")
        trace.finish("failed", ValueError("抽取结果为空"))

    summary = observability.job_trace("job2")
    assert summary["status"] == "failed"
    # 失败阶段即当时所处阶段
    assert summary["stage"] == "结构化抽取"
    assert summary["error_type"] == "ValueError"
    assert summary["error"] == "抽取结果为空"


def test_scope_records_uncaught_exception(log_env):
    with pytest.raises(RuntimeError), observability.job_scope("job3", "bid"):
        observability.stage("章节写作")
        raise RuntimeError("模型超时")

    summary = observability.job_trace("job3")
    assert summary["status"] == "failed"
    assert summary["stage"] == "章节写作"
    assert summary["error"] == "模型超时"


def test_latest_stage_tracks_running_job(log_env):
    with observability.job_scope("job4", "parse"):
        observability.stage("解析文档")
        observability.stage("建立检索索引")
        assert observability.latest_stage("job4") == "建立检索索引"


def test_metrics_aggregates_status_and_failures(log_env):
    with observability.job_scope("a1", "bid") as trace:
        observability.stage("规划章节")
        trace.finish("succeeded")
    with observability.job_scope("a2", "parse") as trace:
        observability.stage("解析文档")
        trace.finish("failed", ValueError("不是 PDF"))

    data = observability.metrics()
    assert data["jobs_total"] == 2
    assert data["by_status"] == {"succeeded": 1, "failed": 1}
    assert data["by_kind"] == {"bid": 1, "parse": 1}
    assert "bid" in data["avg_duration_ms"]
    assert "parse" in data["avg_duration_ms"]
    failures = data["recent_failures"]
    assert len(failures) == 1
    assert failures[0]["job_id"] == "a2"
    assert failures[0]["stage"] == "解析文档"
    assert failures[0]["error_type"] == "ValueError"


def test_trace_missing_returns_none(log_env):
    assert observability.job_trace("not-exist") is None
    assert observability.latest_stage("not-exist") is None
