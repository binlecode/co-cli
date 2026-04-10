"""Functional tests for check_capabilities tool."""

import asyncio

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings
from tests._timeouts import HTTP_HEALTH_TIMEOUT_SECS

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.display._core import TerminalFrontend
from co_cli.tools.capabilities import check_capabilities
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=settings)


@pytest.mark.asyncio
async def test_new_runtime_fields_present() -> None:
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    async with asyncio.timeout(HTTP_HEALTH_TIMEOUT_SECS):
        result = await check_capabilities(ctx)
    assert "tool_count" in result.metadata
    assert "mcp_mode" in result.metadata
    assert result.metadata["mcp_mode"] in ("mcp", "native-only")
    assert isinstance(result.metadata["tool_count"], int)


@pytest.mark.asyncio
async def test_capabilities_emits_doctor_progress_updates() -> None:
    statuses: list[str] = []
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
    )
    deps.runtime.tool_progress_callback = statuses.append
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

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
    deps = CoDeps(shell=ShellBackend(), config=make_settings(mcp_servers={}))
    deps.runtime.tool_progress_callback = lambda msg, _tid=tool_id: frontend.on_tool_progress(
        _tid, msg
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

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
