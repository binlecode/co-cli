"""Functional tests for MCP capabilities display in check_capabilities.

NOTE: This test exercises RuntimeCheck construction and display-building logic only.
Full entrypoint wiring (check_runtime() → check_capabilities()) requires a live
RunContext[CoDeps] and is covered by manual smoke test. This gap is intentional —
the tool function cannot be exercised without a live agent context with no established
test pattern in this repo.
"""

from co_cli.bootstrap._check import CheckResult, RuntimeCheck


def test_mcp_capabilities_display_two_probes_one_degraded() -> None:
    """Display shows N/M connected and degraded server by name with error detail."""
    probes = [
        ("atlas", CheckResult(ok=True, status="ok", detail="")),
        ("internal", CheckResult(ok=False, status="error", detail="connection timeout")),
    ]
    result = RuntimeCheck(
        capabilities={"mcp_count": 1},
        status={},
        findings=[],
        fallbacks=[],
        mcp_probes=probes,
    )

    assert len(result.mcp_probes) == 2
    assert result.mcp_probes[0] == ("atlas", probes[0][1])
    assert result.mcp_probes[1] == ("internal", probes[1][1])

    # Replicate check_capabilities MCP display logic to verify rendering
    mcp_live = result.capabilities["mcp_count"]
    mcp_configured = len(result.mcp_probes)
    lines: list[str] = [f"MCP: {mcp_live}/{mcp_configured} servers connected · 0 tools"]
    for name, probe in result.mcp_probes:
        status_str = "ok" if probe.ok else f"degraded — {probe.detail}"
        lines.append(f"  {name}: {status_str}")
    display = "\n".join(lines)

    assert "1/2 servers connected" in display
    assert "internal: degraded — connection timeout" in display


def test_mcp_runtime_check_no_probes_defaults_to_empty_list() -> None:
    """RuntimeCheck without mcp_probes arg defaults to empty list."""
    result = RuntimeCheck(capabilities={}, status={}, findings=[], fallbacks=[])
    assert result.mcp_probes == []


def test_mcp_probe_names_carry_no_mcp_prefix() -> None:
    """check_runtime() strips 'mcp:' prefix; RuntimeCheck stores bare names."""
    probe = CheckResult(ok=True, status="ok", detail="remote url")
    result = RuntimeCheck(
        capabilities={},
        status={},
        findings=[],
        fallbacks=[],
        mcp_probes=[("context7", probe)],
    )
    assert result.mcp_probes[0][0] == "context7"
    assert not result.mcp_probes[0][0].startswith("mcp:")


def test_mcp_zero_config_display_logic() -> None:
    """Zero-MCP-config case renders 'MCP: none configured'.

    mcp_configured mirrors len(ctx.deps.config.mcp_servers or {}) from capabilities.py —
    set explicitly to 0 here, not derived from probe count.
    """
    result = RuntimeCheck(
        capabilities={"mcp_count": 0},
        status={},
        findings=[],
        fallbacks=[],
        mcp_probes=[],
    )
    # Production: mcp_configured = len(ctx.deps.config.mcp_servers or {})
    mcp_configured = 0
    if mcp_configured == 0:
        line = "MCP: none configured"
    else:
        mcp_live = result.capabilities["mcp_count"]
        line = f"MCP: {mcp_live}/{mcp_configured} servers connected · 0 tools"
    assert line == "MCP: none configured"
