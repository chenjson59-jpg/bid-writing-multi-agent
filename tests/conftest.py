"""共享夹具：local 后端 + 产物目录重定向到临时目录，测试不碰真实数据。"""
import pytest
from fastapi.testclient import TestClient

from api import dispatch
from api.main import app
from src.config import settings


@pytest.fixture
def local_backend(monkeypatch, tmp_path):
    """切到 local 后端，并把上传/解析/产物目录全部指向 tmp_path。"""
    from api import library, routes
    from src import pipeline
    from src.bid_writer import supplement as supplement_module

    monkeypatch.setattr(settings, "task_backend", "local")
    monkeypatch.setattr(settings, "log_dir", str(tmp_path / "logs"))
    # 测试不连真实 PostgreSQL：持久化降级为无操作，避免连接跨事件循环泄漏
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(dispatch, "_backend", None)

    files = tmp_path / "upload" / "files"
    parsed = tmp_path / "upload" / "parsed"
    bids = tmp_path / "upload" / "bids"
    images = tmp_path / "upload" / "images"

    # 接口层按值导入了目录常量，需逐个重定向
    monkeypatch.setattr(routes, "FILES_DIR", files)
    monkeypatch.setattr(routes, "_INCOMING", files / ".incoming")
    monkeypatch.setattr(routes, "PARSED_DIR", parsed)
    monkeypatch.setattr(routes, "BIDS_DIR", bids)
    monkeypatch.setattr(library, "PARSED_DIR", parsed)
    monkeypatch.setattr(library, "BIDS_DIR", bids)
    monkeypatch.setattr(supplement_module, "BIDS_DIR", bids)

    # 流水线内部按模块全局变量取目录（端到端用例走真实解析与写作）
    monkeypatch.setattr(pipeline, "FILES_DIR", files)
    monkeypatch.setattr(pipeline, "PARSED_DIR", parsed)
    monkeypatch.setattr(pipeline, "IMAGES_DIR", images)
    monkeypatch.setattr(pipeline, "BIDS_DIR", bids)
    return tmp_path


@pytest.fixture
def client(local_backend):
    return TestClient(app)
