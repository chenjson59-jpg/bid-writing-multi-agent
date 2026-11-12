"""全局事实表（T-029）。

从既有 ``TenderDocument`` 抽取结果**确定性**构建强约束事实（预算、保证金、
工期、有效期、项目名称、项目编号），供 ``rules.check_fact_consistency`` 做
跨章节数字口径校验。零模型调用、零 IO、零网络，全部为纯函数。

设计要点：

- 事实抽取端与规则校验端共用本模块的锚点正则与单位归一函数，保证同一把尺子；
- 金额用 ``Decimal`` 归一到「元」并量化 0.01，禁止 float 直比；
- 月↔天、工作日↔自然日含业务假设，不做自动换算，跨单位在规则端跳过；
- 同 ``(fact_type, value, unit)`` 去重，同类型冲突事实全部保留。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

# ---- 事实类型 ----
FACT_BUDGET = "budget"
FACT_DEPOSIT = "deposit"
FACT_DURATION = "duration"
FACT_VALIDITY = "validity"
FACT_PROJECT_NAME = "project_name"
FACT_TENDER_NO = "tender_no"

LEDGER_VERSION = 1
LEDGER_SOURCE = "deterministic_v1"

# ---- 标准单位 ----
UNIT_YUAN = "元"
UNIT_DAY = "天"
UNIT_WORKDAY = "工作日"
UNIT_MONTH = "月"

# 锚点长词在前：正则 alternation 左优先，避免「投标保证金」被「保证金」截断
_DEPOSIT_ANCHORS = ("投标保证金", "保证金")
_BUDGET_ANCHORS = ("预算金额", "最高限价", "招标控制价", "控制价", "预算")
_DURATION_ANCHORS = (
    "工期", "服务期限", "服务期", "供货期限", "供货期", "交付期限", "交付期",
)
_VALIDITY_ANCHORS = ("投标有效期", "有效期")
_NAME_ANCHORS = ("项目名称", "工程名称")
_NO_ANCHORS = ("项目编号", "招标编号", "采购编号")

_AMOUNT_UNITS = ("万元", "元")
_DURATION_UNITS = ("日历天", "工作日", "天", "个月", "月")
_VALIDITY_UNITS = ("日历天", "工作日", "天")

_NUM = r"\d+(?:\.\d+)?"
# 锚点与数字之间只允许 12 个非句读字符，防止跨句拼接
_WINDOW = r"[^。；;\n]{0,12}?"
_PLACEHOLDER_MARK = "【待填写"
_MAX_RAW = 200


def _anchor_pattern(anchors: tuple[str, ...], units: tuple[str, ...]) -> re.Pattern:
    return re.compile(
        rf"({'|'.join(anchors)}){_WINDOW}({_NUM})\s*({'|'.join(units)})"
    )


DEPOSIT_RE = _anchor_pattern(_DEPOSIT_ANCHORS, _AMOUNT_UNITS)
BUDGET_RE = _anchor_pattern(_BUDGET_ANCHORS, _AMOUNT_UNITS)
DURATION_RE = _anchor_pattern(_DURATION_ANCHORS, _DURATION_UNITS)
VALIDITY_RE = _anchor_pattern(_VALIDITY_ANCHORS, _VALIDITY_UNITS)

# 扁平 budget_amount 字段可能没有锚点前缀（如「437.677万元」），用裸金额解析
BARE_AMOUNT_RE = re.compile(rf"({_NUM})\s*({'|'.join(_AMOUNT_UNITS)})")

PROJECT_NAME_RE = re.compile(rf"({'|'.join(_NAME_ANCHORS)})[：:\s]*([^\s，。；;、（）()]{{4,40}})")
TENDER_NO_RE = re.compile(rf"({'|'.join(_NO_ANCHORS)})[：:\s]*([A-Za-z0-9][A-Za-z0-9\-_]{{3,39}})")

# 规则端复用的数值型扫描规格：(事实类型, 正则, 单位族)
AMOUNT_SCANS = ((FACT_DEPOSIT, DEPOSIT_RE), (FACT_BUDGET, BUDGET_RE))
DURATION_SCANS = ((FACT_DURATION, DURATION_RE), (FACT_VALIDITY, VALIDITY_RE))


def canonical_amount(number: str, unit: str) -> Decimal:
    """金额归一到元并量化 0.01。"""
    value = Decimal(number)
    if unit == "万元":
        value *= Decimal(10000)
    return value.quantize(Decimal("0.01"))


def canonical_duration_unit(unit: str) -> str:
    """工期单位归一：日历天/天→天；工作日、月各自独立，不跨族换算。"""
    if unit in ("日历天", "天"):
        return UNIT_DAY
    if unit == "工作日":
        return UNIT_WORKDAY
    return UNIT_MONTH  # 个月、月


def canonical_duration_value(number: str) -> str:
    """工期数值规范化（整数量化到个位，小数保留），返回十进制字符串。"""
    return format(Decimal(number), "f")


@dataclass
class Fact:
    """一条强约束事实。"""

    fact_type: str
    label: str
    value: str
    unit: str
    raw_text: str
    source: str
    confidence: float
    reviewed: bool = False

    def __post_init__(self) -> None:
        if len(self.raw_text) > _MAX_RAW:
            self.raw_text = self.raw_text[:_MAX_RAW]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FactLedger:
    """全局事实表；空表时规则检查整体跳过。"""

    facts: list[Fact] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.facts

    def by_type(self, fact_type: str) -> list[Fact]:
        return [f for f in self.facts if f.fact_type == fact_type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": LEDGER_VERSION,
            "ledger_source": LEDGER_SOURCE,
            "facts": [f.to_dict() for f in self.facts],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> FactLedger:
        """容忍 None / 非 dict / 缺键 / 非法条目，旧产物反序列化不炸。"""
        if not isinstance(raw, dict) or not isinstance(raw.get("facts"), list):
            return cls()
        facts: list[Fact] = []
        for item in raw["facts"]:
            if not isinstance(item, dict) or not item.get("fact_type"):
                continue
            facts.append(
                Fact(
                    fact_type=str(item["fact_type"]),
                    label=str(item.get("label") or ""),
                    value=str(item.get("value") or ""),
                    unit=str(item.get("unit") or ""),
                    raw_text=str(item.get("raw_text") or ""),
                    source=str(item.get("source") or ""),
                    confidence=float(item.get("confidence") or 0.0),
                    reviewed=bool(item.get("reviewed")),
                )
            )
        return cls(facts=facts)


class _LedgerBuilder:
    """收集事实并按 (type, value, unit) 去重的小构造器。"""

    def __init__(self) -> None:
        self._facts: list[Fact] = []
        self._keys: set[tuple[str, str, str]] = set()

    def add(
        self,
        fact_type: str,
        label: str,
        value: str,
        unit: str,
        raw_text: str,
        source: str,
        confidence: float,
    ) -> None:
        key = (fact_type, value, unit)
        if key in self._keys:
            return
        self._keys.add(key)
        self._facts.append(
            Fact(
                fact_type=fact_type,
                label=label,
                value=value,
                unit=unit,
                raw_text=raw_text.strip(),
                source=source,
                confidence=confidence,
            )
        )

    def build(self) -> FactLedger:
        return FactLedger(facts=list(self._facts))


def _iter_text_fields(tender: Any) -> list[tuple[str, str]]:
    """展开参与规则扫描的文本字段，返回 (source 名, 文本行)。鸭子类型，缺字段跳过。"""
    lines: list[tuple[str, str]] = []
    list_fields = (
        "contract_highlights",
        "technical_requirements",
        "service_requirements",
        "preliminary_review",
    )
    for field_name in list_fields:
        value = getattr(tender, field_name, None)
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str) and item.strip():
                    lines.append((f"{field_name}[{index}]", item))
    qualifications = getattr(tender, "qualifications", None)
    if isinstance(qualifications, list):
        for index, item in enumerate(qualifications):
            if isinstance(item, dict):
                requirement = str(item.get("requirement") or "").strip()
                if requirement:
                    lines.append((f"qualifications[{index}].requirement", requirement))
    return lines


def _add_amount_facts(
    builder: _LedgerBuilder,
    fact_type: str,
    pattern: re.Pattern,
    text: str,
    source: str,
    confidence: float,
) -> None:
    for match in pattern.finditer(text):
        if _PLACEHOLDER_MARK in match.group(0):
            continue
        anchor, number, unit = match.group(1), match.group(2), match.group(3)
        builder.add(
            fact_type,
            anchor,
            str(canonical_amount(number, unit)),
            UNIT_YUAN,
            match.group(0),
            source,
            confidence,
        )


def _add_duration_facts(
    builder: _LedgerBuilder,
    fact_type: str,
    pattern: re.Pattern,
    text: str,
    source: str,
    confidence: float,
) -> None:
    for match in pattern.finditer(text):
        if _PLACEHOLDER_MARK in match.group(0):
            continue
        anchor, number, unit = match.group(1), match.group(2), match.group(3)
        builder.add(
            fact_type,
            anchor,
            canonical_duration_value(number),
            canonical_duration_unit(unit),
            match.group(0),
            source,
            0.8,
        )


def _add_text_facts(builder: _LedgerBuilder, text: str, source: str) -> None:
    for fact_type, pattern in AMOUNT_SCANS:
        _add_amount_facts(builder, fact_type, pattern, text, source, 0.8)
    for fact_type, pattern in DURATION_SCANS:
        _add_duration_facts(builder, fact_type, pattern, text, source, 0.8)

    for match in PROJECT_NAME_RE.finditer(text):
        if _PLACEHOLDER_MARK in match.group(0):
            continue
        builder.add(
            FACT_PROJECT_NAME, match.group(1), match.group(2), "",
            match.group(0), source, 0.8,
        )
    for match in TENDER_NO_RE.finditer(text):
        if _PLACEHOLDER_MARK in match.group(0):
            continue
        builder.add(
            FACT_TENDER_NO, match.group(1), match.group(2), "",
            match.group(0), source, 0.8,
        )


def build_fact_ledger(tender: Any) -> FactLedger:
    """从结构化抽取结果确定性构建事实表（T-029）。

    扁平结构化字段 confidence=1.0；文本规则抽取 confidence=0.8。
    任何字段缺失或无法解析都按跳过处理，不抛异常。
    """
    builder = _LedgerBuilder()

    # 1. 扁平结构化字段
    budget_amount = str(getattr(tender, "budget_amount", "") or "").strip()
    if budget_amount:
        match = BARE_AMOUNT_RE.search(budget_amount)
        if match:
            number, unit = match.group(1), match.group(2)
            builder.add(
                FACT_BUDGET, "预算金额",
                str(canonical_amount(number, unit)), UNIT_YUAN,
                budget_amount, "basic.budget_amount", 1.0,
            )
    project_name = str(getattr(tender, "project_name", "") or "").strip()
    if project_name:
        builder.add(
            FACT_PROJECT_NAME, "项目名称", project_name, "",
            project_name, "basic.project_name", 1.0,
        )
    tender_no = str(getattr(tender, "tender_no", "") or "").strip()
    if tender_no:
        builder.add(
            FACT_TENDER_NO, "项目编号", tender_no, "",
            tender_no, "basic.tender_no", 1.0,
        )

    # 2. 文本字段规则扫描
    for source, line in _iter_text_fields(tender):
        _add_text_facts(builder, line, source)

    return builder.build()
