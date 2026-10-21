"""检索索引（A15–A17）测试。"""
import pytest

from src.errors import IngestError
from src.parsing.models import ParsedDocument, Section
from src.vectorstore.chunking import chunk_document
from src.vectorstore.ingest import collection_name, ingest_document
from src.vectorstore.retrieval import ChromaRetriever


def make_section(
    index: int, title: str, text: str, number: str = "", path=None, page_start: int = 1
) -> Section:
    return Section(
        index=index,
        level=1,
        number=number,
        title=title,
        path=path or [],
        page_start=page_start,
        page_end=page_start,
        text=text,
    )


def make_doc(
    sections=None,
    markdown: str = "",
    doc_id: str = "abc",
    doc_type: str = "tender_file",
) -> ParsedDocument:
    return ParsedDocument(
        doc_id=doc_id,
        doc_type=doc_type,
        parser="local",
        source_name="test.pdf",
        source_path="files/test.pdf",
        source_sha256="deadbeef",
        source_bytes=100,
        pages=1,
        toc=[],
        sections=sections or [],
        images=[],
        warnings=[],
        artifacts_dir="upload/parsed/abc",
        markdown_path="upload/parsed/abc/content.md",
        images_dir="upload/images/abc",
        parsed_at="2026-01-01T00:00:00",
        parser_meta={},
        markdown=markdown,
    )


# ---- A15 章节感知分块 ----


def test_chunk_by_sections_metadata():
    doc = make_doc(
        sections=[
            make_section(0, "第一章 投标邀请", "<!-- page: 1 -->\n项目概况说明。", number="第一章"),
            make_section(1, "第二章 评标办法", "<!-- page: 2 -->\n价格因素为30分。", number="第二章"),
        ]
    )
    chunks = chunk_document(doc, project_name="测试项目")

    assert len(chunks) == 2
    assert chunks[0].id == "abc:0000"
    assert chunks[0].metadata["chunk_index"] == 0
    assert chunks[0].metadata["chunk_total"] == 2
    assert chunks[0].metadata["section_title"] == "第一章 投标邀请"
    assert chunks[0].metadata["section_no"] == "第一章"
    assert chunks[0].metadata["page_hint"] == "1"
    assert chunks[0].metadata["doc_id"] == "abc"
    assert len(chunks[0].metadata["content_sha256"]) == 16
    assert "【测试项目】" in chunks[0].text
    assert "项目概况说明" in chunks[0].body


def test_chunk_skips_toc_page():
    toc_text = "第一章 投标邀请....2\n第二章 投标人须知....5\n第三章 评标办法....10"
    doc = make_doc(sections=[make_section(0, "目录", toc_text)])
    assert chunk_document(doc) == []


def test_chunk_long_section_splits():
    long_text = "<!-- page: 1 -->\n" + "这是一个很长的句子。" * 200
    doc = make_doc(sections=[make_section(0, "长章节", long_text)])
    chunks = chunk_document(doc)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.body) <= 700


def test_chunk_fallback_by_pages():
    doc = make_doc(
        markdown="<!-- page: 1 -->\n第二页正文内容，满足长度要求。\n\n<!-- page: 2 -->\n第三页正文。"
    )
    chunks = chunk_document(doc)

    assert chunks
    assert all(chunk.metadata["schema_version"] == "1" for chunk in chunks)
    # 短于最小兜底长度的页会被跳过，因此应至少有符合长度的片段存在
    assert any("第 " in chunk.metadata["section_path"] for chunk in chunks)


def test_chunk_empty_returns_empty():
    doc = make_doc()
    assert chunk_document(doc) == []


# ---- A16 幂等入库 ----


