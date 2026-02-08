"""SSRF protection: block requests to private/internal network addresses."""

import ipaddress
import socket
from ipaddress import IPv4Network, IPv6Network
from urllib.parse import urlparse

_BLOCKED_NETWORKS: tuple[IPv4Network | IPv6Network, ...] = (
    IPv4Network("127.0.0.0/8"),
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
    IPv4Network("169.254.0.0/16"),
    IPv4Network("100.64.0.0/10"),
    IPv6Network("::1/128"),
    IPv6Network("fe80::/10"),
    IPv6Network("fc00::/7"),
)

_BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "metadata.google.internal",
    "metadata.internal",
})


def is_url_safe(url: str) -> bool:
    """Check whether *url* resolves to a public (non-private) address.

    Returns ``False`` (fail-closed) for:
    - Unresolvable hostnames or DNS errors
    - IPs in private/loopback/link-local/metadata ranges
    - Cloud metadata hostnames
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except Exception:
        return False

    if not hostname:
        return False

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        return False

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError):
        return False

    for info in addr_infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                return False

    return True
