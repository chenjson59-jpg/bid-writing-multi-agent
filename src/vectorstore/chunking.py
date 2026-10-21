"""章节感知分块（核心算法 A15）。

不以固定窗口切断条款，尽量保持章节语义完整；超长章节才在句末标点处
切分。有书签按章节分块，无书签按页兜底。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.parsing.models import ParsedDocument
from src.parsing.toc import looks_like_toc_page
from src.textutils import html_tables_to_text

TARGET = 450             # 段落打包目标长度（A15.7.1.2）
HARD_MAX = 700           # 单片段硬上限
OVERLAP = 80             # 硬切时的重叠字符数
MIN_FALLBACK_CHARS = 10  # 无书签兜底时的最短片段
SCHEMA_VERSION = "1"     # 元数据版本

_PAGE_MARKER_RE = re.compile(r"<!--\s*page:\s*\d+\s*-->")
_PAGE_NO_RE = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")
_SENT_END = "。；！？"


@dataclass
class Chunk:
    """一个检索片段。`body` 为纯正文，`text` 为面包屑 + 正文（写入索引）。"""
    id: str
    body: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _split_paragraphs(text: str) -> list[str]:
    """按空行切分段落。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _split_sentences(text: str) -> list[str]:
    """在句末标点（。；！？）后切分，保留标点（A15.7.1.5）。"""
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SENT_END:
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    return parts


def _hard_split(text: str, hard_max: int = HARD_MAX, overlap: int = OVERLAP) -> list[str]:
    """句末硬切：优先在句末标点后切，超长句按固定长度硬切，带重叠。"""
    pieces: list[str] = []
    buffer = ""
    for sentence in _split_sentences(text):
        if len(buffer) + len(sentence) > hard_max and buffer:
            pieces.append(buffer)
            buffer = buffer[-overlap:] if overlap else ""
        buffer += sentence
        while len(buffer) > hard_max:
            pieces.append(buffer[:hard_max])
            buffer = buffer[hard_max - overlap :]
    if buffer:
        pieces.append(buffer)
    return [p.strip() for p in pieces if p.strip()]


def _pack(text: str, target: int = TARGET, hard_max: int = HARD_MAX) -> list[str]:
    """段落打包：优先保持段落完整，单段超长硬切（A15.7.1.4）。"""
    pieces: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            pieces.append(buffer)
        buffer = ""

    for paragraph in _split_paragraphs(text):
        if len(paragraph) > hard_max:
            flush()
            pieces.extend(_hard_split(paragraph, hard_max))
            continue
        if buffer and len(buffer) + len(paragraph) > target:
            flush()
        buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph

    flush()
    return pieces


def _breadcrumb(project_name: str, path: str) -> str:
    """拼面包屑前缀（A15.7.1.3）：【项目名】【祖先 > 当前章节】。"""
    parts = [f"【{project_name}】"] if project_name else []
    parts.append(f"【{path}】")
    return "".join(parts)


def _base_metadata(doc: ParsedDocument) -> dict[str, Any]:
    """片段公共元数据（A15.7.1.7）。"""
    return {
        "doc_id": doc.doc_id,
        "doc_type": doc.doc_type,
        "source_name": doc.source_name,
        "source_sha256": doc.source_sha256,
        "parser": doc.parser,
        "schema_version": SCHEMA_VERSION,
    }


def _make_metadata(
    base: dict[str, Any],
    *,
    body: str,
    section_no: str,
    section_title: str,
    section_path: str,
    page_hint: str,
) -> dict[str, Any]:
    """构造单个片段的完整元数据。"""
    meta = dict(base)
    meta.update(
        {
            "section_no": section_no,
            "section_title": section_title,
            "section_path": section_path,
            "page_hint": page_hint,
            "char_count": len(body),
            "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    return meta


def _chunk_one(
    body: str,
    *,
    doc: ParsedDocument,
    project_name: str,
    path: str,
    section_no: str,
    section_title: str,
    page_hint: str,
) -> list[Chunk]:
    """把一段正文切成若干 Chunk（面包屑 + 元数据一致）。"""
    prefix = _breadcrumb(project_name, path)
    base = _base_metadata(doc)
    chunks: list[Chunk] = []
    for piece in _pack(body):
        meta = _make_metadata(
            base,
            body=piece,
            section_no=section_no,
            section_title=section_title,
            section_path=path,
            page_hint=page_hint,
        )
        chunks.append(
            Chunk(id="", body=piece, text=f"{prefix}\n{piece}", metadata=meta)
        )
    return chunks


def _chunk_by_sections(doc: ParsedDocument, project_name: str) -> list[Chunk]:
    """有书签：按章节分块（A15.7.1.3）。"""
    chunks: list[Chunk] = []
    for section in doc.sections:
        cleaned = _PAGE_MARKER_RE.sub("", section.text)
        body = html_tables_to_text(cleaned).strip()
        if not body or looks_like_toc_page(body):
            continue
        path = " > ".join([*section.path, section.title]) if section.title else ""
        chunks.extend(
            _chunk_one(
                body,
                doc=doc,
                project_name=project_name,
                path=path,
                section_no=section.number,
                section_title=section.title,
                page_hint=str(section.page_start),
            )
        )
    return chunks


def _split_by_page_markers(markdown: str) -> list[tuple[int, str]]:
    """按 `<!-- page: N -->` 拆分全文；无标记则整篇作为一页（页码 -1）。"""
    matches = list(_PAGE_NO_RE.finditer(markdown))
    if not matches:
        return [(-1, markdown.strip())] if markdown.strip() else []
    pages: list[tuple[int, str]] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        pages.append((int(match.group(1)), markdown[start:end].strip()))
    return pages


def _chunk_by_pages(doc: ParsedDocument, project_name: str) -> list[Chunk]:
    """无书签或章节全被跳过：按页兜底（A15.7.1.6）。"""
    chunks: list[Chunk] = []
    pages = _split_by_page_markers(doc.markdown)

    for page_no, raw in pages:
        body = html_tables_to_text(raw).strip()
        if not body or len(body) < MIN_FALLBACK_CHARS:
            continue
        if looks_like_toc_page(body):
            continue

        if page_no == -1:
            label, hint = "全文", ""
        else:
            label, hint = f"第 {page_no} 页", str(page_no)

        base = _base_metadata(doc)
        for piece in _pack(body):
            # 尾部小于 10 字的碎片丢弃
            if len(piece) < MIN_FALLBACK_CHARS:
                continue
            meta = _make_metadata(
                base,
                body=piece,
                section_no="",
                section_title="",
                section_path=label,
                page_hint=hint,
            )
            prefix = _breadcrumb(project_name, label)
            chunks.append(
                Chunk(id="", body=piece, text=f"{prefix}\n{piece}", metadata=meta)
            )
    return chunks


def chunk_document(doc: ParsedDocument, *, project_name: str = "") -> list[Chunk]:
    """把 `ParsedDocument` 切成检索片段（A15）。

    有书签按章节分块，无书签或章节全被跳过时按页兜底。
    最后统一回填片段 id 与 `chunk_index` / `chunk_total`。
    """
    chunks = _chunk_by_sections(doc, project_name)
    if not chunks:
        chunks = _chunk_by_pages(doc, project_name)

    for i, chunk in enumerate(chunks):
        chunk.id = f"{doc.doc_id}:{i:04d}"
        chunk.metadata["chunk_index"] = i
        chunk.metadata["chunk_total"] = len(chunks)
    return chunks