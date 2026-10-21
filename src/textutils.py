"""文本处理工具（核心算法 A8：HTML 表格扁平化）。"""
from __future__ import annotations

import re
from html.parser import HTMLParser

_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)


class _TableExtractor(HTMLParser):
    """收集每个 tr 的单元格文本。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            if self._row is not None:
                self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _table_to_text(table_html: str) -> str:
    parser = _TableExtractor()
    try:
        parser.feed(table_html)
    except Exception:  # noqa: BLE001 - HTMLParser 可抛任意异常，降级保留原始 HTML
        return table_html
    lines = [" | ".join(row) for row in parser.rows if row]
    return "\n".join(lines)


def html_tables_to_text(text: str) -> str:
    """把文本中的 <table> 替换为「单元格 | 单元格」的纯文本行。"""
    return _TABLE_RE.sub(lambda m: _table_to_text(m.group(0)), text)
