"""Web search tool — Brave Search API with retry and backoff."""

import asyncio
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output, tool_output_raw

# ---------------------------------------------------------------------------
# HTTP retry helpers
# ---------------------------------------------------------------------------

RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
TERMINAL_STATUS_CODES = {400, 401, 403, 404, 422}


@dataclass(frozen=True)
class WebRetryResult:
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
        dt = dt.replace(tzinfo=UTC)
    now = datetime.now(UTC)
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
) -> WebRetryResult:
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
                return WebRetryResult(
                    retryable=True,
                    message=f"{tool_name} rate limited (HTTP 429) for {target}.",
                    delay_seconds=delay or 1.0,
                    status_code=code,
                )
            return WebRetryResult(
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
            return WebRetryResult(
                retryable=False,
                message=msg,
                status_code=code,
            )

        return WebRetryResult(
            retryable=True,
            message=f"{tool_name} server error (HTTP {code}) for {target}.",
            delay_seconds=delay or 1.0,
            status_code=code,
        )

    if isinstance(error, httpx.TimeoutException):
        return WebRetryResult(
            retryable=True,
            message=f"{tool_name} timed out while contacting {target}.",
            delay_seconds=1.0,
        )

    if isinstance(error, httpx.RequestError):
        return WebRetryResult(
            retryable=True,
            message=f"{tool_name} network error while contacting {target}: {error}.",
            delay_seconds=1.0,
        )

    return WebRetryResult(
        retryable=True,
        message=f"{tool_name} transport error for {target}: {error}.",
        delay_seconds=1.0,
    )


async def _http_get_with_retries(
    *,
    client: httpx.AsyncClient,
    tool_name: str,
    target: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None,
    max_retries: int,
    backoff_base_seconds: float,
    backoff_max_seconds: float,
    backoff_jitter_ratio: float,
    cf_fallback_headers: dict[str, str] | None = None,
) -> "httpx.Response | ToolReturn":
    attempts_total = max(0, max_retries) + 1

    for attempt in range(1, attempts_total + 1):
        try:
            resp = await client.get(url, headers=headers, params=params)
            # Cloudflare TLS fingerprint mismatch — retry once with
            # honest headers before falling through to error handling.
            if cf_fallback_headers and _is_cloudflare_challenge(resp):
                resp = await client.get(
                    url,
                    headers=cf_fallback_headers,
                    params=params,
                )
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            decision = classify_web_http_error(
                tool_name=tool_name,
                target=target,
                error=exc,
                max_retry_after_seconds=backoff_max_seconds,
            )
            if not decision.retryable:
                return tool_output_raw(decision.message, error=True)

            if attempt >= attempts_total:
                return tool_output_raw(
                    f"{decision.message} Retries exhausted ({max_retries}).", error=True
                )

            delay = compute_backoff_delay(
                attempt=attempt,
                base_seconds=backoff_base_seconds,
                max_seconds=backoff_max_seconds,
                jitter_ratio=backoff_jitter_ratio,
            )
            if decision.delay_seconds > 0:
                delay = max(delay, min(decision.delay_seconds, backoff_max_seconds))
            await asyncio.sleep(delay)

    return tool_output_raw(f"{tool_name} failed for {target}.", error=True)


def _is_cloudflare_challenge(resp: httpx.Response) -> bool:
    """Detect Cloudflare TLS fingerprint mismatch block."""
    return resp.status_code == 403 and resp.headers.get("cf-mitigated") == "challenge"


# ---------------------------------------------------------------------------
# Web search constants
# ---------------------------------------------------------------------------

_MAX_RESULTS = 8
_SEARCH_TIMEOUT = 12
_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def _get_api_key(ctx: RunContext[CoDeps]) -> str:
    """Extract and validate Brave Search API key from context."""
    key = ctx.deps.config.brave_search_api_key
    if not key:
        raise ModelRetry("Web search not configured. Set BRAVE_SEARCH_API_KEY in settings or env.")
    return key


# ---------------------------------------------------------------------------
# web_search tool
# ---------------------------------------------------------------------------


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
    retries=3,
)
async def web_search(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
    domains: list[str] | None = None,
) -> ToolReturn:
    """Search the web for current or external information via Brave Search.

    Use for up-to-date external information: documentation, release notes,
    API references, news, or anything not available in the local workspace.
    Returns ranked snippets with titles and URLs. For full page content,
    pass a result URL to web_fetch.

    Do not guess or fabricate URLs — always use URLs from these results.
    Scope searches to specific sites with the domains parameter (e.g.
    domains=["docs.python.org"]).

    Returns a dict with:
    - display: numbered results with title, snippet, and URL — show directly
      to the user
    - results: list of {title, url, snippet} dicts
    - count: number of results returned

    Caveats:
    - Max 8 results per call (capped regardless of max_results value)
    - Requires BRAVE_SEARCH_API_KEY to be configured

    Args:
        query: Search query string (e.g. "python asyncio tutorial",
               "latest pydantic-ai release notes").
        max_results: Number of results to return (default 5, max 8).
        domains: Restrict to these domains (e.g. ["github.com", "stackoverflow.com"]).
    """
    if not query or not query.strip():
        raise ModelRetry("Query is required for web_search.")

    api_key = _get_api_key(ctx)
    capped = min(max_results, _MAX_RESULTS)

    effective_query = query.strip()
    if domains:
        site_prefix = " OR ".join(f"site:{d}" for d in domains)
        effective_query = f"{site_prefix} {effective_query}"

    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
        resp_or_error = await _http_get_with_retries(
            client=client,
            tool_name="web_search",
            target=effective_query,
            url=_BRAVE_SEARCH_URL,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
            params={"q": effective_query, "count": capped},
            max_retries=ctx.deps.config.web.http_max_retries,
            backoff_base_seconds=ctx.deps.config.web.http_backoff_base_seconds,
            backoff_max_seconds=ctx.deps.config.web.http_backoff_max_seconds,
            backoff_jitter_ratio=ctx.deps.config.web.http_jitter_ratio,
        )
    if not isinstance(resp_or_error, httpx.Response):
        return resp_or_error
    resp = resp_or_error

    data = resp.json()
    raw_results = data.get("web", {}).get("results", [])

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", ""),
        }
        for r in raw_results
    ]

    if not results:
        return tool_output(f"No results for '{query}'.", ctx=ctx, results=[], count=0)

    lines = []
    for idx, r in enumerate(results, 1):
        lines.append(f"{idx}. **{r['title']}** — {r['snippet']}")
        lines.append(f"   {r['url']}")
        lines.append("")

    display = f"Web search results for '{query}':\n\n" + "\n".join(lines).rstrip()
    return tool_output(display, ctx=ctx, results=results, count=len(results))
