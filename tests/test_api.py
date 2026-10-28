"""接口层测试：状态映射、进度节流、上传校验与端点。"""
import hashlib
import json
import time
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from api import dispatch
from api.jobs import map_state
from api.main import app
from src import tasks
from src.bid_writer.schema import BidReport


@pytest.fixture
def local_backend(monkeypatch, tmp_path):
    """切到 local 后端并把产物目录重定向到临时目录。"""
    from api import library, routes
    from src.config import settings

    monkeypatch.setattr(settings, "task_backend", "local")
    monkeypatch.setattr(settings, "log_dir", str(tmp_path / "logs"))
    # 测试不连真实 PostgreSQL：持久化降级为无操作，避免连接跨事件循环泄漏
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(dispatch, "_backend", None)
    files = tmp_path / "upload" / "files"
    parsed = tmp_path / "upload" / "parsed"
    bids = tmp_path / "upload" / "bids"
    from src.bid_writer import supplement as supplement_module

    monkeypatch.setattr(routes, "FILES_DIR", files)
    monkeypatch.setattr(routes, "_INCOMING", files / ".incoming")
    monkeypatch.setattr(routes, "PARSED_DIR", parsed)
    monkeypatch.setattr(library, "PARSED_DIR", parsed)
    monkeypatch.setattr(library, "BIDS_DIR", bids)
    monkeypatch.setattr(routes, "BIDS_DIR", bids)
    monkeypatch.setattr(supplement_module, "BIDS_DIR", bids)
    return tmp_path


@pytest.fixture
def client(local_backend):
    return TestClient(app)


# ---- 状态映射（A22）----


def test_map_state():
    assert map_state("PENDING") == "pending"
    assert map_state("RECEIVED") == "pending"
    assert map_state("STARTED") == "running"
    assert map_state("PROGRESS") == "running"
    assert map_state("RETRY") == "running"
    assert map_state("SUCCESS") == "succeeded"
    assert map_state("FAILURE") == "failed"
    assert map_state("REVOKED") == "failed"
    assert map_state("未知") == "running"


# ---- 进度节流（A22）----


def test_progress_reporter_throttle(monkeypatch):
    calls: list[list[str]] = []
    reporter = tasks.ProgressReporter(calls.append)
    clock = [0.0]
    monkeypatch.setattr(tasks.time, "monotonic", lambda: clock[0])

    reporter.report("a")  # 0.0 - 0.0 < 0.5，不 flush
    assert calls == []

    clock[0] = 0.6
    reporter.report("b")  # 0.6 - 0.0 >= 0.5，flush
    assert calls == [["a", "b"]]
    assert reporter.snapshot() == ["a", "b"]


def test_progress_reporter_swallows_sink_error(monkeypatch):
    def boom(_messages):
        raise RuntimeError("上报失败")

    reporter = tasks.ProgressReporter(boom)
    clock = [1.0]
    monkeypatch.setattr(tasks.time, "monotonic", lambda: clock[0])
    reporter.report("x")  # 不抛异常，主任务继续
    assert reporter.snapshot() == ["x"]


# ---- 上传校验 ----


def test_parse_rejects_non_pdf(client):
    resp = client.post(
        "/api/parse", files={"file": ("a.txt", b"%PDF-1.4", "text/plain")}
    )
    assert resp.status_code == 400


def test_parse_rejects_empty(client):
    resp = client.post(
        "/api/parse", files={"file": ("a.pdf", b"", "application/pdf")}
    )
    assert resp.status_code == 400


def test_parse_rejects_bad_header(client):
    resp = client.post(
        "/api/parse", files={"file": ("a.pdf", b"not a pdf", "application/pdf")}
    )
    assert resp.status_code == 400


def test_parse_rejects_oversize(client, monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "api_max_upload_mb", 0)
    resp = client.post(
        "/api/parse", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}
    )
    assert resp.status_code == 413


