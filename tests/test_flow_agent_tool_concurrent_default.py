"""Behavioral tests for agent_tool dispatch backstop and reviewer semaphore sharing."""

import asyncio

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import (
    MAX_TOOL_DISPATCH_WORKERS,
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    fork_deps_for_reviewer,
)
from co_cli.tools.shell_backend import ShellBackend


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
    )


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
