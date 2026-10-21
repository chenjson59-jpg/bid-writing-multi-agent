"""inspect 子命令：查看索引内容或执行检索自检。"""
from __future__ import annotations

import json
from typing import Any

from src.vectorstore.ingest import get_collection
from src.vectorstore.retrieval import ChromaRetriever


def register(subparsers) -> None:
    p = subparsers.add_parser("inspect", help="查看索引内容或执行检索自检")
    p.add_argument("collection", help="集合名，如 tender_document")
    p.add_argument("--query", help="检索查询词；缺省时列出索引内容")
    p.add_argument("--limit", type=int, default=5, help="返回条数")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(handler=run)


def _list_chunks(result: dict[str, Any]) -> list[dict[str, Any]]:
    """把 Chroma `get` 结果转成片段列表（无距离字段）。"""
    ids = result.get("ids") or []
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    chunks: list[dict[str, Any]] = []
    for i, cid in enumerate(ids):
        chunks.append(
            {
                "id": cid,
                "text": documents[i] if i < len(documents) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
            }
        )
    return chunks


def _print(chunks: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print(json.dumps(chunks, ensure_ascii=False, indent=2))
        return
    if not chunks:
        print("（无结果）")
        return
    for chunk in chunks:
        preview = chunk["text"].replace("\n", " ")[:80]
        print(f"- {chunk['id']}  {preview}")


def run(args) -> int:
    if args.query:
        chunks = ChromaRetriever(args.collection).search(args.query, limit=args.limit)
    else:
        collection = get_collection(args.collection)
        chunks = _list_chunks(
            collection.get(limit=args.limit, include=["documents", "metadatas"])
        )
    _print(chunks, args.json)
    return 0
