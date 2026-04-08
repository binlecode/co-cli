"""Web intelligence tools: search (Brave) and fetch (direct HTTP)."""

import asyncio
import re
from urllib.parse import urlparse
from typing import Any

import html2text
import httpx
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.tools.tool_errors import tool_error
from co_cli.tools._http_retry import classify_web_http_error, compute_backoff_delay
from pydantic_ai.messages import ToolReturn
from co_cli.tools.tool_output import tool_output
from co_cli.tools._url_safety import is_url_safe

_MAX_RESULTS = 8
_SEARCH_TIMEOUT = 12
_FETCH_TIMEOUT = 15
_MAX_FETCH_CHARS = 100_000
_MAX_FETCH_BYTES = 1_048_576  # 1 MB pre-decode limit
_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_FETCH_USER_AGENT = "co-cli/0.3 (+https://github.com/binlecode/co-cli)"

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
    hostname: str, allowed: list[str], blocked: list[str],
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
        for domain in allowed:
            if hostname == domain or hostname.endswith("." + domain):
                return True
        return False
    return True


def _get_api_key(ctx: RunContext[CoDeps]) -> str:
    """Extract and validate Brave Search API key from context."""
    key = ctx.deps.config.brave_search_api_key
    if not key:
        raise ModelRetry(
            "Web search not configured. Set BRAVE_SEARCH_API_KEY in settings or env."
        )
    return key


def _is_cloudflare_challenge(resp: httpx.Response) -> bool:
    """Detect Cloudflare TLS fingerprint mismatch block.

    Cloudflare compares the User-Agent (browser-like) against the actual TLS
    handshake fingerprint (Python httpx).  When they mismatch, Cloudflare
    returns 403 with ``cf-mitigated: challenge``.  Retrying with an honest
    tool-only User-Agent removes the mismatch and often succeeds.
    """
    return (
        resp.status_code == 403
        and resp.headers.get("cf-mitigated") == "challenge"
    )


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
    if hostname and (
        hostname.endswith("wikipedia.org")
        or hostname.endswith("wikimedia.org")
    ):
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
                    url, headers=cf_fallback_headers, params=params,
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
                return tool_error(decision.message)

            if attempt >= attempts_total:
                return tool_error(
                    f"{decision.message} Retries exhausted ({max_retries})."
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

    return tool_error(f"{tool_name} failed for {target}.")


async def web_search(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
    domains: list[str] | None = None,
) -> ToolReturn:
    """Search the web via Brave Search. Returns ranked result snippets with
    titles and URLs. Each result includes a short text preview.

    For full page content, pass a result URL to web_fetch. Do not guess or
    fabricate URLs — always use URLs from these search results.

    Scope searches to specific sites with the domains parameter (e.g.
    domains=["docs.python.org"] to search only Python docs).

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


async def web_fetch(
    ctx: RunContext[CoDeps],
    url: str,
) -> ToolReturn:
    """Fetch a web page and return its content converted to readable markdown.
    HTML pages are converted to markdown; JSON and XML are returned as-is.

    Accepts any URL — from the user's message, from web_search results, or
    from tool output. Never guess or fabricate URLs yourself.
    If fetch returns 403 or is blocked by Cloudflare, retry the same URL
    with run_shell_command: curl -sL <url>.

    Returns a dict with:
    - display: page content as markdown text — show directly to the user
    - url: final URL after redirects
    - content_type: the response Content-Type
    - truncated: true if content was cut to fit size limits

    Caveats:
    - Only fetches text-based content (HTML, JSON, XML, plain text). Binary
      formats (images, PDFs, zip) are rejected
    - Content is truncated at ~100K characters
    - Some sites block automated fetches — use curl fallback via shell if needed
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
        raise ModelRetry("web_fetch blocked: redirect target resolves to a private or internal address.")

    content_type = resp.headers.get("content-type", "")

    if not _is_content_type_allowed(content_type):
        return tool_error(
            f"web_fetch blocked: unsupported content type '{content_type}'. "
            "Only text and structured data formats are supported."
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
