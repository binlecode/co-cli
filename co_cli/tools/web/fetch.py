"""Web fetch tool — direct HTTP fetch with HTML-to-markdown conversion."""

import re
from importlib.metadata import version as _pkg_version
from typing import Literal

FetchFormat = Literal["markdown", "html", "text"]
from urllib.parse import urlparse

import html2text
import httpx
import trafilatura
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output
from co_cli.tools.web._ssrf import (
    SSRFRedirectError,
    is_url_safe,
    make_ssrf_safe_transport,
    ssrf_redirect_guard,
)
from co_cli.tools.web.search import _http_get_with_retries

# ---------------------------------------------------------------------------
# Web fetch constants
# ---------------------------------------------------------------------------

_FETCH_TIMEOUT = 15
_MAX_FETCH_CHARS = 100_000
# 1 MB pre-decode limit
_MAX_FETCH_BYTES = 1_048_576

_FETCH_USER_AGENT = f"co-cli/{_pkg_version('co-cli')} (+https://github.com/binlecode/co-cli)"

_ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/x-yaml",
    "application/yaml",
)

# Minimal honest headers used when Cloudflare blocks the primary request.
_CF_FALLBACK_HEADERS: dict[str, str] = {"User-Agent": _FETCH_USER_AGENT}


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


def _html_to_markdown(html: str) -> str:
    """Convert HTML to readable markdown text."""
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    # 0 disables line wrapping
    converter.body_width = 0
    return converter.handle(html)


def _extract_main_content(html: str, url: str) -> str | None:
    """Extract main-article markdown from HTML; None when nothing usable extracted.

    Fail-open: any extraction error returns None so the caller falls back to
    full-page conversion. Drops nav/header/footer/sidebar boilerplate so the
    model receives content, not chrome. favor_recall=True biases toward keeping
    borderline content rather than over-pruning — safer for doc/reference pages.
    """
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            favor_recall=True,
        )
    except Exception:
        return None
    if not extracted or not extracted.strip():
        return None
    return extracted


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


# ---------------------------------------------------------------------------
# web_fetch tool
# ---------------------------------------------------------------------------


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_concurrent_safe=True,
    retries=3,
)
async def web_fetch(
    ctx: RunContext[CoDeps],
    url: str,
    format: FetchFormat = "markdown",
    timeout: int = _FETCH_TIMEOUT,
) -> ToolReturn:
    """Fetch a web page and return its content as readable markdown text.

    Use to read the full content of a known URL — HTML is converted to
    markdown, JSON and XML are returned as-is.

    Accepts any URL — from the user's message, from web_search results, or
    from tool output. Never guess or fabricate URLs yourself.

    If fetch returns 403 or is blocked by Cloudflare, retry with shell
    (curl -sL <url>) — only for fetch failures, not as the default path.

    Args:
        url: Full URL to fetch (must start with http:// or https://).
        format: "markdown" (default) converts HTML to markdown; "html" and "text" return the raw
            decoded body. Ignored for JSON/XML/plain-text, returned as-is.
        timeout: Max seconds to wait for the response (default 15). Increase for slow sites.
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

    try:
        async with httpx.AsyncClient(
            transport=make_ssrf_safe_transport(),
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
            event_hooks={"response": [ssrf_redirect_guard]},
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
    except SSRFRedirectError as exc:
        raise ModelRetry(
            f"web_fetch blocked: redirect target resolves to a private or "
            f"internal address ({exc})."
        ) from exc

    if not isinstance(resp_or_error, httpx.Response):
        return tool_error(resp_or_error, ctx=ctx)
    resp = resp_or_error

    final_url = str(resp.url)

    content_type = resp.headers.get("content-type", "")

    if not _is_content_type_allowed(content_type):
        return tool_error(
            f"web_fetch blocked: unsupported content type '{content_type}'. "
            "Only text and structured data formats are supported.",
            ctx=ctx,
        )

    raw_bytes = resp.content[:_MAX_FETCH_BYTES]
    text = raw_bytes.decode(resp.encoding or "utf-8", errors="replace")

    if "html" in content_type and format == "markdown":
        extracted = _extract_main_content(text, final_url)
        text = extracted if extracted is not None else _html_to_markdown(text)

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
