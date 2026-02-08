"""Web intelligence tools: search (Brave) and fetch (direct HTTP)."""

import re
from typing import Any

import html2text
import httpx
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.tools._url_safety import is_url_safe

_MAX_RESULTS = 8
_SEARCH_TIMEOUT = 12
_FETCH_TIMEOUT = 15
_MAX_FETCH_CHARS = 100_000
_MAX_FETCH_BYTES = 1_048_576  # 1 MB pre-decode limit
_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

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


def _get_api_key(ctx: RunContext[CoDeps]) -> str:
    """Extract and validate Brave Search API key from context."""
    key = ctx.deps.brave_search_api_key
    if not key:
        raise ModelRetry(
            "Web search not configured. Set BRAVE_SEARCH_API_KEY in settings or env."
        )
    return key


def _html_to_markdown(html: str) -> str:
    """Convert HTML to readable markdown text."""
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0  # No line wrapping
    return converter.handle(html)


async def web_search(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search the web via Brave Search. Returns results with title, URL, and snippet.

    Args:
        query: Search query string.
        max_results: Number of results to return (default 5, max 8).
    """
    if not query or not query.strip():
        raise ModelRetry("Query is required for web_search.")

    api_key = _get_api_key(ctx)
    capped = min(max_results, _MAX_RESULTS)

    try:
        async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
            resp = await client.get(
                _BRAVE_SEARCH_URL,
                params={"q": query.strip(), "count": capped},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        raise ModelRetry("Web search timed out. Retry with a shorter query.")
    except httpx.HTTPStatusError as e:
        raise ModelRetry(f"Web search error (HTTP {e.response.status_code}). Retry later.")
    except httpx.HTTPError as e:
        raise ModelRetry(f"Web search error: {e}")

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

    Args:
        url: The URL to fetch (must be http:// or https://).
    """
    if not url or not re.match(r"https?://", url.strip()):
        raise ModelRetry("web_fetch requires an http:// or https:// URL.")

    url = url.strip()

    if not is_url_safe(url):
        raise ModelRetry("web_fetch blocked: URL resolves to a private or internal address.")

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "co-cli/web_fetch"})
            resp.raise_for_status()
    except httpx.TimeoutException:
        raise ModelRetry(f"web_fetch timed out fetching {url}. Try a different URL.")
    except httpx.HTTPStatusError as e:
        raise ModelRetry(f"web_fetch error (HTTP {e.response.status_code}) for {url}.")
    except httpx.HTTPError as e:
        raise ModelRetry(f"web_fetch error: {e}")

    final_url = str(resp.url)
    if final_url != url and not is_url_safe(final_url):
        raise ModelRetry("web_fetch blocked: redirect target resolves to a private or internal address.")

    content_type = resp.headers.get("content-type", "")

    if not _is_content_type_allowed(content_type):
        raise ModelRetry(
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
