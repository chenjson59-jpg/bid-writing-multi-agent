"""抽取 Schema 与宽容字段归一化（核心算法 A11）。

模型输出常出现：列表给 `null`、分值写 `"30分"`、布尔写 `"是/否"`、
类别写近义词、基础字段包在 `basic`/`info`/`project` 子对象中。

本模块把任意模型输出归一化为规范字典（`ChapterExtraction` 结构），
非法字段丢弃、可救字段保留，并返回归一化过程中的警告。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# ---- 布尔真值 / 假值集合（A11.5.3）----
TRUE_VALUES = {"是", "true", "yes", "y", "1", "需要", "必须", "须", "要求"}
FALSE_VALUES = {"否", "false", "no", "n", "0", "无需", "不需要", ""}

# ---- 类别关键词（A11.5.4），顺序即匹配优先级 ----
QUALIFICATION_CATEGORIES = ("资质", "财务", "业绩", "信誉", "人员")
EVALUATION_CATEGORIES = ("形式评审", "资格评审", "响应性评审", "商务", "技术", "报价")

# 基础字段可能被包在这些子对象里（A11.5.5）
BASIC_CONTAINERS = ("basic", "info", "project", "basic_info", "project_info")

BASIC_SCALAR_KEYS = (
    "project_name",
    "tender_no",
    "budget_amount",
    "bid_deadline",
    "bid_open_place",
)

NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def normalize_number(value: Any) -> float | None:
    """数值归一化（A11.5.2）：`"30分"` → `30.0`，非法 → `None`。"""
    if value is None or isinstance(value, bool) or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = NUMBER_RE.search(str(value))
    return float(match.group(0)) if match else None


def normalize_bool(value: Any) -> bool | None:
    """布尔归一化（A11.5.3）：真值/假值集合，其他 → `None`。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def normalize_str_list(value: Any) -> list[str]:
    """字符串列表归一化（A11.5.1）：`None`→`[]`，标量→单元素，过滤 `None`。"""
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(v) for v in values if v is not None and str(v).strip() != ""]


def as_list(value: Any) -> list[Any]:
    """把值归一化为列表（保留元素类型），供字典列表字段使用。"""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def normalize_category(value: Any, categories: tuple[str, ...]) -> str:
    """类别归一化（A11.5.4）：目标串中命中第一个类别关键词，否则 `其他`。"""
    if value is None:
        return "其他"
    text = str(value)
    for category in categories:
        if category in text:
            return category
    return "其他"


