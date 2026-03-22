"""Functional tests for check_capabilities tool."""
import asyncio
from pathlib import Path

import pytest
from pydantic_ai._run_context import RunContext
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import RunUsage, UsageLimits

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.context._orchestrate import _run_stream_turn
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.capabilities import check_capabilities
from tests.test_orchestrate import RecordingFrontend, StaticEventAgent

_AGENT, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))


@pytest.mark.asyncio
async def test_new_runtime_fields_present() -> None:
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    async with asyncio.timeout(15):
        result = await check_capabilities(ctx)
    assert "tool_count" in result
    assert "mcp_mode" in result
    assert result["mcp_mode"] in ("mcp", "native-only")
    assert isinstance(result["tool_count"], int)


@pytest.mark.asyncio
async def test_capabilities_emits_doctor_progress_updates() -> None:
    statuses: list[str] = []
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
    )
    deps.runtime.tool_progress_callback = statuses.append
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    async with asyncio.timeout(15):
        await check_capabilities(ctx)

    assert statuses[0] == "Doctor: starting runtime diagnostics..."
    assert "Doctor: checking provider and model availability..." in statuses
    assert "Doctor: checking configured integrations..." in statuses
    assert "Doctor: checking knowledge backend..." in statuses
    assert "Doctor: checking loaded skills..." in statuses


@pytest.mark.asyncio
async def test_capabilities_progress_routes_to_frontend_via_curried_lambda() -> None:
    """Progress callback wired as curried lambda routes to RecordingFrontend.on_tool_progress.

    Validates the join between the tool's tool_progress_callback usage and the frontend
    protocol using the same curried lambda pattern _run_stream_turn() applies at
    FunctionToolCallEvent time. Existing tests wire a plain list appender; this test
    wires via the lambda so the tool_id binding and RecordingFrontend are both exercised.
    """
    frontend = RecordingFrontend()
    tool_id = "cap1"
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())
    deps.runtime.tool_progress_callback = (
        lambda msg, _tid=tool_id: frontend.on_tool_progress(_tid, msg)
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    async with asyncio.timeout(15):
        result = await check_capabilities(ctx)

    progress_events = [(tid, msg) for kind, (tid, msg) in frontend.events if kind == "tool_progress"]
    assert len(progress_events) >= 1, "Expected at least one tool_progress event"
    assert progress_events[0] == (tool_id, "Doctor: starting runtime diagnostics...")
    assert all(tid == tool_id for tid, _ in progress_events), (
        f"All progress events must carry tool_id={tool_id!r}; got: {progress_events}"
    )
    assert result.get("_kind") == "tool_result", f"check_capabilities must return ToolResult; got: {result!r}"
    assert result.get("display"), "display field missing or empty in check_capabilities ToolResult"


@pytest.mark.asyncio
async def test_stream_events_real_check_capabilities_result_dispatches_correctly() -> None:
    """Real check_capabilities() ToolResult shape survives _run_stream_turn() dispatch.

    Gets the actual return value of check_capabilities (not a hand-crafted dict) and
    feeds it through StaticEventAgent → _run_stream_turn() → RecordingFrontend. Validates
    that the real tool output shape triggers on_tool_complete with the full ToolResult,
    not None (which would happen if the _kind discriminator were missing or misspelled
    in the tool's make_result() call).
    """
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    async with asyncio.timeout(15):
        real_result = await check_capabilities(ctx)

    frontend = RecordingFrontend()
    call_part = ToolCallPart(tool_name="check_capabilities", args="{}", tool_call_id="cap2")
    return_part = ToolReturnPart(
        tool_name="check_capabilities",
        content=real_result,
        tool_call_id="cap2",
    )
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=return_part),
    ])
    deps2 = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _run_stream_turn(
        agent, user_input="check", deps=deps2, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    complete_events = [payload for kind, payload in frontend.events if kind == "tool_complete"]
    assert len(complete_events) == 1
    tool_id, result = complete_events[0]
    assert tool_id == "cap2"
    assert isinstance(result, dict), (
        f"Expected ToolResult dict, got {type(result).__name__}: {result!r}"
    )
    assert result.get("_kind") == "tool_result", (
        f"_kind discriminator lost in dispatch; got: {result!r}"
    )
    assert result.get("display"), "display field missing or empty in dispatched ToolResult"
    assert isinstance(result.get("tool_count"), int), (
        "tool_count metadata field missing — real ToolResult not passed through intact"
    )


def test_build_agent_per_tool_retry_budget() -> None:
    """Per-tool retry budgets are set correctly by tier.

    Write-once tier: retries=1 (write_file, edit_file).
    Network tier: retries=3 (web_search, web_fetch).
    Default tier: max_retries matches the agent-level default (list_directory has no
    explicit override, so pydantic-ai propagates config.tool_retries to max_retries).

    Note: pydantic-ai stores the agent-level default in Tool.max_retries when no
    per-tool retries kwarg is passed — there is no None sentinel for "use default".
    The invariant tested here is that default-tier tools are NOT in the write-once
    bucket (max_retries != 1), distinguishing them from write-once tools.
    """
    config = CoConfig()
    agent, _, _ = build_agent(config=config)
    tools = agent._function_toolset.tools
    assert tools["write_file"].max_retries == 1, (
        f"write_file expected max_retries=1, got {tools['write_file'].max_retries}"
    )
    assert tools["edit_file"].max_retries == 1, (
        f"edit_file expected max_retries=1, got {tools['edit_file'].max_retries}"
    )
    assert tools["web_search"].max_retries == 3, (
        f"web_search expected max_retries=3, got {tools['web_search'].max_retries}"
    )
    assert tools["web_fetch"].max_retries == 3, (
        f"web_fetch expected max_retries=3, got {tools['web_fetch'].max_retries}"
    )
    # Default tier: no explicit retries kwarg — pydantic-ai propagates the agent-level
    # default (config.tool_retries) so max_retries equals the agent default, not the
    # write-once budget of 1. Assert it equals the configured agent-level default.
    assert tools["list_directory"].max_retries == config.tool_retries, (
        f"list_directory expected agent-level default ({config.tool_retries}), "
        f"got {tools['list_directory'].max_retries}"
    )
