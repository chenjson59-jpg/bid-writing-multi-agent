"""智谱嵌入包装（ZhipuEmbeddingFunction）。

包装 zai-sdk 调用智谱 `embedding-3`，供 Chroma 集合作为 embedding_function。
显式指定 `encoding_format="float"`（zai-sdk 原样透传），避免 OpenAI SDK 强制
base64 导致的协议不兼容（见 `README.md` 设计要点）。
"""
from __future__ import annotations

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from src.config import settings


class ZhipuEmbeddingFunction(EmbeddingFunction[Documents]):
    """把文本列表转成智谱嵌入向量，内部按 `EMBEDDING_BATCH_SIZE` 分批。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._api_key = api_key or settings.zhipu_api_key
        self._base_url = base_url or settings.zhipu_base_url
        self._model = model or settings.embedding_model
        self._dimensions = dimensions or settings.embedding_dimensions
        self._batch_size = batch_size or settings.embedding_batch_size
        self._client = None

    @staticmethod
    def name() -> str:
        return "zhipu-embedding-3"

    def _get_client(self):
        """惰性创建客户端，供同一次分块入库复用。"""
        if self._client is None:
            from zai import ZhipuAiClient

            self._client = ZhipuAiClient(
                api_key=self._api_key, base_url=self._base_url
            )
        return self._client

    def __call__(self, input: Documents) -> Embeddings:
        client = self._get_client()
        embeddings: list[list[float]] = []
        for i in range(0, len(input), self._batch_size):
            batch = input[i : i + self._batch_size]
            resp = client.embeddings.create(
                input=batch,
                model=self._model,
                dimensions=self._dimensions,
                encoding_format="float",
            )
            ordered = sorted(resp.data, key=lambda e: e.index)
            embeddings.extend([e.embedding for e in ordered])
        return embeddings