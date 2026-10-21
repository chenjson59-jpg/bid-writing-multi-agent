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
    monkeypatch.setattr(dispatch, "_backend", None)
    files = tmp_path / "upload" / "files"
    parsed = tmp_path / "upload" / "parsed"
    bids = tmp_path / "upload" / "bids"
    monkeypatch.setattr(routes, "FILES_DIR", files)
    monkeypatch.setattr(routes, "_INCOMING", files / ".incoming")
    monkeypatch.setattr(routes, "PARSED_DIR", parsed)
    monkeypatch.setattr(library, "PARSED_DIR", parsed)
    monkeypatch.setattr(library, "BIDS_DIR", bids)
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
