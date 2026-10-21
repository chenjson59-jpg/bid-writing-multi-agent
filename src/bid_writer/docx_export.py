"""Markdown → Word 导出（核心算法 A21）。

行级解析：代码围栏、标题、表格、列表、分页符与普通段落；正文宋体 11pt、
标题黑体、表格 Table Grid。不处理斜体、链接、图片、嵌套列表与脚注。
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_UL_RE = re.compile(r"^[-*]\s+(.*)$")
_OL_RE = re.compile(r"^(\d+)[.、]\s+(.*)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

_HEADING_SIZES = {1: 16, 2: 14, 3: 13, 4: 12}


def markdown_to_docx(markdown: str, output_path: str | Path) -> None:
    """把 Markdown 写入 Word 文档。"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    _set_default_style(doc)

    table_lines: list[str] = []
    in_fence = False

    def flush_table() -> None:
        if table_lines:
            _add_table(doc, table_lines)
            table_lines.clear()

    for line in markdown.splitlines():
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_table()
            in_fence = not in_fence
            continue

        if in_fence:
            doc.add_paragraph(line)
            continue

        if stripped.startswith("|"):
            table_lines.append(line)
            continue

        if table_lines:
            flush_table()

        if not stripped:
            continue

        if stripped == "---":
            doc.add_page_break()
            continue

        _add_line(doc, stripped)

    flush_table()
    doc.save(str(output_path))


def _set_default_style(doc: Document) -> None:
    """正文默认宋体 11pt（8.5.1）。"""
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def _add_line(doc: Document, line: str) -> None:
    """行级分派（8.5.2）。"""
    match = _HEADING_RE.match(line)
    if match:
        _add_heading(doc, match.group(2), len(match.group(1)))
        return

    match = _UL_RE.match(line)
    if match:
        _add_paragraph(doc, match.group(1), style="List Bullet")
        return

    match = _OL_RE.match(line)
    if match:
        _add_paragraph(doc, match.group(2), style="List Number")
        return

    _add_paragraph(doc, line)


def _add_heading(doc: Document, text: str, level: int) -> None:
    """标题使用黑体（8.5.1）。"""
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    _set_font(run, "黑体", _HEADING_SIZES.get(level, 12), bold=True)


def _add_paragraph(doc: Document, text: str, style: str | None = None) -> None:
    paragraph = doc.add_paragraph(style=style)
    _add_runs(paragraph, text)


def _add_runs(paragraph, text: str) -> None:
    """按粗体标记拆分 run（8.5.4）。"""
    pos = 0
    for match in _BOLD_RE.finditer(text):
        if match.start() > pos:
            _add_run(paragraph, text[pos : match.start()], bold=False)
        _add_run(paragraph, match.group(1), bold=True)
        pos = match.end()
    if pos < len(text):
        _add_run(paragraph, text[pos:], bold=False)


def _add_run(paragraph, text: str, bold: bool) -> None:
    run = paragraph.add_run(text)
    _set_font(run, "宋体", 11, bold=bold)


def _set_font(run, name: str, size: int, bold: bool) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def _add_table(doc: Document, table_lines: list[str]) -> None:
    """表格解析（8.5.3）：去分隔行、按最大列数补空。"""
    rows = [_parse_table_row(line) for line in table_lines]
    if not rows:
        return

    if len(rows) > 1 and _is_separator(rows[1]):
        rows = rows[:1] + rows[2:]
    if not rows:
        return

    cols = max(len(row) for row in rows)
    table = doc.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    for i, row in enumerate(rows):
        for j in range(cols):
            table.cell(i, j).text = row[j] if j < len(row) else ""


def _parse_table_row(line: str) -> list[str]:
    stripped = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator(cells: list[str]) -> bool:
    """判断是否为 Markdown 表格分隔行（`---`、`:`、空格组成）。"""
    return bool(cells) and any("-" in cell for cell in cells) and all(
        re.fullmatch(r"[-: ]*", cell) for cell in cells
    )
