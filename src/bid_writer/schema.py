"""投标编写阶段的数据模型（核心算法 A18–A20 的结构定义）。

涵盖：章节计划、评审结果、章节草稿、投标人资料、编写参数与覆盖报告，
以及标题归一化与占位符识别等跨模块复用的小工具。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# ---- 章节策略（核心算法 8.1.3）----
STRATEGY_GENERATE = "generate"
STRATEGY_FILL_TEMPLATE = "fill_template"
STRATEGY_PLACEHOLDER = "placeholder"

# 策略关键词（8.1.3 标题关键词 → 策略）
_GENERATE_KEYWORDS = ("施工组织设计", "项目管理机构", "其他评分", "其他资料")
_FILL_KEYWORDS = ("投标函", "授权委托书", "身份证明", "承诺", "声明")

# ---- 占位符（核心算法 8.4.1）----
PLACEHOLDER_RE = re.compile(r"【待填写[^】]*】")

# ---- 投标人资料字段 → 缺失中文名（核心算法 8.4.2）----
PROFILE_FIELD_LABELS: dict[str, str] = {
    "company_name": "公司名称",
    "legal_representative": "法定代表人",
    "registered_capital": "注册资本",
    "established_date": "成立日期",
    "address": "注册地址",
    "qualifications": "企业资质",
    "certifications": "认证证书",
    "similar_projects": "类似项目业绩",
    "personnel": "项目管理人员",
    "financials": "财务数据",
    "contact": "联系方式",
}

# 缺失判定的字段顺序（8.4.2 的固定顺序）
MISSING_FIELD_ORDER: tuple[str, ...] = (
    "company_name",
    "legal_representative",
    "registered_capital",
    "established_date",
    "address",
    "qualifications",
    "certifications",
    "similar_projects",
    "personnel",
    "financials",
    "contact",
)


def normalize_title(title: str) -> str:
    """规范化标题（8.3.3 / 8.1.4）：去掉全部空白，用于去重与模糊匹配。"""
    return re.sub(r"\s+", "", title)


def choose_strategy(title: str) -> str:
    """按标题关键词选择章节策略（8.1.3），未命中返回 `placeholder`。"""
    for keyword in _FILL_KEYWORDS:
        if keyword in title:
            return STRATEGY_FILL_TEMPLATE
    for keyword in _GENERATE_KEYWORDS:
        if keyword in title:
            return STRATEGY_GENERATE
    return STRATEGY_PLACEHOLDER


def extract_placeholders(text: str) -> list[str]:
    """提取文本中的去重占位符（8.4.1），按首次出现顺序返回。"""
    seen: list[str] = []
    for match in PLACEHOLDER_RE.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


@dataclass
class BidderProfile:
    """投标人资料（开发文档 §9）。

    `notes` 不参与缺失判定；`contact` 与 `financials` 允许为 `None`。
    """

    company_name: str = ""
    legal_representative: str = ""
    registered_capital: str = ""
    established_date: str = ""
    address: str = ""
    qualifications: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    similar_projects: list[dict[str, Any]] = field(default_factory=list)
    personnel: list[dict[str, Any]] = field(default_factory=list)
    financials: dict[str, Any] | None = None
    contact: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> BidderProfile:
        """从请求体构造资料，忽略未知字段，缺失按空处理（9.3 前端归一化）。"""
        raw = raw or {}
        fields = {
            key: raw.get(key, default)
            for key, default in cls().to_dict().items()
        }
        return cls(**fields)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def missing_fields(self) -> list[str]:
        """返回缺失字段的中文名列表，顺序固定（8.4.2）。"""
        missing: list[str] = []
        for field_key in MISSING_FIELD_ORDER:
            value = getattr(self, field_key)
            if value in (None, "", [], {}):
                missing.append(PROFILE_FIELD_LABELS[field_key])
        return missing


@dataclass
class SectionPlan:
    """一个章节的写作计划（核心算法 8.1.1）。"""

    id: str
    title: str
    order: int
    strategy: str = STRATEGY_PLACEHOLDER
    target_words: int = 1200
    scoring_factors: list[str] = field(default_factory=list)
    requirements: str = ""
    retrieval_query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewResult:
    """评审输出（核心算法 8.2.4）。"""

    score: int = 0
    covered_points: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    placeholder_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SectionDraft:
    """单个章节的写作结果（核心算法 8.2.1）。"""

    plan: SectionPlan
    markdown: str
    review: ReviewResult
    score_before: int
    score_after: int
    revisions: int
    retrieved: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.plan.id,
            "title": self.plan.title,
            "order": self.plan.order,
            "strategy": self.plan.strategy,
            "score_before": self.score_before,
            "score_after": self.score_after,
            "revisions": self.revisions,
            "word_count": _char_count(self.markdown),
            "placeholder_count": len(extract_placeholders(self.markdown)),
            "retrieved_count": len(self.retrieved),
            "scoring_factors": self.plan.scoring_factors,
        }


@dataclass
class WriteOptions:
    """编写参数（开发文档 §7.5）。"""

    max_revisions: int = 1
    min_score: int = 75
    max_sections: int = 0
    export_word: bool = True
    target_words: int = 1200
    top_k: int = 6

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> WriteOptions:
        raw = raw or {}
        return cls(
            max_revisions=raw.get("max_revisions", 1) or 1,
            min_score=raw.get("min_score", 75) or 75,
            max_sections=raw.get("max_sections", 0) or 0,
            export_word=raw.get("export_word", True),
            target_words=raw.get("target_words", 1200) or 1200,
            top_k=raw.get("top_k", 6) or 6,
        )


def _char_count(text: str) -> int:
    """字数统计：去除空白后的字符数（8.3.2）。"""
    return len(re.sub(r"\s+", "", text))


@dataclass
class BidReport:
    """覆盖报告（核心算法 8.3.5），`to_dict` 即 `report.json` 内容。"""

    doc_id: str
    project_name: str
    bidder: str
    generated_at: str
    total_sections: int
    total_words: int
    sections: list[dict[str, Any]] = field(default_factory=list)
    format_coverage: list[dict[str, Any]] = field(default_factory=list)
    evaluation_coverage: list[dict[str, Any]] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    missing_profile_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 本次任务的模型用量（P1-5），见 `bid_writer.usage.UsageMeter.summary`
    llm_usage: dict[str, Any] = field(default_factory=dict)
    # 补写产物所基于的原产物 bid_id（P1-1），正常生成为空
    supplement_of: str = ""
    # 生成参数（P1-2）：章节级重写时按原参数执行
    write_options: dict[str, Any] = field(default_factory=dict)
    # 最后编辑时间（P1-2）：章节编辑 / 重写 / 锁定后回填
    updated_at: str = ""
    # 产物路径（落盘时由编排层回填）
    output_dir: str = ""
    markdown_path: str = ""
    docx_path: str = ""
    report_path: str = ""
    outline_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """`bid_summary`（8.3.6）：供历史列表与任务结果使用。"""
        format_total = len(self.format_coverage)
        format_covered = sum(1 for c in self.format_coverage if c.get("section_id"))
        eval_total = len(self.evaluation_coverage)
        eval_covered = sum(1 for c in self.evaluation_coverage if c.get("covered"))
        uncovered = [
            c["name"]
            for c in self.evaluation_coverage
            if not c.get("covered") and c.get("score") is not None
        ]
        return {
            "doc_id": self.doc_id,
            "project_name": self.project_name,
            "bidder": self.bidder,
            "total_sections": self.total_sections,
            "total_words": self.total_words,
            "placeholder_count": len(self.placeholders),
            "format_items_total": format_total,
            "format_items_covered": format_covered,
            "evaluation_factors_total": eval_total,
            "evaluation_factors_covered": eval_covered,
            "uncovered_factors": uncovered,
            "missing_profile_fields": self.missing_profile_fields,
            "sections": self.sections,
            "warnings": self.warnings,
            "llm_usage": self.llm_usage,
            "supplement_of": self.supplement_of,
            "output_dir": self.output_dir,
            "markdown_path": self.markdown_path,
            "docx_path": self.docx_path,
            "report_path": self.report_path,
        }
