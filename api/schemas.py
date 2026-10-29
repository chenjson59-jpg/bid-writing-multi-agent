"""接口响应模型（与 `开发文档.md` §7 接口规范对齐）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    version: str
    task_backend: str
    broker_ok: bool | None
    worker_ok: bool | None = None
    queue_depth: int | None = None


class ParseAccepted(BaseModel):
    job_id: str
    doc_id: str
    kind: str
    status: str
    job_url: str


class JobAccepted(BaseModel):
    """任意任务被接受的响应；字段与 ParseAccepted 一致，前端可复用同一类型。"""

    job_id: str
    doc_id: str
    kind: str
    status: str
    job_url: str


class JobStatus(BaseModel):
    job_id: str
    kind: str
    status: str
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    options: dict[str, Any] = {}
    source_name: str | None = None
    progress: list[str] = []
    result: dict[str, Any] | None = None
    extraction: dict[str, Any] | None = None
    ingest: dict[str, Any] | None = None
    bid: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None
    stage: str | None = None  # 当前/最后阶段（来自任务轨迹，P1-9）


class JobTraceStage(BaseModel):
    name: str | None = None
    duration_ms: int = 0


class JobTraceResponse(BaseModel):
    """任务轨迹：阶段耗时与终态异常，失败任务据此定位问题阶段。"""

    job_id: str
    kind: str = ""
    status: str
    stage: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    stages: list[JobTraceStage] = []
    error: str | None = None
    error_type: str | None = None


class MetricsResponse(BaseModel):
    """运行指标：队列状态、任务终态分布与平均耗时。"""

    task_backend: str
    broker_ok: bool | None = None
    worker_ok: bool | None = None
    queue_depth: int = 0
    jobs_total: int = 0
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    avg_duration_ms: dict[str, int] = {}
    recent_failures: list[dict[str, Any]] = []


class DocumentSummary(BaseModel):
    doc_id: str
    source_name: str
    doc_type: str
    parser: str
    pages: int
    sections: int
    has_extraction: bool
    extraction_reviewed: bool = False
    parsed_at: str | None


class ExtractionBasic(BaseModel):
    """抽取结果的基础字段：项目名、招标编号、预算、截止时间与开标地点。"""

    project_name: str = Field(default="", max_length=200)
    tender_no: str = Field(default="", max_length=100)
    budget_amount: str = Field(default="", max_length=100)
    bid_deadline: str = Field(default="", max_length=100)
    bid_open_place: str = Field(default="", max_length=200)

    @field_validator("*")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class ExtractionFactor(BaseModel):
    """评分因素：人工校正的核心对象，名称参与覆盖统计。"""

    category: str = Field(default="其他", max_length=20)
    name: str = Field(min_length=1, max_length=100)
    score: float | None = Field(default=None, ge=0, le=1000)
    source_quote: str = Field(default="", max_length=500)

    @field_validator("category", "name", "source_quote")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("评分因素名称不能为空")
        return name


class ExtractionFormatItem(BaseModel):
    """投标文件格式条目：决定产物需要哪些章节。"""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=2000)

    @field_validator("title", "content")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("title")
    @classmethod
    def _require_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("格式条目标题不能为空")
        return title


class ExtractionQualification(BaseModel):
    """资格条件：门槛要求，写前提示依据。"""

    category: str = Field(default="其他", max_length=20)
    requirement: str = Field(min_length=1, max_length=500)
    source_quote: str = Field(default="", max_length=500)
    thresholds: list[str] = []

    @field_validator("category", "requirement", "source_quote")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("requirement")
    @classmethod
    def _require_requirement(cls, value: str) -> str:
        requirement = value.strip()
        if not requirement:
            raise ValueError("资格条件不能为空")
        return requirement


class ExtractionUpdate(BaseModel):
    """保存人工校正结果：只覆盖可编辑的三张表与基础字段，其余抽取信息保留。"""

    basic: ExtractionBasic
    evaluation_factors: list[ExtractionFactor] = []
    qualification_items: list[ExtractionQualification] = []
    bid_format_items: list[ExtractionFormatItem] = []

    @field_validator("evaluation_factors", "qualification_items", "bid_format_items")
    @classmethod
    def _cap(cls, value: list) -> list:
        if len(value) > 200:
            raise ValueError("条目数不能超过 200")
        return value


class ExtractionDetail(BaseModel):
    """抽取结果视图：扁平结构供前端三张表直接编辑。"""

    doc_id: str
    doc_type: str = ""
    basic: dict[str, Any] = {}
    evaluation_factors: list[dict[str, Any]] = []
    qualification_items: list[dict[str, Any]] = []
    bid_format_items: list[dict[str, Any]] = []
    extraction_meta: dict[str, Any] = {}
    reviewed_at: str | None = None


class BidCreate(BaseModel):
    doc_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    profile: dict[str, Any] | None = None
    max_revisions: int | None = Field(default=None, ge=0, le=3)
    min_score: int | None = Field(default=None, ge=0, le=100)
    max_sections: int | None = Field(default=None, ge=0, le=50)
    export_word: bool = True


class BidAccepted(BaseModel):
    job_id: str
    doc_id: str
    kind: str
    status: str
    job_url: str


class BidHistoryItem(BaseModel):
    bid_id: str
    generated_at: str | None
    summary: dict[str, Any]


class TaskHistoryItem(BaseModel):
    job_id: str
    kind: str
    doc_id: str | None = None
    source_name: str | None = None
    status: str
    options: dict[str, Any] = {}
    summary: dict[str, Any] | None = None
    error: str | None = None
    created_at: str | None = None
    finished_at: str | None = None


class SectionInfo(BaseModel):
    """章节列表项（P1-2）：锁定状态与统计信息。"""

    id: str
    title: str
    order: int
    strategy: str = ""
    locked: bool = False
    word_count: int = 0
    placeholder_count: int = 0
    score_before: int = 0
    score_after: int = 0
    revisions: int = 0


class SectionDetail(SectionInfo):
    """单章详情：列表项 + Markdown 正文。"""

    markdown: str


class SectionUpdate(BaseModel):
    """保存单章：`markdown` 为空表示只改锁定状态。"""

    markdown: str | None = None
    locked: bool | None = None


class SectionRewriteRequest(BaseModel):
    """重写章节：`section_ids` 为空表示全部未锁定章节。"""

    section_ids: list[str] | None = None


class BidderProfileItem(BaseModel):
    id: int
    company_name: str
    data: dict[str, Any] = {}
    created_at: str | None = None


class BidderProfileUpsert(BaseModel):
    """新建/覆盖投标人资料：公司名作为业务主键，data 结构同 `BidderProfile`。"""

    company_name: str = Field(min_length=1, max_length=255)
    data: dict[str, Any] = {}

    @field_validator("company_name")
    @classmethod
    def _strip_company_name(cls, value: str) -> str:
        """去掉首尾空白；全空白视为非法，避免资料库里出现无名记录。"""
        stripped = value.strip()
        if not stripped:
            raise ValueError("公司名不能为空")
        return stripped
