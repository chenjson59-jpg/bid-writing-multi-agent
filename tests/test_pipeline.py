"""流水线编排（A22 部分）测试。"""
import hashlib
import json

import pymupdf
import pytest

from src import pipeline
from src.parsing.models import Section


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """把 upload 目录重定向到临时目录，隔离真实产物。"""
    monkeypatch.setattr(pipeline, "FILES_DIR", tmp_path / "upload" / "files")
    monkeypatch.setattr(pipeline, "PARSED_DIR", tmp_path / "upload" / "parsed")
    monkeypatch.setattr(pipeline, "IMAGES_DIR", tmp_path / "upload" / "images")
    return tmp_path


def _make_pdf(path, with_toc: bool = True):
    doc = pymupdf.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), f"第{i + 1}页正文内容")
    if with_toc:
        doc.set_toc([[1, "第一章 投标邀请", 1], [1, "第二章 评标办法", 2]])
    doc.save(path)
    doc.close()


def test_safe_filename_sanitizes():
    assert pipeline._safe_filename("a/b?.pdf") == "b_.pdf"
    assert pipeline._safe_filename("normal.pdf") == "normal.pdf"
    assert pipeline._safe_filename("") == "document"


def test_sha256_stable(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("hello", encoding="utf-8")
    assert pipeline._sha256(path) == hashlib.sha256(b"hello").hexdigest()


def test_restore_section_texts():
    markdown = (
        "## 第一章 投标邀请\n\n正文一\n\n## 第二章 评标办法\n\n正文二"
    )
    sections = [
        Section(index=0, level=1, number="第一章", title="第一章 投标邀请",
                path=[], page_start=1, page_end=1, text=""),
        Section(index=1, level=1, number="第二章", title="第二章 评标办法",
                path=[], page_start=2, page_end=2, text=""),
    ]

    pipeline._restore_section_texts(markdown, sections)

    assert sections[0].text == "正文一"
    assert sections[1].text == "正文二"


# ---- run_parse ----


def test_run_parse_local(workspace):
    pdf = workspace / "test.pdf"
    _make_pdf(pdf)

    doc = pipeline.run_parse(pdf, parser="local")

    assert doc.doc_id
    assert len(doc.doc_id) == 16
    assert doc.parser == "local"
    assert doc.pages == 2
    assert len(doc.sections) == 2
    assert doc.parser_meta["structure_source"] == "bookmark"
    # 产物落盘
    assert doc.markdown_path and doc.markdown
    assert (workspace / "upload" / "parsed" / doc.doc_id / "manifest.json").exists()


def test_run_parse_reuses_cache(workspace):
    pdf = workspace / "test.pdf"
    _make_pdf(pdf)

    first = pipeline.run_parse(pdf, parser="local")
    # 第二次 run_parse 应复用已存在产物
    second = pipeline.run_parse(pdf, parser="local")

    assert first.doc_id == second.doc_id
    assert second.sections
    # 复用后仍恢复章节正文
    assert second.sections[0].text == first.sections[0].text


def test_run_parse_unknown_parser_raises(workspace):
    pdf = workspace / "test.pdf"
    _make_pdf(pdf)
    with pytest.raises(pipeline.ConfigError):
        pipeline.run_parse(pdf, parser="bogus")


def test_run_parse_missing_file_raises(workspace):
    with pytest.raises(pipeline.ParseError):
        pipeline.run_parse(workspace / "nope.pdf", parser="local")


# ---- run_extract ----


class _FakeCaller:
    def __init__(self):
        self.calls = 0

    def generate(self, messages, json_mode):
        self.calls += 1
        return {
            "content": json.dumps(
                {
                    "basic": {"project_name": "测试项目"},
                    "evaluation": {"factors": [], "preliminary_review": [], "score_table": []},
                    "qualification": {"items": []},
                    "bid_format": {"items": []},
                    "contract": {"highlights": []},
                    "tech": {"requirements": []},
                },
                ensure_ascii=False,
            ),
            "finish_reason": "stop",
            "json_mode_used": True,
        }


def test_run_extract_from_pdf(workspace):
    pdf = workspace / "test.pdf"
    _make_pdf(pdf)
    caller = _FakeCaller()

    result = pipeline.run_extract(pdf, parser="local", caller=caller)

    assert result.project_name == "测试项目"
    assert result.doc_id
    assert result.extraction_meta["calls"] == caller.calls


def test_run_extract_from_doc_id(workspace):
    pdf = workspace / "test.pdf"
    _make_pdf(pdf)
    parsed = pipeline.run_parse(pdf, parser="local")

    result = pipeline.run_extract(parsed.doc_id, caller=_FakeCaller())

    assert result.doc_id == parsed.doc_id


def test_run_extract_missing_doc_id_raises(workspace):
    with pytest.raises(pipeline.ParseError):
        pipeline.run_extract("nonexistent_id", caller=_FakeCaller())


# ---- run_ingest ----


def test_run_ingest_returns_summary(workspace, monkeypatch):
    pdf = workspace / "test.pdf"
    _make_pdf(pdf)

    captured = {}

    def fake_ingest(doc, project_name=""):
        captured["project_name"] = project_name
        captured["doc_id"] = doc.doc_id
        return pipeline.IngestSummary(
            collection="tender_document",
            added=2,
            updated=0,
            deleted=0,
            unchanged=0,
            total=2,
        )

    monkeypatch.setattr(pipeline, "ingest_document", fake_ingest)

    summary = pipeline.run_ingest(pdf, parser="local", caller=_FakeCaller())

    assert summary.added == 2
    assert captured["doc_id"]
    assert captured["project_name"] == "测试项目"