"""共享检索（核心算法 A17）。

多角色并行写作时共享一个检索器，内部持有唯一集合引用，查询用互斥锁
串行化，避免并发创建多个 PersistentClient 触发租户错误。
"""
from __future__ import annotations

import threading
from typing import Any

from src.config import settings
from src.vectorstore.embedding import ZhipuEmbeddingFunction
from src.vectorstore.ingest import get_client


class ChromaRetriever:
    """一轮编写持有的检索器，查询按 `doc_id` 过滤（可选）。"""

    def __init__(
        self,
        collection_name: str,
        *,
        doc_id: str | None = None,
        client=None,
    ) -> None:
        self._doc_id = doc_id
        self._lock = threading.Lock()
        self.collection = None
        try:
            c = client or get_client()
            self.collection = c.get_collection(
                collection_name, embedding_function=ZhipuEmbeddingFunction()
            )
        except Exception:  # noqa: BLE001 - 集合不存在或嵌入不可用时检索降级为空列表
            self.collection = None

    def search(self, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        """按查询检索 Top-K 片段；失败返回空列表（A17.7.3.4）。"""
        if self.collection is None:
            return []
        limit = limit or settings.bid_writer_top_k
        where = {"doc_id": self._doc_id} if self._doc_id else None
        with self._lock:
            result = self.collection.query(
                query_texts=[query], n_results=limit, where=where
            )
        return _to_chunks(result)


def _to_chunks(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把 Chroma `query` 结果转成结构化片段列表。"""
    if not result:
        return []
    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    chunks: list[dict[str, Any]] = []
    for i, cid in enumerate(ids):
        chunks.append(
            {
                "id": cid,
                "text": documents[i] if i < len(documents) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distances[i] if i < len(distances) else None,
            }
        )
    return chunks