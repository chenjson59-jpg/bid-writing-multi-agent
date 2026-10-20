"""MinerU 云端解析（精准解析 API，批量上传流程）。

流程：申请上传链接 → PUT 上传分片 → 轮询批量结果 → 下载 zip 并安全解压 →
定位 `content_list.json` / `full.md`，产出 `MineruOutput` 列表。

MinerU 契约（https://mineru.net/doc/docs/）：
- `POST /api/v4/file-urls/batch` 申请上传链接，返回 `batch_id` 与 `file_urls`；
- 文件上传完成后系统自动提交解析任务，无需额外调用；
- `GET /api/v4/extract-results/batch/{batch_id}` 轮询，`data.extract_result`
  为逐文件结果，`state=done` 时携带 `full_zip_url`；
- 解压 zip 含 `full.md` 与 `*_content_list.json`。
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import httpx

from src.config import settings
from src.errors import ParseError
from src.parsing.models import MineruOutput

# 批量结果的终态集合
_TERMINAL_STATES = {"done", "failed"}


def _auth_header(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def request_upload_urls(
    file_names: list[str],
    *,
    api_key: str,
    client: httpx.Client,
) -> tuple[str, list[str]]:
    """申请批量上传链接，返回 `(batch_id, file_urls)`（与文件顺序一一对应）。"""
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
    resp = client.post(
        settings.mineru_batch_upload_endpoint,
        json=payload,
        headers=_auth_header(api_key),
    )
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data", {})
    batch_id = data.get("batch_id")
    urls = data.get("file_urls", [])
    if not batch_id or not urls:
        raise ParseError(f"MinerU 申请上传链接失败：{body.get('msg')}")
    return batch_id, urls


def _upload_file(url: str, path: Path, client: httpx.Client) -> None:
    with path.open("rb") as handle:
        resp = client.put(url, content=handle.read())
    resp.raise_for_status()


def _poll_batch(batch_id: str, api_key: str, client: httpx.Client) -> list[dict]:
    """轮询直到所有文件进入终态，返回 `extract_result` 列表。"""
    url = settings.mineru_batch_result_endpoint.format(batch_id=batch_id)
    for _ in range(settings.mineru_max_polls):
        resp = client.get(url, headers=_auth_header(api_key))
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("extract_result", [])
        if not results:
            raise ParseError("MinerU 批量结果为空")
        if {r.get("state") for r in results} <= _TERMINAL_STATES:
            return results
        time.sleep(settings.mineru_poll_interval)
    raise ParseError("MinerU 解析超时")


def _download(url: str, client: httpx.Client) -> bytes:
    resp = client.get(url, timeout=settings.mineru_download_timeout)
    resp.raise_for_status()
    return resp.content


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
) -> list[MineruOutput]:
    """上传分片并解析，返回与 `shards` 同序的 `MineruOutput` 列表。

    `shards` 每项为 `(分片 PDF 路径, page_offset)`；`client` 为 None 时自建。
    """
    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=settings.mineru_timeout, follow_redirects=True)

    try:
        file_names = [path.name for path, _ in shards]
        batch_id, urls = request_upload_urls(file_names, api_key=api_key, client=client)
        if len(urls) != len(shards):
            raise ParseError("MinerU 上传链接数量与分片数不一致")

        for (path, _), url in zip(shards, urls):
            _upload_file(url, path, client)

        results = _poll_batch(batch_id, api_key, client)

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
            _safe_extract(_download(zip_url, client), shard_dir)
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
