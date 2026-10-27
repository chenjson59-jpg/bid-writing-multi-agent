"""火山方舟嵌入包装（VolcengineEmbeddingFunction）。

用 OpenAI 兼容 SDK 调用火山方舟 `doubao-embedding-large`，供 Chroma 集合作为
embedding_function。显式指定 `encoding_format="float"` 返回浮点向量，避免 base64
导致协议不兼容。火山方舟该模型支持 `dimensions` 降维（4096/2048/1024/512）。
"""
from __future__ import annotations

import time

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from src.config import settings


class VolcengineEmbeddingFunction(EmbeddingFunction[Documents]):
    """把文本列表转成火山方舟嵌入向量，内部按 `EMBEDDING_BATCH_SIZE` 分批。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._api_key = api_key or settings.embedding_api_key
        self._base_url = base_url or settings.embedding_base_url
        self._model = model or settings.embedding_model
        self._dimensions = dimensions or settings.embedding_dimensions
        self._batch_size = batch_size or settings.embedding_batch_size
        self._client = None

    @staticmethod
    def name() -> str:
        return "volcengine-doubao-embedding"

    def _get_client(self):
        """惰性创建客户端，供同一次分块入库复用。"""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                max_retries=settings.embedding_max_retries,
                timeout=settings.embedding_timeout,
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
            if i + self._batch_size < len(input):
                time.sleep(settings.embedding_request_interval)
        return embeddings