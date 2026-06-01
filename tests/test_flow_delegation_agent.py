"""Tests for sub-agent delegation: depth enforcement and fork_deps state isolation."""

import pytest
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import CoDeps, CoSessionState, fork_deps
from co_cli.llm.factory import build_model
from co_cli.tools.agents.delegation import MAX_AGENT_DEPTH, web_research
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)


def _make_deps(agent_depth: int = 0) -> CoDeps:
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
    )
    deps.runtime.agent_depth = agent_depth
    return deps


def _ctx(agent_depth: int = 0) -> RunContext:
    return RunContext(deps=_make_deps(agent_depth=agent_depth), model=None, usage=RunUsage())


def test_fork_deps_increments_agent_depth():
    """fork_deps must produce a child with agent_depth exactly one greater than the parent.

    Also covers chained forks: a grandchild reaches depth=2 when fork is applied twice.
    Catches the bug class where fork_deps mistakenly returns a constant depth (e.g. =1)
    rather than incrementing — single-level test alone would not.
    """
    root = _make_deps(agent_depth=0)
    child = fork_deps(root)
    grandchild = fork_deps(child)
    assert child.runtime.agent_depth == 1
    assert grandchild.runtime.agent_depth == 2


def test_fork_deps_starts_fresh_runtime():
    """fork_deps must start the child with a clean runtime — no inherited turn_usage."""
    parent = _make_deps()
    child = fork_deps(parent)
    assert child.runtime.turn_usage is None
    assert child.runtime.resume_tool_names is None


@pytest.mark.asyncio
async def test_web_research_raises_model_retry_at_max_depth():
    """web_research must raise ModelRetry when agent_depth >= MAX_AGENT_DEPTH.

    The depth check lives in the tool wrapper, before forking.
    """
    ctx = _ctx(agent_depth=MAX_AGENT_DEPTH)
    with pytest.raises(ModelRetry):
        await web_research(ctx, "Some problem.")
