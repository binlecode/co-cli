"""Web intelligence tools: search (Brave) and fetch (direct HTTP)."""

import asyncio
import re
from urllib.parse import urlparse
from typing import Any

import html2text
import httpx
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.tools._errors import terminal_error
from co_cli.tools._http_retry import classify_web_http_error, compute_backoff_delay
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
    key = ctx.deps.brave_search_api_key
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
) -> httpx.Response | dict[str, Any]:
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
                return terminal_error(decision.message)

            if attempt >= attempts_total:
                return terminal_error(
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

    return terminal_error(f"{tool_name} failed for {target}.")


async def web_search(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
    domains: list[str] | None = None,
) -> dict[str, Any]:
    """Search the web via Brave Search. Returns result snippets with URLs.

    For full page content, follow up with web_fetch on result URLs.
    Do not guess URLs — always use URLs from search results.

    Args:
        query: Search query string.
        max_results: Number of results to return (default 5, max 8).
        domains: Optional list of domains to scope the search to (adds site: operators).
    """
    if ctx.deps.web_policy.search == "deny":
        raise ModelRetry("web_search: web access disabled by policy.")

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
            max_retries=ctx.deps.web_http_max_retries,
            backoff_base_seconds=ctx.deps.web_http_backoff_base_seconds,
            backoff_max_seconds=ctx.deps.web_http_backoff_max_seconds,
            backoff_jitter_ratio=ctx.deps.web_http_jitter_ratio,
        )
    if isinstance(resp_or_error, dict):
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
        return {"display": f"No results for '{query}'.", "results": [], "count": 0}

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}** — {r['snippet']}")
        lines.append(f"   {r['url']}")
        lines.append("")

    display = "\n".join(lines).rstrip()
    return {"display": display, "results": results, "count": len(results)}


async def web_fetch(
    ctx: RunContext[CoDeps],
    url: str,
) -> dict[str, Any]:
    """Fetch a web page and return its content as markdown.

    Use URLs from web_search results. If fetch returns 403 or is blocked,
    retry the same URL with run_shell_command: curl -sL <url>.

    Args:
        url: The URL to fetch (must be http:// or https://).
    """
    if ctx.deps.web_policy.fetch == "deny":
        raise ModelRetry("web_fetch: web access disabled by policy.")

    if not url or not re.match(r"https?://", url.strip()):
        raise ModelRetry("web_fetch requires an http:// or https:// URL.")

    url = url.strip()

    hostname = urlparse(url).hostname
    if hostname and not _is_domain_allowed(
        hostname,
        ctx.deps.web_fetch_allowed_domains,
        ctx.deps.web_fetch_blocked_domains,
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
            max_retries=ctx.deps.web_http_max_retries,
            backoff_base_seconds=ctx.deps.web_http_backoff_base_seconds,
            backoff_max_seconds=ctx.deps.web_http_backoff_max_seconds,
            backoff_jitter_ratio=ctx.deps.web_http_jitter_ratio,
            cf_fallback_headers=_CF_FALLBACK_HEADERS,
        )
    if isinstance(resp_or_error, dict):
        return resp_or_error
    resp = resp_or_error

    final_url = str(resp.url)
    if final_url != url and not is_url_safe(final_url):
        raise ModelRetry("web_fetch blocked: redirect target resolves to a private or internal address.")

    content_type = resp.headers.get("content-type", "")

    if not _is_content_type_allowed(content_type):
        return terminal_error(
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

    return {
        "display": display,
        "url": final_url,
        "content_type": content_type,
        "truncated": truncated,
    }
