"""Functional tests for the search_tools progressive discovery tool."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent, build_tool_registry
from co_cli.config import settings
from co_cli.deps import CoDeps, CoConfig
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.tool_search import search_tools

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_TOOL_REG = build_tool_registry(_CONFIG)
_AGENT = build_agent(config=_CONFIG)


def _make_deps() -> CoDeps:
    """Build real CoDeps with tool_index populated from build_tool_registry()."""
    return CoDeps(
        shell=ShellBackend(),
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG,
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
    granted = (result.metadata or {}).get("granted", [])
    assert "edit_file" in granted, f"edit_file missing from granted list: {granted}"
    assert "unlocked" in result.return_value, "display must mention 'unlocked'"


@pytest.mark.asyncio
async def test_search_tools_no_match_returns_hint() -> None:
    """search_tools with no matching query returns the fallback hint text."""
    deps = _make_deps()
    ctx = _make_ctx(deps)

    result = await search_tools(ctx, "zzznomatch_xyzzy_unlikely_token")

    assert "Try:" in result.return_value, (
        f"Fallback hint expected in display: {result.return_value!r}"
    )
    assert (result.metadata or {}).get("granted", []) == []


@pytest.mark.asyncio
async def test_search_tools_always_loaded_tool_already_available() -> None:
    """search_tools for an always-loaded tool shows 'already available' and does not discover it."""
    deps = _make_deps()
    ctx = _make_ctx(deps)

    result = await search_tools(ctx, "web search")

    assert "web_search" not in deps.session.discovered_tools, (
        "web_search is always-loaded — must not be added to discovered_tools"
    )
    assert "already available" in result.return_value, (
        f"'already available' expected in display: {result.return_value!r}"
    )
    granted = (result.metadata or {}).get("granted", [])
    assert "web_search" not in granted, "web_search must not appear in granted list"
