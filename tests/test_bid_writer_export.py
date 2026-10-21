"""Word 导出测试（A21）：标题、段落、粗体、列表、表格、分页。"""
from __future__ import annotations

from docx import Document

from src.bid_writer.docx_export import markdown_to_docx


def _texts(path) -> list[str]:
    return [p.text for p in Document(path).paragraphs]


def test_heading_and_paragraph(tmp_path):
    out = tmp_path / "bid.docx"
    markdown_to_docx("# 标题\n\n正文内容", out)
    texts = _texts(out)
    assert "标题" in texts
    assert "正文内容" in texts


def test_table_removes_separator_row(tmp_path):
    out = tmp_path / "bid.docx"
    markdown_to_docx("| 列1 | 列2 |\n| --- | --- |\n| 甲 | 乙 |", out)
    table = Document(out).tables[0]
    assert len(table.rows) == 2
    assert table.rows[0].cells[0].text == "列1"
    assert table.rows[1].cells[0].text == "甲"


def test_bold_run(tmp_path):
    out = tmp_path / "bid.docx"
    markdown_to_docx("这是**重点**内容", out)
    runs = Document(out).paragraphs[0].runs
    assert any(run.bold for run in runs)


def test_lists(tmp_path):
    out = tmp_path / "bid.docx"
    markdown_to_docx("- 项目一\n- 项目二\n1. 第一\n2. 第二", out)
    texts = _texts(out)
    assert "项目一" in texts
    assert "第一" in texts


def test_page_break(tmp_path):
    out = tmp_path / "bid.docx"
    markdown_to_docx("上\n---\n下", out)
    texts = _texts(out)
    assert "上" in texts
    assert "下" in texts
