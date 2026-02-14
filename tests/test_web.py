"""Functional tests for web intelligence tools.

NOTE on skips: The functional tests that hit the real Brave Search API are
skipped when BRAVE_SEARCH_API_KEY is not configured — without a valid key
these tests hang on network timeouts rather than failing with a useful error.
Validation and no-key tests run unconditionally.
"""

from dataclasses import dataclass

import pytest
from pydantic_ai import ModelRetry

from co_cli.tools.web import web_search, web_fetch
from co_cli.config import settings, WebPolicy
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps


def _make_ctx(brave_search_api_key: str | None = None) -> Context:
    return Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        session_id="test",
        brave_search_api_key=brave_search_api_key,
    ))


def _make_policy_ctx(
    *,
    brave_search_api_key: str | None = None,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    search_policy: str = "allow",
    fetch_policy: str = "allow",
) -> Context:
    return Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        session_id="test",
        brave_search_api_key=brave_search_api_key,
        web_fetch_allowed_domains=allowed_domains or [],
        web_fetch_blocked_domains=blocked_domains or [],
        web_policy=WebPolicy(search=search_policy, fetch=fetch_policy),
    ))


_skip_no_key = pytest.mark.skipif(
    not settings.brave_search_api_key,
    reason="BRAVE_SEARCH_API_KEY not configured — skipped to avoid timeout",
)


# --- Validation ---


@pytest.mark.asyncio
async def test_web_search_empty_query():
    """web_search raises ModelRetry on empty query."""
    ctx = _make_ctx(brave_search_api_key="fake-key")
    with pytest.raises(ModelRetry, match="Query is required"):
        await web_search(ctx, "")


@pytest.mark.asyncio
async def test_web_fetch_invalid_scheme():
    """web_fetch raises ModelRetry on non-http URL."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="http:// or https://"):
        await web_fetch(ctx, "ftp://example.com")


@pytest.mark.asyncio
async def test_web_search_no_key():
    """web_search raises ModelRetry when brave_search_api_key is None."""
    ctx = _make_ctx(brave_search_api_key=None)
    with pytest.raises(ModelRetry, match="Web search not configured"):
        await web_search(ctx, "test query")


# --- web_search functional (require BRAVE_SEARCH_API_KEY) ---


@_skip_no_key
@pytest.mark.asyncio
async def test_web_search_functional():
    """Test real Brave Search API call."""
    ctx = _make_ctx(brave_search_api_key=settings.brave_search_api_key)
    result = await web_search(ctx, "python programming language")
    assert isinstance(result, dict)
    assert "display" in result
    assert "results" in result
    assert "count" in result
    assert result["count"] > 0
    assert isinstance(result["results"], list)
    first = result["results"][0]
    assert "title" in first
    assert "url" in first
    assert "snippet" in first


@_skip_no_key
@pytest.mark.asyncio
async def test_web_search_domains_parameter():
    """web_search with domains parameter scopes results to specified sites."""
    ctx = _make_policy_ctx(brave_search_api_key=settings.brave_search_api_key)
    result = await web_search(ctx, "test", domains=["example.com"])
    assert isinstance(result, dict)
    assert "results" in result


# --- web_fetch functional (no API key needed) ---


@pytest.mark.asyncio
async def test_web_fetch_functional():
    """Test fetching a real page and converting to markdown."""
    ctx = _make_ctx()
    result = await web_fetch(ctx, "https://httpbin.org/html")
    assert isinstance(result, dict)
    assert "display" in result
    assert "url" in result
    assert "content_type" in result
    assert "truncated" in result
    assert "html" in result["content_type"]
    assert result["truncated"] is False
    assert "Herman Melville" in result["display"]


@pytest.mark.asyncio
async def test_web_fetch_plain_text():
    """Test fetching a plain text endpoint."""
    ctx = _make_ctx()
    result = await web_fetch(ctx, "https://httpbin.org/robots.txt")
    assert isinstance(result, dict)
    assert "display" in result
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_web_fetch_allows_json():
    """web_fetch succeeds for JSON content."""
    ctx = _make_ctx()
    result = await web_fetch(ctx, "https://httpbin.org/json")
    assert "json" in result["content_type"]


@pytest.mark.asyncio
async def test_web_fetch_blocks_binary_content():
    """web_fetch returns terminal error for binary content types."""
    ctx = _make_ctx()
    result = await web_fetch(ctx, "https://httpbin.org/image/png")
    assert result["error"] is True
    assert "unsupported content type" in result["display"]


@pytest.mark.asyncio
async def test_web_fetch_truncates_large_response():
    """web_fetch sets truncated=True when response exceeds _MAX_FETCH_CHARS."""
    from co_cli.tools.web import _MAX_FETCH_CHARS

    ctx = _make_ctx()
    result = await web_fetch(ctx, "https://norvig.com/big.txt")
    assert result["truncated"] is True
    prefix_len = len(f"Content from {result['url']}:\n\n")
    body_len = len(result["display"]) - prefix_len
    assert body_len <= _MAX_FETCH_CHARS


# --- HTTP error handling (functional via httpbin) ---


@pytest.mark.asyncio
async def test_web_fetch_http_403_is_terminal():
    """HTTP 403 returns terminal error result (no ModelRetry)."""
    ctx = _make_ctx()
    result = await web_fetch(ctx, "https://httpbin.org/status/403")
    assert result["error"] is True
    assert "HTTP 403" in result["display"]


@pytest.mark.asyncio
async def test_web_fetch_http_503_retries_then_terminal():
    """HTTP 503 retries within tool and returns terminal error on exhaustion."""
    ctx = _make_ctx()
    result = await web_fetch(ctx, "https://httpbin.org/status/503")
    assert result["error"] is True
    assert "HTTP 503" in result["display"]
    assert "Retries exhausted" in result["display"]


# --- SSRF protection (functional via web_fetch) ---


@pytest.mark.asyncio
async def test_web_fetch_blocks_loopback():
    """web_fetch raises ModelRetry for loopback addresses."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="private or internal address"):
        await web_fetch(ctx, "http://127.0.0.1/secret")


