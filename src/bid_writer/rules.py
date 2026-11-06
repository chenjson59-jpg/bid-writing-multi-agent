"""确定性规则检查（T-013，AGENTS.md 5.4）。

纯函数、零模型调用、不读文件不联网：引用有效性、报价禁令与占位符事实由
规则判定，输出与 LLM Reviewer 的语义评分完全分离。规则码保持稳定，供测试
与前端（T-017）直接引用。
"""
from __future__ import annotations

import re
from typing import Any

from src.bid_writer.schema import RuleCheckResult, SectionPlan, extract_placeholders

# 规则码
CITE_EMPTY = "CITE_EMPTY"
CITE_OUT_OF_RANGE = "CITE_OUT_OF_RANGE"
CITE_DOC_MISMATCH = "CITE_DOC_MISMATCH"
QUOTE_FORBIDDEN = "QUOTE_FORBIDDEN"

# 仅匹配括号内纯数字的引用标记，Markdown 链接 [文字](url) 不会命中
_CITATION_RE = re.compile(r"\[(\d+)\]")
# 货币符号紧贴数字：¥1200000、￥ 100
_CURRENCY_RE = re.compile(r"[¥￥]\s*\d")
# 数字 + 金额单位；\b 排除「元素」等中文组合，年月日不受影响
_MONEY_UNIT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万元|元)\b")
# 决策关键词 12 字内出现百分比：让利 5%、报价下浮 3.5%
_DISCOUNT_RE = re.compile(
    r"(?:让利|下浮|优惠|报价|单价)[^。；;\n]{0,12}?\d+(?:\.\d+)?\s*%"
)


def check_section_rules(
    markdown: str,
    plan: SectionPlan,
    retrieved: list[dict[str, Any]],
    *,
    doc_id: str = "",
) -> RuleCheckResult:
    """对单章节正文执行确定性规则检查。

    `retrieved` 为本章节实际传入写作提示词的片段，`doc_id` 为当前招标文档标识；
    缺失任一前提时对应检查跳过，不臆造错误。
    """
    markers = _citation_markers(markdown)
    numbers = sorted({int(marker.strip("[]")) for marker in markers})
    errors: list[dict[str, str]] = []

    if markers and not retrieved:
        errors.append(
            {
                "code": CITE_EMPTY,
                "message": f"无检索结果却出现引用标记：{'、'.join(markers)}",
            }
        )
    elif markers:
        out_of_range = [n for n in numbers if n < 1 or n > len(retrieved)]
        if out_of_range:
            errors.append(
                {
                    "code": CITE_OUT_OF_RANGE,
                    "message": (
                        f"引用编号越界：{'、'.join(f'[{n}]' for n in out_of_range)}，"
                        f"本轮仅提供 {len(retrieved)} 条片段"
                    ),
                }
            )
        mismatches = _doc_mismatches(retrieved, doc_id)
        if mismatches:
            errors.append(
                {
                    "code": CITE_DOC_MISMATCH,
                    "message": (
                        f"检索片段不属于当前文档 {doc_id}：{'、'.join(mismatches)}"
                    ),
                }
            )

    quote_hits = _quote_hits(markdown)
    if quote_hits:
        errors.append(
            {
                "code": QUOTE_FORBIDDEN,
                "message": f"正文出现被禁止的具体报价/让利/单价：{'、'.join(quote_hits)}",
            }
        )

    return RuleCheckResult(
        section_id=plan.id,
        passed=not errors,
        citation_markers=markers,
        errors=errors,
        placeholders=extract_placeholders(markdown),
    )


def _citation_markers(markdown: str) -> list[str]:
    """提取去重并按编号升序排列的引用标记。"""
    numbers: set[int] = set()
    for match in _CITATION_RE.finditer(markdown):
        numbers.add(int(match.group(1)))
    return [f"[{n}]" for n in sorted(numbers)]


def _chunk_doc_id(chunk: dict[str, Any]) -> str:
    """取片段所属 doc_id：优先 metadata，缺失时从 `{doc_id}:{index}` id 前缀推断。"""
    metadata = chunk.get("metadata") or {}
    meta_doc_id = metadata.get("doc_id")
    if meta_doc_id:
        return str(meta_doc_id)
    chunk_id = chunk.get("id")
    if isinstance(chunk_id, str) and ":" in chunk_id:
        prefix = chunk_id.split(":", 1)[0]
        if prefix:
            return prefix
    return ""


def _doc_mismatches(retrieved: list[dict[str, Any]], expected_doc_id: str) -> list[str]:
    """返回不属于当前文档的片段标识（去重）；无法识别或未传 doc_id 时跳过。"""
    if not expected_doc_id:
        return []
    mismatches: list[str] = []
    for chunk in retrieved:
        actual = _chunk_doc_id(chunk)
        if actual and actual != expected_doc_id:
            label = str(chunk.get("id") or actual)
            if label not in mismatches:
                mismatches.append(label)
    return mismatches


def _quote_hits(markdown: str) -> list[str]:
    """收集报价禁令命中的原文片段（跨模式去重，保留出现顺序）。"""
    hits: list[str] = []
    for pattern in (_CURRENCY_RE, _MONEY_UNIT_RE, _DISCOUNT_RE):
        for match in pattern.finditer(markdown):
            token = match.group(0).strip()
            if token not in hits:
                hits.append(token)
    return hits
