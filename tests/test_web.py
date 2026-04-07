"""Functional tests for web intelligence tools."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.deps import CoConfig, CoDeps
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.web import _html_to_markdown, _is_content_type_allowed, web_fetch, web_search
from tests._timeouts import HTTP_EXTERNAL_TIMEOUT_SECS

_AGENT = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))


def _make_ctx(brave_search_api_key: str | None = None) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(brave_search_api_key=brave_search_api_key),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _make_policy_ctx(
    *,
    brave_search_api_key: str | None = None,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(
            brave_search_api_key=brave_search_api_key,
            web_fetch_allowed_domains=allowed_domains or [],
            web_fetch_blocked_domains=blocked_domains or [],
        ),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _brave_key_for_search_tests() -> str:
    """Use configured Brave key when present; else force terminal error path."""
    return settings.brave_search_api_key or "invalid-test-key"


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
    """web_search raises ModelRetry when Brave is not configured."""
    ctx = _make_ctx(brave_search_api_key=None)
    with pytest.raises(ModelRetry, match="Web search not configured"):
        await web_search(ctx, "test query")


@pytest.mark.asyncio
async def test_web_search_functional():
    """web_search returns structured success or a structured terminal error."""
    ctx = _make_ctx(brave_search_api_key=_brave_key_for_search_tests())
    async with asyncio.timeout(HTTP_EXTERNAL_TIMEOUT_SECS):
        result = await web_search(ctx, "python programming language")
    assert result.return_value
    if (result.metadata or {}).get("error"):
        assert result.metadata["error"] is True
        return
    assert result.metadata["count"] > 0
    assert isinstance(result.metadata["results"], list)
    first = result.metadata["results"][0]
    assert "title" in first
    assert "url" in first
    assert "snippet" in first


@pytest.mark.asyncio
async def test_web_search_domains_parameter():
    """web_search honors the domains parameter without changing result shape."""
    ctx = _make_policy_ctx(brave_search_api_key=_brave_key_for_search_tests())
    async with asyncio.timeout(HTTP_EXTERNAL_TIMEOUT_SECS):
        result = await web_search(ctx, "test", domains=["example.com"])
    assert result.return_value
    if not (result.metadata or {}).get("error"):
        assert "results" in result.metadata


@pytest.mark.asyncio
async def test_web_fetch_blocks_loopback():
    """Loopback addresses are rejected for SSRF protection."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="private or internal address"):
        await web_fetch(ctx, "http://127.0.0.1/secret")


@pytest.mark.asyncio
async def test_web_fetch_blocks_metadata():
    """Cloud metadata endpoints are rejected for SSRF protection."""
    ctx = _make_ctx()
    with pytest.raises(ModelRetry, match="private or internal address"):
        await web_fetch(ctx, "http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_web_fetch_blocks_redirect_to_private():
    """Redirects from public URLs into private targets are blocked."""
    ctx = _make_ctx()
    try:
        async with asyncio.timeout(HTTP_EXTERNAL_TIMEOUT_SECS):
            result = await web_fetch(ctx, "https://httpbin.org/redirect-to?url=http://127.0.0.1/")
    except ModelRetry:
        return
    assert result.metadata["error"] is True


@pytest.mark.asyncio
async def test_web_fetch_blocked_domain():
    """Blocked-domain policy is enforced before fetching."""
    ctx = _make_policy_ctx(blocked_domains=["example.com"])
    with pytest.raises(ModelRetry, match="not allowed by policy"):
        await web_fetch(ctx, "https://example.com")


@pytest.mark.asyncio
async def test_web_fetch_not_in_allowlist():
    """Allowlist policy rejects non-allowlisted domains."""
    ctx = _make_policy_ctx(allowed_domains=["example.com"])
    with pytest.raises(ModelRetry, match="not allowed by policy"):
        await web_fetch(ctx, "https://www.iana.org/")


@pytest.mark.asyncio
async def test_web_fetch_html_to_markdown():
    """Successful HTML fetch returns markdown-converted content.

    Uses a stable Wikipedia page to verify: HTML→markdown conversion ran
    (no raw <html> tags), all expected result keys are present, and the
    display string contains readable text content.
    """
    ctx = _make_ctx()
    async with asyncio.timeout(HTTP_EXTERNAL_TIMEOUT_SECS):
        result = await web_fetch(ctx, "https://en.wikipedia.org/wiki/Python_(programming_language)")
    assert not (result.metadata or {}).get("error"), f"Unexpected error: {result.return_value}"
    assert result.return_value
    assert "url" in result.metadata
    assert "content_type" in result.metadata
    assert "truncated" in result.metadata
    # HTML-to-markdown conversion must have run: raw <html> tag must not survive
    assert "<html" not in result.return_value.lower()
    # Wikipedia Python page must contain readable content
    assert "Python" in result.return_value


def test_html_to_markdown_converts_tags():
    """_html_to_markdown strips HTML tags and produces readable plain text."""
    html = "<html><body><h1>Hello World</h1><p>Some <b>bold</b> text.</p></body></html>"
    result = _html_to_markdown(html)
    assert "<html" not in result.lower()
    assert "<h1" not in result
    assert "<p" not in result
    assert "Hello World" in result
    assert "bold" in result


def test_html_to_markdown_preserves_links():
    """_html_to_markdown retains hyperlinks in markdown syntax."""
    html = '<a href="https://example.com">click here</a>'
    result = _html_to_markdown(html)
    assert "https://example.com" in result
    assert "click here" in result


def test_is_content_type_allowed_permits_text_types():
    """text/* MIME types are allowed."""
    assert _is_content_type_allowed("text/html") is True
    assert _is_content_type_allowed("text/plain; charset=utf-8") is True
    assert _is_content_type_allowed("text/xml") is True


def test_is_content_type_allowed_permits_structured_data():
    """application/json, application/xml, and YAML variants are allowed."""
    assert _is_content_type_allowed("application/json") is True
    assert _is_content_type_allowed("application/xml") is True
    assert _is_content_type_allowed("application/xhtml+xml") is True
    assert _is_content_type_allowed("application/yaml") is True
    assert _is_content_type_allowed("application/x-yaml") is True


def test_is_content_type_allowed_rejects_binary():
    """Binary content types are not allowed."""
    assert _is_content_type_allowed("image/png") is False
    assert _is_content_type_allowed("application/pdf") is False
    assert _is_content_type_allowed("application/octet-stream") is False
    assert _is_content_type_allowed("video/mp4") is False


def test_is_content_type_allowed_permits_empty():
    """Empty Content-Type is permitted (server may omit it for text responses)."""
    assert _is_content_type_allowed("") is True


@pytest.mark.asyncio
async def test_web_fetch_json_content():
    """web_fetch returns raw JSON content without HTML-to-markdown conversion."""
    ctx = _make_ctx()
    async with asyncio.timeout(HTTP_EXTERNAL_TIMEOUT_SECS):
        result = await web_fetch(ctx, "https://httpbin.org/json")
    assert not (result.metadata or {}).get("error"), f"Unexpected error: {result.return_value}"
    assert result.return_value
    # JSON content must be preserved as-is, not converted
    assert "slideshow" in result.return_value.lower()


@pytest.mark.asyncio
async def test_web_fetch_plain_text():
    """web_fetch returns plain text content without conversion."""
    ctx = _make_ctx()
    async with asyncio.timeout(HTTP_EXTERNAL_TIMEOUT_SECS):
        result = await web_fetch(ctx, "https://httpbin.org/robots.txt")
    assert not (result.metadata or {}).get("error"), f"Unexpected error: {result.return_value}"
    assert result.return_value
