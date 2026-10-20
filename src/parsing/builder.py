"""解析产物组装与落盘（核心算法 A6、A23）。

两条解析路径最终都收敛为 `ParseStage`（章节还原前的页面级产物），
本模块负责：
- A6：把 MinerU 分片结果合并为统一的页面级产物，失败时降级为 Markdown 拼接；
- A23：判定文档类型；
- 章节还原（有书签按最深归属、无书签按页顺序）并落盘 `content.md` + `manifest.json`。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.parsing.models import (
    MineruOutput,
    ParsedDocument,
    ParseStage,
    Section,
    TocEntry,
)
from src.parsing.toc import assign_pages_to_entries, compose_sections

# 文档类型判定的三个特征词（A23）
_TENDER_FEATURES = ("评标办法", "投标人须知", "投标文件格式")


def _as_block_list(data: Any) -> list[Any]:
    """把 content_list.json 的顶层结构归一为块列表，容忍不同版本包装。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("content_list", "blocks", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        for value in data.values():
            if isinstance(value, list):
                return value
    return []


def _table_body_to_text(body: Any) -> str:
    """把 table_body 的多种形态（字符串 / 二维数组 / 字典行）归一为文本。"""
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        rows: list[str] = []
        for row in body:
            if isinstance(row, list):
                rows.append(" | ".join(str(cell) for cell in row))
            elif isinstance(row, dict):
                rows.append(" | ".join(str(v) for v in row.values()))
            else:
                rows.append(str(row))
        return "\n".join(rows)
    return ""


def _block_to_text(block: dict[str, Any]) -> str:
    """把一个 content_list 块转成文本（A6 类型规则）。"""
    btype = block.get("type", "")
    if btype == "table":
        caption = block.get("table_caption", "")
        body = block.get("table_body") or block.get("text", "")
        body_text = _table_body_to_text(body)
        return "\n".join(part for part in (caption, body_text) if part)
    if btype == "image":
        img_path = block.get("img_path", "")
        caption = block.get("img_caption", "")
        if caption:
            return f"![{caption}]({img_path})"
        return f"![]({img_path})" if img_path else ""
    return block.get("text", "")


def read_content_list(path: Path, page_offset: int) -> dict[int, str]:
    """读取单个分片的 content_list，返回 `{原始 1-based 页码: 页面文本}`。

    页码由 `page_idx + page_offset + 1` 还原（A6 页偏移规则）。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    pages: dict[int, str] = {}
    for block in _as_block_list(data):
        if not isinstance(block, dict):
            continue
        text = _block_to_text(block)
        if not text:
            continue
        page_no = int(block.get("page_idx", 0)) + page_offset + 1
        pages.setdefault(page_no, []).append(text)
    return {p: "\n\n".join(chunks) for p, chunks in pages.items()}


def _join_markdown(outputs: list[MineruOutput]) -> str:
    """降级时按分片顺序拼接 Markdown。"""
    parts: list[str] = []
    for output in outputs:
        if output.markdown_path is not None and output.markdown_path.exists():
            parts.append(output.markdown_path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def merge_mineru(
    outputs: list[MineruOutput],
    *,
    entries: list[TocEntry],
    page_count: int,
) -> ParseStage:
    """合并多个 MinerU 分片为统一的 `ParseStage`（A6）。

    - 优先用 content_list 还原页码；任一分片缺失或解析失败则整体降级；
    - 降级时按分片顺序拼接 Markdown，章节结构留空。
    """
    pages: dict[int, str] = {}
    warnings: list[str] = []
    degraded = False

    for output in outputs:
        if output.content_list_path is None or not output.content_list_path.exists():
            degraded = True
            continue
        try:
            part = read_content_list(output.content_list_path, output.page_offset)
        except Exception as exc:  # noqa: BLE001 - 单个分片失败只降级，不中断
            degraded = True
            warnings.append(
                f"解析 content_list 失败：{output.content_list_path.name}（{exc}）"
            )
            continue
        if not part:
            degraded = True
            continue
        pages.update(part)

    if not degraded and pages:
        return ParseStage(
            page_count=page_count,
            entries=entries,
            pages=pages,
            images=[],
            warnings=warnings,
            parser_meta={"structure_source": "content_list"},
        )

    warnings.append("无法从 content_list 还原页码，已按分片顺序拼接，章节边界可能偏移")
    return ParseStage(
        page_count=page_count,
        entries=entries,
        pages={},
        images=[],
        warnings=warnings,
        parser_meta={"structure_source": "markdown_order", "degraded": True},
        markdown=_join_markdown(outputs),
    )


def detect_doc_type(markdown: str, toc_count: int) -> str:
    """判定文档类型（A23）：`tender_file` / `announcement` / `unknown`。"""
    hits = sum(1 for word in _TENDER_FEATURES if word in markdown)
    if hits >= 2:
        return "tender_file"
    if "招标公告" in markdown:
        return "announcement"
    if toc_count > 20:
        return "tender_file"
    return "unknown"


def _restore_structure(stage: ParseStage) -> tuple[str, list[Section]]:
    """章节还原：有书签走最深归属，无书签按页顺序拼接。"""
    if stage.entries:
        assigned, uncovered = assign_pages_to_entries(stage.entries, stage.pages)
        return compose_sections(stage.entries, assigned, uncovered)

    if stage.markdown:
        return stage.markdown, []

    if stage.pages:
        markdown = "\n\n".join(
            f"<!-- page: {page_no} -->\n{text}"
            for page_no, text in sorted(stage.pages.items())
        )
        return markdown, []

    return "", []


def assemble(
    stage: ParseStage,
    *,
    doc_id: str,
    parser: str,
    source_name: str,
    source_path: str,
    source_sha256: str,
    source_bytes: int,
    artifacts_dir: Path,
    images_dir: Path,
) -> ParsedDocument:
    """章节还原 + 文档类型判定 + 落盘 `content.md` 与 `manifest.json`。

    返回 `ParsedDocument`，其中全文以 `content.md` 为准，`manifest.json`
    不重复保存全文。
    """
    markdown, sections = _restore_structure(stage)
    doc_type = detect_doc_type(markdown, len(stage.entries))

    warnings = stage.warnings
    if not stage.entries and not any("无书签" in w for w in warnings):
        warnings = warnings + ["PDF 无书签，章节结构无法还原"]

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    content_path = artifacts_dir / "content.md"
    content_path.write_text(markdown, encoding="utf-8")

    parsed_at = datetime.now().isoformat(timespec="seconds")
    doc = ParsedDocument(
        doc_id=doc_id,
        doc_type=doc_type,
        parser=parser,
        source_name=source_name,
        source_path=source_path,
        source_sha256=source_sha256,
        source_bytes=source_bytes,
        pages=stage.page_count,
        toc=stage.entries,
        sections=sections,
        images=stage.images,
        warnings=warnings,
        artifacts_dir=str(artifacts_dir),
        markdown_path=str(content_path),
        images_dir=str(images_dir),
        parsed_at=parsed_at,
        parser_meta=stage.parser_meta,
        markdown=markdown,
    )

    manifest_path = artifacts_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(doc.to_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return doc
