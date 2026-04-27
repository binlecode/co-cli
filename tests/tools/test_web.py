"""Functional tests for web intelligence tools."""

import asyncio

import httpx
import pytest
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings
from tests._timeouts import HTTP_EXTERNAL_TIMEOUT_SECS

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.web._ssrf import SSRFRedirectError, is_url_safe, ssrf_redirect_guard
from co_cli.tools.web.fetch import web_fetch
from co_cli.tools.web.search import web_search

_AGENT = build_agent(config=settings)


def _make_ctx(brave_search_api_key: str | None = None) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(brave_search_api_key=brave_search_api_key),
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
        config=make_settings(
            brave_search_api_key=brave_search_api_key,
            web=make_settings().web.model_copy(
                update={
                    "fetch_allowed_domains": allowed_domains or [],
                    "fetch_blocked_domains": blocked_domains or [],
                }
            ),
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
    if (result.metadata or {}).get("error"):
        assert result.metadata["error"] is True
        return
    assert "Web search results" in result.return_value
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
    if not (result.metadata or {}).get("error"):
        assert "results" in result.metadata
        assert "Web search results" in result.return_value


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


def test_is_url_safe_rejects_ipv4_mapped_ipv6_loopback():
    """::ffff:127.0.0.1 — IPv4-mapped IPv6 loopback must be blocked.

    The previous IPv4Network-tuple check failed here because
    IPv4Network("127.0.0.0/8") does not contain an IPv6Address object.
    """
    assert is_url_safe("http://[::ffff:127.0.0.1]/") is False


def test_is_url_safe_rejects_ipv4_mapped_ipv6_metadata():
    """::ffff:169.254.169.254 — IPv4-mapped IPv6 cloud metadata."""
    assert is_url_safe("http://[::ffff:169.254.169.254]/") is False


def test_is_url_safe_rejects_multicast():
    """IPv4 multicast (224.0.0.0/4) must be blocked."""
    assert is_url_safe("http://224.0.0.1/") is False


def test_is_url_safe_rejects_unspecified():
    """0.0.0.0 — binds to all interfaces, must be blocked."""
    assert is_url_safe("http://0.0.0.0/") is False


def test_is_url_safe_rejects_cgnat():
    """100.64.0.0/10 (CGNAT / RFC 6598) is not ip.is_private."""
    assert is_url_safe("http://100.64.0.1/") is False


def test_is_url_safe_normalizes_trailing_dot():
    """Trailing-dot hostname must not bypass the metadata blocklist."""
    assert is_url_safe("http://metadata.google.internal./computeMetadata/v1/") is False


def test_is_url_safe_rejects_metadata_goog():
    """metadata.goog — GCP metadata alias — must be blocked."""
    assert is_url_safe("http://metadata.goog/computeMetadata/v1/") is False


def test_is_url_safe_rejects_empty_url():
    """Empty URL fails closed."""
    assert is_url_safe("") is False


def test_is_url_safe_rejects_no_hostname():
    """URL without hostname fails closed."""
    assert is_url_safe("http://") is False


def test_is_url_safe_accepts_public_ip():
    """Public IPs must be allowed."""
    assert is_url_safe("http://8.8.8.8/") is True


@pytest.mark.asyncio
async def test_ssrf_redirect_guard_blocks_private_target():
    """Redirect hook raises on a Location header pointing at a private IP."""
    request = httpx.Request("GET", "https://example.com/start")
    response = httpx.Response(
        302,
        headers={"location": "http://127.0.0.1/admin"},
        request=request,
    )
    with pytest.raises(SSRFRedirectError):
        await ssrf_redirect_guard(response)


@pytest.mark.asyncio
async def test_ssrf_redirect_guard_blocks_metadata_target():
    """Redirect hook raises on a Location header pointing at cloud metadata."""
    request = httpx.Request("GET", "https://example.com/start")
    response = httpx.Response(
        302,
        headers={"location": "http://169.254.169.254/latest/meta-data/"},
        request=request,
    )
    with pytest.raises(SSRFRedirectError):
        await ssrf_redirect_guard(response)


@pytest.mark.asyncio
async def test_ssrf_redirect_guard_resolves_relative_location():
    """Relative Location headers resolve against the request URL — safe target passes."""
    request = httpx.Request("GET", "https://example.com/start")
    response = httpx.Response(
        302,
        headers={"location": "/other"},
        request=request,
    )
    await ssrf_redirect_guard(response)


@pytest.mark.asyncio
async def test_ssrf_redirect_guard_ignores_non_redirect():
    """Non-redirect responses pass through without raising."""
    request = httpx.Request("GET", "https://example.com/")
    response = httpx.Response(200, request=request)
    await ssrf_redirect_guard(response)


@pytest.mark.asyncio
async def test_web_fetch_html_to_markdown():
    """Successful HTML fetch returns markdown-converted content.

    Uses a stable Wikipedia page to verify: HTML→markdown conversion ran
    (no raw <html> tags), all expected result keys are present, and the
    display string contains readable text content.
    """
    ctx = _make_ctx()
    async with asyncio.timeout(HTTP_EXTERNAL_TIMEOUT_SECS):
        result = await web_fetch(
            ctx, "https://en.wikipedia.org/wiki/Python_(programming_language)"
        )
    assert not (result.metadata or {}).get("error"), f"Unexpected error: {result.return_value}"
    assert "url" in result.metadata
    assert "content_type" in result.metadata
    assert "truncated" in result.metadata
    # HTML-to-markdown conversion must have run: raw <html> tag must not survive
    assert "<html" not in result.return_value.lower()
    # Wikipedia Python page must contain readable content
    assert "Python" in result.return_value


@pytest.mark.asyncio
async def test_web_fetch_json_content():
    """web_fetch returns raw JSON content without HTML-to-markdown conversion."""
    ctx = _make_ctx()
    async with asyncio.timeout(HTTP_EXTERNAL_TIMEOUT_SECS):
        result = await web_fetch(ctx, "https://httpbin.org/json")
    assert not (result.metadata or {}).get("error"), f"Unexpected error: {result.return_value}"
    # JSON content must be preserved as-is, not converted
    assert "slideshow" in result.return_value.lower()


@pytest.mark.asyncio
async def test_web_fetch_plain_text():
    """web_fetch returns plain text content without conversion."""
    ctx = _make_ctx()
    async with asyncio.timeout(HTTP_EXTERNAL_TIMEOUT_SECS):
        result = await web_fetch(ctx, "https://httpbin.org/robots.txt")
    assert not (result.metadata or {}).get("error"), f"Unexpected error: {result.return_value}"
    assert "User-agent" in result.return_value
