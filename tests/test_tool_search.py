"""Functional tests for the search_tools progressive discovery tool."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent, build_tool_registry
from co_cli.config._core import settings
from co_cli.deps import CoDeps, ToolInfo, LoadPolicy, ToolSource
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_search import search_tools

_CONFIG = settings
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


@pytest.mark.asyncio
async def test_search_tools_no_false_positive_substring() -> None:
    """'create' must not match 'recreate_session' — word-boundary scoring."""
    deps = _make_deps()
    # Inject a test tool whose name/description only contain "recreate", not "create"
    deps.tool_index["recreate_session"] = ToolInfo(
        name="recreate_session",
        description="Recreate session state",
        approval=False,
        source=ToolSource.NATIVE,
        load=LoadPolicy.DEFERRED,
    )
    ctx = _make_ctx(deps)

    result = await search_tools(ctx, "create")

    assert "recreate_session" not in result.return_value, (
        "'create' must not match 'recreate_session' via substring"
    )


@pytest.mark.asyncio
async def test_search_tools_no_false_negatives() -> None:
    """Word-boundary scoring must not break exact token matches (BC-2)."""
    deps = _make_deps()
    ctx = _make_ctx(deps)

    # "edit" should match "edit_file"
    result = await search_tools(ctx, "edit")
    assert "edit_file" in result.return_value, (
        f"'edit' must match 'edit_file': {result.return_value!r}"
    )

    # "file" should match both "read_file" and "write_file"
    deps2 = _make_deps()
    ctx2 = _make_ctx(deps2)
    result2 = await search_tools(ctx2, "file")
    assert "read_file" in result2.return_value, (
        f"'file' must match 'read_file': {result2.return_value!r}"
    )
    assert "write_file" in result2.return_value, (
        f"'file' must match 'write_file': {result2.return_value!r}"
    )

    # "search" should match tools with "search" as a word token
    deps3 = _make_deps()
    ctx3 = _make_ctx(deps3)
    result3 = await search_tools(ctx3, "search")
    assert "search_memories" in result3.return_value, (
        f"'search' must match 'search_memories': {result3.return_value!r}"
    )


def test_deferred_prompt_includes_search_hint() -> None:
    """Deferred prompt shows [hints: ...] when search_hint is present."""
    from co_cli.context._deferred_tool_prompt import build_deferred_tool_prompt

    idx = {
        "tool_a": ToolInfo(
            name="tool_a", description="does stuff", approval=False,
            source=ToolSource.NATIVE, load=LoadPolicy.DEFERRED,
            search_hint="kw1 kw2",
        ),
    }
    prompt = build_deferred_tool_prompt(idx, set())
    assert prompt is not None
    assert "[hints: kw1 kw2]" in prompt


def test_filter_returns_false_for_unknown_tool() -> None:
    """_filter returns False for tools not in tool_index (default-deny)."""
    from pydantic_ai.tools import ToolDefinition

    deps = _make_deps()
    ctx = _make_ctx(deps)
    # Use the filter_func from the FilteredToolset
    filter_fn = _TOOL_REG.toolset.filter_func
    unknown_tool = ToolDefinition(name="totally_unknown_tool_xyz", description="test")
    assert filter_fn(ctx, unknown_tool) is False


def test_deferred_prompt_omits_hints_when_none() -> None:
    """Deferred prompt omits [hints: ...] when search_hint is None."""
    from co_cli.context._deferred_tool_prompt import build_deferred_tool_prompt

    idx = {
        "tool_b": ToolInfo(
            name="tool_b", description="does stuff", approval=False,
            source=ToolSource.NATIVE, load=LoadPolicy.DEFERRED,
        ),
    }
    prompt = build_deferred_tool_prompt(idx, set())
    assert prompt is not None
    assert "[hints:" not in prompt
