"""Shared HTTP retry helpers for web tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import re
from typing import Mapping

import httpx


RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
TERMINAL_STATUS_CODES = {400, 401, 403, 404, 422}


@dataclass(frozen=True)
class WebRetryDecision:
    retryable: bool
    message: str
    delay_seconds: float = 0.0
    status_code: int | None = None


def _parse_seconds(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        val = float(raw.strip())
    except ValueError:
        return None
    if val < 0:
        return 0.0
    return val


def _parse_retry_after_date(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (dt - now).total_seconds())


def parse_retry_after(
    headers: Mapping[str, str] | None,
    body: object | None = None,
    *,
    max_seconds: float = 60.0,
) -> float | None:
    """Parse retry delay from headers/body and cap to max_seconds."""
    if headers:
        header_map = {k.lower(): v for k, v in headers.items()}

        retry_after_ms = _parse_seconds(header_map.get("retry-after-ms"))
        if retry_after_ms is not None:
            return min(max(retry_after_ms / 1000.0, 0.0), max_seconds)

        retry_after = header_map.get("retry-after")
        delay = _parse_seconds(retry_after)
        if delay is None:
            delay = _parse_retry_after_date(retry_after)
        if delay is not None:
            return min(delay, max_seconds)

    if body is not None:
        text = str(body)
        match = re.search(r'retry[_-]after["\s:]+(\d+(?:\.\d+)?)', text, re.IGNORECASE)
        if match:
            return min(float(match.group(1)), max_seconds)

    return None


def compute_backoff_delay(
    *,
    attempt: int,
    base_seconds: float,
    max_seconds: float,
    jitter_ratio: float,
) -> float:
    """Compute capped exponential backoff with bounded jitter."""
    attempt = max(attempt, 1)
    base_seconds = max(base_seconds, 0.0)
    max_seconds = max(max_seconds, 0.0)
    jitter_ratio = min(max(jitter_ratio, 0.0), 1.0)

    delay = min(base_seconds * (2 ** (attempt - 1)), max_seconds)
    if jitter_ratio == 0.0:
        return delay

    jitter = delay * jitter_ratio
    low = max(0.0, delay - jitter)
    high = min(max_seconds, delay + jitter)
    if high < low:
        high = low
    return random.uniform(low, high)


def classify_web_http_error(
    *,
    tool_name: str,
    target: str,
    error: httpx.HTTPError,
    max_retry_after_seconds: float = 60.0,
) -> WebRetryDecision:
    """Classify an HTTP error into retryable or terminal behavior."""
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        delay = parse_retry_after(
            error.response.headers,
            error.response.text,
            max_seconds=max_retry_after_seconds,
        )

        if code in RETRYABLE_STATUS_CODES:
            if code == 429:
                return WebRetryDecision(
                    retryable=True,
                    message=f"{tool_name} rate limited (HTTP 429) for {target}.",
                    delay_seconds=delay or 1.0,
                    status_code=code,
                )
            return WebRetryDecision(
                retryable=True,
                message=f"{tool_name} transient HTTP {code} for {target}.",
                delay_seconds=delay or 1.0,
                status_code=code,
            )

        if code in TERMINAL_STATUS_CODES or 400 <= code < 500:
            if code == 401:
                msg = f"{tool_name} blocked (HTTP 401) for {target}: authentication required."
            elif code == 403:
                msg = f"{tool_name} blocked (HTTP 403) for {target}: origin policy denied access."
            elif code == 404:
                msg = f"{tool_name} not found (HTTP 404) for {target}."
            elif code == 422:
                msg = f"{tool_name} rejected (HTTP 422) for {target}: request not processable."
            else:
                msg = f"{tool_name} rejected (HTTP {code}) for {target}."
            return WebRetryDecision(
                retryable=False, message=msg, status_code=code,
            )

        return WebRetryDecision(
            retryable=True,
            message=f"{tool_name} server error (HTTP {code}) for {target}.",
            delay_seconds=delay or 1.0,
            status_code=code,
        )

    if isinstance(error, httpx.TimeoutException):
        return WebRetryDecision(
            retryable=True,
            message=f"{tool_name} timed out while contacting {target}.",
            delay_seconds=1.0,
        )

    if isinstance(error, httpx.RequestError):
        return WebRetryDecision(
            retryable=True,
            message=f"{tool_name} network error while contacting {target}: {error}.",
            delay_seconds=1.0,
        )

    return WebRetryDecision(
        retryable=True,
        message=f"{tool_name} transport error for {target}: {error}.",
        delay_seconds=1.0,
    )