def test_parse_missing_file(client):
    resp = client.post("/api/parse")
    assert resp.status_code == 422


# ---- 提交与轮询 ----


@dataclass
class _FakeDoc:
    doc_id: str = "0123456789abcdef"
    doc_type: str = "tender_file"
    parser: str = "local"
    pages: int = 1
    sections: list = field(default_factory=list)
    source_name: str = "a.pdf"


def test_parse_submit_and_poll(client, monkeypatch):
    monkeypatch.setattr(tasks, "run_parse", lambda *a, **k: _FakeDoc())

    content = b"%PDF-1.4 test"
    resp = client.post(
        "/api/parse", files={"file": ("a.pdf", content, "application/pdf")}
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["kind"] == "parse"
    assert data["doc_id"] == hashlib.sha256(content).hexdigest()[:16]
    assert data["job_url"] == f"/api/jobs/{data['job_id']}"

    job = _poll(client, data["job_id"])
    assert job["status"] == "succeeded"
    assert job["result"]["doc_id"] == "0123456789abcdef"


def test_parse_task_failure(client, monkeypatch):
    def boom(*a, **k):
        raise ValueError("解析失败")

    monkeypatch.setattr(tasks, "run_parse", boom)
    resp = client.post(
        "/api/parse", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}
    )
    assert resp.status_code == 202

    job = _poll(client, resp.json()["job_id"])
    assert job["status"] == "failed"
    assert job["error_type"] == "ValueError"


