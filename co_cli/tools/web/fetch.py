"""Web fetch tool — direct HTTP fetch with HTML-to-markdown conversion."""

import re
from importlib.metadata import version as _pkg_version
from urllib.parse import urlparse

import html2text
import httpx
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output
from co_cli.tools.web._ssrf import is_url_safe
from co_cli.tools.web.search import _http_get_with_retries

# ---------------------------------------------------------------------------
# Web fetch constants
# ---------------------------------------------------------------------------

_FETCH_TIMEOUT = 15
_MAX_FETCH_CHARS = 100_000
_MAX_FETCH_BYTES = 1_048_576  # 1 MB pre-decode limit

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
    converter.body_width = 0  # No line wrapping
    return converter.handle(html)


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
    with shell: curl -sL <url>. Use the shell fallback only for
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
