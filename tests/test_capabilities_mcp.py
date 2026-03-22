"""Functional tests for MCP liveness via check_runtime().

Tests call check_runtime() with real CoDeps to exercise the discovery-error
priority logic and binary probe fallback introduced in TASK-3 (G2 fix).
"""

from co_cli.bootstrap._check import check_runtime
from co_cli.config import MCPServerConfig
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend


def test_check_runtime_mcp_probe_name_matches_config_key() -> None:
    """check_runtime() stores bare config key in mcp_probes, not 'mcp:<name>'."""
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            mcp_servers={"mysvr": MCPServerConfig(command="ls")},
        ),
        session=CoSessionState(),
    )
    result = check_runtime(deps)
    assert len(result.mcp_probes) == 1
    assert result.mcp_probes[0][0] == "mysvr"


def test_check_runtime_discovery_error_overrides_passing_binary_probe() -> None:
    """Discovery error degrades server even when binary is on PATH.

    Proves discovery errors take priority: without the fix, binary probe would
    return ok for 'ls' and no finding would be emitted.
    """
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            mcp_servers={"mysvr": MCPServerConfig(command="ls")},
        ),
        session=CoSessionState(
            mcp_discovery_errors={"mysvr": "connection refused"},
        ),
    )
    result = check_runtime(deps)

    assert any(f["component"] == "mcp:mysvr" for f in result.findings)


def test_check_runtime_no_discovery_error_binary_probe_passes() -> None:
    """No discovery error + binary on PATH → server reported healthy, no finding.

    Proves the fallback path works: binary probe is used and returns ok.
    """
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            mcp_servers={"mysvr": MCPServerConfig(command="ls")},
        ),
        session=CoSessionState(
            mcp_discovery_errors={},
        ),
    )
    result = check_runtime(deps)

    assert not any(f["component"] == "mcp:mysvr" for f in result.findings)
