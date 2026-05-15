"""SSRF protection — critical security invariants for is_url_safe."""

from __future__ import annotations

from co_cli.tools.web._ssrf import is_url_safe


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
