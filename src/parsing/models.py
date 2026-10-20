"""解析领域模型：书签条目、章节、解析产物。

`ParsedDocument` 是跨模块的领域对象（抽取/索引/编写都消费它）。
`manifest.json` 由 `ParsedDocument.to_manifest()` 生成，不重复保存全文。
"""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class TocEntry:
    """PDF 书签树中的一个节点，页码均为 1-based。"""

    level: int
    title: str
    page: int
    page_end: int


@dataclass
class Section:
    """章节：由书签节点与其负责的正文组装而来。"""

    index: int
    level: int
    number: str
    title: str
    path: list[str]
    page_start: int
    page_end: int
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 manifest.json 的章节条目（不含正文，正文在 content.md）。"""
        return {
            "index": self.index,
            "level": self.level,
            "number": self.number,
            "title": self.title,
            "path": self.path,
            "page_start": self.page_start,
            "page_end": self.page_end,
        }


@dataclass
class ParseStage:
    """解析中间结果：章节还原前的页面级产物（本地/云端两条路径统一）。"""

    page_count: int
    entries: list[TocEntry]
    pages: dict[int, str]
    images: list[str]
    warnings: list[str]
    parser_meta: dict[str, Any]
    markdown: str = ""  # 降级时的顺序拼接全文（正常路径为空）


@dataclass
class MineruOutput:
    """单个分片的云端解析产物（本地解压后）。"""

    content_list_path: Path | None
    markdown_path: Path | None
    page_offset: int
    images_dir: Path | None = None


@dataclass
class ParsedDocument:
    """解析产物。全文以 content.md 为准，本对象与 manifest 均不重复保存全文。"""

    doc_id: str
    doc_type: str
    parser: str
    source_name: str
    source_path: str
    source_sha256: str
    source_bytes: int
    pages: int
    toc: list[TocEntry]
    sections: list[Section]
    images: list[str]
    warnings: list[str]
    artifacts_dir: str
    markdown_path: str
    images_dir: str
    parsed_at: str
    parser_meta: dict[str, Any]
    # 内存中的全文（写 content.md 用）；manifest.json 不重复保存全文
    markdown: str = ""

    def to_manifest(self) -> dict[str, Any]:
        """生成 manifest.json 内容。"""
        return {
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "parser": self.parser,
            "source_name": self.source_name,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "source_bytes": self.source_bytes,
            "pages": self.pages,
            "toc": [asdict(t) for t in self.toc],
            "sections": [s.to_dict() for s in self.sections],
            "images": self.images,
            "markdown": "",  # 全文以 content.md 为准，不重复保存
            "warnings": self.warnings,
            "artifacts_dir": self.artifacts_dir,
            "markdown_path": self.markdown_path,
            "images_dir": self.images_dir,
            "parsed_at": self.parsed_at,
            "parser_meta": self.parser_meta,
        }
