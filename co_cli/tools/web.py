"""Web intelligence tools: search (Brave) and fetch (direct HTTP)."""

import asyncio
import ipaddress
import random
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib.metadata import version as _pkg_version
from ipaddress import IPv4Network, IPv6Network
from typing import Any
from urllib.parse import urlparse

import html2text
import httpx
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools._agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output, tool_output_raw

# ---------------------------------------------------------------------------
# SSRF protection (inlined from _url_safety.py — only used by this module)
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS: tuple[IPv4Network | IPv6Network, ...] = (
    IPv4Network("127.0.0.0/8"),
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
    IPv4Network("169.254.0.0/16"),
    IPv4Network("100.64.0.0/10"),
    IPv6Network("::1/128"),
    IPv6Network("fe80::/10"),
    IPv6Network("fc00::/7"),
)

_BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.internal",
    }
)


def is_url_safe(url: str) -> bool:
    """Check whether *url* resolves to a public (non-private) address.

    Returns ``False`` (fail-closed) for:
    - Unresolvable hostnames or DNS errors
    - IPs in private/loopback/link-local/metadata ranges
    - Cloud metadata hostnames
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except Exception:
        return False

    if not hostname:
        return False

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        return False

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError):
        return False

    for info in addr_infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                return False

    return True


# ---------------------------------------------------------------------------
# HTTP retry helpers (inlined from _http_retry.py — only used by this module)
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


# ---------------------------------------------------------------------------
# Web tool constants
# ---------------------------------------------------------------------------

_MAX_RESULTS = 8
_SEARCH_TIMEOUT = 12
_FETCH_TIMEOUT = 15
_MAX_FETCH_CHARS = 100_000
_MAX_FETCH_BYTES = 1_048_576  # 1 MB pre-decode limit
_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

_FETCH_USER_AGENT = f"co-cli/{_pkg_version('co-cli')} (+https://github.com/binlecode/co-cli)"

_ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/x-yaml",
    "application/yaml",
)


def _is_content_type_allowed(content_type: str) -> bool:
    """Check whether a Content-Type header value is in the text allowlist.

    Empty Content-Type is allowed (servers often omit it for text).
    """
    if not content_type:
        return True
    mime = content_type.split(";")[0].strip().lower()
    return any(mime.startswith(prefix) for prefix in _ALLOWED_CONTENT_TYPES)


def _is_domain_allowed(
    hostname: str,
    allowed: list[str],
    blocked: list[str],
) -> bool:
    """Check whether a hostname passes domain policy.

    - If hostname matches any entry in *blocked* (exact or subdomain) → False
    - If *allowed* is non-empty and hostname doesn't match any entry → False
    - Otherwise → True
    """
    hostname = hostname.lower()
    for domain in blocked:
        if hostname == domain or hostname.endswith("." + domain):
            return False
    if allowed:
        return any(hostname == domain or hostname.endswith("." + domain) for domain in allowed)
    return True


def _get_api_key(ctx: RunContext[CoDeps]) -> str:
    """Extract and validate Brave Search API key from context."""
    key = ctx.deps.config.brave_search_api_key
    if not key:
        raise ModelRetry("Web search not configured. Set BRAVE_SEARCH_API_KEY in settings or env.")
    return key


def _is_cloudflare_challenge(resp: httpx.Response) -> bool:
    """Detect Cloudflare TLS fingerprint mismatch block.

    Cloudflare compares the User-Agent (browser-like) against the actual TLS
    handshake fingerprint (Python httpx).  When they mismatch, Cloudflare
    returns 403 with ``cf-mitigated: challenge``.  Retrying with an honest
    tool-only User-Agent removes the mismatch and often succeeds.
    """
    return resp.status_code == 403 and resp.headers.get("cf-mitigated") == "challenge"


def _build_fetch_headers(hostname: str | None) -> dict[str, str]:
    """Build request headers for web fetch.

    Wikimedia endpoints may block generic/scraper signatures unless a policy-
    compliant agent string is provided. Add Api-User-Agent for Wikimedia hosts.
    """
    headers = {
        "User-Agent": _FETCH_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if hostname and (hostname.endswith("wikipedia.org") or hostname.endswith("wikimedia.org")):
        headers["Api-User-Agent"] = _FETCH_USER_AGENT
    return headers


# Minimal honest headers used when Cloudflare blocks the primary request.
_CF_FALLBACK_HEADERS: dict[str, str] = {"User-Agent": _FETCH_USER_AGENT}


def _html_to_markdown(html: str) -> str:
    """Convert HTML to readable markdown text."""
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0  # No line wrapping
    return converter.handle(html)


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
        except httpx.HTTPError as e:
            decision = classify_web_http_error(
                tool_name=tool_name,
                target=target,
                error=e,
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
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}** — {r['snippet']}")
        lines.append(f"   {r['url']}")
        lines.append("")

    display = f"Web search results for '{query}':\n\n" + "\n".join(lines).rstrip()
    return tool_output(display, ctx=ctx, results=results, count=len(results))


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
    retries=3,
)
async def web_fetch(
    ctx: RunContext[CoDeps],
    url: str,
) -> ToolReturn:
    """Fetch a web page and return its content as readable markdown text.

    Returns the fetched page content directly — HTML is converted to markdown,
    JSON and XML are returned as-is. This is a direct HTTP fetch, not an
    extraction or summarization step.

    Accepts any URL — from the user's message, from web_search results, or
    from tool output. Never guess or fabricate URLs yourself.

    Shell fallback: if fetch returns 403 or is blocked by Cloudflare, retry
    with run_shell_command: curl -sL <url>. Use the shell fallback only for
    fetch failures or site-specific blocking, not as the default path.

    Returns a dict with:
    - display: page content as markdown text — show directly to the user
    - url: final URL after redirects
    - content_type: the response Content-Type
    - truncated: true if content was cut to fit size limits

    Caveats:
    - Only fetches text-based content (HTML, JSON, XML, plain text). Binary
      formats (images, PDFs, zip) are rejected
    - Content is truncated at ~100K characters
    - Domain allow/block lists from settings are enforced

    Args:
        url: Full URL to fetch (must start with http:// or https://).
    """
    if not url or not re.match(r"https?://", url.strip()):
        raise ModelRetry("web_fetch requires an http:// or https:// URL.")

    url = url.strip()

    hostname = urlparse(url).hostname
    if hostname and not _is_domain_allowed(
        hostname,
        ctx.deps.config.web.fetch_allowed_domains,
        ctx.deps.config.web.fetch_blocked_domains,
    ):
        raise ModelRetry(f"web_fetch blocked: domain '{hostname}' not allowed by policy.")

    if not is_url_safe(url):
        raise ModelRetry("web_fetch blocked: URL resolves to a private or internal address.")

    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT,
        follow_redirects=True,
        max_redirects=5,
    ) as client:
        resp_or_error = await _http_get_with_retries(
            client=client,
            tool_name="web_fetch",
            target=url,
            url=url,
            headers=_build_fetch_headers(hostname),
            params=None,
            max_retries=ctx.deps.config.web.http_max_retries,
            backoff_base_seconds=ctx.deps.config.web.http_backoff_base_seconds,
            backoff_max_seconds=ctx.deps.config.web.http_backoff_max_seconds,
            backoff_jitter_ratio=ctx.deps.config.web.http_jitter_ratio,
            cf_fallback_headers=_CF_FALLBACK_HEADERS,
        )
    if not isinstance(resp_or_error, httpx.Response):
        return resp_or_error
    resp = resp_or_error

    final_url = str(resp.url)
    if final_url != url and not is_url_safe(final_url):
        raise ModelRetry(
            "web_fetch blocked: redirect target resolves to a private or internal address."
        )

    content_type = resp.headers.get("content-type", "")

    if not _is_content_type_allowed(content_type):
        return tool_error(
            f"web_fetch blocked: unsupported content type '{content_type}'. "
            "Only text and structured data formats are supported.",
            ctx=ctx,
        )

    raw_bytes = resp.content[:_MAX_FETCH_BYTES]
    text = raw_bytes.decode(resp.encoding or "utf-8", errors="replace")

    if "html" in content_type:
        text = _html_to_markdown(text)

    truncated = len(text) > _MAX_FETCH_CHARS
    if truncated:
        text = text[:_MAX_FETCH_CHARS]

    display = f"Content from {final_url}:\n\n{text}"

    return tool_output(
        display,
        ctx=ctx,
        url=final_url,
        content_type=content_type,
        truncated=truncated,
    )
