"""接口响应模型（与 `开发文档.md` §7 接口规范对齐）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    version: str
    task_backend: str
    broker_ok: bool | None


class ParseAccepted(BaseModel):
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


class BidderProfileItem(BaseModel):
    id: int
    company_name: str
    data: dict[str, Any] = {}
    created_at: str | None = None
