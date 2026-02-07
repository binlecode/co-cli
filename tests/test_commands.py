"""Functional tests for slash commands.

All tests use real agent/deps — no mocks, no stubs.
"""

import pytest

from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox
from co_cli._commands import dispatch, CommandContext, COMMANDS


def _make_ctx(message_history: list | None = None) -> CommandContext:
    """Build a real CommandContext with live agent and deps."""
    agent, _ = get_agent()
    deps = CoDeps(
        sandbox=Sandbox(container_name="co-test-commands"),
        auto_confirm=False,
        session_id="test-commands",
    )
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=agent,
        tool_count=len(agent._function_toolset.tools),
    )


# --- Dispatch routing ---


@pytest.mark.asyncio
async def test_dispatch_non_slash():
    """Non-slash input returns (False, None) — not consumed."""
    ctx = _make_ctx()
    handled, new_history = await dispatch("hello world", ctx)
    assert handled is False
    assert new_history is None


@pytest.mark.asyncio
async def test_dispatch_unknown_command():
    """Unknown /command returns (True, None) — consumed, no crash."""
    ctx = _make_ctx()
    handled, new_history = await dispatch("/unknown", ctx)
    assert handled is True
    assert new_history is None


@pytest.mark.asyncio
async def test_dispatch_with_extra_args():
    """/help with trailing args still dispatches correctly."""
    ctx = _make_ctx()
    handled, new_history = await dispatch("/help some extra args", ctx)
    assert handled is True
    assert new_history is None  # /help is display-only


# --- Individual commands ---


@pytest.mark.asyncio
async def test_cmd_help():
    """/help returns None (display-only)."""
    ctx = _make_ctx()
    handled, new_history = await dispatch("/help", ctx)
    assert handled is True
    assert new_history is None


@pytest.mark.asyncio
async def test_cmd_clear():
    """/clear returns empty list."""
    ctx = _make_ctx(message_history=["fake_msg_1", "fake_msg_2"])
    handled, new_history = await dispatch("/clear", ctx)
    assert handled is True
    assert new_history == []


@pytest.mark.asyncio
async def test_cmd_status():
    """/status returns None (display-only), no exception."""
    ctx = _make_ctx()
    handled, new_history = await dispatch("/status", ctx)
    assert handled is True
    assert new_history is None


@pytest.mark.asyncio
async def test_cmd_tools():
    """/tools returns None, and context has tools registered."""
    ctx = _make_ctx()
    assert ctx.tool_count > 0
    handled, new_history = await dispatch("/tools", ctx)
    assert handled is True
    assert new_history is None


@pytest.mark.asyncio
async def test_cmd_history_empty():
    """/history with empty history returns None."""
    ctx = _make_ctx()
    handled, new_history = await dispatch("/history", ctx)
    assert handled is True
    assert new_history is None


@pytest.mark.asyncio
async def test_cmd_history_with_messages():
    """/history with seeded messages returns None (display-only)."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    msgs = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelRequest(parts=[UserPromptPart(content="world")]),
    ]
    ctx = _make_ctx(message_history=msgs)
    handled, new_history = await dispatch("/history", ctx)
    assert handled is True
    assert new_history is None


@pytest.mark.asyncio
async def test_cmd_yolo_toggle():
    """/yolo toggles auto_confirm: False → True → False."""
    ctx = _make_ctx()
    assert ctx.deps.auto_confirm is False

    await dispatch("/yolo", ctx)
    assert ctx.deps.auto_confirm is True

    await dispatch("/yolo", ctx)
    assert ctx.deps.auto_confirm is False


@pytest.mark.asyncio
async def test_cmd_compact_empty_history():
    """/compact with empty history returns None (no-op)."""
    ctx = _make_ctx()
    handled, new_history = await dispatch("/compact", ctx)
    assert handled is True
    assert new_history is None


@pytest.mark.asyncio
async def test_cmd_compact():
    """/compact with seeded history returns a new list.

    Requires a running LLM provider — will fail if unconfigured.
    """
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    msgs = [
        ModelRequest(parts=[UserPromptPart(content="What is Docker?")]),
    ]
    ctx = _make_ctx(message_history=msgs)
    handled, new_history = await dispatch("/compact", ctx)
    assert handled is True
    assert isinstance(new_history, list)
    assert len(new_history) > 0


# --- Registry sanity ---


def test_commands_registry_complete():
    """All 7 expected commands are registered."""
    expected = {"help", "clear", "status", "tools", "history", "compact", "yolo"}
    assert set(COMMANDS.keys()) == expected
