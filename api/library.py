"""文档库与 Markdown 预览端点。"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from api.schemas import BidHistoryItem, DocumentSummary
from src.pipeline import BIDS_DIR, PARSED_DIR

router = APIRouter(prefix="/api")

_DOC_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_BID_ID_RE = re.compile(r"^[0-9a-f]{16}-\d{8}-\d{6}$")
_MAX_DOCUMENTS = 200
_MAX_BIDS = 100
_ALLOWED_BID_FILES = {"bid.md", "bid.docx", "report.json", "outline.json"}
_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".json": "application/json",
}


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


@router.get("/bids", response_model=list[BidHistoryItem])
def list_bids() -> list[BidHistoryItem]:
    """标书历史：扫描产物目录，读回 report.json 的摘要。"""
    from src.bid_writer.schema import BidReport

    if not BIDS_DIR.is_dir():
        return []

    items: list[BidHistoryItem] = []
    dirs = sorted(BIDS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for d in dirs:
        if not d.is_dir():
            continue
        report = d / "report.json"
        if not report.is_file():
            continue
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            summary = BidReport(**data).summary()
        except (json.JSONDecodeError, OSError, TypeError):
            continue
        items.append(
            BidHistoryItem(
                bid_id=d.name,
                generated_at=data.get("generated_at"),
                summary=summary,
            )
        )
        if len(items) >= _MAX_BIDS:
            break
    return items


@router.get("/bids/{bid_id}", response_model=BidHistoryItem)
def get_bid(bid_id: str) -> BidHistoryItem:
    """单个产物的最新摘要：章节编辑 / 重写后前端据此刷新。"""
    from src.bid_writer.schema import BidReport

    if not _BID_ID_RE.match(bid_id):
        raise HTTPException(422, "bid_id 格式非法")
    report = BIDS_DIR / bid_id / "report.json"
    if not report.is_file():
        raise HTTPException(404, "产物不存在")
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(422, f"报告损坏：{exc}") from exc
    return BidHistoryItem(
        bid_id=bid_id,
        generated_at=data.get("generated_at"),
        summary=BidReport(**data).summary(),
    )


@router.get("/bids/{bid_id}/files/{name}")
def download_bid_file(bid_id: str, name: str, download: bool = False) -> Response:
    """产物文件下载，仅允许白名单文件，且路径必须落在产物目录内。"""
    if not _BID_ID_RE.match(bid_id):
        raise HTTPException(422, "bid_id 格式非法")
    if name not in _ALLOWED_BID_FILES:
        raise HTTPException(404, "文件不存在")

    path = (BIDS_DIR / bid_id / name).resolve()
    if not path.is_relative_to(BIDS_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, "文件不存在")

    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    media_type = _MEDIA_TYPES.get(Path(name).suffix, "application/octet-stream")
    return Response(path.read_bytes(), media_type=media_type, headers=headers)
