"""本地解析（A7）测试。"""
import pymupdf

from src.parsing.local_parser import _clean_page_text, parse_local


def test_clean_page_text_drops_fingerprint_and_page_number():
    fingerprint = "a" * 20 + "-" + "1" * 10
    text = f"{fingerprint}\n正文内容\n12\n结尾"

    cleaned = _clean_page_text(text)

    assert "正文内容" in cleaned
    assert "结尾" in cleaned
    assert fingerprint not in cleaned
    assert "12" not in cleaned.splitlines()


def test_clean_page_text_collapses_blank_lines():
    cleaned = _clean_page_text("第一行\n\n\n第二行\n")
    assert cleaned == "第一行\n第二行"


def test_parse_local_with_bookmarks():
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"第{i + 1}页内容")
    doc.set_toc([[1, "第一章", 1], [1, "第二章", 2]])

    stage = parse_local(doc)

    assert stage.page_count == 3
    assert len(stage.entries) == 2
    assert stage.entries[0].title == "第一章"
    assert 1 in stage.pages
    assert stage.parser_meta["structure_source"] == "bookmark"


def test_parse_local_without_bookmarks():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "无书签正文")

    stage = parse_local(doc)

    assert stage.entries == []
    assert stage.parser_meta["structure_source"] == "page_order"
    assert any("无书签" in w for w in stage.warnings)


def test_parse_local_extracts_no_images_when_none(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "无图片正文")

    stage = parse_local(doc, images_dir=tmp_path)

    assert stage.images == []
