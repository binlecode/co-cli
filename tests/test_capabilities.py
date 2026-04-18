"""Functional tests for check_capabilities tool."""

import asyncio

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings
from tests._timeouts import HTTP_HEALTH_TIMEOUT_SECS

from co_cli.agent._core import build_agent
from co_cli.bootstrap.check import check_runtime
from co_cli.config._core import MCPServerSettings, settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display._core import TerminalFrontend
from co_cli.tools.capabilities import check_capabilities
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=settings)


def _make_deps(**settings_overrides) -> CoDeps:
    """Build runtime deps for doctor-style checks without test doubles."""
    return CoDeps(
        shell=ShellBackend(),
        config=make_settings(**settings_overrides),
        session=CoSessionState(),
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# ---------------------------------------------------------------------------
# Doctor tool workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_runtime_fields_present() -> None:
    deps = _make_deps()
    ctx = _make_ctx(deps)
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await check_capabilities(ctx)
    assert "tool_count" in result.metadata
    assert "mcp_mode" in result.metadata
    assert result.metadata["mcp_mode"] in ("mcp", "native-only")
    assert isinstance(result.metadata["tool_count"], int)


@pytest.mark.asyncio
async def test_capabilities_emits_doctor_progress_updates() -> None:
    statuses: list[str] = []
    deps = _make_deps()
    deps.runtime.tool_progress_callback = statuses.append
    ctx = _make_ctx(deps)

    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        await check_capabilities(ctx)

    assert statuses[0] == "Doctor: starting runtime diagnostics..."
    assert "Doctor: checking provider and model availability..." in statuses
    assert "Doctor: checking configured integrations..." in statuses
    assert "Doctor: checking knowledge backend..." in statuses
    assert "Doctor: checking loaded skills..." in statuses


@pytest.mark.asyncio
async def test_capabilities_progress_routes_to_frontend_via_curried_lambda() -> None:
    """Progress callback wired as curried lambda routes to the real terminal frontend.

    Validates the join between the tool's tool_progress_callback usage and the frontend
    protocol using the same curried lambda pattern _execute_stream_segment() applies at
    FunctionToolCallEvent time. This uses the real TerminalFrontend instead of a
    recording fake and asserts only on the frontend's public inspection API.
    """
    frontend = TerminalFrontend()
    tool_id = "cap1"
    deps = _make_deps(mcp_servers={})
    deps.runtime.tool_progress_callback = lambda msg, _tid=tool_id: frontend.on_tool_progress(
        _tid, msg
    )
    ctx = _make_ctx(deps)

    try:
        async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
            result = await check_capabilities(ctx)
        assert frontend.active_surface() == "tool"
        assert frontend.active_tool_messages(), "Expected tool progress to be rendered"
        assert frontend.active_tool_messages()[0] == "Doctor: checking loaded skills..."
        assert result.return_value, (
            "return_value missing or empty in check_capabilities ToolReturn"
        )
    finally:
        frontend.cleanup()


# ---------------------------------------------------------------------------
# Runtime health probing dependencies
# ---------------------------------------------------------------------------


def test_check_runtime_mcp_probe_name_matches_config_key() -> None:
    """Runtime checks must preserve the config key for each MCP probe result."""
    result = check_runtime(
        _make_deps(
            mcp_servers={"mysvr": MCPServerSettings(command="ls")},
        )
    )

    assert len(result.mcp_probes) == 1
    assert result.mcp_probes[0][0] == "mysvr"


def test_check_runtime_binary_probe_passes_when_command_on_path() -> None:
    """Healthy MCP commands on PATH should not emit a degraded finding."""
    result = check_runtime(
        _make_deps(
            mcp_servers={"mysvr": MCPServerSettings(command="ls")},
        )
    )

    assert not any(finding["component"] == "mcp:mysvr" for finding in result.findings)
