"""幂等入库（docs/algorithms.md A16）。

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

COLLECTION_ANNOUNCEMENT = "tender_announcement"
COLLECTION_DOCUMENT = "tender_document"
# 删除路径需要扫描的全部已知集合，与 collection_name 的映射保持一致
KNOWN_COLLECTIONS = (COLLECTION_ANNOUNCEMENT, COLLECTION_DOCUMENT)

_client = None
_client_lock = threading.Lock()


@dataclass
class IngestSummary:
    """入库统计（docs/api-contract.md §6.2 与 §7 约定字段）。"""
    collection: str
    added: int
    updated: int
    deleted: int
    unchanged: int
    total: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collection_name(doc_type: str) -> str:
    """文档类型 → 集合名映射（docs/tech-decisions.md §3.5）。

    `tender_file`/`unknown` 归入 `tender_document`，`announcement` 归入
    `tender_announcement`。
    """
    if doc_type == "announcement":
        return COLLECTION_ANNOUNCEMENT
    return COLLECTION_DOCUMENT


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


def delete_document_vectors(doc_id: str, *, client=None) -> int:
    """删除两个已知集合中属于 `doc_id` 的全部片段（T-016 级联清理）。

    - 只通过 `list_collections` 读取已存在的集合，绝不 get_or_create 凭空建集合；
    - 集合缺失或无匹配片段时返回 0，整体幂等，重复删除不报错；
    - 底层异常统一包装为带 `doc_id` 上下文的 `IngestError`，不静默吞错。

    返回实际删除的片段条数（两个集合相加）。
    """
    client = client or get_client()
    try:
        existing = {collection.name for collection in client.list_collections()}
        deleted_total = 0
        for name in KNOWN_COLLECTIONS:
            if name not in existing:
                continue
            collection = client.get_collection(
                name, embedding_function=VolcengineEmbeddingFunction()
            )
            ids = collection.get(where={"doc_id": doc_id}).get("ids") or []
            if ids:
                collection.delete(ids=list(ids))
                deleted_total += len(ids)
        return deleted_total
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError(f"清理文档 {doc_id} 的向量片段失败：{exc}") from exc
