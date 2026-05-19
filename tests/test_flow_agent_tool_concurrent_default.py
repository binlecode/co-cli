"""Behavioral tests for agent_tool concurrent-default flip and dispatch backstop."""

import asyncio

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import (
    MAX_TOOL_DISPATCH_WORKERS,
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    VisibilityPolicyEnum,
    fork_deps_for_reviewer,
)
from co_cli.tools.agent_tool import AGENT_TOOL_ATTR, agent_tool
from co_cli.tools.code.execute import code_execute
from co_cli.tools.files.write import file_patch, file_write
from co_cli.tools.shell_backend import ShellBackend


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
    )


def test_default_is_concurrent():
    @agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, register=False)
    async def _dummy(ctx, x: int) -> str:
        """Dummy tool."""
        return str(x)

    info = getattr(_dummy, AGENT_TOOL_ATTR)
    assert info.is_concurrent_safe is True


def test_read_only_implies_concurrent():
    @agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, register=False)
    async def _dummy(ctx, x: int) -> str:
        """Dummy read-only tool."""
        return str(x)

    info = getattr(_dummy, AGENT_TOOL_ATTR)
    assert info.is_concurrent_safe is True


def test_explicit_opt_out():
    @agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_concurrent_safe=False, register=False)
    async def _dummy(ctx, x: int) -> str:
        """Dummy sequential tool."""
        return str(x)

    info = getattr(_dummy, AGENT_TOOL_ATTR)
    assert info.is_concurrent_safe is False


@pytest.mark.parametrize(
    ("tool_fn", "name"),
    [
        (code_execute, "code_execute"),
        (file_write, "file_write"),
        (file_patch, "file_patch"),
    ],
)
def test_unsafe_tools_are_sequential(tool_fn, name):
    info = getattr(tool_fn, AGENT_TOOL_ATTR)
    assert info.is_concurrent_safe is False, f"{name} must have is_concurrent_safe=False"


@pytest.mark.asyncio
async def test_dispatch_backstop():
    sem = asyncio.Semaphore(MAX_TOOL_DISPATCH_WORKERS)
    max_concurrent = 0
    current = 0
    lock = asyncio.Lock()

    async def _task():
        nonlocal max_concurrent, current
        async with sem:
            async with lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.05)
            async with lock:
                current -= 1

    tasks = [asyncio.create_task(_task()) for _ in range(MAX_TOOL_DISPATCH_WORKERS + 5)]
    await asyncio.gather(*tasks)
    assert max_concurrent <= MAX_TOOL_DISPATCH_WORKERS


def test_semaphore_shared_across_reviewer_fork():
    deps = _make_deps()
    reviewer = fork_deps_for_reviewer(deps)
    assert reviewer.tool_dispatch_sem is deps.tool_dispatch_sem
