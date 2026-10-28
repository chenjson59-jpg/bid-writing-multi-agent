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


class DocumentSummary(BaseModel):
    doc_id: str
    source_name: str
    doc_type: str
    parser: str
    pages: int
    sections: int
    has_extraction: bool
    parsed_at: str | None


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
