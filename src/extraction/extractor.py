"""结构化抽取主流程（docs/algorithms.md A9、A12、A13、A14）。

按章 map-reduce：把 `ParsedDocument` 切成 ≤12000 字的抽取单元，
逐单元调用模型产出部分结果，合并、溯源核对后落盘 `extraction.json`。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.config import settings
from src.errors import ExtractError
from src.extraction.prompts import PROMPT_VERSION, build_prompt, focus_for_chapter
from src.extraction.schema import (
    TenderDocument,
    normalize_chapter_extraction,
)
from src.parsing.models import ParsedDocument, Section, TocEntry
from src.parsing.toc import (
    ancestor_titles,
    chapter_indices,
    subtree_range,
    subtree_text,
)

MAX_CHARS_PER_CALL = 12000  # A9 单次抽取最大字符数
MIN_BISECT_CHARS = 1200     # A14 截断二分最小长度
MAX_BISECT_DEPTH = 2        # A14 二分最大深度

_LIST_DEDUP_KEYS = ("title", "name", "factor", "topic", "order")

_GROUND_SCALAR_FIELDS = {
    "project_name": "项目名称",
    "tender_no": "招标编号",
    "budget_amount": "预算金额",
    "bid_deadline": "投标截止时间",
    "bid_open_place": "开标地点",
}


@dataclass(frozen=True)
class Unit:
    """一个抽取单元：标题、祖先路径与正文文本。"""
    title: str
    path: list[str]
    text: str


@dataclass
class ExtractStats:
    """抽取统计：模型调用数与是否全程使用 JSON 模式。"""
    calls: int = 0
    json_mode_used: bool = True


class ModelCaller(Protocol):
    """模型调用协议，测试时注入 fake 实现以隔离网络。"""

    def generate(self, messages: list[dict], json_mode: bool) -> dict[str, Any]:
        """返回 `{"content", "finish_reason", "json_mode_used"}`。"""
        ...


# ---------------------------------------------------------------------------
# A9 单元切分
# ---------------------------------------------------------------------------

def split_by_blank_lines(text: str) -> list[str]:
    """按空行切分段落。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _hard_slice(unit: Unit, paragraph: str, limit: int) -> list[Unit]:
    """单段超长时按固定长度硬切（A9.6.2.3）。"""
    return [
        Unit(unit.title, unit.path, paragraph[i : i + limit])
        for i in range(0, len(paragraph), limit)
    ]


def _pack_paragraphs(unit: Unit, limit: int) -> list[Unit]:
    """段落打包：优先保持段落完整，单段超长硬切（A9.6.2.3）。"""
    paragraphs = split_by_blank_lines(unit.text)
    units: list[Unit] = []
    buffer: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buffer, size
        if buffer:
            units.append(Unit(unit.title, unit.path, "\n\n".join(buffer)))
        buffer, size = [], 0

    for paragraph in paragraphs:
        if len(paragraph) > limit:
            flush()
            units.extend(_hard_slice(unit, paragraph, limit))
            continue
        if size + len(paragraph) > limit and buffer:
            flush()
        buffer.append(paragraph)
        size += len(paragraph)

    flush()
    return units


def _units_for_chapter(
    sections: list[Section], entries: list[TocEntry], index: int, limit: int
) -> list[Unit]:
    """按“第X章”节点递归细分（A9.6.2.2）。"""
    entry = entries[index]
    own_text = sections[index].text if index < len(sections) else ""
    full_text = subtree_text(sections, entries, index)
    path = ancestor_titles(entries, index)
    start, end = subtree_range(entries, index)
    children = [j for j in range(start, end) if entries[j].level == entry.level + 1]

    if len(full_text) <= limit:
        return [Unit(entry.title, path, full_text)] if full_text.strip() else []

    units: list[Unit] = []
    if own_text.strip():
        units.extend(_pack_paragraphs(Unit(entry.title, path, own_text), limit))
    for child in children:
        units.extend(_units_for_chapter(sections, entries, child, limit))
    if not own_text.strip() and not children:
        units.extend(_pack_paragraphs(Unit(entry.title, path, full_text), limit))
    return units


