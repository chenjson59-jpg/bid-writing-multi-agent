"""上传、任务查询与健康检查端点。"""
from __future__ import annotations

import hashlib
import os
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi import Path as PathParam

from api import dispatch
from api.schemas import (
    BidAccepted,
    BidCreate,
    BidderProfileItem,
    BidderProfileUpsert,
    HealthResponse,
    JobStatus,
    ParseAccepted,
    TaskHistoryItem,
)
from src.config import settings
from src.db.repository import (
    delete_profile,
    list_profiles,
    list_tasks,
    save_profile,
    update_profile,
)
from src.pipeline import BIDS_DIR, FILES_DIR, PARSED_DIR, _safe_filename

router = APIRouter(prefix="/api")

_VERSION = "0.1.0"  # 与 pyproject.toml 保持同步
_PDF_HEADER = b"%PDF"
_INCOMING = FILES_DIR / ".incoming"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    backend = settings.task_backend
    broker_ok = dispatch.queue_available() if backend == "celery" else None
    return HealthResponse(
        status="ok",
        version=_VERSION,
        task_backend=backend,
        broker_ok=broker_ok,
        worker_ok=dispatch.worker_status(),
        queue_depth=dispatch.queue_depth(),
    )


@router.post("/parse", response_model=ParseAccepted, status_code=202)
async def create_parse(
    file: Annotated[UploadFile, File()],
    parser: Literal["auto", "cloud", "local"] = "auto",
    force: bool = False,
    extract: bool = False,
    ingest: bool = False,
) -> ParseAccepted:
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")

    content = await file.read()
    if len(content) > settings.api_max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过大小上限 {settings.api_max_upload_mb} MB")
    if not content:
        raise HTTPException(400, "文件为空")
    if not content.startswith(_PDF_HEADER):
        raise HTTPException(400, "缺少 PDF 文件头")

    doc_id = hashlib.sha256(content).hexdigest()[:16]

    # 先写临时文件，再原子转正，避免残留半成品
    _INCOMING.mkdir(parents=True, exist_ok=True)
    tmp = _INCOMING / f"{uuid.uuid4().hex}.pdf"
    tmp.write_bytes(content)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    dest = FILES_DIR / f"{doc_id}_{_safe_filename(filename)}"
    os.replace(tmp, dest)

    if not dispatch.queue_available():
        raise HTTPException(503, "任务队列不可用")
    if dispatch.queue_depth() >= settings.api_max_pending_jobs:
        raise HTTPException(429, "排队任务达到上限")

    options = {"parser": parser, "force": force, "extract": extract, "ingest": ingest}
    job_id = dispatch.submit_parse(str(dest), options, doc_id, filename)

    return ParseAccepted(
        job_id=job_id,
        doc_id=doc_id,
        kind="parse",
        status="pending",
        job_url=f"/api/jobs/{job_id}",
    )


@router.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    job = dispatch.get_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已过期")
    return JobStatus(**job)


@router.post("/bids", response_model=BidAccepted, status_code=202)
def create_bid(body: BidCreate) -> BidAccepted:
    extraction = PARSED_DIR / body.doc_id / "extraction.json"
    if not extraction.is_file():
        raise HTTPException(404, "文档没有结构化抽取结果")

    if not dispatch.queue_available():
        raise HTTPException(503, "任务队列不可用")
    if dispatch.queue_depth() >= settings.api_max_pending_jobs:
        raise HTTPException(429, "排队任务达到上限")

    options = {
        "max_revisions": body.max_revisions,
        "min_score": body.min_score,
        "max_sections": body.max_sections,
        "export_word": body.export_word,
    }
    job_id = dispatch.submit_bid(body.doc_id, body.profile, options)

    return BidAccepted(
        job_id=job_id,
        doc_id=body.doc_id,
        kind="bid",
        status="pending",
        job_url=f"/api/jobs/{job_id}",
    )


@router.post("/bids/{bid_id}/supplement", response_model=BidAccepted, status_code=202)
def supplement_bid(
    bid_id: Annotated[str, PathParam(pattern=r"^[0-9a-f]{16}-\d{8}-\d{6}$")],
) -> BidAccepted:
    """为未覆盖评分因素补写章节，生成新产物（不覆盖原产物）。"""
    from src.bid_writer.supplement import supplement_plan

    if not (BIDS_DIR / bid_id / "report.json").is_file():
        raise HTTPException(404, "产物不存在")
    try:
        factors = supplement_plan(bid_id)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    if not factors:
        raise HTTPException(409, "该产物已覆盖全部评分因素")

    if not dispatch.queue_available():
        raise HTTPException(503, "任务队列不可用")
    if dispatch.queue_depth() >= settings.api_max_pending_jobs:
        raise HTTPException(429, "排队任务达到上限")

    doc_id = bid_id.split("-")[0]
    job_id = dispatch.submit_supplement(bid_id, doc_id)
    return BidAccepted(
        job_id=job_id,
        doc_id=doc_id,
        kind="supplement",
        status="pending",
        job_url=f"/api/jobs/{job_id}",
    )


@router.get("/tasks", response_model=list[TaskHistoryItem])
async def list_task_history(limit: int = 100, offset: int = 0) -> list[TaskHistoryItem]:
    """任务历史：从数据库读，跨重启可查（尽力而为，库不可用返回空）。"""
    tasks = await list_tasks(limit=limit, offset=offset)
    return [TaskHistoryItem(**item) for item in tasks]


@router.get("/profiles", response_model=list[BidderProfileItem])
async def list_bidder_profiles(limit: int = 100) -> list[BidderProfileItem]:
    """投标人资料：编写任务提交时落库的公司资料。"""
    profiles = await list_profiles(limit=limit)
    return [BidderProfileItem(**item) for item in profiles]


@router.post("/profiles", response_model=BidderProfileItem, status_code=201)
async def create_bidder_profile(body: BidderProfileUpsert) -> BidderProfileItem:
    """新建资料。公司名已存在时覆盖最近一条（一次录入、多次复用）。"""
    company_name = body.company_name.strip()
    profile_id = await save_profile(company_name, body.data)
    if profile_id is None:
        raise HTTPException(503, "数据库不可用，资料未保存")
    return BidderProfileItem(id=profile_id, company_name=company_name, data=body.data)


@router.put("/profiles/{profile_id}", response_model=BidderProfileItem)
async def update_bidder_profile(
    profile_id: int, body: BidderProfileUpsert
) -> BidderProfileItem:
    company_name = body.company_name.strip()
    updated = await update_profile(profile_id, company_name, body.data)
    if updated is None:
        raise HTTPException(503, "数据库不可用")
    if not updated:
        raise HTTPException(404, "资料不存在")
    return BidderProfileItem(id=profile_id, company_name=company_name, data=body.data)


@router.delete("/profiles/{profile_id}")
async def delete_bidder_profile(profile_id: int) -> dict[str, bool]:
    deleted = await delete_profile(profile_id)
    if deleted is None:
        raise HTTPException(503, "数据库不可用")
    if not deleted:
        raise HTTPException(404, "资料不存在")
    return {"deleted": True}
