"""SSRF protection — URL safety checks, redirect guard, and IP-pinning transport for web tools."""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

logger = logging.getLogger(__name__)


_BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata.internal",
    }
)

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


class SSRFRedirectError(Exception):
    """Raised by the redirect guard when a redirect target is unsafe."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if *ip* falls in any range that must not be contacted."""
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    # Unwrap IPv4-mapped IPv6 (::ffff:x.x.x.x) and check the embedded IPv4 address.
    # Python's is_loopback/is_private don't always fire for mapped addresses (e.g.
    # ::ffff:127.0.0.1 has is_loopback=False), so explicit unwrapping is required.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    return bool(ip.version == 4 and ip in _CGNAT_NETWORK)


def is_url_safe(url: str) -> bool:
    """Check whether *url* resolves to a public (non-private) address.

    Returns ``False`` (fail-closed) for:
    - Empty/malformed URLs, unresolvable hostnames, or unexpected errors
    - Hostnames in the cloud-metadata blocklist
    - IPs that are private, loopback, link-local, reserved, multicast,
      unspecified, or in the CGNAT (100.64.0.0/10) range
    - IPv4-mapped IPv6 addresses whose mapped v4 hits any of the above
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if not hostname:
            return False

        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("SSRF: blocked internal hostname: %s", hostname)
            return False

        try:
            addr_infos = socket.getaddrinfo(hostname, None)
        except (socket.gaierror, OSError):
            logger.warning("SSRF: DNS resolution failed for %s — failing closed", hostname)
            return False

        for info in addr_infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                return False
            if _is_blocked_ip(ip):
                logger.warning("SSRF: blocked %s -> %s", hostname, ip)
                return False

        return True

    except Exception as exc:
        logger.warning("SSRF: fail-closed on unexpected error for %s: %s", url, exc)
        return False


async def ssrf_redirect_guard(response: httpx.Response) -> None:
    """httpx response hook — reject redirects whose target is unsafe.

    httpx only populates ``response.next_request`` when ``follow_redirects``
    is False, so the hook must resolve the redirect target from the
    ``Location`` header itself. Runs on every response (including
    intermediate redirects) before httpx follows them.

    Raises :class:`SSRFRedirectError` to abort the redirect chain.
    """
    if not response.is_redirect:
        return
    location = response.headers.get("location")
    if not location:
        return
    target = urljoin(str(response.request.url), location)
    if not is_url_safe(target):
        raise SSRFRedirectError(target)


class SSRFSafeNetworkBackend(httpcore.AsyncNetworkBackend):
    """Wraps a delegate backend, resolving and pinning DNS before every TCP connect.

    For each ``connect_tcp`` call, this backend:
    1. Resolves the hostname asynchronously (via executor, non-blocking).
    2. Validates every returned IP with ``_is_blocked_ip``.
    3. Connects to the first safe IP instead of re-resolving inside httpcore.

    Because httpcore sets SNI and certificate validation from ``origin.host``
    (the URL hostname), not from the address passed to ``connect_tcp``,
    HTTPS requests remain correctly validated against the original hostname.
    This closes the DNS-rebinding TOCTOU gap: the IP validated here is the
    IP that actually receives the connection.
    """

    def __init__(self, wrapped: httpcore.AsyncNetworkBackend) -> None:
        self._wrapped = wrapped

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.AsyncNetworkStream:
        loop = asyncio.get_running_loop()
        try:
            addr_infos = await loop.run_in_executor(
                None, lambda: socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM)
            )
        except (socket.gaierror, OSError) as exc:
            raise OSError(f"SSRF: DNS resolution failed for {host}: {exc}") from exc

        safe_ip: str | None = None
        for info in addr_infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if _is_blocked_ip(ip):
                raise OSError(f"SSRF: blocked connection to {host} -> {ip}")
            if safe_ip is None:
                safe_ip = str(ip)

        if safe_ip is None:
            raise OSError(f"SSRF: no safe address resolved for {host}")

        return await self._wrapped.connect_tcp(
            safe_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: object = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._wrapped.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        return await self._wrapped.sleep(seconds)


def make_ssrf_safe_transport() -> httpx.AsyncHTTPTransport:
    """Return an httpx transport with SSRF IP-pinning injected at the network layer.

    Creates a standard ``httpx.AsyncHTTPTransport`` and replaces its internal
    httpcore connection pool's network backend with ``SSRFSafeNetworkBackend``.
    Raises ``RuntimeError`` if the expected internal structure is absent (security
    invariant: fail loudly rather than silently skip protection).
    """
    transport = httpx.AsyncHTTPTransport()
    pool = getattr(transport, "_pool", None)
    if pool is None or not hasattr(pool, "_network_backend"):
        raise RuntimeError(
            "SSRF backend injection failed: httpx/httpcore internal structure changed — "
            "update make_ssrf_safe_transport() to match the new transport API."
        )
    pool._network_backend = SSRFSafeNetworkBackend(pool._network_backend)
    return transport
