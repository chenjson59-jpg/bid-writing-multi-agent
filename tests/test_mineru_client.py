"""MinerU 云端客户端传输重试/退避测试（T-026）。

全部离线：以 httpx.MockTransport 脚本化故障注入，sleep/rng 均注入替身，
零真实网络、零真实等待。业务错误（2xx 响应体内的失败）不进入传输重试。
"""
from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from src.config import settings
from src.errors import ParseError
from src.parsing.mineru_client import (
    RetryPolicy,
    _download,
    _poll_batch,
    _upload_file,
    parse_cloud,
    request_upload_urls,
)


class FakeSleep:
    """记录每次睡眠秒数，不真实等待。"""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _policy(
    *, max_retries: int = 3, base: float = 1.0, sleep: FakeSleep | None = None
) -> RetryPolicy:
    return RetryPolicy(
        max_retries=max_retries,
        backoff_base=base,
        sleep=sleep or FakeSleep(),
        rng=lambda: 0.0,  # 固定零抖动，退避序列可精确断言
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_batch_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://s3.test/put"]}},
    )


def _status_error(status_code: int, *, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    """构造与真实 raise_for_status 同构的 HTTPStatusError。"""
    request = httpx.Request("GET", "https://mineru.test/x")
    response = httpx.Response(status_code, headers=headers or {}, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status_code}'", request=request, response=response
    )


def _make_pdf_file(tmp_path, name: str = "a.pdf"):
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4 fake")
    return pdf


# ---- 可重试错误：先失败 N 次后成功 ----


def test_post_retries_503_then_succeeds_with_backoff_sequence():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            return httpx.Response(503, json={"msg": "busy"})
        return _ok_batch_response()

    sleep = FakeSleep()
    client = _client(handler)

    batch_id, urls = request_upload_urls(
        ["a.pdf"],
        api_key="key",
        client=client,
        policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
    )

    assert attempts == 3  # 初始 1 次 + 重试 2 次
    assert sleep.calls == [pytest.approx(1.0), pytest.approx(2.0)]  # base*2^0、base*2^1
    assert batch_id == "batch-1"
    assert urls == ["https://s3.test/put"]


def test_retry_after_header_overrides_formula():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return _ok_batch_response()

    sleep = FakeSleep()
    client = _client(handler)

    request_upload_urls(
        ["a.pdf"], api_key="key", client=client,
        policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
    )

    assert sleep.calls == [pytest.approx(3.0)]


@pytest.mark.parametrize("bad_header", ["abc", "200", "0", "-5"])
def test_invalid_or_exceeding_retry_after_falls_back_to_formula(bad_header):
    def handler(request: httpx.Request) -> httpx.Response:
        if not getattr(handler, "called", False):
            handler.called = True
            return httpx.Response(503, headers={"Retry-After": bad_header})
        return _ok_batch_response()

    sleep = FakeSleep()
    client = _client(handler)

    request_upload_urls(
        ["a.pdf"], api_key="key", client=client,
        policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
    )

    assert sleep.calls == [pytest.approx(1.0)]  # 回退 base*2^0 + 0 抖动


def test_poll_retries_transport_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "mineru_poll_interval", 0)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise httpx.ReadTimeout("simulated timeout")
        return httpx.Response(
            200,
            json={"data": {"extract_result": [
                {"state": "done", "full_zip_url": "https://s3.test/z.zip"}
            ]}},
        )

    sleep = FakeSleep()
    client = _client(handler)

    results = _poll_batch(
        "batch-1", "key", client, policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0)
    )

    assert attempts == 3
    assert len(sleep.calls) == 2
    assert results[0]["state"] == "done"


def test_put_upload_retries_500_then_succeeds(tmp_path):
    pdf = _make_pdf_file(tmp_path)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, text="server error")
        return httpx.Response(200)

    sleep = FakeSleep()
    client = _client(handler)

    _upload_file(
        "https://s3.test/put", pdf, client,
        policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
    )

    assert attempts == 2
    assert len(sleep.calls) == 1


def test_download_retries_until_limit_then_reraises_original_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, text="server error")

    sleep = FakeSleep()
    client = _client(handler)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        _download(
            "https://s3.test/z.zip", client,
            policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
        )

    assert exc_info.value.response.status_code == 500
    assert attempts == 4  # 初始 + 3 次重试
    assert len(sleep.calls) == 3


# ---- 不可重试错误：立即失败、零 sleep ----


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_4xx_never_retried(status_code):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, json={"msg": "no"})

    sleep = FakeSleep()
    client = _client(handler)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        request_upload_urls(
            ["a.pdf"], api_key="key", client=client,
            policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
        )

    assert exc_info.value.response.status_code == status_code
    assert attempts == 1
    assert sleep.calls == []


def test_zero_max_retries_matches_legacy_behavior():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    sleep = FakeSleep()
    client = _client(handler)

    with pytest.raises(httpx.HTTPStatusError):
        request_upload_urls(
            ["a.pdf"], api_key="key", client=client,
            policy=RetryPolicy(0, 1.0, sleep, lambda: 0.0),
        )

    assert attempts == 1
    assert sleep.calls == []


# ---- 2xx 业务错误：不进入传输重试 ----


