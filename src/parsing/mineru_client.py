"""MinerU 云端解析（精准解析 API，批量上传流程）。

流程：申请上传链接 → PUT 上传分片 → 轮询批量结果 → 下载 zip 并安全解压 →
定位 `content_list.json` / `full.md`，产出 `MineruOutput` 列表。

MinerU 契约（https://mineru.net/doc/docs/）：
- `POST /api/v4/file-urls/batch` 申请上传链接，返回 `batch_id` 与 `file_urls`；
- 文件上传完成后系统自动提交解析任务，无需额外调用；
- `GET /api/v4/extract-results/batch/{batch_id}` 轮询，`data.extract_result`
  为逐文件结果，`state=done` 时携带 `full_zip_url`；
- 解压 zip 含 `full.md` 与 `*_content_list.json`。

传输重试（T-026）：四个 httpx 调用统一经 `RetryPolicy` 包装，仅对 429/5xx 与
传输层异常（超时、连接错误等 `httpx.TransportError`）做指数退避重试；4xx 鉴权/
参数错误与 2xx 业务错误立即抛出。重试内置于本模块，对 `pipeline` 的降级判断透明。
"""
from __future__ import annotations

import io
import logging
import random
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from src.config import settings
from src.errors import ParseError
from src.parsing.models import MineruOutput

logger = logging.getLogger(__name__)

# 批量结果的终态集合
_TERMINAL_STATES = {"done", "failed"}

# Retry-After 允许采纳的上限（秒），与 openai SDK 的 MAX_RETRY_AFTER_DELAY 对齐
_MAX_RETRY_AFTER_SECONDS = 120.0


