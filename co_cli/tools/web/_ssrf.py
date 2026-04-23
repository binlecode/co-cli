"""SSRF protection — URL safety checks and redirect guard for web tools.

Limitations (acknowledged, out of scope at this layer):
- DNS rebinding (TOCTOU): the hostname is resolved at check time; httpx
  re-resolves when connecting. An attacker-controlled DNS server with TTL=0
  can return a public IP for the check and a private IP for the actual
  connection. Closing this gap requires transport-level IP pinning (custom
  ``httpx.AsyncHTTPTransport``) or an egress proxy.
"""

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

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
