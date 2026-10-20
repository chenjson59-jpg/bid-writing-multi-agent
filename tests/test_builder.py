"""解析产物组装与落盘（A6、A23）测试。"""
import json

from src.parsing.builder import (
    assemble,
    detect_doc_type,
    merge_mineru,
    read_content_list,
)
from src.parsing.models import MineruOutput, ParseStage
from src.parsing.toc import read_toc


def test_detect_doc_type_tender_file():
    markdown = "评标办法\n投标人须知\n采购需求"
    assert detect_doc_type(markdown, toc_count=5) == "tender_file"


def test_detect_doc_type_announcement():
    markdown = "招标公告\n项目名称"
    assert detect_doc_type(markdown, toc_count=5) == "announcement"


def test_detect_doc_type_unknown_then_tender_by_toc():
    markdown = "采购需求说明"
    assert detect_doc_type(markdown, toc_count=5) == "unknown"
    assert detect_doc_type(markdown, toc_count=30) == "tender_file"


def test_read_content_list_page_offset_and_types(tmp_path):
    data = [
        {"type": "text", "text": "第一章 正文", "page_idx": 0},
        {
            "type": "table",
            "table_caption": "评分表",
            "table_body": [["因素", "分值"], ["价格", "30"]],
            "page_idx": 1,
        },
        {
            "type": "image",
            "img_path": "images/a.png",
            "img_caption": "图示",
            "page_idx": 1,
        },
    ]
    path = tmp_path / "content_list.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    pages = read_content_list(path, page_offset=10)

    assert pages[11] == "第一章 正文"
    assert pages[12] == "评分表\n因素 | 分值\n价格 | 30\n\n![图示](images/a.png)"


def test_merge_mineru_normal(tmp_path):
    cl1 = tmp_path / "c1.json"
    cl1.write_text(
        json.dumps([{"type": "text", "text": "页1", "page_idx": 0}]),
        encoding="utf-8",
    )
    cl2 = tmp_path / "c2.json"
    cl2.write_text(
        json.dumps([{"type": "text", "text": "页6", "page_idx": 0}]),
        encoding="utf-8",
    )

    outputs = [
        MineruOutput(content_list_path=cl1, markdown_path=None, page_offset=0),
        MineruOutput(content_list_path=cl2, markdown_path=None, page_offset=5),
    ]
    stage = merge_mineru(outputs, entries=[], page_count=10)

    assert stage.pages == {1: "页1", 6: "页6"}
    assert not stage.parser_meta.get("degraded")
    assert stage.markdown == ""


def test_merge_mineru_degraded_on_missing_content_list(tmp_path):
    markdown = tmp_path / "full.md"
    markdown.write_text("分片内容", encoding="utf-8")

    outputs = [MineruOutput(content_list_path=None, markdown_path=markdown, page_offset=0)]
    stage = merge_mineru(outputs, entries=[], page_count=1)

    assert stage.markdown == "分片内容"
    assert stage.pages == {}
    assert stage.parser_meta["degraded"] is True
    assert any("content_list" in w for w in stage.warnings)


def test_merge_mineru_partial_missing_degrades(tmp_path):
    cl1 = tmp_path / "c1.json"
    cl1.write_text(
        json.dumps([{"type": "text", "text": "页1", "page_idx": 0}]),
        encoding="utf-8",
    )
    outputs = [
        MineruOutput(content_list_path=cl1, markdown_path=None, page_offset=0),
        MineruOutput(content_list_path=None, markdown_path=None, page_offset=5),
    ]
    stage = merge_mineru(outputs, entries=[], page_count=10)

    assert stage.parser_meta["degraded"] is True


def test_assemble_writes_artifacts(tmp_path):
    raw = [(1, "第一章 投标人须知", 1), (1, "第二章 评标办法", 3)]
    entries = read_toc(raw, page_count=5)
    stage = ParseStage(
        page_count=5,
        entries=entries,
        pages={1: "封面内容", 3: "评分内容"},
        images=["p001_x1.png"],
        warnings=[],
        parser_meta={"structure_source": "bookmark"},
    )

    artifacts_dir = tmp_path / "parsed" / "abc"
    images_dir = tmp_path / "images" / "abc"
    doc = assemble(
        stage,
        doc_id="abc",
        parser="local",
        source_name="test.pdf",
        source_path="files/test.pdf",
        source_sha256="deadbeef",
        source_bytes=100,
        artifacts_dir=artifacts_dir,
        images_dir=images_dir,
    )

    assert doc.doc_id == "abc"
    assert doc.doc_type == "tender_file"
    assert len(doc.sections) == 2

    content = (artifacts_dir / "content.md").read_text(encoding="utf-8")
    assert "## 第一章 投标人须知" in content
    assert "封面内容" in content

    manifest = json.loads((artifacts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["doc_id"] == "abc"
    assert manifest["doc_type"] == "tender_file"
    assert manifest["markdown"] == ""  # 全文以 content.md 为准
    assert len(manifest["sections"]) == 2


def test_assemble_adds_no_toc_warning(tmp_path):
    stage = ParseStage(
        page_count=2,
        entries=[],
        pages={1: "a", 2: "b"},
        images=[],
        warnings=[],
        parser_meta={},
    )
    doc = assemble(
        stage,
        doc_id="x",
        parser="local",
        source_name="x.pdf",
        source_path="files/x.pdf",
        source_sha256="s",
        source_bytes=1,
        artifacts_dir=tmp_path / "parsed" / "x",
        images_dir=tmp_path / "images" / "x",
    )

    assert doc.sections == []
    assert any("无书签" in w for w in doc.warnings)

    content = (tmp_path / "parsed" / "x" / "content.md").read_text(encoding="utf-8")
    assert "<!-- page: 1 -->" in content