def build_units(
    sections: list[Section], entries: list[TocEntry], markdown: str = ""
) -> list[Unit]:
    """把文档切成抽取单元（A9）。"""
    chapters = chapter_indices(entries)
    if chapters:
        units: list[Unit] = []
        for index in chapters:
            units.extend(
                _units_for_chapter(sections, entries, index, MAX_CHARS_PER_CALL)
            )
        if units:
            return units

    full = markdown or "\n\n".join(s.text for s in sections if s.text)
    if not full.strip():
        raise ExtractError("文档正文为空，无法抽取")
    return _pack_paragraphs(Unit("全文", [], full), MAX_CHARS_PER_CALL)


# ---------------------------------------------------------------------------
# A14 截断二分 + JSON 调用容错
# ---------------------------------------------------------------------------

def extract_json(text: str) -> Any:
    """从模型返回文本中提取 JSON 对象（docs/algorithms.md 6.4.2）。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stripped[start : end + 1])
    raise ExtractError("未找到合法 JSON 对象")


def _call_and_parse(
    unit: Unit, caller: ModelCaller, stats: ExtractStats
) -> list[dict[str, Any]]:
    """调用模型并解析为规范 `ChapterExtraction` 列表（单元素）。"""
    messages = build_prompt(unit.path, unit.title, unit.text)
    stats.calls += 1
    result = caller.generate(messages, json_mode=True)
    stats.json_mode_used = stats.json_mode_used and bool(result.get("json_mode_used"))

    finish_reason = result.get("finish_reason")
    if finish_reason == "length":
        raise ExtractError("输出被 max_tokens 截断")
    content = (result.get("content") or "").strip()
    if not content:
        raise ExtractError("返回空内容")

    raw = extract_json(content)
    normalized, _warnings = normalize_chapter_extraction(raw)
    return [normalized]


def _is_empty_for_focus(partial: dict[str, Any], focus: str) -> bool:
    """判断单元结果是否为空：评标重点只看 evaluation 块，其余看全字段。"""
    if focus == "evaluation":
        block = partial.get("evaluation", {})
        return not any(
            block.get(key) for key in ("factors", "preliminary_review", "score_table")
        )
    return not any(
        value
        for block in partial.values()
        if isinstance(block, dict)
        for value in block.values()
    )


def _extract_unit(
    unit: Unit, caller: ModelCaller, stats: ExtractStats, depth: int = 0
) -> list[dict[str, Any]]:
    """抽取单个单元，输出被截断时二分递归重试（A14）。

    模型偶发返回全空结果时重试一次，避免整章字段丢失。
    """
    try:
        result = _call_and_parse(unit, caller, stats)
        focus = focus_for_chapter(unit.path, unit.title)
        if result and unit.text.strip() and _is_empty_for_focus(result[0], focus):
            result = _call_and_parse(unit, caller, stats)
        return result
    except ExtractError as exc:
        if (
            "截断" in str(exc)
            and len(unit.text) >= MIN_BISECT_CHARS
            and depth < MAX_BISECT_DEPTH
        ):
            middle = len(unit.text) // 2
            upper = _extract_unit(
                Unit(unit.title + "（上）", unit.path, unit.text[:middle]),
                caller, stats, depth + 1,
            )
            lower = _extract_unit(
                Unit(unit.title + "（下）", unit.path, unit.text[middle:]),
                caller, stats, depth + 1,
            )
            return upper + lower
        raise


# ---------------------------------------------------------------------------
# A12 结果合并
# ---------------------------------------------------------------------------

def _list_key(item: Any, key_order: tuple[str, ...]) -> str:
    """列表条目的去重键：取第一个非空键值，否则整条 JSON 规范化串。"""
    if isinstance(item, dict):
        for key in key_order:
            value = item.get(key)
            if value not in (None, "", [], {}):
                return f"{key}={json.dumps(value, ensure_ascii=False, sort_keys=True)}"
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def merge_list(
    base: list[Any], extra: list[Any], key_order: tuple[str, ...] = _LIST_DEDUP_KEYS
) -> list[Any]:
    """列表合并（A12.6.2）：按去重键只追加未出现项。"""
    result = list(base)
    seen = {_list_key(item, key_order) for item in result}
    for item in extra:
        key = _list_key(item, key_order)
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def merge_dict(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """字典递归合并（A12.6.6.4）：标量先到先得，列表/字典递归。"""
    for key, value in extra.items():
        if value is None:
            continue
        if isinstance(value, list):
            base[key] = merge_list(base.get(key) or [], value)
        elif isinstance(value, dict):
            base[key] = merge_dict(base.get(key) or {}, value)
        elif base.get(key) in (None, "", [], {}):
            base[key] = value
    return base


def merge_partials(partials: list[dict[str, Any]]) -> dict[str, Any]:
    """合并所有部分结果（A12）。"""
    merged: dict[str, Any] = {}
    for partial in partials:
        merged = merge_dict(merged, partial)
    return merged


# 投标格式模板标题：带「实质性格式」后缀的标题，或独立命名的模板/表格
_FORMAT_SUFFIX_RE = re.compile(
    r"^.{2,40}（(?:非)?实质性格式）$"
)
_FORMAT_NAME_RE = re.compile(
    r"^(?:附：)?(?:[一二三四五六七八九十]+、)?(?:\d+(?:-\d+)*[、.\s]+)?"
    r"(?:投标书|授权委托书|开标一览表|投标分项报价表|合同条款偏离表|"
    r"采购需求偏离表|关于符合本国产品标准的声明函|产品成本占比承诺函|"
    r"中小企业声明函（货物）格式|残疾人福利性单位声明函格式|"
    r"投标人资格声明书|投标人本项目主要管理、服务人员情况表|"
    r"投标人类似项目业绩一览表)$"
)
_FORMAT_ATTACH_RE = re.compile(r"^附：[^，。；：]{2,20}(?:身份证明|证明文件)$")
# 编号行且以格式关键词结尾（章节级格式分组，如「7 本国产品标准证明文件（如有）」）
_FORMAT_GROUP_RE = re.compile(
    r"^(?:附：)?(?:[一二三四五六七八九十]+、|\d+(?:-\d+)*[、.\s]+)"
    r"[^，。；：]{2,55}"
    r"(?:格式|模板|表格|声明函|承诺函|证明文件|其他材料|封面)"
    r"(?:（如有）|（格式自拟）|（如[^）]*）)?$"
)
# 章节级分组标题（如「一、资格证明文件格式」），不是具体投标模板
_GROUP_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、.{0,20}格式$")


def _format_title_key(title: str) -> str:
    """模板标题归一化键：去掉编号、「附：」与「格式」后缀，用于去重。"""
    key = title.strip()
    key = re.sub(r"^附：", "", key)
    key = re.sub(r"^(?:[一二三四五六七八九十]+、|\d+(?:-\d+)*[、.\s]+)", "", key)
    key = re.sub(r"（(?:非)?实质性格式）$", "", key)
    key = re.sub(r"格式$", "", key)
    return key.strip()


def _scan_bid_format_titles(text: str) -> list[dict[str, Any]]:
    """从「投标文件格式」章节正文扫描独立模板标题。

    模型对长章节常只概括出 1 条，这里按模板标题行确定性补齐，
    保证格式条目与原文模板数量一致。
    """
    titles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 60 or _GROUP_HEADING_RE.match(line):
            continue
        matched = (
            _FORMAT_SUFFIX_RE.match(line)
            or _FORMAT_NAME_RE.match(line)
            or _FORMAT_ATTACH_RE.match(line)
        )
        if not matched:
            matched = _FORMAT_GROUP_RE.match(line) is not None
        if not matched:
            continue
        key = _format_title_key(line)
        if not key or key in seen:
            continue
        seen.add(key)
        titles.append({"title": line})
    return titles


def _drop_misplaced_bid_format(
    partials: list[dict[str, Any]], path: list[str], title: str
) -> list[dict[str, Any]]:
    """非投标格式章节中误填的 `bid_format.items` 清空，避免资格条款变成格式章节。"""
    breadcrumb = " ".join([*path, title])
    if focus_for_chapter(path, title) == "bid_format" or "投标文件格式" in breadcrumb:
        return partials
    for partial in partials:
        block = partial.get("bid_format")
        if isinstance(block, dict) and block.get("items"):
            block["items"] = []
    return partials


def _ensure_bid_format_items(
    partials: list[dict[str, Any]], path: list[str], title: str, text: str = ""
) -> list[dict[str, Any]]:
    """投标格式补齐（docs/algorithms.md 6.8）：空白模板按标题兜底，再从正文扫描模板。

    条件：章节路径含「投标文件格式」。返回新列表（深拷贝首个元素），
    不改动传入对象。
    """
    if "投标文件格式" not in " ".join([*path, title]):
        return partials

    scanned = _scan_bid_format_titles(text)
    if not partials:
        return [{"bid_format": {"items": scanned}}] if scanned else partials

    result = copy.deepcopy(partials)
    items = result[0].setdefault("bid_format", {}).setdefault("items", [])
    seen = {
        _format_title_key(item.get("title", ""))
        for item in items
        if isinstance(item, dict) and item.get("title")
    }
    for entry in scanned:
        key = _format_title_key(entry["title"])
        if key and key not in seen:
            items.append(entry)
            seen.add(key)
    if not items and not title.startswith("第八章"):
        items.append({"title": title})
    return result


_BUDGET_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(万元|元)")


def _normalize_budget(value: Any) -> str:
    """预算金额归一：从「项目预算金额：300 万元、最高限价：300 万元」取「300万元」。"""
    text = str(value or "").strip()
    if not text:
        return ""
    match = _BUDGET_RE.search(text)
    if not match:
        return text
    return f"{match.group(1)}{match.group(2)}"


def _flatten(merged: dict[str, Any]) -> dict[str, Any]:
    """把嵌套块结构展平为 TenderDocument 扁平字段。"""
    basic = merged.get("basic", {})
    evaluation = merged.get("evaluation", {})
    qualification = merged.get("qualification", {})
    bid_format = merged.get("bid_format", {})
    contract = merged.get("contract", {})
    tech = merged.get("tech", {})
    service_spec = merged.get("service_spec", {})
    return {
        "project_name": basic.get("project_name", ""),
        "tender_no": basic.get("tender_no", ""),
        "budget_amount": _normalize_budget(basic.get("budget_amount", "")),
        "bid_deadline": basic.get("bid_deadline", ""),
        "bid_open_place": basic.get("bid_open_place", ""),
        "evaluation_factors": evaluation.get("factors", []),
        "preliminary_review": evaluation.get("preliminary_review", []),
        "score_table": evaluation.get("score_table", []),
        "qualifications": qualification.get("items", []),
        "bid_format_items": bid_format.get("items", []),
        "contract_highlights": contract.get("highlights", []),
        "technical_requirements": tech.get("requirements", []),
        "service_requirements": service_spec.get("requirements", []),
    }


# ---------------------------------------------------------------------------
# A13 溯源核对
# ---------------------------------------------------------------------------

def _normalize_for_ground(text: Any) -> str:
    """去掉所有空白、竖线 `|` 与全角竖线 `｜`（A13.6.7.1）。"""
    return re.sub(r"[\s|｜]", "", str(text or ""))


def ground_check(doc: dict[str, Any], full_text: str) -> list[str]:
    """核对关键字段是否能在原文逐字找到，返回未核实字段清单（A13）。"""
    norm_full = _normalize_for_ground(full_text)
    ungrounded: list[str] = []

    for field_key, label in _GROUND_SCALAR_FIELDS.items():
        value = doc.get(field_key, "")
        if value and _normalize_for_ground(value) not in norm_full:
            ungrounded.append(label)

    for i, factor in enumerate(doc.get("evaluation_factors", [])):
        quote = factor.get("source_quote", "")
        if quote and _normalize_for_ground(quote) not in norm_full:
            ungrounded.append(f"评分因素[{i}]")

    for i, qual in enumerate(doc.get("qualifications", [])):
        quote = qual.get("source_quote", "")
        thresholds = " ".join(qual.get("thresholds", []))
        combined = f"{quote}{thresholds}"
        if quote and _normalize_for_ground(combined) not in norm_full:
            ungrounded.append(f"资格条件[{i}]")

    return ungrounded


# ---------------------------------------------------------------------------
# 编排：缓存 + 遍历 + 合并 + 核对 + 落盘
# ---------------------------------------------------------------------------

def _content_sha256(doc: ParsedDocument) -> str:
    """正文内容哈希：正文变化必须使抽取缓存失效。"""
    return hashlib.sha256(_full_text(doc).encode("utf-8")).hexdigest()


def _cache_tag(doc: ParsedDocument) -> str:
    """单元缓存标签：解析器 + 提示词版本 + 正文哈希（A13 缓存失效）。"""
    return f"{doc.parser}|{PROMPT_VERSION}|{_content_sha256(doc)[:16]}"


def _prepare_cache(parts_dir: Path, doc: ParsedDocument, force: bool = False) -> None:
    """准备单元缓存目录，标签过期或强制重跑时清空旧缓存。"""
    parts_dir.mkdir(parents=True, exist_ok=True)
    tag_path = parts_dir / "cache_tag.txt"
    tag = _cache_tag(doc)
    stale = tag_path.exists() and tag_path.read_text(encoding="utf-8") != tag
    if force or stale:
        for f in parts_dir.glob("*.json"):
            f.unlink()
    tag_path.write_text(tag, encoding="utf-8")


def _load_cached_extraction(output_dir: Path, doc: ParsedDocument) -> TenderDocument | None:
    """整篇缓存复用（A13.6.10.3）：解析器、提示词版本与正文哈希一致才复用。"""
    path = output_dir / "extraction.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("extraction_meta", {})
    if (
        meta.get("parser") == doc.parser
        and meta.get("prompt_version") == PROMPT_VERSION
        and meta.get("content_sha256") == _content_sha256(doc)
    ):
        document = TenderDocument(**{k: v for k, v in data.items()})
        document.extraction_meta["cache_hit"] = True
        return document
    return None


def _full_text(doc: ParsedDocument) -> str:
    """抽取与核对用的全文，优先内存中的 markdown，其次读 content.md。"""
    if doc.markdown:
        return doc.markdown
    path = Path(doc.markdown_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "\n\n".join(s.text for s in doc.sections if s.text)


def run_extraction(
    doc: ParsedDocument,
    *,
    caller: ModelCaller | None = None,
    output_dir: Path | None = None,
    force: bool = False,
) -> TenderDocument:
    """对已解析文档执行结构化抽取并落盘 `extraction.json`。

    `caller` 为 None 时使用 DeepSeek；测试注入 fake 实现。
    """
    output_dir = output_dir or Path(doc.artifacts_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not force:
        cached = _load_cached_extraction(output_dir, doc)
        if cached is not None:
            return cached

    if caller is None:
        caller = DeepSeekModelCaller()

    units = build_units(doc.sections, doc.toc, doc.markdown)
    parts_dir = output_dir / "extraction_parts"
    _prepare_cache(parts_dir, doc, force=force)

    start = time.monotonic()
    stats = ExtractStats()
    partials: list[dict[str, Any]] = []
    warnings: list[str] = []

    for i, unit in enumerate(units):
        cache_path = parts_dir / f"{i:03d}.json"
        if cache_path.exists():
            try:
                partials.extend(json.loads(cache_path.read_text(encoding="utf-8")))
                continue
            except (json.JSONDecodeError, TypeError):
                pass  # 缓存损坏，重新抽取

        try:
            result = _extract_unit(unit, caller, stats)
            result = _drop_misplaced_bid_format(result, unit.path, unit.title)
            result = _ensure_bid_format_items(result, unit.path, unit.title, unit.text)
            partials.extend(result)
            cache_path.write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
        except ExtractError as exc:
            warnings.append(f"单元「{unit.title}」抽取失败：{exc}")

    if not partials:
        raise ExtractError("所有抽取单元均失败")

    merged = merge_partials(partials)
    flat = _flatten(merged)
    ungrounded = ground_check(flat, _full_text(doc))

    document = TenderDocument(
        doc_id=doc.doc_id,
        doc_type=doc.doc_type,
        **flat,
        extraction_meta={
            "model": settings.deepseek_model,
            "prompt_version": PROMPT_VERSION,
            "parser": doc.parser,
            "content_sha256": _content_sha256(doc),
            "cache_hit": False,
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "calls": stats.calls,
            "json_mode_used": stats.json_mode_used,
            "ungrounded_fields": ungrounded,
            "warnings": warnings,
        },
    )

    (output_dir / "extraction.json").write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return document


# ---------------------------------------------------------------------------
# DeepSeek 默认调用实现（测试通过注入 fake 绕过）
# ---------------------------------------------------------------------------

class DeepSeekModelCaller:
    """用 openai SDK 调用 DeepSeek 文本模型（关闭思考链）。"""

    def __init__(self) -> None:
        from openai import OpenAI  # 延迟导入，测试无需真实依赖

        self._client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.llm_timeout,
        )
        self._model = settings.deepseek_model

    def generate(self, messages: list[dict], json_mode: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if settings.llm_thinking == "disabled":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        return {
            "content": choice.message.content,
            "finish_reason": choice.finish_reason,
            "json_mode_used": json_mode,
            "model": self._model,
            "usage": {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        }