def test_missing_batch_id_is_business_error_no_retry():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"code": 1, "data": {}, "msg": "bad request"})

    sleep = FakeSleep()
    client = _client(handler)

    with pytest.raises(ParseError, match="申请上传链接失败"):
        request_upload_urls(
            ["a.pdf"], api_key="key", client=client,
            policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
        )

    assert attempts == 1
    assert sleep.calls == []


def test_poll_empty_result_fails_fast_without_retry(monkeypatch):
    monkeypatch.setattr(settings, "mineru_poll_interval", 0)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"data": {"extract_result": []}})

    sleep = FakeSleep()
    client = _client(handler)

    with pytest.raises(ParseError, match="批量结果为空"):
        _poll_batch(
            "batch-1", "key", client,
            policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
        )

    assert attempts == 1
    assert sleep.calls == []


def test_state_failed_is_terminal_business_error(tmp_path):
    pdf = _make_pdf_file(tmp_path)
    poll_attempts = 0
    sleep = FakeSleep()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_attempts
        if request.method == "POST":
            return _ok_batch_response()
        if request.method == "PUT":
            return httpx.Response(200)
        poll_attempts += 1
        return httpx.Response(
            200,
            json={"data": {"extract_result": [
                {"state": "failed", "err_msg": "engine crash", "file_name": "a.pdf"}
            ]}},
        )

    client = _client(handler)

    with pytest.raises(ParseError, match="MinerU 解析失败"):
        parse_cloud(
            [(pdf, 0)], api_key="key", raw_dir=tmp_path / "raw",
            client=client, policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
        )

    assert poll_attempts == 1  # 终态失败不重试传输
    assert sleep.calls == []


def test_zip_traversal_is_business_error_no_retry(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("../evil.txt", b"x")
    zip_bytes = buf.getvalue()

    download_attempts = 0
    sleep = FakeSleep()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal download_attempts
        if request.method == "POST":
            return _ok_batch_response()
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET" and "extract-results" in str(request.url):
            return httpx.Response(
                200,
                json={"data": {"extract_result": [
                    {"state": "done", "full_zip_url": "https://s3.test/z.zip"}
                ]}},
            )
        download_attempts += 1
        return httpx.Response(200, content=zip_bytes)

    client = _client(handler)

    with pytest.raises(ParseError, match="越界路径"):
        parse_cloud(
            [(_make_pdf_file(tmp_path), 0)],
            api_key="key", raw_dir=tmp_path / "raw",
            client=client, policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
        )

    assert download_attempts == 1
    assert sleep.calls == []


# ---- 退避公式、分类与配置装配 ----


def test_backoff_formula_uses_base_power_and_jitter():
    sleep = FakeSleep()
    rng_values = iter([0.1, 0.2, 0.3])
    policy = RetryPolicy(3, 1.0, sleep, lambda: next(rng_values))
    exc = httpx.ReadTimeout("simulated")

    assert policy.delay_for(0, exc) == pytest.approx(1.1)  # 1*2^0 + 0.1
    assert policy.delay_for(1, exc) == pytest.approx(2.2)  # 1*2^1 + 0.2
    assert policy.delay_for(2, exc) == pytest.approx(4.3)  # 1*2^2 + 0.3


def test_retry_after_precedence_in_delay_for():
    policy = _policy()
    assert policy.delay_for(0, _status_error(429, headers={"Retry-After": "10"})) == pytest.approx(10.0)
    assert policy.delay_for(0, httpx.ReadTimeout("simulated")) == pytest.approx(1.0)


def test_retryable_classification():
    policy = _policy()

    assert policy.is_retryable(_status_error(429)) is True
    assert policy.is_retryable(_status_error(500)) is True
    assert policy.is_retryable(_status_error(503)) is True
    assert policy.is_retryable(_status_error(401)) is False
    assert policy.is_retryable(_status_error(403)) is False
    assert policy.is_retryable(_status_error(400)) is False
    assert policy.is_retryable(httpx.ReadTimeout("x")) is True
    assert policy.is_retryable(httpx.ConnectError("x")) is True
    assert policy.is_retryable(ValueError("not transport")) is False


def test_default_policy_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, "mineru_max_retries", 5)
    monkeypatch.setattr(settings, "mineru_retry_backoff_base_seconds", 0.5)

    policy = RetryPolicy.from_settings()

    assert policy.max_retries == 5
    assert policy.backoff_base == pytest.approx(0.5)


def test_parse_cloud_happy_path_with_injected_policy(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("full.md", "# 解析结果\n")
        archive.writestr("a_content_list.json", json_content_list())
    zip_bytes = buf.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_batch_response()
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET" and "extract-results" in str(request.url):
            return httpx.Response(
                200,
                json={"data": {"extract_result": [
                    {"state": "done", "full_zip_url": "https://s3.test/z.zip"}
                ]}},
            )
        return httpx.Response(200, content=zip_bytes)

    sleep = FakeSleep()
    client = _client(handler)

    outputs = parse_cloud(
        [(_make_pdf_file(tmp_path), 0)],
        api_key="key", raw_dir=tmp_path / "raw",
        client=client, policy=RetryPolicy(3, 1.0, sleep, lambda: 0.0),
    )

    assert len(outputs) == 1
    assert outputs[0].page_offset == 0
    assert sleep.calls == []  # 全程无故障，零重试零睡眠


def json_content_list() -> str:
    import json

    return json.dumps([{"type": "text", "text": "正文", "page_idx": 0}])