@dataclass
class RetryPolicy:
    """MinerU 传输层重试策略：分类判定 + 指数退避（含抖动）。

    `max_retries` 为重试次数（不含首次调用），0 表示关闭重试；`sleep`/`rng`
    可注入替身，便于离线测试零真实等待并断言退避序列。
    """

    max_retries: int
    backoff_base: float
    sleep: Callable[[float], None] = time.sleep
    rng: Callable[[], float] = random.random

    @classmethod
    def from_settings(cls) -> RetryPolicy:
        """从全局配置装配生产策略（真实 sleep/rng）。"""
        return cls(
            max_retries=settings.mineru_max_retries,
            backoff_base=settings.mineru_retry_backoff_base_seconds,
        )

    @staticmethod
    def is_retryable(exc: Exception) -> bool:
        """429/5xx 与传输层异常可重试；其余 4xx 与非 httpx 异常不可重试。"""
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            return status_code == 429 or status_code >= 500
        return isinstance(exc, httpx.TransportError)

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> float | None:
        """解析 Retry-After（delta-seconds 或 HTTP 日期）；非法/越界返回 None。"""
        if not isinstance(exc, httpx.HTTPStatusError):
            return None
        raw = exc.response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                return None
            if retry_at is None:
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        if 0 < seconds <= _MAX_RETRY_AFTER_SECONDS:
            return seconds
        return None

    def delay_for(self, attempt: int, exc: Exception) -> float:
        """第 `attempt` 次重试（0-based）的等待秒数：Retry-After 优先，否则指数退避。"""
        retry_after = self._retry_after_seconds(exc)
        if retry_after is not None:
            return retry_after
        return self.backoff_base * 2**attempt + self.rng() * self.backoff_base

    def execute(self, operation: Callable[[], object], *, label: str) -> object:
        """执行一次带重试的 httpx 调用；耗尽后裸抛最后一个异常，保持原语义。"""
        for attempt in range(self.max_retries + 1):
            try:
                return operation()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                if not self.is_retryable(exc) or attempt >= self.max_retries:
                    raise
                delay = self.delay_for(attempt, exc)
                logger.warning(
                    "MinerU %s 第 %d/%d 次调用失败（%s），%.3f 秒后重试",
                    label,
                    attempt + 1,
                    self.max_retries,
                    _describe_error(exc),
                    delay,
                )
                self.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _auth_header(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def request_upload_urls(
    file_names: list[str],
    *,
    api_key: str,
    client: httpx.Client,
    policy: RetryPolicy | None = None,
) -> tuple[str, list[str]]:
    """申请批量上传链接，返回 `(batch_id, file_urls)`（与文件顺序一一对应）。"""
    policy = policy or RetryPolicy.from_settings()
    payload = {
        "files": [
            {"name": name, "is_ocr": settings.mineru_is_ocr}
            for name in file_names
        ],
        "model_version": settings.mineru_model_version,
        "enable_formula": settings.mineru_enable_formula,
        "enable_table": settings.mineru_enable_table,
        "language": settings.mineru_language,
    }

    def _post() -> httpx.Response:
        resp = client.post(
            settings.mineru_batch_upload_endpoint,
            json=payload,
            headers=_auth_header(api_key),
        )
        resp.raise_for_status()
        return resp

    resp = policy.execute(_post, label="申请上传链接")
    body = resp.json()
    data = body.get("data", {})
    batch_id = data.get("batch_id")
    urls = data.get("file_urls", [])
    if not batch_id or not urls:
        raise ParseError(f"MinerU 申请上传链接失败：{body.get('msg')}")
    return batch_id, urls


def _upload_file(
    url: str,
    path: Path,
    client: httpx.Client,
    policy: RetryPolicy | None = None,
) -> None:
    policy = policy or RetryPolicy.from_settings()
    content = path.read_bytes()  # 同一 URL、同一字节序列重试，PUT 覆盖语义幂等

    def _put() -> None:
        resp = client.put(url, content=content)
        resp.raise_for_status()

    policy.execute(_put, label="上传分片")


def _poll_batch(
    batch_id: str,
    api_key: str,
    client: httpx.Client,
    policy: RetryPolicy | None = None,
) -> list[dict]:
    """轮询直到所有文件进入终态，返回 `extract_result` 列表。"""
    policy = policy or RetryPolicy.from_settings()
    url = settings.mineru_batch_result_endpoint.format(batch_id=batch_id)
    for _ in range(settings.mineru_max_polls):
        def _get() -> httpx.Response:
            resp = client.get(url, headers=_auth_header(api_key))
            resp.raise_for_status()
            return resp

        resp = policy.execute(_get, label="轮询批量结果")
        results = resp.json().get("data", {}).get("extract_result", [])
        if not results:
            raise ParseError("MinerU 批量结果为空")
        if {r.get("state") for r in results} <= _TERMINAL_STATES:
            return results
        time.sleep(settings.mineru_poll_interval)
    raise ParseError("MinerU 解析超时")


def _download(
    url: str,
    client: httpx.Client,
    policy: RetryPolicy | None = None,
) -> bytes:
    policy = policy or RetryPolicy.from_settings()

    def _get() -> httpx.Response:
        resp = client.get(url, timeout=settings.mineru_download_timeout)
        resp.raise_for_status()
        return resp

    return policy.execute(_get, label="下载结果压缩包").content


def _safe_extract(zip_bytes: bytes, dest_dir: Path) -> None:
    """安全解压：拒绝路径穿越成员。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = dest_dir.resolve()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for member in archive.namelist():
            if not (root / member).resolve().is_relative_to(root):
                raise ParseError("MinerU 压缩包含越界路径，已拒绝解压")
        archive.extractall(dest_dir)


def find_output_files(extracted_dir: Path) -> tuple[Path | None, Path | None, Path | None]:
    """在解压目录定位 `(markdown_path, content_list_path, images_dir)`。

    - Markdown 优先 `full.md`，否则取排序后的第一个 `*.md`；
    - content_list 取排序后的第一个 `*content_list*.json`。
    """
    markdown_path = None
    markdown_files = sorted(extracted_dir.rglob("full.md"))
    if not markdown_files:
        markdown_files = sorted(extracted_dir.rglob("*.md"))
    if markdown_files:
        markdown_path = markdown_files[0]

    content_list_path = None
    content_files = sorted(extracted_dir.rglob("*content_list*.json"))
    if content_files:
        content_list_path = content_files[0]

    images_dir = extracted_dir / "images"
    if not images_dir.is_dir():
        images_dir = None

    return markdown_path, content_list_path, images_dir


def parse_cloud(
    shards: list[tuple[Path, int]],
    *,
    api_key: str,
    raw_dir: Path,
    client: httpx.Client | None = None,
    policy: RetryPolicy | None = None,
) -> list[MineruOutput]:
    """上传分片并解析，返回与 `shards` 同序的 `MineruOutput` 列表。

    `shards` 每项为 `(分片 PDF 路径, page_offset)`；`client` 为 None 时自建。
    `policy` 为 None 时按 settings 装配，四个 httpx 调用共用同一重试策略。
    """
    policy = policy or RetryPolicy.from_settings()
    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=settings.mineru_timeout, follow_redirects=True)

    try:
        file_names = [path.name for path, _ in shards]
        batch_id, urls = request_upload_urls(
            file_names, api_key=api_key, client=client, policy=policy
        )
        if len(urls) != len(shards):
            raise ParseError("MinerU 上传链接数量与分片数不一致")

        for (path, _), url in zip(shards, urls):
            _upload_file(url, path, client, policy)

        results = _poll_batch(batch_id, api_key, client, policy)

        outputs: list[MineruOutput] = []
        for (_, offset), result in zip(shards, results):
            if result.get("state") != "done":
                raise ParseError(
                    f"MinerU 解析失败：{result.get('err_msg') or result.get('file_name')}"
                )
            zip_url = result.get("full_zip_url")
            if not zip_url:
                raise ParseError("MinerU 结果缺少下载链接")

            shard_dir = raw_dir / f"shard_{offset:04d}"
            _safe_extract(_download(zip_url, client, policy), shard_dir)
            markdown_path, content_list_path, images_dir = find_output_files(shard_dir)
            outputs.append(
                MineruOutput(
                    content_list_path=content_list_path,
                    markdown_path=markdown_path,
                    page_offset=offset,
                    images_dir=images_dir,
                )
            )
        return outputs
    finally:
        if own_client:
            client.close()