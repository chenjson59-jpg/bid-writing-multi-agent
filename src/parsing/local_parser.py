"""PyMuPDF 本地解析（docs/algorithms.md A7）。

云端不可用或失败时的兜底路径：逐页提取文本与图片，页面级产物统一为
ParseStage，由 builder 统一做章节还原与落盘。
"""
from __future__ import annotations

import re
from pathlib import Path

from src.parsing.models import ParseStage, TocEntry
from src.parsing.toc import read_toc, reconstruct_toc

# 文档指纹行（形如 32 位 hex 串 + 时间戳）
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{20,}-\d{10,}$")
# 纯页码行
_PAGE_NUMBER_RE = re.compile(r"^\s*[-—–]?\s*\d{1,4}\s*[-—–]?\s*$")
# 页眉/页脚区域（按页高比例），仅在该区域内剔除纯页码，避免误删表格数字
_MARGIN_RATIO = 0.07


def _clean_page_text(text: str) -> str:
    """纯文本清洗：丢弃指纹行，收敛空行。

    页码需要坐标信息才能与表格内数字区分，见 `_page_text`。
    """
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _FINGERPRINT_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _page_text(page) -> str:
    """按行坐标清洗单页文本：只剔除页眉/页脚区域内的纯页码。"""
    height = page.rect.height or 1.0
    top = height * _MARGIN_RATIO
    bottom = height * (1 - _MARGIN_RATIO)

    lines: list[str] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(
                span.get("text", "") for span in line.get("spans", [])
            ).strip()
            if not text:
                continue
            if _FINGERPRINT_RE.match(text):
                continue
            y0, y1 = line["bbox"][1], line["bbox"][3]
            if (y0 <= top or y1 >= bottom) and _PAGE_NUMBER_RE.match(text):
                continue
            lines.append(text)
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
        text = _page_text(doc.load_page(page_no - 1))
        if text:
            pages[page_no] = text

    warnings: list[str] = []
    structure_source = "bookmark" if entries else "page_order"
    if not entries:
        entries = reconstruct_toc(pages, page_count)
        if entries:
            structure_source = "toc_page"
            warnings.append("PDF 无书签，已从目录页还原章节结构")
        else:
            warnings.append("PDF 无书签，章节结构无法还原")

    images: list[str] = []
    if images_dir is not None:
        images, img_warnings = _extract_images(doc, images_dir)
        warnings.extend(img_warnings)

    return ParseStage(
        page_count=page_count,
        entries=entries,
        pages=pages,
        images=images,
        warnings=warnings,
        parser_meta={"structure_source": structure_source},
    )
