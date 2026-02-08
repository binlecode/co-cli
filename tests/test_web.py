"""Functional tests for web intelligence tools.

NOTE on skips: The functional tests that hit the real Brave Search API are
skipped when BRAVE_SEARCH_API_KEY is not configured.  This follows the Slack
test precedent — without a valid key these tests hang on network timeouts
rather than failing with a useful error.  Validation and no-key tests run
unconditionally.
"""

from dataclasses import dataclass

import pytest
from pydantic_ai import ModelRetry

from co_cli.tools.web import web_search, web_fetch
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps


def _make_ctx(brave_search_api_key: str | None = None) -> Context:
    return Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        auto_confirm=True,
        session_id="test",
        brave_search_api_key=brave_search_api_key,
    ))


_skip_no_key = pytest.mark.skipif(
    not settings.brave_search_api_key,
    reason="BRAVE_SEARCH_API_KEY not configured — skipped to avoid timeout",
)


# --- Validation: empty/invalid input raises ModelRetry ---


@pytest.mark.asyncio
async def test_web_search_empty_query():
    """web_search raises ModelRetry on empty query."""
    ctx = _make_ctx(brave_search_api_key="fake-key")
    with pytest.raises(ModelRetry, match="Query is required"):
        await web_search(ctx, "")


@pytest.mark.asyncio
async def test_web_search_whitespace_query():
    """web_search raises ModelRetry on whitespace-only query."""
    ctx = _make_ctx(brave_search_api_key="fake-key")
    with pytest.raises(ModelRetry, match="Query is required"):
        await web_search(ctx, "   ")


@pytest.mark.asyncio
async def test_web_fetch_empty_url():
    """web_fetch raises ModelRetry on empty URL."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="http:// or https://"):
        await web_fetch(ctx, "")


@pytest.mark.asyncio
async def test_web_fetch_invalid_scheme():
    """web_fetch raises ModelRetry on non-http URL."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="http:// or https://"):
        await web_fetch(ctx, "ftp://example.com")


@pytest.mark.asyncio
async def test_web_fetch_plain_string():
    """web_fetch raises ModelRetry on plain string (no scheme)."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="http:// or https://"):
        await web_fetch(ctx, "example.com")


# --- No API key raises ModelRetry ---


@pytest.mark.asyncio
async def test_web_search_no_key():
    """web_search raises ModelRetry when brave_search_api_key is None."""
    ctx = _make_ctx(brave_search_api_key=None)
    with pytest.raises(ModelRetry, match="Web search not configured"):
        await web_search(ctx, "test query")


# --- Functional tests (require BRAVE_SEARCH_API_KEY) ---


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


def test_web_search_max_results_cap():
    """max_results is capped at _MAX_RESULTS internally."""
    from co_cli.tools.web import _MAX_RESULTS
    assert min(50, _MAX_RESULTS) == 8
    assert min(3, _MAX_RESULTS) == 3


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
    # httpbin.org/html returns a page with "Herman Melville"
    assert "Herman Melville" in result["display"]


@pytest.mark.asyncio
async def test_web_fetch_plain_text():
    """Test fetching a plain text endpoint."""
    ctx = _make_ctx()
    result = await web_fetch(ctx, "https://httpbin.org/robots.txt")
    assert isinstance(result, dict)
    assert "display" in result
    assert result["truncated"] is False