class FakeCollection:
    """注入 ingest/retriever 的内存集合，记录 upsert/delete 调用。"""

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.upserted: list[str] = []
        self.deleted: list[str] = []

    def get(self, where=None, include=None):
        ids = [
            cid
            for cid, v in self.store.items()
            if where and v["metadata"].get("doc_id") == where.get("doc_id")
        ]
        return {"ids": ids, "metadatas": [self.store[cid]["metadata"] for cid in ids]}

    def upsert(self, ids, documents=None, metadatas=None):
        for i, cid in enumerate(ids):
            self.upserted.append(cid)
            self.store[cid] = {"document": documents[i], "metadata": metadatas[i]}

    def delete(self, ids):
        self.deleted.extend(ids)
        for cid in ids:
            self.store.pop(cid, None)

    def count(self):
        return len(self.store)

    def query(self, query_texts=None, n_results=10, where=None):
        items = list(self.store.items())
        if where and where.get("doc_id"):
            items = [it for it in items if it[1]["metadata"].get("doc_id") == where["doc_id"]]
        items = items[: n_results]
        return {
            "ids": [[cid for cid, _ in items]],
            "documents": [[v["document"] for _, v in items]],
            "metadatas": [[v["metadata"] for _, v in items]],
            "distances": [[1.0 - i * 0.1 for i in range(len(items))]],
        }


def _sections(text1="第一章正文。", text2="第二章正文。"):
    return [
        make_section(0, "第一章", f"<!-- page: 1 -->\n{text1}"),
        make_section(1, "第二章", f"<!-- page: 2 -->\n{text2}"),
    ]


def test_collection_name_mapping():
    assert collection_name("tender_file") == "tender_document"
    assert collection_name("unknown") == "tender_document"
    assert collection_name("announcement") == "tender_announcement"


def test_ingest_first_run():
    col = FakeCollection()
    summary = ingest_document(make_doc(sections=_sections()), collection=col)

    assert summary.added == 2
    assert summary.updated == 0
    assert summary.deleted == 0
    assert summary.total == 2


def test_ingest_idempotent():
    col = FakeCollection()
    doc = make_doc(sections=_sections())
    ingest_document(doc, collection=col)
    col.upserted.clear()

    summary = ingest_document(doc, collection=col)

    assert summary.added == 0
    assert summary.updated == 0
    assert summary.unchanged == 2
    assert col.upserted == []


def test_ingest_updated_when_content_changes():
    col = FakeCollection()
    ingest_document(make_doc(sections=_sections()), collection=col)
    col.upserted.clear()

    doc = make_doc(sections=_sections(text1="第一章正文已修改。"))
    summary = ingest_document(doc, collection=col)

    assert summary.added == 0
    assert summary.updated == 1
    assert summary.unchanged == 1


def test_ingest_deleted_when_chunks_shrink():
    col = FakeCollection()
    ingest_document(make_doc(sections=_sections()), collection=col)

    doc = make_doc(sections=[_sections()[0]])
    summary = ingest_document(doc, collection=col)

    assert summary.deleted == 1
    assert summary.total == 1


def test_ingest_empty_raises():
    with pytest.raises(IngestError):
        ingest_document(make_doc(), collection=FakeCollection())


# ---- A17 共享检索 ----


class FakeClient:
    def __init__(self, collection=None, raise_on_get: bool = False):
        self._collection = collection
        self._raise = raise_on_get

    def get_collection(self, name, embedding_function=None):
        if self._raise:
            raise RuntimeError("collection not found")
        return self._collection


def test_retriever_search_returns_chunks():
    col = FakeCollection()
    ingest_document(make_doc(sections=_sections()), collection=col)

    retriever = ChromaRetriever("tender_document", client=FakeClient(col))
    result = retriever.search("第一章", limit=2)

    assert len(result) == 2
    assert result[0]["id"] == "abc:0000"
    assert "第一章" in result[0]["text"]
    assert "distance" in result[0]


def test_retriever_none_collection_returns_empty():
    retriever = ChromaRetriever("missing", client=FakeClient(raise_on_get=True))
    assert retriever.search("查询") == []


def test_retriever_limits_results():
    col = FakeCollection()
    ingest_document(make_doc(sections=_sections()), collection=col)

    retriever = ChromaRetriever("tender_document", client=FakeClient(col))
    result = retriever.search("查询", limit=1)

    assert len(result) == 1