@pytest.mark.asyncio
async def test_web_fetch_blocks_metadata():
    """web_fetch raises ModelRetry for cloud metadata endpoint."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="private or internal address"):
        await web_fetch(ctx, "http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_web_fetch_blocks_redirect_to_private():
    """web_fetch blocks when a public URL redirects to a private IP."""
    ctx = _make_ctx()
    try:
        result = await web_fetch(ctx, "https://httpbin.org/redirect-to?url=http://127.0.0.1/")
    except ModelRetry:
        return
    assert result["error"] is True


# --- Policy gates (functional via web_fetch/web_search) ---


@pytest.mark.asyncio
async def test_web_fetch_deny_mode():
    """web_fetch raises ModelRetry when web_policy.fetch is 'deny'."""
    ctx = _make_policy_ctx(fetch_policy="deny")
    with pytest.raises(ModelRetry, match="disabled by policy"):
        await web_fetch(ctx, "https://example.com")


@pytest.mark.asyncio
async def test_web_search_deny_mode():
    """web_search raises ModelRetry when web_policy.search is 'deny'."""
    ctx = _make_policy_ctx(brave_search_api_key="fake-key", search_policy="deny")
    with pytest.raises(ModelRetry, match="disabled by policy"):
        await web_search(ctx, "test query")


@pytest.mark.asyncio
async def test_web_fetch_blocked_domain():
    """web_fetch raises ModelRetry when domain is in blocked list."""
    ctx = _make_policy_ctx(blocked_domains=["httpbin.org"])
    with pytest.raises(ModelRetry, match="not allowed by policy"):
        await web_fetch(ctx, "https://httpbin.org/html")


@pytest.mark.asyncio
async def test_web_fetch_not_in_allowlist():
    """web_fetch raises ModelRetry when domain is not in allowlist."""
    ctx = _make_policy_ctx(allowed_domains=["example.com"])
    with pytest.raises(ModelRetry, match="not allowed by policy"):
        await web_fetch(ctx, "https://httpbin.org/html")


@pytest.mark.asyncio
async def test_web_fetch_in_allowlist():
    """web_fetch succeeds when domain is in allowlist."""
    ctx = _make_policy_ctx(allowed_domains=["httpbin.org"])
    result = await web_fetch(ctx, "https://httpbin.org/html")
    assert "Herman Melville" in result["display"]
