"""Functional tests for MCP liveness via check_runtime().

Tests call check_runtime() with real CoDeps to exercise the binary probe
path for MCP server checks.
"""

from co_cli.bootstrap._check import check_runtime
from co_cli.config._core import MCPServerConfig
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend
from tests._settings import test_settings


def test_check_runtime_mcp_probe_name_matches_config_key() -> None:
    """check_runtime() stores bare config key in mcp_probes, not 'mcp:<name>'."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=test_settings(
            mcp_servers={"mysvr": MCPServerConfig(command="ls")},
        ),
        session=CoSessionState(),
    )
    result = check_runtime(deps)
    assert len(result.mcp_probes) == 1
    assert result.mcp_probes[0][0] == "mysvr"


def test_check_runtime_binary_probe_passes_when_command_on_path() -> None:
    """Binary on PATH → server reported healthy, no finding."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=test_settings(
            mcp_servers={"mysvr": MCPServerConfig(command="ls")},
        ),
    )
    result = check_runtime(deps)

    assert not any(f["component"] == "mcp:mysvr" for f in result.findings)
