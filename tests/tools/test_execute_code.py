"""Functional tests for the code_execute tool."""

import asyncio

import pytest
from pydantic_ai import ApprovalRequired, RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings
from tests._timeouts import SUBPROCESS_TIMEOUT_SECS

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.tools.execute_code import code_execute
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=settings)


def _make_ctx(*, tool_call_approved: bool = True) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
    )
    return RunContext(
        deps=deps,
        model=_AGENT.model,
        usage=RunUsage(),
        tool_call_approved=tool_call_approved,
    )


@pytest.mark.asyncio
async def test_execute_code_deny_pattern_returns_error() -> None:
    """DENY-pattern commands are blocked and return a terminal error, not raised."""
    ctx = _make_ctx()
    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        result = await code_execute(ctx, "rm -rf /")
    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_execute_code_requires_approval_when_not_approved() -> None:
    """execute_code always raises ApprovalRequired when tool_call_approved is False."""
    ctx = _make_ctx(tool_call_approved=False)
    with pytest.raises(ApprovalRequired):
        async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
            await code_execute(ctx, "python --version")


@pytest.mark.asyncio
async def test_execute_code_approved_runs_command() -> None:
    """Approved execute_code calls shell.run_command and returns tool_output."""
    ctx = _make_ctx(tool_call_approved=True)
    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        result = await code_execute(ctx, "echo hello")
    assert "hello" in result.return_value
