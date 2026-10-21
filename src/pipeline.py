"""流水线编排（A22 部分）：parse → extract → ingest 三阶段串联。

命令行与接口复用同一套 `run_parse` / `run_extract` / `run_ingest`，
产物统一落在仓库根的 `upload/` 目录。源文件按内容 sha256 前 16 位作为
`doc_id`，同一文件重跑复用已解析产物目录。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pymupdf

from src.config import REPO_ROOT, settings
from src.errors import ConfigError, ParseError
from src.extraction.extractor import run_extraction
from src.extraction.schema import TenderDocument
from src.parsing.builder import assemble, merge_mineru
from src.parsing.local_parser import parse_local
from src.parsing.mineru_client import parse_cloud
from src.parsing.models import ParsedDocument, ParseStage, Section, TocEntry
from src.parsing.splitter import plan_splits
from src.parsing.toc import read_toc
from src.vectorstore.ingest import IngestSummary, ingest_document

UPLOAD_DIR = REPO_ROOT / "upload"
FILES_DIR = UPLOAD_DIR / "files"
PARSED_DIR = UPLOAD_DIR / "parsed"
IMAGES_DIR = UPLOAD_DIR / "images"

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)
_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sha256(path: Path) -> str:
    """源文件完整内容 sha256。"""
    with path.open("rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _safe_filename(name: str) -> str:
    """清理文件名中的目录部分与非法字符（开发文档 §6.5）。"""
    base = Path(name).name
    return _ILLEGAL_CHARS_RE.sub("_", base) or "document"


def _restore_section_texts(markdown: str, sections: list[Section]) -> None:
    """从 `content.md` 恢复各章节正文（仅用于从磁盘加载复用）。

    `manifest.json` 不保存全文，此处按 Markdown 标题区间反解；标题顺序与
    `compose_sections` 产出一致，故按顺序对齐。
    """
    matches = list(_HEADING_RE.finditer(markdown))
    for i, section in enumerate(sections):
        if i >= len(matches):
            break
        start = matches[i].end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        section.text = markdown[start:end].strip()


def load_parsed_document(doc_id: str) -> ParsedDocument:
    """从 `upload/parsed/<doc_id>` 读回已解析文档。"""
    artifacts_dir = PARSED_DIR / doc_id
    manifest_path = artifacts_dir / "manifest.json"
    if not manifest_path.exists():
        raise ParseError(f"未找到已解析文档：{doc_id}")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    content = (artifacts_dir / "content.md").read_text(encoding="utf-8")

    toc = [TocEntry(**t) for t in data.get("toc", [])]
    sections = [
        Section(
            index=s["index"],
            level=s["level"],
            number=s["number"],
            title=s["title"],
            path=s["path"],
            page_start=s["page_start"],
            page_end=s["page_end"],
            text="",
        )
        for s in data.get("sections", [])
    ]
    _restore_section_texts(content, sections)

    return ParsedDocument(
        doc_id=data["doc_id"],
        doc_type=data["doc_type"],
        parser=data["parser"],
        source_name=data["source_name"],
        source_path=data["source_path"],
        source_sha256=data["source_sha256"],
        source_bytes=data["source_bytes"],
        pages=data["pages"],
        toc=toc,
        sections=sections,
        images=data.get("images", []),
        warnings=data.get("warnings", []),
        artifacts_dir=data["artifacts_dir"],
        markdown_path=data["markdown_path"],
        images_dir=data["images_dir"],
        parsed_at=data["parsed_at"],
        parser_meta=data.get("parser_meta", {}),
        markdown=content,
    )


def _write_shards(doc, splits, raw_dir: Path) -> list[tuple[Path, int]]:
    """按分片计划写出子 PDF，返回 `(子 PDF 路径, page_offset)` 列表。"""
    shards: list[tuple[Path, int]] = []
    for split in splits:
        sub = pymupdf.open()
        sub.insert_pdf(doc, from_page=split.from_page, to_page=split.to_page)
        path = raw_dir / f"shard_{split.page_offset:04d}.pdf"
        sub.save(path)
        sub.close()
        shards.append((path, split.page_offset))
    return shards


def _parse_cloud(
    doc,
    entries: list[TocEntry],
    page_count: int,
    api_key: str,
    artifacts_dir: Path,
    source_pdf: Path,
) -> ParseStage:
    """云端路径：分片（超 200 页）→ 上传解析 → 合并。"""
    raw_dir = artifacts_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    splits = plan_splits(page_count, settings.mineru_max_pages)
    if len(splits) == 1:
        shards = [(source_pdf, 0)]
    else:
        shards = _write_shards(doc, splits, raw_dir)

    outputs = parse_cloud(shards, api_key=api_key, raw_dir=raw_dir)
    return merge_mineru(outputs, entries=entries, page_count=page_count)


def _do_parse(
    doc,
    entries: list[TocEntry],
    page_count: int,
    *,
    parser: str,
    strict_parser: bool,
    api_key: str,
    artifacts_dir: Path,
    images_dir: Path,
    source_pdf: Path,
) -> tuple[ParseStage, str]:
    """按解析方式执行解析，返回 `(ParseStage, 实际解析器)` 并处理回退。"""
    images_dir.mkdir(parents=True, exist_ok=True)

    if parser == "local":
        return parse_local(doc, images_dir=images_dir), "local"

    key = api_key or settings.mineru_api_key
    if parser == "auto" and not key:
        stage = parse_local(doc, images_dir=images_dir)
        stage.warnings.append("跳过云端解析（no_api_key）")
        return stage, "local"

    if parser not in ("auto", "cloud"):
        raise ConfigError(f"未知解析器：{parser}")

    try:
        stage = _parse_cloud(doc, entries, page_count, key, artifacts_dir, source_pdf)
        return stage, "cloud"
    except Exception as exc:  # 云端失败统一在此处理回退
        if parser == "cloud" or strict_parser:
            raise ParseError(f"云端解析失败：{exc}") from exc
        stage = parse_local(doc, images_dir=images_dir)
        stage.warnings.append(f"云端解析失败，回退本地解析：{exc}")
        return stage, "local"


def run_parse(
    pdf_path: str | Path,
    *,
    parser: str = "auto",
    force: bool = False,
    strict_parser: bool = False,
    api_key: str | None = None,
) -> ParsedDocument:
    """解析 PDF 为 `ParsedDocument` 并落盘（content.md + manifest.json + images/）。"""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise ParseError(f"源文件不存在：{pdf_path}")

    content_bytes = pdf_path.read_bytes()
    full_hash = hashlib.sha256(content_bytes).hexdigest()
    doc_id = full_hash[:16]
    artifacts_dir = PARSED_DIR / doc_id
    images_dir = IMAGES_DIR / doc_id

    # 复用已有产物
    if not force and (artifacts_dir / "manifest.json").exists():
        return load_parsed_document(doc_id)

    # 复制源文件
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    dest = FILES_DIR / f"{doc_id}_{_safe_filename(pdf_path.name)}"
    shutil.copy2(pdf_path, dest)

    doc = pymupdf.open(pdf_path)
    try:
        page_count = doc.page_count
        entries = read_toc(doc.get_toc(simple=True), page_count)
        stage, actual_parser = _do_parse(
            doc,
            entries,
            page_count,
            parser=parser,
            strict_parser=strict_parser,
            api_key=api_key or settings.mineru_api_key,
            artifacts_dir=artifacts_dir,
            images_dir=images_dir,
            source_pdf=dest,
        )
    finally:
        doc.close()

    return assemble(
        stage,
        doc_id=doc_id,
        parser=actual_parser,
        source_name=pdf_path.name,
        source_path=f"files/{dest.name}",
        source_sha256=full_hash,
        source_bytes=len(content_bytes),
        artifacts_dir=artifacts_dir,
        images_dir=images_dir,
    )


def run_extract(
    target: str | Path,
    *,
    parser: str = "auto",
    force: bool = False,
    strict_parser: bool = False,
    api_key: str | None = None,
    caller=None,
) -> TenderDocument:
    """对已解析文档执行结构化抽取。

    `target` 可以是 PDF 路径（先解析）或已有 `doc_id`（从磁盘加载）。
    """
    path = Path(target)
    if path.exists():
        doc = run_parse(
            path,
            parser=parser,
            force=force,
            strict_parser=strict_parser,
            api_key=api_key,
        )
    else:
        doc = load_parsed_document(str(target))

    return run_extraction(doc, caller=caller, force=force)


def run_ingest(
    pdf_path: str | Path,
    *,
    parser: str = "auto",
    force: bool = False,
    strict_parser: bool = False,
    api_key: str | None = None,
    caller=None,
) -> IngestSummary:
    """端到端流水线：解析 + 抽取 + 入库，返回入库统计。"""
    doc = run_parse(
        pdf_path,
        parser=parser,
        force=force,
        strict_parser=strict_parser,
        api_key=api_key,
    )
    extraction = run_extraction(doc, caller=caller, force=force)
    return ingest_document(doc, project_name=extraction.project_name)