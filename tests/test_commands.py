"""Functional tests for slash commands, approval flow, and safe command classification.

All tests use real agent/deps — no mocks, no stubs.
"""

import pytest

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.usage import UsageLimits

from co_cli._approval import _is_safe_command
from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.shell_backend import ShellBackend
from co_cli._commands import dispatch, CommandContext, COMMANDS


def _make_ctx(message_history: list | None = None) -> CommandContext:
    """Build a real CommandContext with live agent and deps."""
    agent, _, tool_names = get_agent()
    deps = CoDeps(
        shell=ShellBackend(),
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
        shell=ShellBackend(),
        session_id="test-approval",
    )
    return agent, model_settings, deps


async def _trigger_shell_call(agent, deps, model_settings, *, retries: int = 3):
    """Ask the LLM to run a shell command. Returns DeferredToolRequests result.

    Retries up to *retries* times because smaller models occasionally respond
    with text instead of calling the tool.
    """
    prompt = (
        "Use the run_shell_command tool to execute: echo hello_approval_test\n"
        "Do NOT describe what you would do — call the tool now."
    )
    last_output = None
    for _ in range(retries):
        result = await agent.run(
            prompt,
            deps=deps,
            model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=settings.max_request_limit),
        )
        if isinstance(result.output, DeferredToolRequests):
            assert len(result.output.approvals) > 0
            return result
        last_output = result.output
    pytest.fail(
        f"Expected DeferredToolRequests after {retries} attempts, "
        f"got {type(last_output).__name__}: {last_output!r}"
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


# --- State-changing commands ---


@pytest.mark.asyncio
async def test_cmd_clear():
    """/clear returns empty list."""
    ctx = _make_ctx(message_history=["fake_msg_1", "fake_msg_2"])
    handled, new_history = await dispatch("/clear", ctx)
    assert handled is True
    assert new_history == []


@pytest.mark.asyncio
async def test_cmd_compact():
    """/compact with seeded history returns a new list.

    Requires a running LLM provider.
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
    expected = {"help", "clear", "status", "tools", "history", "compact", "model", "forget"}
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

        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = True

        resumed = await agent.run(
            None,
            deps=deps,
            message_history=result.all_messages(),
            deferred_tool_results=approvals,
            model_settings=model_settings,
            usage_limits=turn_limits,
            usage=result.usage(),
        )

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
        deps.shell.cleanup()


@pytest.mark.asyncio
async def test_approval_deny():
    """Denying a deferred tool call sends ToolDenied; LLM still responds.

    Requires running LLM + Docker.
    """
    agent, model_settings, deps = _make_agent_and_deps("co-test-deny")
    turn_limits = UsageLimits(request_limit=settings.max_request_limit)
    try:
        result = await _trigger_shell_call(agent, deps, model_settings)

        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")

        resumed = await agent.run(
            None,
            deps=deps,
            message_history=result.all_messages(),
            deferred_tool_results=approvals,
            model_settings=model_settings,
            usage_limits=turn_limits,
            usage=result.usage(),
        )

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
        deps.shell.cleanup()


@pytest.mark.asyncio
async def test_approval_budget_cumulative():
    """Multi-hop approval cannot exceed a single per-turn request budget.

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

        assert result.usage().requests <= budget
        assert isinstance(result.output, str)
    finally:
        deps.shell.cleanup()


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
    assert _is_safe_command("ls & rm -rf /", _SAFE_LIST) is False
    assert _is_safe_command("ls > /tmp/out", _SAFE_LIST) is False
    assert _is_safe_command("ls >> /tmp/out", _SAFE_LIST) is False
    assert _is_safe_command("sort < /etc/passwd", _SAFE_LIST) is False
    assert _is_safe_command("cat << EOF", _SAFE_LIST) is False
    assert _is_safe_command("ls\nrm -rf /", _SAFE_LIST) is False


def test_safe_command_partial_name_no_match():
    """A command starting with a safe prefix but not followed by space should not match."""
    assert _is_safe_command("lsblk", _SAFE_LIST) is False
    assert _is_safe_command("caterpillar", _SAFE_LIST) is False
