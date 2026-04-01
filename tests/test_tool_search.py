"""Functional tests for the search_tools progressive discovery tool."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoCapabilityState, CoConfig, CoServices
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.tool_search import search_tools

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_AGENT_RESULT = build_agent(config=_CONFIG)
_AGENT = _AGENT_RESULT.agent


def _make_deps() -> CoDeps:
    """Build real CoDeps with tool_index populated from build_agent()."""
    return CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=_CONFIG,
        capabilities=CoCapabilityState(
            tool_index=dict(_AGENT_RESULT.tool_index),
        ),
    )


def _make_ctx(deps: CoDeps) -> RunContext:
    """Build a real RunContext bound to the given deps."""
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


@pytest.mark.asyncio
async def test_search_tools_discovers_deferred_tool() -> None:
    """search_tools('edit file') unlocks edit_file and adds it to discovered_tools."""
    deps = _make_deps()
    ctx = _make_ctx(deps)

    result = await search_tools(ctx, "edit file")

    assert "edit_file" in deps.session.discovered_tools, (
        "edit_file must be added to discovered_tools after search"
    )
    granted = result.get("granted", [])
    assert "edit_file" in granted, f"edit_file missing from granted list: {granted}"
    assert "unlocked" in result["display"], "display must mention 'unlocked'"


@pytest.mark.asyncio
async def test_search_tools_no_match_returns_hint() -> None:
    """search_tools with no matching query returns the fallback hint text."""
    deps = _make_deps()
    ctx = _make_ctx(deps)

    result = await search_tools(ctx, "zzznomatch_xyzzy_unlikely_token")

    assert "Try:" in result["display"], (
        f"Fallback hint expected in display: {result['display']!r}"
    )
    assert result.get("granted", []) == []


@pytest.mark.asyncio
async def test_search_tools_always_loaded_tool_already_available() -> None:
    """search_tools for an always-loaded tool shows 'already available' and does not discover it."""
    deps = _make_deps()
    ctx = _make_ctx(deps)

    result = await search_tools(ctx, "web search")

    assert "web_search" not in deps.session.discovered_tools, (
        "web_search is always-loaded — must not be added to discovered_tools"
    )
    assert "already available" in result["display"], (
        f"'already available' expected in display: {result['display']!r}"
    )
    granted = result.get("granted", [])
    assert "web_search" not in granted, "web_search must not appear in granted list"
