"""SSRF protection — critical security invariants for the web-tool network path.

Covers the four production surfaces in co_cli/tools/web/_ssrf.py:
- is_url_safe: hostname/IP blocklisting + DNS resolution.
- ssrf_redirect_guard: redirect-chain rejection (the response hook).
- SSRFSafeNetworkBackend.connect_tcp: DNS-rebind IP-pinning at the TCP layer.
- make_ssrf_safe_transport: the fail-loud injection, verified end-to-end.

All assertions exercise observable behavior (return value / raised exception)
with real objects (real httpx.Response, real wrapped httpcore backend, real
httpx.AsyncClient). No mocks. The cases use numeric IPs and the .invalid TLD so
they stay offline and deterministic — the only resolver hit is a guaranteed
NXDOMAIN for the DNS-failure path.
"""

from __future__ import annotations

import asyncio

import httpcore
import httpx
import pytest
from tests._timeouts import HTTP_HEALTH_TIMEOUT_SECS

from co_cli.tools.web._ssrf import (
    SSRFRedirectError,
    SSRFSafeNetworkBackend,
    is_url_safe,
    make_ssrf_safe_transport,
    ssrf_redirect_guard,
)


def test_is_url_safe_blocks_private_and_internal_addresses() -> None:
    # Cloud metadata endpoint — canonical SSRF target
    assert not is_url_safe("http://169.254.169.254/latest/meta-data/")
    # Blocked by hostname list
    assert not is_url_safe("http://metadata.google.internal/")
    # RFC1918 private ranges
    assert not is_url_safe("http://10.0.0.1")
    assert not is_url_safe("http://192.168.1.1")


def test_is_url_safe_resolves_hostnames_not_just_ip_strings() -> None:
    # localhost resolves to 127.0.0.1 — proves DNS resolution actually runs
    assert not is_url_safe("http://localhost")


def test_is_url_safe_blocks_ipv4_mapped_ipv6_loopback() -> None:
    # ::ffff:127.0.0.1 has is_loopback=False in Python; the explicit ipv4_mapped
    # unwrap in _is_blocked_ip is what catches it. A regression there would let a
    # mapped loopback address through.
    assert not is_url_safe("http://[::ffff:127.0.0.1]/")


def test_is_url_safe_blocks_cgnat_range() -> None:
    # 100.64.0.0/10 (carrier-grade NAT) is not is_private; the explicit range
    # check in _is_blocked_ip is the only guard.
    assert not is_url_safe("http://100.64.0.1/")


def _redirect_response(location: str) -> httpx.Response:
    """A real 302 response whose Location header is *location*."""
    return httpx.Response(
        302,
        headers={"location": location},
        request=httpx.Request("GET", "http://example.com/start"),
    )


@pytest.mark.asyncio
async def test_redirect_guard_rejects_redirect_to_blocked_target() -> None:
    """A redirect whose target resolves to a blocked address aborts the chain."""
    response = _redirect_response("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(SSRFRedirectError):
        await ssrf_redirect_guard(response)


@pytest.mark.asyncio
async def test_redirect_guard_rejects_protocol_relative_redirect_to_blocked_host() -> None:
    """A protocol-relative Location ("//host/...") is resolved before the safety check.

    urljoin against the request URL turns "//169.254.169.254/" into
    "http://169.254.169.254/" — a real SSRF bypass vector a guard that only
    inspected absolute Location headers would miss. This is the only case that
    exercises the urljoin resolution branch.
    """
    response = _redirect_response("//169.254.169.254/latest/meta-data/")
    with pytest.raises(SSRFRedirectError):
        await ssrf_redirect_guard(response)


@pytest.mark.asyncio
async def test_redirect_guard_allows_redirect_to_public_target() -> None:
    """A redirect to a public address passes the guard without raising."""
    # Numeric public IP — no DNS, no network; is_url_safe returns True.
    response = _redirect_response("http://93.184.216.34/elsewhere")
    await ssrf_redirect_guard(response)


@pytest.mark.asyncio
async def test_redirect_guard_ignores_non_redirect_response() -> None:
    """A non-redirect (200) response is a no-op — the guard returns cleanly."""
    response = httpx.Response(200, request=httpx.Request("GET", "http://example.com/"))
    await ssrf_redirect_guard(response)


@pytest.mark.asyncio
async def test_redirect_guard_ignores_redirect_without_location() -> None:
    """A redirect status with no Location header is a no-op — nothing to validate."""
    response = httpx.Response(302, request=httpx.Request("GET", "http://example.com/"))
    await ssrf_redirect_guard(response)


def _safe_backend() -> SSRFSafeNetworkBackend:
    """Wrap a real httpcore backend. On the block path the wrapped backend is
    never reached, so no real connection is attempted."""
    return SSRFSafeNetworkBackend(httpcore.AnyIOBackend())


@pytest.mark.asyncio
async def test_connect_tcp_blocks_host_resolving_to_blocked_ip() -> None:
    """connect_tcp refuses a host that resolves to a blocked IP, before connecting.

    This is the DNS-rebind TOCTOU close: the IP validated here is the one that
    would receive the connection. The numeric host resolves offline and trips
    _is_blocked_ip (link-local), so the wrapped backend is never invoked.
    """
    backend = _safe_backend()
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        with pytest.raises(OSError, match="blocked"):
            await backend.connect_tcp("169.254.169.254", 80)


@pytest.mark.asyncio
async def test_connect_tcp_fails_closed_on_dns_failure() -> None:
    """A host that getaddrinfo rejects raises OSError rather than connecting anywhere.

    An empty host raises socket.gaierror immediately and offline (no resolver
    round-trip); connect_tcp must surface that resolution failure as a
    fail-closed OSError instead of falling through to a connection.
    """
    backend = _safe_backend()
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        with pytest.raises(OSError, match="DNS resolution failed"):
            await backend.connect_tcp("", 80)


@pytest.mark.asyncio
async def test_safe_transport_blocks_request_to_internal_address() -> None:
    """End-to-end: a client using the SSRF-safe transport cannot reach a blocked IP.

    Proves make_ssrf_safe_transport actually installs the IP-pinning backend —
    the request to the metadata IP is refused at connect time with the backend's
    SSRF block error, never reaching the network.
    """
    transport = make_ssrf_safe_transport()
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(OSError, match="SSRF"):
                await client.get("http://169.254.169.254/latest/meta-data/")
