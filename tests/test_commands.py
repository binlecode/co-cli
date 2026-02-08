"""Functional tests for slash commands and approval flow.

All tests use real agent/deps — no mocks, no stubs.
"""

import pytest

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.usage import RunUsage, UsageLimits

from co_cli._approval import _is_safe_command
from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox
from co_cli._commands import dispatch, CommandContext, COMMANDS


def _make_ctx(message_history: list | None = None) -> CommandContext:
    """Build a real CommandContext with live agent and deps."""
    agent, _, tool_names = get_agent()
    deps = CoDeps(
        sandbox=Sandbox(container_name="co-test-commands"),
        auto_confirm=False,
        session_id="test-commands",
    )
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=agent,
        tool_names=tool_names,
    )


def _make_agent_and_deps(container_name: str = "co-test-approval"):
    """Build a real agent + deps for approval flow tests."""
    agent, model_settings, _ = get_agent()
    deps = CoDeps(
        sandbox=Sandbox(container_name=container_name),
        auto_confirm=False,
        session_id="test-approval",
    )
    return agent, model_settings, deps


async def _trigger_shell_call(agent, deps, model_settings):
    """Ask the LLM to run a shell command. Returns DeferredToolRequests result."""
    result = await agent.run(
        "Run this exact shell command: echo hello_approval_test",
        deps=deps,
        model_settings=model_settings,
        usage_limits=UsageLimits(request_limit=settings.max_request_limit),
    )
    # The LLM should call run_shell_command which has requires_approval=True
    assert isinstance(result.output, DeferredToolRequests), (
        f"Expected DeferredToolRequests, got {type(result.output).__name__}. "
        "LLM may not have called a side-effectful tool."
    )
    assert len(result.output.approvals) > 0
    return result


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
    assert len(ctx.tool_names) > 0
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
    """All expected commands are registered."""
    expected = {"help", "clear", "status", "tools", "history", "compact", "yolo", "model"}
    assert set(COMMANDS.keys()) == expected


# --- Approval flow (programmatic, no TTY) ---


@pytest.mark.asyncio
async def test_approval_approve():
    """Approving a deferred tool call executes it and returns LLM response.

    Requires running LLM + Docker.
    """
    agent, model_settings, deps = _make_agent_and_deps("co-test-approve")
    turn_limits = UsageLimits(request_limit=settings.max_request_limit)
    try:
        result = await _trigger_shell_call(agent, deps, model_settings)

        # Approve all pending calls
        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = True

        # Resume — agent executes the tool and returns final text
        resumed = await agent.run(
            None,
            deps=deps,
            message_history=result.all_messages(),
            deferred_tool_results=approvals,
            model_settings=model_settings,
            usage_limits=turn_limits,
            usage=result.usage(),
        )

        # May trigger further deferred calls; keep approving
        while isinstance(resumed.output, DeferredToolRequests):
            more_approvals = DeferredToolResults()
            for call in resumed.output.approvals:
                more_approvals.approvals[call.tool_call_id] = True
            resumed = await agent.run(
                None,
                deps=deps,
                message_history=resumed.all_messages(),
                deferred_tool_results=more_approvals,
                model_settings=model_settings,
                usage_limits=turn_limits,
                usage=resumed.usage(),
            )

        assert isinstance(resumed.output, str)
        assert len(resumed.all_messages()) > 0
    finally:
        deps.sandbox.cleanup()


@pytest.mark.asyncio
async def test_approval_deny():
    """Denying a deferred tool call sends ToolDenied; LLM still responds.

    Requires running LLM + Docker.
    """
    agent, model_settings, deps = _make_agent_and_deps("co-test-deny")
    turn_limits = UsageLimits(request_limit=settings.max_request_limit)
    try:
        result = await _trigger_shell_call(agent, deps, model_settings)

        # Deny all pending calls
        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")

        # Resume — LLM sees the denial and produces a text response
        resumed = await agent.run(
            None,
            deps=deps,
            message_history=result.all_messages(),
            deferred_tool_results=approvals,
            model_settings=model_settings,
            usage_limits=turn_limits,
            usage=result.usage(),
        )

        # LLM may retry with another tool call or just respond with text
        while isinstance(resumed.output, DeferredToolRequests):
            deny_approvals = DeferredToolResults()
            for call in resumed.output.approvals:
                deny_approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")
            resumed = await agent.run(
                None,
                deps=deps,
                message_history=resumed.all_messages(),
                deferred_tool_results=deny_approvals,
                model_settings=model_settings,
                usage_limits=turn_limits,
                usage=resumed.usage(),
            )

        assert isinstance(resumed.output, str)
    finally:
        deps.sandbox.cleanup()


# --- Safe command classification ---


_SAFE_LIST = ["ls", "cat", "grep", "git status", "git diff", "git log"]


def test_safe_command_simple():
    """Simple safe command is recognized."""
    assert _is_safe_command("ls", _SAFE_LIST) is True
    assert _is_safe_command("ls -la", _SAFE_LIST) is True
    assert _is_safe_command("cat /etc/hosts", _SAFE_LIST) is True


