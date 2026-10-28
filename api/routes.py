"""上传、任务查询与健康检查端点。"""
from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi import Path as PathParam

from api import dispatch
from api.schemas import (
    BidAccepted,
    BidCreate,
    BidderProfileItem,
    BidderProfileUpsert,
    HealthResponse,
    JobAccepted,
    JobStatus,
    JobTraceResponse,
    MetricsResponse,
    ParseAccepted,
    SectionDetail,
    SectionInfo,
    SectionRewriteRequest,
    SectionUpdate,
    TaskHistoryItem,
)
from src.config import settings
from src.db.repository import (
    delete_profile,
    get_task,
    list_profiles,
    list_tasks,
    save_profile,
    update_profile,
)
from src.errors import BidWriteError
from src.observability import job_trace
from src.observability import metrics as job_metrics
from src.pipeline import BIDS_DIR, FILES_DIR, PARSED_DIR, _safe_filename

router = APIRouter(prefix="/api")

_VERSION = "0.1.0"  # 与 pyproject.toml 保持同步
_PDF_HEADER = b"%PDF"
_INCOMING = FILES_DIR / ".incoming"
_BID_ID_PATTERN = r"^[0-9a-f]{16}-\d{8}-\d{6}$"


def _bid_dir_or_404(bid_id: str) -> Path:
    """产物目录必须存在且落在 `BIDS_DIR` 内，避免路径穿越。"""
    target = (BIDS_DIR / bid_id).resolve()
    if not target.is_relative_to(BIDS_DIR.resolve()) or not target.is_dir():
        raise HTTPException(404, "产物不存在")
    return target


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


@router.get("/jobs/{job_id}/trace", response_model=JobTraceResponse)
def get_job_trace(job_id: str) -> JobTraceResponse:
    """任务轨迹：各阶段耗时与终态异常，失败任务据此定位问题阶段。"""
    trace = job_trace(job_id)
    if trace is None:
        raise HTTPException(404, "任务轨迹不存在（任务未执行或日志已清理）")
    return JobTraceResponse(**trace)


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics() -> MetricsResponse:
    """运行指标：队列深度、任务终态分布、平均耗时与最近失败。"""
    backend = settings.task_backend
    snapshot = job_metrics()
    return MetricsResponse(
        task_backend=backend,
        broker_ok=dispatch.queue_available() if backend == "celery" else None,
        worker_ok=dispatch.worker_status(),
        queue_depth=dispatch.queue_depth(),
        jobs_total=snapshot["jobs_total"],
        by_status=snapshot["by_status"],
        by_kind=snapshot["by_kind"],
        avg_duration_ms=snapshot["avg_duration_ms"],
        recent_failures=snapshot["recent_failures"],
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, bool]:
    """取消尚未结束的任务；Celery 用 revoke，local 后端标记取消后丢弃结果。"""
    job = dispatch.get_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已过期")
    if job.get("status") not in ("pending", "running"):
        raise HTTPException(409, "任务已结束，无法取消")
    if not dispatch.cancel_job(job_id):
        raise HTTPException(409, "任务已结束，无法取消")
    return {"cancelled": True}


@router.post("/jobs/{job_id}/retry", response_model=JobAccepted, status_code=202)
async def retry_job(job_id: str) -> JobAccepted:
    """失败任务带原参数重试，参数取自任务历史（库不可用或记录缺失返回 404）。"""
    task = await get_task(job_id)
    if task is None:
        raise HTTPException(404, "任务记录不存在（数据库不可用或记录已被清理）")
    if task.get("status") != "failed":
        raise HTTPException(409, "仅失败任务可重试")

    kind = task.get("kind") or "parse"
    doc_id = task.get("doc_id") or ""
    options = dict(task.get("options") or {})

    if not dispatch.queue_available():
        raise HTTPException(503, "任务队列不可用")
    if dispatch.queue_depth() >= settings.api_max_pending_jobs:
        raise HTTPException(429, "排队任务达到上限")

    if kind == "bid":
        if not (PARSED_DIR / doc_id / "extraction.json").is_file():
            raise HTTPException(404, "文档没有结构化抽取结果，请重新解析")
        profile = options.pop("profile", None)
        new_id = dispatch.submit_bid(doc_id, profile, options)
    elif kind == "supplement":
        bid_id = task.get("source_name") or ""
        if not (BIDS_DIR / bid_id / "report.json").is_file():
            raise HTTPException(404, "原产物已被清理，无法重试补写")
        new_id = dispatch.submit_supplement(bid_id, doc_id)
    elif kind == "rewrite":
        bid_id = task.get("source_name") or ""
        if not (BIDS_DIR / bid_id / "report.json").is_file():
            raise HTTPException(404, "原产物已被清理，无法重试章节重写")
        new_id = dispatch.submit_section_rewrite(
            bid_id, doc_id, options.get("section_ids")
        )
    else:
        source = None
        if doc_id:
            source = next(iter(sorted(FILES_DIR.glob(f"{doc_id}_*"))), None)
        if source is None:
            raise HTTPException(404, "原始 PDF 已被清理，请重新上传")
        new_id = dispatch.submit_parse(
            str(source), options, doc_id, task.get("source_name") or source.name
        )

    return JobAccepted(
        job_id=new_id,
        doc_id=doc_id,
        kind=kind,
        status="pending",
        job_url=f"/api/jobs/{new_id}",
    )


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


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: Annotated[str, PathParam(pattern=r"^[0-9a-f]{16}$")],
) -> dict[str, bool]:
    """删除文档的解析结果与上传的原件（P1-7 数据治理）。"""
    parsed = PARSED_DIR / doc_id
    originals = list(FILES_DIR.glob(f"{doc_id}_*")) if FILES_DIR.is_dir() else []
    if not parsed.is_dir() and not originals:
        raise HTTPException(404, "文档不存在")
    if parsed.is_dir():
        shutil.rmtree(parsed)
    for path in originals:
        path.unlink(missing_ok=True)
    return {"deleted": True}


