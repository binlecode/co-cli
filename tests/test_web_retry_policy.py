"""Tests for shared web retry policy helpers."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx

from co_cli.tools._http_retry import (
    RETRYABLE_STATUS_CODES,
    classify_web_http_error,
    compute_backoff_delay,
    parse_retry_after,
)


def _status_error(
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
    body: str = "",
) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.com/resource")
    response = httpx.Response(
        status_code=status_code,
        headers=headers or {},
        text=body,
        request=request,
    )
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


def test_parse_retry_after_seconds_header():
    delay = parse_retry_after({"Retry-After": "7"}, max_seconds=60.0)
    assert delay == 7.0


def test_parse_retry_after_ms_header():
    delay = parse_retry_after({"Retry-After-Ms": "2500"}, max_seconds=60.0)
    assert delay == 2.5


def test_parse_retry_after_http_date():
    future = datetime.now(timezone.utc) + timedelta(seconds=5)
    delay = parse_retry_after({"Retry-After": format_datetime(future)}, max_seconds=60.0)
    assert delay is not None
    assert 0.0 <= delay <= 6.0


def test_parse_retry_after_body_fallback():
    delay = parse_retry_after({}, body='{"retry-after":"9"}', max_seconds=60.0)
    assert delay == 9.0


def test_parse_retry_after_caps_delay():
    delay = parse_retry_after({"Retry-After": "120"}, max_seconds=8.0)
    assert delay == 8.0


def test_classify_403_is_terminal():
    decision = classify_web_http_error(
        tool_name="web_fetch",
        target="https://example.com",
        error=_status_error(403),
    )
    assert decision.retryable is False
    assert decision.status_code == 403
    assert "origin policy denied access" in decision.message


def test_classify_429_is_retryable_and_uses_retry_after():
    decision = classify_web_http_error(
        tool_name="web_search",
        target="query",
        error=_status_error(429, headers={"Retry-After": "3"}),
    )
    assert decision.retryable is True
    assert decision.status_code == 429
    assert decision.delay_seconds == 3.0


def test_classify_500_is_retryable():
    decision = classify_web_http_error(
        tool_name="web_fetch",
        target="https://example.com",
        error=_status_error(500),
    )
    assert decision.retryable is True
    assert decision.status_code == 500
    assert 500 in RETRYABLE_STATUS_CODES


def test_classify_timeout_is_retryable():
    request = httpx.Request("GET", "https://example.com")
    decision = classify_web_http_error(
        tool_name="web_fetch",
        target="https://example.com",
        error=httpx.ReadTimeout("timed out", request=request),
    )
    assert decision.retryable is True
    assert "timed out" in decision.message


def test_compute_backoff_delay_without_jitter():
    delay = compute_backoff_delay(
        attempt=3,
        base_seconds=1.0,
        max_seconds=10.0,
        jitter_ratio=0.0,
    )
    assert delay == 4.0


def test_compute_backoff_delay_with_jitter_bounds():
    delay = compute_backoff_delay(
        attempt=2,
        base_seconds=2.0,
        max_seconds=10.0,
        jitter_ratio=0.25,
    )
    # attempt=2 => base delay=4.0, jitter=+/-1.0
    assert 3.0 <= delay <= 5.0