def _first_scalar(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """在 dict 内按顺序取第一个非空标量。"""
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _normalize_factor(value: Any) -> dict[str, Any] | None:
    """归一化评分因素，缺 `name` 时丢弃整个条目。"""
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    if not name:
        return None
    return {
        "category": normalize_category(value.get("category"), EVALUATION_CATEGORIES),
        "name": name,
        "score": normalize_number(value.get("score")),
        "source_quote": str(value.get("source_quote") or ""),
    }


def _normalize_qualification(value: Any) -> dict[str, Any] | None:
    """归一化资格条件，缺 `requirement` 时丢弃。"""
    if not isinstance(value, dict):
        return None
    requirement = str(value.get("requirement") or "").strip()
    if not requirement:
        return None
    return {
        "category": normalize_category(value.get("category"), QUALIFICATION_CATEGORIES),
        "requirement": requirement,
        "source_quote": str(value.get("source_quote") or ""),
        "thresholds": normalize_str_list(value.get("thresholds")),
    }


def _normalize_format_item(value: Any) -> dict[str, Any] | None:
    """归一化投标文件格式条目，缺 `title` 时丢弃。"""
    if not isinstance(value, dict):
        return None
    title = str(value.get("title") or "").strip()
    if not title:
        return None
    return {"title": title, "content": str(value.get("content") or "")}


def _normalize_score_row(value: Any) -> dict[str, Any] | None:
    """归一化评分表行，缺 `factor` 时丢弃。"""
    if not isinstance(value, dict):
        return None
    factor = str(value.get("factor") or "").strip()
    if not factor:
        return None
    return {
        "factor": factor,
        "score": str(value.get("score") or ""),
        "comment": str(value.get("comment") or ""),
    }


def normalize_chapter_extraction(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """把一次模型输出归一化为规范 `ChapterExtraction` 字典。

    返回 `(规范字典, 警告列表)`；只保留 schema 已声明字段，
    非法子条目丢弃（A11.5.5 字段级降级）。
    """
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return _empty_extraction(), ["模型返回非 JSON 对象，已按空结果处理"]

    # 1. 展开 basic/info/project 子对象，提取基础标量字段
    basic: dict[str, Any] = {}
    top_basic: dict[str, Any] = {}
    for key in BASIC_CONTAINERS:
        if isinstance(raw.get(key), dict):
            top_basic.update(raw[key])
    for key in BASIC_SCALAR_KEYS:
        value = _first_scalar(raw, (key,)) if key in raw else None
        if value is None:
            value = top_basic.get(key)
        basic[key] = str(value).strip() if value not in (None, "") else ""

    # 2. evaluation 块
    evaluation = raw.get("evaluation") if isinstance(raw.get("evaluation"), dict) else {}
    factors = [
        _normalize_factor(f) for f in as_list(evaluation.get("factors")) if f is not None
    ]
    factors = [f for f in factors if f is not None]
    score_table = [
        r
        for r in (_normalize_score_row(row) for row in as_list(evaluation.get("score_table")))
        if r is not None
    ]
    preliminary = normalize_str_list(evaluation.get("preliminary_review"))

    # 3. qualification 块
    qualification = raw.get("qualification") if isinstance(raw.get("qualification"), dict) else {}
    items = [_normalize_qualification(q) for q in as_list(qualification.get("items")) if q is not None]
    items = [q for q in items if q is not None]

    # 4. bid_format 块
    bid_format = raw.get("bid_format") if isinstance(raw.get("bid_format"), dict) else {}
    bid_items = [
        i
        for i in (_normalize_format_item(x) for x in as_list(bid_format.get("items")))
        if i is not None
    ]

    # 5. contract / tech 块
    contract = raw.get("contract") if isinstance(raw.get("contract"), dict) else {}
    tech = raw.get("tech") if isinstance(raw.get("tech"), dict) else {}
    highlights = normalize_str_list(contract.get("highlights"))
    requirements = normalize_str_list(tech.get("requirements"))

    return {
        "basic": basic,
        "evaluation": {
            "factors": factors,
            "preliminary_review": preliminary,
            "score_table": score_table,
        },
        "qualification": {"items": items},
        "bid_format": {"items": bid_items},
        "contract": {"highlights": highlights},
        "tech": {"requirements": requirements},
    }, warnings


def _empty_extraction() -> dict[str, Any]:
    """空 `ChapterExtraction` 结构。"""
    return {
        "basic": {key: "" for key in BASIC_SCALAR_KEYS},
        "evaluation": {"factors": [], "preliminary_review": [], "score_table": []},
        "qualification": {"items": []},
        "bid_format": {"items": []},
        "contract": {"highlights": []},
        "tech": {"requirements": []},
    }


@dataclass
class TenderDocument:
    """结构化抽取的最终结果（核心算法 6.11）。

    扁平字段供后续投标编写阶段直接消费；`extraction_meta` 记录模型、提示词
    版本、解析器、耗时、调用数、未核实字段与警告。
    """

    doc_id: str
    doc_type: str
    project_name: str = ""
    tender_no: str = ""
    budget_amount: str = ""
    bid_deadline: str = ""
    bid_open_place: str = ""
    evaluation_factors: list[dict[str, Any]] = field(default_factory=list)
    preliminary_review: list[str] = field(default_factory=list)
    score_table: list[dict[str, Any]] = field(default_factory=list)
    qualifications: list[dict[str, Any]] = field(default_factory=list)
    bid_format_items: list[dict[str, Any]] = field(default_factory=list)
    contract_highlights: list[str] = field(default_factory=list)
    technical_requirements: list[str] = field(default_factory=list)
    extraction_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 `extraction.json` 的内容。"""
        return asdict(self)