@router.delete("/bids/{bid_id}")
def delete_bid(
    bid_id: Annotated[str, PathParam(pattern=r"^[0-9a-f]{16}-\d{8}-\d{6}$")],
) -> dict[str, bool]:
    """删除一整套标书产物（只删产物目录，不动原始招标文件）。"""
    target = (BIDS_DIR / bid_id).resolve()
    if not target.is_relative_to(BIDS_DIR.resolve()) or not target.is_dir():
        raise HTTPException(404, "产物不存在")
    shutil.rmtree(target)
    return {"deleted": True}


# ---- 章节级编辑、重写与锁定（P1-2）----


@router.get("/bids/{bid_id}/sections", response_model=list[SectionInfo])
def list_bid_sections(
    bid_id: Annotated[str, PathParam(pattern=_BID_ID_PATTERN)],
) -> list[SectionInfo]:
    """章节列表：顺序、锁定状态、字数与占位符数量。"""
    from src.bid_writer.sections import section_infos

    bid_dir = _bid_dir_or_404(bid_id)
    try:
        infos = section_infos(bid_dir)
    except BidWriteError as exc:
        raise HTTPException(422, str(exc)) from exc
    return [SectionInfo(**info) for info in infos]


@router.get("/bids/{bid_id}/sections/{section_id}", response_model=SectionDetail)
def get_bid_section(
    bid_id: Annotated[str, PathParam(pattern=_BID_ID_PATTERN)],
    section_id: str,
) -> SectionDetail:
    """单章详情，供编辑弹窗回填正文。"""
    from src.bid_writer.sections import section_detail

    bid_dir = _bid_dir_or_404(bid_id)
    try:
        detail = section_detail(bid_dir, section_id)
    except BidWriteError as exc:
        raise HTTPException(404, str(exc)) from exc
    return SectionDetail(**detail)


@router.put("/bids/{bid_id}/sections/{section_id}")
def save_bid_section(
    bid_id: Annotated[str, PathParam(pattern=_BID_ID_PATTERN)],
    section_id: str,
    body: SectionUpdate,
) -> dict[str, Any]:
    """保存单章正文 / 切换锁定，并刷新整套产物（返回最新摘要）。"""
    from src.bid_writer.sections import save_section

    if body.markdown is None and body.locked is None:
        raise HTTPException(422, "markdown 与 locked 至少提供一个")
    bid_dir = _bid_dir_or_404(bid_id)
    try:
        report = save_section(bid_dir, section_id, body.markdown, locked=body.locked)
    except BidWriteError as exc:
        raise HTTPException(422, str(exc)) from exc
    return report.summary()


@router.get("/bids/{bid_id}/sections/{section_id}/file")
def download_bid_section(
    bid_id: Annotated[str, PathParam(pattern=_BID_ID_PATTERN)],
    section_id: str,
) -> Response:
    """导出单章 Markdown（附件下载）。"""
    from src.bid_writer.sections import section_detail

    bid_dir = _bid_dir_or_404(bid_id)
    try:
        detail = section_detail(bid_dir, section_id)
    except BidWriteError as exc:
        raise HTTPException(404, str(exc)) from exc
    filename = f"{detail['order']:02d}-{detail['title']}.md".replace("/", "_")
    return Response(
        detail["markdown"],
        media_type="text/markdown",
        headers={
            # 中文文件名需按 RFC 5987 编码，否则响应头 latin-1 编码会报错
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.post("/bids/{bid_id}/sections/rewrite", response_model=BidAccepted, status_code=202)
def rewrite_bid_sections(
    bid_id: Annotated[str, PathParam(pattern=_BID_ID_PATTERN)],
    body: SectionRewriteRequest,
) -> BidAccepted:
    """重写指定章节（默认全部未锁定章节），锁定章节保持不变。"""
    if not (BIDS_DIR / bid_id / "report.json").is_file():
        raise HTTPException(404, "产物不存在")
    if not dispatch.queue_available():
        raise HTTPException(503, "任务队列不可用")
    if dispatch.queue_depth() >= settings.api_max_pending_jobs:
        raise HTTPException(429, "排队任务达到上限")

    doc_id = bid_id.split("-")[0]
    job_id = dispatch.submit_section_rewrite(bid_id, doc_id, body.section_ids)
    return BidAccepted(
        job_id=job_id,
        doc_id=doc_id,
        kind="rewrite",
        status="pending",
        job_url=f"/api/jobs/{job_id}",
    )
