"""保留期清理脚本测试（T-016：级联清向量、失败可重试）。"""
import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "cleanup_script", _REPO_ROOT / "scripts" / "cleanup.py"
)
cleanup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cleanup)

_DOC_ID = "0123456789abcdef"
_BID_ID = f"{_DOC_ID}-20260101-000000"


def _layout(tmp_path: Path):
    parsed = tmp_path / "parsed"
    bids = tmp_path / "bids"
    files = tmp_path / "files"
    doc_dir = parsed / _DOC_ID
    doc_dir.mkdir(parents=True)
    (doc_dir / "content.md").write_text("# 内容", encoding="utf-8")
    bid_dir = bids / _BID_ID
    bid_dir.mkdir(parents=True)
    (bid_dir / "bid.md").write_text("# 标书", encoding="utf-8")
    original = files / f"{_DOC_ID}_a.pdf"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"%PDF-1.4")
    return parsed, bids, files, doc_dir, bid_dir, original


def _redirect(monkeypatch, parsed, bids, files):
    from src import pipeline

    monkeypatch.setattr(pipeline, "PARSED_DIR", parsed)
    monkeypatch.setattr(pipeline, "BIDS_DIR", bids)
    monkeypatch.setattr(pipeline, "FILES_DIR", files)


def test_cleanup_cascades_vectors_for_parsed_docs(tmp_path, monkeypatch):
    from src.vectorstore import ingest as ingest_mod

    parsed, bids, files, doc_dir, bid_dir, original = _layout(tmp_path)
    _redirect(monkeypatch, parsed, bids, files)

    vector_calls: list[str] = []
    monkeypatch.setattr(
        ingest_mod,
        "delete_document_vectors",
        lambda doc_id: vector_calls.append(doc_id) or 3,
    )
    monkeypatch.setattr("sys.argv", ["cleanup.py", "--all"])

    assert cleanup.main() == 0
    assert vector_calls == [_DOC_ID]
    assert not doc_dir.exists()
    assert not bid_dir.exists()
    assert not original.exists()


def test_cleanup_dry_run_has_no_side_effects(tmp_path, monkeypatch):
    from src.vectorstore import ingest as ingest_mod

    parsed, bids, files, doc_dir, bid_dir, original = _layout(tmp_path)
    _redirect(monkeypatch, parsed, bids, files)

    vector_calls: list[str] = []
    monkeypatch.setattr(
        ingest_mod,
        "delete_document_vectors",
        lambda doc_id: vector_calls.append(doc_id) or 0,
    )
    monkeypatch.setattr("sys.argv", ["cleanup.py", "--all", "--dry-run"])

    assert cleanup.main() == 0
    assert vector_calls == []
    assert doc_dir.is_dir()
    assert bid_dir.is_dir()
    assert original.is_file()


def test_cleanup_vector_failure_keeps_doc_files_and_nonzero_exit(
    tmp_path, monkeypatch
):
    from src.errors import IngestError
    from src.vectorstore import ingest as ingest_mod

    parsed, bids, files, doc_dir, bid_dir, original = _layout(tmp_path)
    _redirect(monkeypatch, parsed, bids, files)

    def broken(doc_id: str) -> int:
        raise IngestError("chroma unavailable")

    monkeypatch.setattr(ingest_mod, "delete_document_vectors", broken)
    monkeypatch.setattr("sys.argv", ["cleanup.py", "--all"])

    assert cleanup.main() == 1
    # 向量清理失败的文档：解析目录与原件保留，等待下次重试
    assert doc_dir.is_dir()
    assert original.is_file()
    # 标书产物不对应向量，照常删除
    assert not bid_dir.exists()