def test_safe_command_multi_word_prefix():
    """Multi-word prefix like 'git status' matches, but 'git push' does not."""
    assert _is_safe_command("git status", _SAFE_LIST) is True
    assert _is_safe_command("git status --short", _SAFE_LIST) is True
    assert _is_safe_command("git diff HEAD~1", _SAFE_LIST) is True
    assert _is_safe_command("git push origin main", _SAFE_LIST) is False
    assert _is_safe_command("git commit -m 'test'", _SAFE_LIST) is False


def test_safe_command_chaining_rejected():
    """Shell chaining operators always force approval."""
    assert _is_safe_command("ls; rm -rf /", _SAFE_LIST) is False
    assert _is_safe_command("cat file && rm file", _SAFE_LIST) is False
    assert _is_safe_command("ls || echo fail", _SAFE_LIST) is False
    assert _is_safe_command("ls | wc -l", _SAFE_LIST) is False
    assert _is_safe_command("echo `whoami`", _SAFE_LIST) is False
    assert _is_safe_command("echo $(whoami)", _SAFE_LIST) is False
    # Backgrounding
    assert _is_safe_command("ls & rm -rf /", _SAFE_LIST) is False
    # Output redirection
    assert _is_safe_command("ls > /tmp/out", _SAFE_LIST) is False
    assert _is_safe_command("ls >> /tmp/out", _SAFE_LIST) is False
    # Input redirection / heredoc
    assert _is_safe_command("sort < /etc/passwd", _SAFE_LIST) is False
    assert _is_safe_command("cat << EOF", _SAFE_LIST) is False
    # Embedded newline
    assert _is_safe_command("ls\nrm -rf /", _SAFE_LIST) is False


def test_safe_command_empty_list():
    """Empty safe list means nothing is auto-approved."""
    assert _is_safe_command("ls", []) is False
    assert _is_safe_command("cat file", []) is False


def test_safe_command_unknown():
    """Commands not in the safe list are rejected."""
    assert _is_safe_command("rm -rf /", _SAFE_LIST) is False
    assert _is_safe_command("curl http://evil.com", _SAFE_LIST) is False
    assert _is_safe_command("python script.py", _SAFE_LIST) is False


def test_safe_command_exact_match():
    """Bare command with no args matches exactly."""
    assert _is_safe_command("grep", _SAFE_LIST) is True
    assert _is_safe_command("git log", _SAFE_LIST) is True


def test_safe_command_partial_name_no_match():
    """A command that starts with a safe prefix but isn't followed by a space should not match."""
    assert _is_safe_command("lsblk", _SAFE_LIST) is False
    assert _is_safe_command("caterpillar", _SAFE_LIST) is False


@pytest.mark.asyncio
async def test_approval_auto_confirm():
    """With auto_confirm=True, approval cycle can be driven programmatically.

    Simulates the yolo flow: all calls get approved without prompting.
    Requires running LLM + Docker.
    """
    agent, model_settings, deps = _make_agent_and_deps("co-test-autoconfirm")
    deps.auto_confirm = True
    turn_limits = UsageLimits(request_limit=settings.max_request_limit)
    try:
        result = await agent.run(
            "Run this exact shell command: echo yolo_test",
            deps=deps,
            model_settings=model_settings,
            usage_limits=turn_limits,
        )

        # Even with auto_confirm on deps, pydantic-ai still returns
        # DeferredToolRequests because requires_approval=True is on the tool.
        # The auto_confirm flag is checked by _handle_approvals in the chat loop.
        # So we approve programmatically here to simulate that.
        while isinstance(result.output, DeferredToolRequests):
            approvals = DeferredToolResults()
            for call in result.output.approvals:
                approvals.approvals[call.tool_call_id] = True
            result = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                model_settings=model_settings,
                usage_limits=turn_limits,
                usage=result.usage(),
            )

        assert isinstance(result.output, str)
    finally:
        deps.sandbox.cleanup()


@pytest.mark.asyncio
async def test_approval_budget_cumulative():
    """Multi-hop approval cannot exceed a single per-turn request budget.

    Uses a tight budget shared across the initial run and all resume hops.
    The cumulative usage from result.usage() must stay within turn_limits.
    Requires running LLM + Docker.
    """
    agent, model_settings, deps = _make_agent_and_deps("co-test-budget")
    budget = settings.max_request_limit
    turn_limits = UsageLimits(request_limit=budget)
    try:
        result = await agent.run(
            "Run this exact shell command: echo budget_test",
            deps=deps,
            model_settings=model_settings,
            usage_limits=turn_limits,
        )

        # Approve and resume with cumulative usage threading
        while isinstance(result.output, DeferredToolRequests):
            approvals = DeferredToolResults()
            for call in result.output.approvals:
                approvals.approvals[call.tool_call_id] = True
            result = await agent.run(
                None,
                deps=deps,
                message_history=result.all_messages(),
                deferred_tool_results=approvals,
                model_settings=model_settings,
                usage_limits=turn_limits,
                usage=result.usage(),
            )

        # The cumulative request count must not exceed the budget
        assert result.usage().requests <= budget, (
            f"Cumulative requests ({result.usage().requests}) exceeded "
            f"turn budget ({budget})"
        )
        assert isinstance(result.output, str)
    finally:
        deps.sandbox.cleanup()
