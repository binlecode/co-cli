"""Functional tests for the search_tools progressive discovery tool."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.context._orchestrate import compute_segment_filter
from co_cli.deps import CoDeps, CoCapabilityState, CoConfig, CoServices
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.tool_search import search_tools

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_AGENT_RESULT = build_agent(config=_CONFIG)
_AGENT = _AGENT_RESULT.agent


def _make_deps() -> CoDeps:
    """Build real CoDeps with tool_catalog populated from build_agent()."""
    return CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=_CONFIG,
        capabilities=CoCapabilityState(
            tool_names=_AGENT_RESULT.tool_names,
            tool_approvals=_AGENT_RESULT.tool_approvals,
            tool_catalog=_AGENT_RESULT.tool_catalog,
        ),
    )


def _make_ctx(deps: CoDeps) -> RunContext:
    """Build a real RunContext bound to the given deps."""
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


@pytest.mark.asyncio
async def test_search_tools_grants_discoverable_tool() -> None:
    """search_tools('edit file') unlocks edit_file and adds it to granted_tools."""
    deps = _make_deps()
    # Simulate an active main-turn segment so edit_file is not yet in active surface.
    deps.runtime.active_tool_filter = compute_segment_filter(deps)
    ctx = _make_ctx(deps)

    result = await search_tools(ctx, "edit file")

    assert "edit_file" in deps.session.granted_tools, (
        "edit_file must be added to granted_tools after search"
    )
    granted = result.get("granted", [])
    assert "edit_file" in granted, f"edit_file missing from granted list: {granted}"
    assert "unlocked" in result["display"], "display must mention 'unlocked'"


@pytest.mark.asyncio
async def test_search_tools_no_match_returns_hint() -> None:
    """search_tools with no matching query returns the fallback hint text."""
    deps = _make_deps()
    deps.runtime.active_tool_filter = compute_segment_filter(deps)
    ctx = _make_ctx(deps)

    result = await search_tools(ctx, "zzznomatch_xyzzy_unlikely_token")

    assert "Try:" in result["display"], (
        f"Fallback hint expected in display: {result['display']!r}"
    )
    assert result.get("granted", []) == []


@pytest.mark.asyncio
async def test_search_tools_core_tool_already_available() -> None:
    """search_tools for a core tool shows 'already available' and does not re-grant it."""
    deps = _make_deps()
    # Set active_tool_filter to the real main-turn surface so web_search is in it.
    deps.runtime.active_tool_filter = compute_segment_filter(deps)
    ctx = _make_ctx(deps)

    result = await search_tools(ctx, "web search")

    assert "web_search" not in deps.session.granted_tools, (
        "web_search is already in core — must not be added to granted_tools"
    )
    assert "already available" in result["display"], (
        f"'already available' expected in display: {result['display']!r}"
    )
    granted = result.get("granted", [])
    assert "web_search" not in granted, "web_search must not appear in granted list"
