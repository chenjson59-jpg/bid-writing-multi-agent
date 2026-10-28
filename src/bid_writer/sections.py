"""章节级编辑、重写与锁定（P1-2）。

产物章节落盘为 `sections/<两位序号>-<章节 id>.md`，`sections/index.json` 记录
顺序、标题、生成计划、评审分数与锁定状态，是章节级操作的事实来源。

- 编辑：改写单个章节正文后重新合并 Markdown / Word / 报告；
- 重写：按原生成参数只重写指定章节（默认全部未锁定章节），锁定章节跳过；
- 锁定：锁定后的章节不再被批量重写覆盖。

旧产物（没有 `sections/`）会先按 `bid.md` + `outline.json` 反推补齐，因此历史
产物同样可编辑。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.bid_writer.assembly import assemble_markdown, build_report
from src.bid_writer.docx_export import markdown_to_docx
from src.bid_writer.schema import (
    BidderProfile,
    BidReport,
    ReviewResult,
    SectionDraft,
    SectionPlan,
    WriteOptions,
    extract_placeholders,
)
from src.errors import BidWriteError
from src.pipeline import BIDS_DIR, load_extraction

SECTIONS_DIRNAME = "sections"
INDEX_NAME = "index.json"

_SAFE_ID_RE = re.compile(r"[^0-9A-Za-z_-]+")


def _char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def sections_dir(bid_dir: Path) -> Path:
    return bid_dir / SECTIONS_DIRNAME


def _filename(plan: SectionPlan) -> str:
    return f"{plan.order:02d}-{_SAFE_ID_RE.sub('_', plan.id) or 'section'}.md"


def _body_path(bid_dir: Path, entry: dict[str, Any]) -> Path:
    name = str(entry.get("filename") or "")
    if not name or Path(name).name != name:
        raise BidWriteError(f"章节文件名非法：{name}")
    return sections_dir(bid_dir) / name


def _read_body(bid_dir: Path, entry: dict[str, Any]) -> str:
    path = _body_path(bid_dir, entry)
    if not path.is_file():
        raise BidWriteError(f"章节正文缺失：{entry.get('title') or entry.get('id')}")
    return path.read_text(encoding="utf-8")


def _write_body(bid_dir: Path, entry: dict[str, Any], markdown: str) -> None:
    path = _body_path(bid_dir, entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown.strip() + "\n", encoding="utf-8")


def _load_index(bid_dir: Path) -> list[dict[str, Any]]:
    path = sections_dir(bid_dir) / INDEX_NAME
    if not path.is_file():
        raise BidWriteError("该产物缺少章节落盘文件，无法继续")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_index(bid_dir: Path, entries: list[dict[str, Any]]) -> None:
    path = sections_dir(bid_dir) / INDEX_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _find(entries: list[dict[str, Any]], section_id: str) -> dict[str, Any]:
    for entry in entries:
        if entry.get("id") == section_id:
            return entry
    raise BidWriteError(f"章节不存在：{section_id}")


def write_sections(bid_dir: Path, drafts: list[SectionDraft]) -> None:
    """把章节正文落盘为独立 Markdown 并写 index（新产物生成时调用）。"""
    directory = sections_dir(bid_dir)
    directory.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for draft in sorted(drafts, key=lambda d: d.plan.order):
        name = _filename(draft.plan)
        (directory / name).write_text(draft.markdown.strip() + "\n", encoding="utf-8")
        entries.append(
            {
                "id": draft.plan.id,
                "title": draft.plan.title,
                "order": draft.plan.order,
                "filename": name,
                "strategy": draft.plan.strategy,
                "locked": False,
                "score_before": draft.score_before,
                "score_after": draft.score_after,
                "revisions": draft.revisions,
                "plan": draft.plan.to_dict(),
            }
        )
    _write_index(bid_dir, entries)


def ensure_sections(bid_dir: Path) -> list[dict[str, Any]]:
    """读取 index；旧产物没有 `sections/` 时按 `bid.md` + `outline.json` 反推补齐。"""
    index_path = sections_dir(bid_dir) / INDEX_NAME
    if index_path.is_file():
        return _load_index(bid_dir)

    # 延迟导入：supplement 反过来也依赖本模块的 write_sections
    from src.bid_writer.supplement import restore_drafts

    drafts, _report, _dir = restore_drafts(bid_dir.name)
    write_sections(bid_dir, drafts)
    return _load_index(bid_dir)


def load_drafts(bid_dir: Path) -> list[SectionDraft]:
    """按 index 顺序还原章节草稿（正文取磁盘文件）。"""
    drafts: list[SectionDraft] = []
    for entry in ensure_sections(bid_dir):
        plan = SectionPlan(**entry["plan"])
        drafts.append(
            SectionDraft(
                plan=plan,
                markdown=_read_body(bid_dir, entry),
                review=ReviewResult(score=int(entry.get("score_after") or 0)),
                score_before=int(entry.get("score_before") or 0),
                score_after=int(entry.get("score_after") or 0),
                revisions=int(entry.get("revisions") or 0),
            )
        )
    drafts.sort(key=lambda d: d.plan.order)
    return drafts


def section_infos(bid_dir: Path) -> list[dict[str, Any]]:
    """章节列表：顺序、锁定状态、字数与占位符数量。"""
    infos: list[dict[str, Any]] = []
    for entry in ensure_sections(bid_dir):
        body = _read_body(bid_dir, entry)
        infos.append(
            {
                "id": entry["id"],
                "title": entry["title"],
                "order": entry["order"],
                "strategy": entry.get("strategy") or "",
                "locked": bool(entry.get("locked")),
                "word_count": _char_count(body),
                "placeholder_count": len(extract_placeholders(body)),
                "score_before": int(entry.get("score_before") or 0),
                "score_after": int(entry.get("score_after") or 0),
                "revisions": int(entry.get("revisions") or 0),
            }
        )
    return infos


def section_detail(bid_dir: Path, section_id: str) -> dict[str, Any]:
    """单章详情：列表信息 + Markdown 正文（供编辑页回填）。"""
    entries = ensure_sections(bid_dir)
    entry = _find(entries, section_id)
    body = _read_body(bid_dir, entry)
    info = next(item for item in section_infos(bid_dir) if item["id"] == section_id)
    return {**info, "markdown": body}


def _load_report_data(bid_dir: Path) -> dict[str, Any]:
    path = bid_dir / "report.json"
    if not path.is_file():
        raise BidWriteError(f"产物不存在或是旧版本：{bid_dir.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _retriever(tender):
    """检索器不可用时按无检索处理，不阻断重写。"""
    try:
        from src.vectorstore.ingest import collection_name
        from src.vectorstore.retrieval import ChromaRetriever

        return ChromaRetriever(collection_name(tender.doc_type), doc_id=tender.doc_id)
    except Exception:  # noqa: BLE001 - 演示环境可能没有索引
        return None


def _search(retriever, query: str, top_k: int) -> list[dict[str, Any]]:
    if retriever is None:
        return []
    try:
        return retriever.search(query, top_k)
    except Exception:  # noqa: BLE001 - 检索失败按空结果处理
        return []


def rebuild_artifact(
    bid_dir: Path,
    *,
    llm_usage: dict[str, Any] | None = None,
    extra_warnings: list[str] | tuple[str, ...] = (),
) -> BidReport:
    """按磁盘章节重新合并 bid.md、导出 docx 并刷新 report.json。"""
    report_data = _load_report_data(bid_dir)
    doc_id = str(report_data.get("doc_id") or "")
    if not doc_id:
        raise BidWriteError("产物报告缺少 doc_id，无法刷新")

    tender = load_extraction(doc_id)
    drafts = load_drafts(bid_dir)
    company = str(report_data.get("bidder") or "")
    profile = BidderProfile(company_name=company)
    now = datetime.now()

    markdown = assemble_markdown(
        tender.project_name, company, drafts, now.strftime("%Y年%m月%d日")
    )
    report = build_report(doc_id, tender.project_name, company, drafts, tender, profile, now)

    # 保留首次生成信息与本次运行无关的字段
    report.generated_at = str(report_data.get("generated_at") or report.generated_at)
    report.updated_at = now.isoformat(timespec="seconds")
    report.missing_profile_fields = list(report_data.get("missing_profile_fields") or [])
    report.supplement_of = str(report_data.get("supplement_of") or "")
    report.write_options = dict(report_data.get("write_options") or {})
    report.llm_usage = (
        dict(llm_usage) if llm_usage is not None else dict(report_data.get("llm_usage") or {})
    )
    report.warnings = [
        str(w) for w in report_data.get("warnings") or [] if not str(w).startswith("Word 导出失败")
    ]
    report.warnings.extend(str(w) for w in extra_warnings)

    markdown_path = bid_dir / "bid.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    outline_path = bid_dir / "outline.json"
    outline_path.write_text(
        json.dumps([d.plan.to_dict() for d in drafts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    try:
        docx_path = bid_dir / "bid.docx"
        markdown_to_docx(markdown, docx_path)
        report.docx_path = str(docx_path)
    except Exception as exc:  # noqa: BLE001 - Word 导出失败不阻断 Markdown 与报告
        report.warnings.append(f"Word 导出失败：{exc}")

    report.output_dir = str(bid_dir)
    report.markdown_path = str(markdown_path)
    report.outline_path = str(outline_path)
    report_path = bid_dir / "report.json"
    report.report_path = str(report_path)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def save_section(
    bid_dir: Path,
    section_id: str,
    markdown: str | None = None,
    *,
    locked: bool | None = None,
) -> BidReport:
    """保存单章正文 / 切换锁定状态，并刷新整套产物。"""
    entries = ensure_sections(bid_dir)
    entry = _find(entries, section_id)
    if markdown is not None:
        _write_body(bid_dir, entry, markdown)
        entry["edited"] = True
    if locked is not None:
        entry["locked"] = bool(locked)
    _write_index(bid_dir, entries)

    notes = []
    if markdown is not None:
        notes.append(f"章节「{entry['title']}」已人工编辑")
    if locked is not None:
        notes.append(f"章节「{entry['title']}」已{'锁定' if locked else '解锁'}")
    return rebuild_artifact(bid_dir, extra_warnings=notes)


def rewrite_sections(
    bid_id: str,
    section_ids: list[str] | None = None,
    *,
    caller=None,
) -> BidReport:
    """按原参数重写指定章节（默认全部未锁定章节），锁定章节保持不变。"""
    from src.bid_writer.supplement import _load_profile

    bid_dir = BIDS_DIR / bid_id
    report_data = _load_report_data(bid_dir)
    entries = ensure_sections(bid_dir)

    if section_ids:
        targets = [_find(entries, section_id) for section_id in section_ids]
    else:
        targets = list(entries)
    runnable = [entry for entry in targets if not entry.get("locked")]
    if not runnable:
        raise BidWriteError("所选章节均已锁定，无法重写")
    skipped = len(targets) - len(runnable)

    from src.bid_writer.agents import default_caller, section_agent
    from src.bid_writer.usage import default_meter

    tender = load_extraction(str(report_data.get("doc_id") or ""))
    profile = _load_profile(str(report_data.get("bidder") or ""))
    options = WriteOptions.from_dict(report_data.get("write_options") or {})
    retriever = _retriever(tender)
    meter = default_meter()
    metered = meter.wrap(caller or default_caller())

    for entry in runnable:
        plan = SectionPlan(**entry["plan"])
        draft = section_agent(
            plan,
            tender,
            profile,
            _search(retriever, plan.retrieval_query or plan.title, options.top_k),
            min_score=options.min_score,
            max_revisions=options.max_revisions,
            caller=metered,
        )
        _write_body(bid_dir, entry, draft.markdown)
        entry.update(
            score_before=draft.score_before,
            score_after=draft.score_after,
            revisions=draft.revisions,
            edited=False,
        )
    _write_index(bid_dir, entries)

    notes = [f"本次重写 {len(runnable)} 章，llm_usage 仅统计本次调用"]
    if skipped:
        notes.append(f"跳过 {skipped} 个锁定章节")
    return rebuild_artifact(bid_dir, llm_usage=meter.summary(), extra_warnings=notes)


__all__ = [
    "ensure_sections",
    "load_drafts",
    "rebuild_artifact",
    "rewrite_sections",
    "save_section",
    "section_detail",
    "section_infos",
    "write_sections",
]