def _poll(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        resp = client.get(f"/api/jobs/{job_id}")
        data = resp.json()
        if data["status"] in ("succeeded", "failed"):
            return data
        time.sleep(0.01)
    return data


def test_get_job_not_found(client):
    resp = client.get("/api/jobs/0000000000000000")
    assert resp.status_code == 404


# ---- 健康检查 ----


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["task_backend"] == "local"
    assert data["broker_ok"] is None
    # local 后端无独立 worker，健康检查按「就绪」返回，前端据此显示绿点
    assert data["worker_ok"] is True
    assert data["queue_depth"] == 0


# ---- 投标人资料库 ----


def test_profile_create_update_delete(client, monkeypatch):
    from api import routes

    async def fake_save(company_name, data):
        assert company_name == "示例公司"  # 服务端会 strip
        return 7

    async def fake_update(profile_id, company_name, data):
        return profile_id == 7

    async def fake_delete(profile_id):
        return profile_id == 7

    monkeypatch.setattr(routes, "save_profile", fake_save)
    monkeypatch.setattr(routes, "update_profile", fake_update)
    monkeypatch.setattr(routes, "delete_profile", fake_delete)

    resp = client.post(
        "/api/profiles", json={"company_name": " 示例公司 ", "data": {"address": "北京"}}
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == 7
    assert resp.json()["company_name"] == "示例公司"

    assert client.put(
        "/api/profiles/7", json={"company_name": "示例公司", "data": {}}
    ).status_code == 200
    assert client.put(
        "/api/profiles/8", json={"company_name": "示例公司", "data": {}}
    ).status_code == 404

    resp = client.delete("/api/profiles/7")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert client.delete("/api/profiles/8").status_code == 404


def test_profile_empty_company_name(client):
    resp = client.post("/api/profiles", json={"company_name": "  ", "data": {}})
    assert resp.status_code == 422


def test_profile_db_unavailable(client, monkeypatch):
    from api import routes

    async def broken_save(company_name, data):
        return None

    monkeypatch.setattr(routes, "save_profile", broken_save)
    resp = client.post("/api/profiles", json={"company_name": "示例公司", "data": {}})
    assert resp.status_code == 503


# ---- 文档库与预览 ----


def test_documents_empty(client):
    resp = client.get("/api/documents")
    assert resp.status_code == 200
    assert resp.json() == []


def test_markdown_invalid_doc_id(client):
    resp = client.get("/api/documents/xyz/markdown")
    assert resp.status_code == 422


def test_markdown_not_found(client):
    resp = client.get("/api/documents/0123456789abcdef/markdown")
    assert resp.status_code == 404


# ---- 投标编写 ----

def test_bids_requires_extraction(client):
    resp = client.post("/api/bids", json={"doc_id": "0123456789abcdef"})
    assert resp.status_code == 404


def test_bids_invalid_doc_id(client):
    resp = client.post("/api/bids", json={"doc_id": "xyz"})
    assert resp.status_code == 422


def test_bids_submit_and_poll(client, monkeypatch, local_backend):
    parsed = local_backend / "upload" / "parsed" / "0123456789abcdef"
    parsed.mkdir(parents=True)
    (parsed / "extraction.json").write_text("{}", encoding="utf-8")

    def fake_run_bid_writer(doc_id, profile=None, options=None):
        return BidReport(
            doc_id=doc_id,
            project_name="测试项目",
            bidder="测试公司",
            generated_at="2026-10-22T00:00:00+08:00",
            total_sections=1,
            total_words=100,
        )

    monkeypatch.setattr(tasks, "run_bid_writer", fake_run_bid_writer)

    resp = client.post(
        "/api/bids",
        json={"doc_id": "0123456789abcdef", "profile": {"company_name": "测试公司"}},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["kind"] == "bid"
    assert data["doc_id"] == "0123456789abcdef"

    job = _poll(client, data["job_id"])
    assert job["status"] == "succeeded"
    assert job["bid"]["project_name"] == "测试项目"
    assert job["bid"]["bidder"] == "测试公司"


def test_bids_history(client, local_backend):
    bids_dir = local_backend / "upload" / "bids"
    bid_dir = bids_dir / "0123456789abcdef-20261022-120000"
    bid_dir.mkdir(parents=True)
    report = BidReport(
        doc_id="0123456789abcdef",
        project_name="测试项目",
        bidder="测试公司",
        generated_at="2026-10-22T12:00:00+08:00",
        total_sections=1,
        total_words=100,
        output_dir=str(bid_dir),
        markdown_path=str(bid_dir / "bid.md"),
    )
    (bid_dir / "report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False), encoding="utf-8"
    )

    resp = client.get("/api/bids")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["bid_id"] == "0123456789abcdef-20261022-120000"
    assert data[0]["summary"]["project_name"] == "测试项目"


def test_bids_file_download(client, local_backend):
    bids_dir = local_backend / "upload" / "bids"
    bid_dir = bids_dir / "0123456789abcdef-20261022-120000"
    bid_dir.mkdir(parents=True)
    (bid_dir / "bid.md").write_text("# 标题", encoding="utf-8")

    resp = client.get("/api/bids/0123456789abcdef-20261022-120000/files/bid.md")
    assert resp.status_code == 200
    assert resp.text == "# 标题"

    resp = client.get("/api/bids/0123456789abcdef-20261022-120000/files/hack.exe")
    assert resp.status_code == 404

    resp = client.get("/api/bids/xyz/files/bid.md")
    assert resp.status_code == 422


# ---- 补写未覆盖评分因素（P1-1）----

_SUPPLEMENT_BID = "0123456789abcdef-20261022-120000"


def _write_coverage(bids_dir, *, covered: bool) -> None:
    bid_dir = bids_dir / _SUPPLEMENT_BID
    bid_dir.mkdir(parents=True)
    report = {
        "doc_id": "0123456789abcdef",
        "project_name": "示例项目",
        "bidder": "测试公司",
        "evaluation_coverage": [
            {
                "name": "财务状况",
                "category": "商务",
                "score": 5,
                "covered": covered,
                "section_id": "section_1" if covered else None,
            }
        ],
    }
    (bid_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )


def test_supplement_unknown_bid(client):
    resp = client.post(f"/api/bids/{_SUPPLEMENT_BID}/supplement")
    assert resp.status_code == 404


def test_supplement_fully_covered(client, local_backend):
    _write_coverage(local_backend / "upload" / "bids", covered=True)
    resp = client.post(f"/api/bids/{_SUPPLEMENT_BID}/supplement")
    assert resp.status_code == 409
    assert "已覆盖全部评分因素" in resp.json()["detail"]


def test_supplement_submits_job(client, local_backend, monkeypatch):
    _write_coverage(local_backend / "upload" / "bids", covered=False)
    monkeypatch.setattr(dispatch, "submit_supplement", lambda bid_id, doc_id: "job-supp")
    resp = client.post(f"/api/bids/{_SUPPLEMENT_BID}/supplement")

    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == "job-supp"
    assert body["kind"] == "supplement"
    assert body["doc_id"] == "0123456789abcdef"


# ---- 任务取消与重试（P1-4）----

_DOC_ID = "0123456789abcdef"
_OLD_JOB = "0123456789abcdef0123456789abcdef"


def test_cancel_unknown_job(client):
    resp = client.post("/api/jobs/0000000000000000/cancel")
    assert resp.status_code == 404


def test_cancel_finished_job(client, monkeypatch):
    monkeypatch.setattr(tasks, "run_parse", lambda *a, **k: _FakeDoc())
    resp = client.post(
        "/api/parse", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}
    )
    job_id = resp.json()["job_id"]
    _poll(client, job_id)

    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 409
    assert "已结束" in resp.json()["detail"]


def test_cancel_running_job(client, monkeypatch):
    def slow(*a, **k):
        time.sleep(0.3)
        return _FakeDoc()

    monkeypatch.setattr(tasks, "run_parse", slow)
    resp = client.post(
        "/api/parse", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}
    )
    job_id = resp.json()["job_id"]

    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error"] == "任务已取消"

    # 后台线程跑完后不得把状态覆盖回 succeeded
    time.sleep(0.5)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"


def _failed_task(**overrides) -> dict:
    task = {
        "job_id": _OLD_JOB,
        "kind": "parse",
        "doc_id": _DOC_ID,
        "source_name": "a.pdf",
        "status": "failed",
        "options": {"parser": "local", "force": False, "extract": False, "ingest": False},
        "summary": None,
        "error": "解析失败",
        "created_at": None,
        "finished_at": None,
    }
    task.update(overrides)
    return task


def test_retry_unknown_task(client, monkeypatch):
    from api import routes

    async def missing(job_id):
        return None

    monkeypatch.setattr(routes, "get_task", missing)
    assert client.post(f"/api/jobs/{_OLD_JOB}/retry").status_code == 404


def test_retry_requires_failed_status(client, monkeypatch):
    from api import routes

    async def finished(job_id):
        return _failed_task(status="succeeded")

    monkeypatch.setattr(routes, "get_task", finished)
    resp = client.post(f"/api/jobs/{_OLD_JOB}/retry")
    assert resp.status_code == 409
    assert "仅失败任务可重试" in resp.json()["detail"]


def test_retry_parse_resubmits(client, monkeypatch, local_backend):
    from api import routes

    async def failed(job_id):
        return _failed_task()

    monkeypatch.setattr(routes, "get_task", failed)
    monkeypatch.setattr(tasks, "run_parse", lambda *a, **k: _FakeDoc())
    files = local_backend / "upload" / "files"
    files.mkdir(parents=True)
    (files / f"{_DOC_ID}_a.pdf").write_bytes(b"%PDF-1.4")

    resp = client.post(f"/api/jobs/{_OLD_JOB}/retry")
    assert resp.status_code == 202
    body = resp.json()
    assert body["kind"] == "parse"
    assert body["job_id"] != _OLD_JOB
    assert _poll(client, body["job_id"])["status"] == "succeeded"


def test_retry_parse_source_missing(client, monkeypatch):
    from api import routes

    async def failed(job_id):
        return _failed_task()

    monkeypatch.setattr(routes, "get_task", failed)
    resp = client.post(f"/api/jobs/{_OLD_JOB}/retry")
    assert resp.status_code == 404
    assert "重新上传" in resp.json()["detail"]


# ---- 删除文档与产物（P1-7）----


def test_delete_document(client, local_backend):
    parsed = local_backend / "upload" / "parsed" / _DOC_ID
    parsed.mkdir(parents=True)
    (parsed / "content.md").write_text("# 内容", encoding="utf-8")
    files = local_backend / "upload" / "files"
    files.mkdir(parents=True)
    original = files / f"{_DOC_ID}_a.pdf"
    original.write_bytes(b"%PDF-1.4")

    resp = client.delete(f"/api/documents/{_DOC_ID}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert not parsed.exists()
    assert not original.exists()

    assert client.delete(f"/api/documents/{_DOC_ID}").status_code == 404
    assert client.delete("/api/documents/xyz").status_code == 422


def test_delete_bid(client, local_backend):
    bid_dir = local_backend / "upload" / "bids" / _SUPPLEMENT_BID
    bid_dir.mkdir(parents=True)
    (bid_dir / "bid.md").write_text("# 标题", encoding="utf-8")

    resp = client.delete(f"/api/bids/{_SUPPLEMENT_BID}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert not bid_dir.exists()

    assert client.delete(f"/api/bids/{_SUPPLEMENT_BID}").status_code == 404
    assert client.delete("/api/bids/xyz").status_code == 422


# ---- 章节级编辑、重写与锁定（P1-2）----

_SECTION_BID = "0123456789abcdef-20261022-120000"


@pytest.fixture
def section_artifact(local_backend, monkeypatch):
    """构造一份旧版产物（无 sections/），并隔离抽取结果与 Word 导出。"""
    from src.bid_writer import sections as sections_module
    from src.extraction.schema import TenderDocument

    bids = local_backend / "upload" / "bids"
    bid_dir = bids / _SECTION_BID
    bid_dir.mkdir(parents=True)
    outline = [
        {
            "id": "section_1",
            "title": "施工组织设计",
            "order": 1,
            "strategy": "generate",
            "target_words": 100,
            "scoring_factors": [],
            "requirements": "",
            "retrieval_query": "施工组织设计",
        }
    ]
    report = {
        "doc_id": _DOC_ID,
        "project_name": "示例项目",
        "bidder": "测试公司",
        "generated_at": "2026-10-22T12:00:00+08:00",
        "total_sections": 1,
        "total_words": 6,
        "sections": [{"id": "section_1", "score_before": 80, "score_after": 85}],
        "format_coverage": [],
        "evaluation_coverage": [],
        "placeholders": [],
        "missing_profile_fields": [],
        "warnings": [],
    }
    markdown = (
        "# 示例项目\n\n**投标人：测试公司**\n**日期：2026年10月22日**\n\n---\n\n"
        "## 目录\n1. 施工组织设计\n\n"
        "## 施工组织设计\n\n原始正文\n"
    )
    (bid_dir / "bid.md").write_text(markdown, encoding="utf-8")
    (bid_dir / "outline.json").write_text(
        json.dumps(outline, ensure_ascii=False), encoding="utf-8"
    )
    (bid_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )

    monkeypatch.setattr(sections_module, "BIDS_DIR", bids)
    monkeypatch.setattr(
        sections_module,
        "load_extraction",
        lambda doc_id: TenderDocument(
            doc_id=_DOC_ID,
            doc_type="tender_file",
            project_name="示例项目",
            evaluation_factors=[],
            bid_format_items=[],
        ),
    )
    monkeypatch.setattr(
        sections_module, "markdown_to_docx", lambda md, path: path.write_bytes(b"docx")
    )
    return bid_dir


def test_list_bid_sections(client, section_artifact):
    resp = client.get(f"/api/bids/{_SECTION_BID}/sections")
    assert resp.status_code == 200
    data = resp.json()
    assert [item["id"] for item in data] == ["section_1"]
    assert data[0]["locked"] is False
    assert data[0]["word_count"] > 0


def test_get_bid_section_detail(client, section_artifact):
    resp = client.get(f"/api/bids/{_SECTION_BID}/sections/section_1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "施工组织设计"
    assert "原始正文" in body["markdown"]

    assert client.get(f"/api/bids/{_SECTION_BID}/sections/nope").status_code == 404


def test_save_bid_section_markdown(client, section_artifact):
    resp = client.put(
        f"/api/bids/{_SECTION_BID}/sections/section_1",
        json={"markdown": "人工改写后的正文"},
    )
    assert resp.status_code == 200
    assert resp.json()["total_words"] > 0
    assert "人工改写后的正文" in (section_artifact / "bid.md").read_text(encoding="utf-8")

    # 空请求体（markdown 与 locked 都未提供）应被拒绝
    assert (
        client.put(f"/api/bids/{_SECTION_BID}/sections/section_1", json={}).status_code
        == 422
    )


def test_lock_bid_section(client, section_artifact):
    resp = client.put(
        f"/api/bids/{_SECTION_BID}/sections/section_1", json={"locked": True}
    )
    assert resp.status_code == 200
    assert client.get(f"/api/bids/{_SECTION_BID}/sections").json()[0]["locked"] is True


def test_download_bid_section(client, section_artifact):
    resp = client.get(f"/api/bids/{_SECTION_BID}/sections/section_1/file")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "原始正文" in resp.text


def test_rewrite_bid_sections(client, section_artifact, monkeypatch):
    monkeypatch.setattr(
        dispatch, "submit_section_rewrite", lambda bid_id, doc_id, ids: "job-rewrite"
    )
    resp = client.post(f"/api/bids/{_SECTION_BID}/sections/rewrite", json={"section_ids": ["section_1"]})
    assert resp.status_code == 202
    body = resp.json()
    assert body["kind"] == "rewrite"
    assert body["job_id"] == "job-rewrite"

    assert (
        client.post(
            "/api/bids/0123456789abcdef-20261022-130000/sections/rewrite", json={}
        ).status_code
        == 404
    )


# ---- 可观测性：任务轨迹与指标（P1-9）----


def test_job_trace_locates_failure(client, monkeypatch):
    def boom(*a, **k):
        raise ValueError("解析失败")

    monkeypatch.setattr(tasks, "run_parse", boom)
    resp = client.post(
        "/api/parse", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}
    )
    job_id = resp.json()["job_id"]
    _poll(client, job_id)

    # 任务视图直接给出阶段，失败原因仍保留在 error 字段
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["stage"] == "解析文档"
    assert job["error"] == "解析失败"

    trace = client.get(f"/api/jobs/{job_id}/trace")
    assert trace.status_code == 200
    data = trace.json()
    assert data["status"] == "failed"
    assert data["stage"] == "解析文档"
    assert data["error_type"] == "ValueError"
    assert data["duration_ms"] is not None
    assert [s["name"] for s in data["stages"]] == ["解析文档"]


def test_job_trace_not_found(client):
    assert client.get("/api/jobs/0000000000000000/trace").status_code == 404


def test_metrics_endpoint(client):
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_backend"] == "local"
    assert data["queue_depth"] == 0
    assert data["jobs_total"] == 0
    assert data["recent_failures"] == []


def test_metrics_counts_successful_parse(client, monkeypatch):
    monkeypatch.setattr(tasks, "run_parse", lambda *a, **k: _FakeDoc())
    resp = client.post(
        "/api/parse", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}
    )
    _poll(client, resp.json()["job_id"])

    data = client.get("/api/metrics").json()
    assert data["by_status"] == {"succeeded": 1}
    assert data["by_kind"] == {"parse": 1}
    assert data["avg_duration_ms"]["parse"] >= 0
