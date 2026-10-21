"""接口响应模型（与 `开发文档.md` §7 接口规范对齐）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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
