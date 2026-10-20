"""PyMuPDF 本地解析（核心算法 A7）。

云端不可用或失败时的兜底路径：逐页提取文本与图片，页面级产物统一为
ParseStage，由 builder 统一做章节还原与落盘。
"""
from __future__ import annotations

import re
from pathlib import Path

from src.parsing.models import ParseStage, TocEntry
from src.parsing.toc import read_toc

# 文档指纹行（形如 32 位 hex 串 + 时间戳）
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{20,}-\d{10,}$")
# 纯页码行
_PAGE_NUMBER_RE = re.compile(r"^\s*[-—–]?\s*\d{1,4}\s*[-—–]?\s*$")


def _clean_page_text(text: str) -> str:
    """清洗单页文本：丢弃指纹行与纯页码，收敛空行。"""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _FINGERPRINT_RE.match(line) or _PAGE_NUMBER_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_images(doc, images_dir: Path) -> tuple[list[str], list[str]]:
    """按 xref 去重抽取图片，返回 (文件名清单, 警告)。"""
    images_dir.mkdir(parents=True, exist_ok=True)
    seen: set[int] = set()
    names: list[str] = []
    warnings: list[str] = []

    for page_no in range(1, doc.page_count + 1):
        page = doc.load_page(page_no - 1)
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                info = doc.extract_image(xref)
                ext = info.get("ext", "png")
                name = f"p{page_no:03d}_x{xref}.{ext}"
                (images_dir / name).write_bytes(info["image"])
                names.append(name)
            except Exception:  # noqa: BLE001 - 单张图片失败不中断解析
                warnings.append(f"图片提取失败 xref={xref}")

    return names, warnings


def parse_local(doc, images_dir: Path | None = None) -> ParseStage:
    """本地逐页解析。images_dir 为 None 时跳过图片抽取。"""
    page_count = doc.page_count
    entries: list[TocEntry] = read_toc(doc.get_toc(simple=True), page_count)

    pages: dict[int, str] = {}
    for page_no in range(1, page_count + 1):
        text = _clean_page_text(doc.load_page(page_no - 1).get_text())
        if text:
            pages[page_no] = text

    warnings: list[str] = []
    if not entries:
        warnings.append("PDF 无书签，章节结构无法还原")

    images: list[str] = []
    if images_dir is not None:
        images, img_warnings = _extract_images(doc, images_dir)
        warnings.extend(img_warnings)

    structure_source = "bookmark" if entries else "page_order"
    return ParseStage(
        page_count=page_count,
        entries=entries,
        pages=pages,
        images=images,
        warnings=warnings,
        parser_meta={"structure_source": structure_source},
    )
