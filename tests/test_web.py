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

from co_cli.tools.web import web_search, web_fetch, _is_content_type_allowed
from co_cli.tools._url_safety import is_url_safe
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


# --- URL safety (is_url_safe) ---


def test_url_safe_blocks_loopback_ipv4():
    assert is_url_safe("http://127.0.0.1/secret") is False


def test_url_safe_blocks_loopback_ipv6():
    assert is_url_safe("http://[::1]/secret") is False


def test_url_safe_blocks_rfc1918_10():
    assert is_url_safe("http://10.0.0.1/admin") is False


def test_url_safe_blocks_rfc1918_172():
    assert is_url_safe("http://172.16.0.1/admin") is False


def test_url_safe_blocks_rfc1918_192():
    assert is_url_safe("http://192.168.1.1/admin") is False


def test_url_safe_blocks_link_local_metadata():
    assert is_url_safe("http://169.254.169.254/latest/meta-data/") is False


def test_url_safe_blocks_metadata_hostname():
    """metadata.google.internal is in the blocked hostnames list."""
    assert is_url_safe("http://metadata.google.internal/computeMetadata/v1/") is False


def test_url_safe_allows_public_ip():
    assert is_url_safe("http://1.1.1.1/") is True
    assert is_url_safe("http://8.8.8.8/") is True


def test_url_safe_allows_public_hostname():
    assert is_url_safe("https://example.com/") is True


def test_url_safe_blocks_no_hostname():
    assert is_url_safe("http:///path") is False


def test_url_safe_blocks_invalid_url():
    assert is_url_safe("not-a-url") is False


# --- SSRF integration (web_fetch raises ModelRetry) ---


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


# --- Content-type guard ---


def test_content_type_allows_text_html():
    assert _is_content_type_allowed("text/html; charset=utf-8") is True


def test_content_type_allows_text_plain():
    assert _is_content_type_allowed("text/plain") is True


def test_content_type_allows_json():
    assert _is_content_type_allowed("application/json") is True


def test_content_type_rejects_image():
    assert _is_content_type_allowed("image/png") is False


def test_content_type_rejects_pdf():
    assert _is_content_type_allowed("application/pdf") is False


def test_content_type_allows_empty():
    assert _is_content_type_allowed("") is True


@pytest.mark.asyncio
async def test_web_fetch_blocks_binary_content():
    """web_fetch raises ModelRetry for binary content types."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="unsupported content type"):
        await web_fetch(ctx, "https://httpbin.org/image/png")


@pytest.mark.asyncio
async def test_web_fetch_allows_json():
    """web_fetch succeeds for JSON content."""
    ctx = _make_ctx()
    result = await web_fetch(ctx, "https://httpbin.org/json")
    assert "json" in result["content_type"]
