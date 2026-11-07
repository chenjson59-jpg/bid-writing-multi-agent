"""Golden Data 加载器（T-020）。

职责：
- 读取 `eval/datasets/golden/<sample>/` 下的样本；
- 把手写招标 Markdown 还原为 `ParsedDocument`（与解析产物同构）；
- 提供按章节标题键控的脚本化 `ModelCaller`，全程零网络。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.parsing.models import ParsedDocument, Section, TocEntry

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_BREADCRUMB_RE = re.compile(r"^章节：(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class GoldenSample:
    """一份 Golden 样本：元信息、Markdown 输入与按章节键控的模型夹具。"""

    name: str
    root: Path
    meta: dict[str, Any]
    markdown: str
    fixtures: dict[str, list[dict[str, Any]]]


def load_sample(root: Path) -> GoldenSample:
    """加载单个样本目录，缺文件或 JSON 非法时抛出带样本名的明确错误。"""
    root = Path(root)
    try:
        meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        markdown = (root / "tender.md").read_text(encoding="utf-8")
        fixtures = json.loads((root / "llm_fixtures.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Golden 样本 {root.name} 加载失败：{exc}") from exc
    if not isinstance(fixtures, dict):
        raise TypeError(f"Golden 样本 {root.name} 的 llm_fixtures.json 必须是对象")
    return GoldenSample(
        name=root.name,
        root=root,
        meta=meta,
        markdown=markdown,
        fixtures=fixtures,
    )


def load_all(datasets_dir: Path | None = None) -> list[GoldenSample]:
    """发现数据集目录下的全部样本，按目录名排序，新增样本无需改代码。"""
    datasets_dir = datasets_dir or Path(__file__).resolve().parents[1] / "datasets" / "golden"
    samples = [
        load_sample(child)
        for child in sorted(datasets_dir.iterdir())
        if child.is_dir() and (child / "meta.json").is_file()
    ]
    if not samples:
        raise ValueError(f"未在 {datasets_dir} 发现任何 Golden 样本")
    return samples


def markdown_to_parsed_doc(markdown: str, meta: dict[str, Any]) -> ParsedDocument:
    """把 `# 第X章 标题` 一级标题切分为章节树，下级标题保留在章节正文中。

    只识别一级标题；生产侧 `chapter_indices` 同样要求标题匹配「第X章」，
    因此样本标题必须遵守该约定才能形成抽取单元。
    """
    headings = list(_H1_RE.finditer(markdown))
    if not headings:
        raise ValueError("tender.md 至少需要一个 '# 第X章 标题' 一级标题")

    toc: list[TocEntry] = []
    sections: list[Section] = []
    for index, match in enumerate(headings):
        title = match.group(1).strip()
        body_start = match.end()
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        body = markdown[body_start:body_end].strip()
        toc.append(TocEntry(level=1, title=title, page=1, page_end=1))
        sections.append(
            Section(
                index=index,
                level=1,
                number="",
                title=title,
                path=[],
                page_start=1,
                page_end=1,
                text=body,
            )
        )

    encoded = markdown.encode("utf-8")
    return ParsedDocument(
        doc_id=str(meta["doc_id"]),
        doc_type=str(meta.get("doc_type") or "tender_file"),
        parser=str(meta.get("parser") or "local"),
        source_name=str(meta.get("source_name") or "golden.pdf"),
        source_path="",
        source_sha256=hashlib.sha256(encoded).hexdigest(),
        source_bytes=len(encoded),
        pages=len(sections),
        toc=toc,
        sections=sections,
        images=[],
        warnings=[],
        artifacts_dir="",
        markdown_path="",
        images_dir="",
        parsed_at=str(meta.get("parsed_at") or "2026-01-01T00:00:00"),
        parser_meta={},
        markdown=markdown,
    )


class GoldenCaller:
    """脚本化 ModelCaller：按单元标题消费夹具队列，可表达重试与脏数据。

    夹具形如 `{"第X章 标题": [{"content": "...", "finish_reason": "stop",
    "json_mode_used": true}, ...]}`，每次调用弹出队首；标题无夹具或队列耗尽时
    抛出 KeyError，让数据集与 Markdown 漂移显式失败。
    """

    def __init__(self, fixtures: dict[str, list[dict[str, Any]]]) -> None:
        self._queues: dict[str, list[dict[str, Any]]] = {
            title: list(responses) for title, responses in fixtures.items()
        }

    @staticmethod
    def _title_of(messages: list[dict[str, str]]) -> str:
        user_content = messages[-1]["content"] if messages else ""
        match = _BREADCRUMB_RE.search(user_content)
        if not match:
            raise KeyError("无法从提示词解析章节标题，夹具键控失败")
        return match.group(1).strip()

    def generate(self, messages: list[dict[str, str]], json_mode: bool) -> dict[str, Any]:
        title = self._title_of(messages)
        queue = self._queues.get(title)
        if not queue:
            raise KeyError(f"Golden 夹具缺失或已耗尽：{title}")
        response = queue.pop(0)
        return dict(response)
