"""幂等入库（核心算法 A16）。

同一文档重复入库时不重复嵌入、不重复计费，并清理过期片段：
先 upsert 新增或内容变化的片段，再删除同一 `doc_id` 下不再存在的旧片段。
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from typing import Any

from src.config import settings
from src.errors import IngestError
from src.parsing.models import ParsedDocument
from src.vectorstore.chunking import chunk_document
from src.vectorstore.embedding import VolcengineEmbeddingFunction

UPSERT_BATCH = 64  # A16.7.2.2 分批 upsert 的默认批大小

_client = None
_client_lock = threading.Lock()


@dataclass
class IngestSummary:
    """入库统计（开发文档 §6.2 与 §7 约定字段）。"""
    collection: str
    added: int
    updated: int
    deleted: int
    unchanged: int
    total: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collection_name(doc_type: str) -> str:
    """文档类型 → 集合名映射（技术栈 §3.5）。

    `tender_file`/`unknown` 归入 `tender_document`，`announcement` 归入
    `tender_announcement`。
    """
    if doc_type == "announcement":
        return "tender_announcement"
    return "tender_document"


def get_client():
    """进程内复用的 `PersistentClient`，避免并发创建多个触发租户错误。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import chromadb

                _client = chromadb.PersistentClient(path=settings.chroma_dir)
    return _client


def get_collection(name: str, client=None):
    """获取或创建指定集合（cosine 距离 + 火山方舟嵌入函数）。"""
    client = client or get_client()
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
        embedding_function=VolcengineEmbeddingFunction(),
    )


def ingest_document(
    doc: ParsedDocument,
    *,
    project_name: str = "",
    collection=None,
) -> IngestSummary:
    """对已解析文档做幂等入库（A16）。

    `collection` 可为测试注入 fake；未传时按文档类型获取真实集合。
    返回入库统计；分块为空时抛 `IngestError`。
    """
    chunks = chunk_document(doc, project_name=project_name)
    if not chunks:
        raise IngestError("没有可入库的正文块")

    if collection is None:
        collection = get_collection(collection_name(doc.doc_type))

    existing = collection.get(where={"doc_id": doc.doc_id}, include=["metadatas"])
    existing_ids: list[str] = list(existing.get("ids") or [])
    metadatas: list[dict[str, Any] | None] = existing.get("metadatas") or []
    existing_hashes: dict[str, str | None] = {
        cid: (meta or {}).get("content_sha256")
        for cid, meta in zip(existing_ids, metadatas)
    }
    existing_set = set(existing_ids)
    new_ids = {chunk.id for chunk in chunks}

    changed = [
        chunk
        for chunk in chunks
        if existing_hashes.get(chunk.id) != chunk.metadata["content_sha256"]
    ]
    unchanged = len(chunks) - len(changed)
    added = len(new_ids - existing_set)
    updated = len(changed) - added

    # 先 upsert 变更片段（触发嵌入）
    for i in range(0, len(changed), UPSERT_BATCH):
        batch = changed[i : i + UPSERT_BATCH]
        collection.upsert(
            ids=[chunk.id for chunk in batch],
            documents=[chunk.text for chunk in batch],
            metadatas=[chunk.metadata for chunk in batch],
        )

    # 再清理不再存在的旧片段
    stale = [cid for cid in existing_ids if cid not in new_ids]
    if stale:
        collection.delete(ids=stale)

    return IngestSummary(
        collection=collection_name(doc.doc_type),
        added=added,
        updated=updated,
        deleted=len(stale),
        unchanged=unchanged,
        total=collection.count(),
    )