"""文档库与 Markdown 预览端点。"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from api.schemas import DocumentSummary
from src.pipeline import PARSED_DIR

router = APIRouter(prefix="/api")

_DOC_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_MAX_DOCUMENTS = 200


def _load_manifest(doc_id: str) -> dict | None:
    manifest = PARSED_DIR / doc_id / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


@router.get("/documents", response_model=list[DocumentSummary])
def list_documents() -> list[DocumentSummary]:
    if not PARSED_DIR.is_dir():
        return []

    items: list[DocumentSummary] = []
    dirs = sorted(PARSED_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for d in dirs:
        if not d.is_dir():
            continue
        manifest = _load_manifest(d.name)
        if manifest is None:
            # manifest 缺失/损坏时用目录名与修改时间兜底
            items.append(
                DocumentSummary(
                    doc_id=d.name,
                    source_name="",
                    doc_type="",
                    parser="",
                    pages=0,
                    sections=0,
                    has_extraction=False,
                    parsed_at=_mtime_iso(d),
                )
            )
            continue
        items.append(
            DocumentSummary(
                doc_id=d.name,
                source_name=manifest.get("source_name", ""),
                doc_type=manifest.get("doc_type", ""),
                parser=manifest.get("parser", ""),
                pages=manifest.get("pages", 0),
                sections=len(manifest.get("sections", [])),
                has_extraction=(d / "extraction.json").is_file(),
                parsed_at=manifest.get("parsed_at"),
            )
        )
        if len(items) >= _MAX_DOCUMENTS:
            break
    return items


@router.get("/documents/{doc_id}/markdown")
def preview_markdown(doc_id: str) -> Response:
    if not _DOC_ID_RE.match(doc_id):
        raise HTTPException(422, "doc_id 格式非法")
    content = PARSED_DIR / doc_id / "content.md"
    if not content.is_file():
        raise HTTPException(404, "文档不存在")
    return Response(content.read_text(encoding="utf-8"), media_type="text/markdown")
