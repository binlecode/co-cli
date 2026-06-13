"""Behavioral tests for the tool_view deferred-tool loader.

Two layers:
- Resolution ladder (the tool_view tool over a synthetic deferred catalog): exact /
  fuzzy-suggest / no-match, and which branch reveals.
- Visibility gate (the real native toolset + per-turn filter): a DEFERRED tool is
  hidden until revealed, tool_view is always present, no tool carries the SDK
  defer_loading flag (so search_tools can never engage), and visibility is driven by
  runtime state — not message history — so reveals survive compaction for free.
"""

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
from co_cli.deps import (
    CoDeps,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.tool_view import tool_view


def _deferred(name: str, description: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=description,
        is_approval_required=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.DEFERRED,
        is_concurrent_safe=False,
    )


# A fabricated deferred catalog. session_search is an A2-candidate name included to show
# the loader is agnostic to which tools are deferred.
_SYNTHETIC_INDEX = {
    "skill_create": _deferred("skill_create", "Author a new skill."),
    "skill_delete": _deferred("skill_delete", "Remove a skill."),
    "session_search": _deferred("session_search", "Search past conversation transcripts."),
}


def _make_deps(tmp_path, tool_catalog) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        tool_catalog=tool_catalog,
        session=CoSessionState(),
        tool_results_dir=tmp_path / "tool-results",
    )


def _ctx(deps: CoDeps, *, messages=None) -> RunContext[CoDeps]:
    return RunContext(
        deps=deps,
        model=None,
        usage=RunUsage(),
        tool_name="tool_view",
        messages=messages or [],
    )


def _is_error(result) -> bool:
    return result.metadata is not None and result.metadata.get("error") is True


# ---------------------------------------------------------------------------
# Resolution ladder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalized_exact_match_reveals_canonical(tmp_path) -> None:
    """A case/separator variant (`Skill-Create`) resolves to the canonical name and reveals it.

    `Skill-Create` exercises the full normalization path — lowercasing, hyphen→space, and
    the whitespace split — so it covers the space-separated form too.
    """
    deps = _make_deps(tmp_path, dict(_SYNTHETIC_INDEX))
    ctx = _ctx(deps)
    result = await tool_view(ctx, name="Skill-Create")
    assert not _is_error(result)
    assert "skill_create" in deps.runtime.revealed_tools


@pytest.mark.asyncio
async def test_typo_suggests_without_revealing(tmp_path) -> None:
    """A near-miss typo returns candidate suggestions and reveals nothing."""
    deps = _make_deps(tmp_path, dict(_SYNTHETIC_INDEX))
    ctx = _ctx(deps)
    result = await tool_view(ctx, name="skil_create")
    assert not _is_error(result)
    assert "skill_create" in result.return_value
    assert deps.runtime.revealed_tools == set()


@pytest.mark.asyncio
async def test_no_overlap_name_is_terminal_and_reveals_nothing(tmp_path) -> None:
    """A name with no fuzzy overlap is a terminal no-retry error, revealing nothing."""
    deps = _make_deps(tmp_path, dict(_SYNTHETIC_INDEX))
    ctx = _ctx(deps)
    result = await tool_view(ctx, name="quantum_flux_capacitor")
    assert _is_error(result)
    assert deps.runtime.revealed_tools == set()


# ---------------------------------------------------------------------------
# Visibility gate over the real native toolset
# ---------------------------------------------------------------------------


async def _visible_tool_names(toolset, ctx) -> set[str]:
    prepared = await toolset.for_run(ctx)
    tools = await prepared.get_tools(ctx)
    return set(tools.keys())


@pytest.mark.asyncio
async def test_deferred_tool_hidden_until_loaded_by_name(tmp_path) -> None:
    """skill_create is hidden until tool_view reveals it; tool_view is always present.

    Also asserts no tool carries the SDK defer_loading flag — so the auto-injected
    search_tools loader can never engage (co owns deferral via the filter).
    """
    native_toolset, tool_catalog = build_native_toolset()
    toolset = assemble_routing_toolset(native_toolset, [])
    deps = _make_deps(tmp_path, tool_catalog)
    ctx = _ctx(deps)

    before = await _visible_tool_names(toolset, ctx)
    assert "tool_view" in before
    assert "skill_create" not in before

    # The load-bearing suppression guarantee: no tool carries defer_loading, so when the
    # Agent wraps this toolset in the auto-injected ToolSearchToolset it stays inert and
    # never produces `search_tools`. Asserted on the flag (not on `search_tools` absence)
    # because this toolset is exercised directly here, without the Agent's outer wrap.
    prepared = await toolset.for_run(ctx)
    tools = await prepared.get_tools(ctx)
    assert all(not t.tool_def.defer_loading for t in tools.values())

    deps.runtime.revealed_tools.add("skill_create")
    after = await _visible_tool_names(toolset, ctx)
    assert "skill_create" in after


@pytest.mark.asyncio
async def test_task_write_and_close_are_deferred(tmp_path) -> None:
    """task_write/task_close are hidden until tool_view reveals them.

    The ALWAYS floor (tool_view, shell_exec) stays visible with no reveal, proving
    the two new interactive-drive tools join the DEFERRED tier and do not widen the
    always-present prefill.
    """
    native_toolset, tool_catalog = build_native_toolset()
    toolset = assemble_routing_toolset(native_toolset, [])
    deps = _make_deps(tmp_path, tool_catalog)
    ctx = _ctx(deps)

    before = await _visible_tool_names(toolset, ctx)
    assert "tool_view" in before
    assert "shell_exec" in before
    assert "task_write" not in before
    assert "task_close" not in before

    deps.runtime.revealed_tools.update({"task_write", "task_close"})
    after = await _visible_tool_names(toolset, ctx)
    assert {"task_write", "task_close"} <= after


@pytest.mark.asyncio
async def test_visibility_independent_of_message_history(tmp_path) -> None:
    """Reveal state lives in runtime, not history — so it survives compaction.

    The same deps with an empty history and with a long history yield identical
    visibility, proving the gate does not re-derive reveals from messages (what made
    the old search_tools preservation coupling necessary).
    """
    native_toolset, tool_catalog = build_native_toolset()
    toolset = assemble_routing_toolset(native_toolset, [])
    deps = _make_deps(tmp_path, tool_catalog)
    deps.runtime.revealed_tools.add("skill_create")

    long_history = [ModelRequest(parts=[UserPromptPart(content="x")]) for _ in range(20)]
    empty_ctx = _ctx(deps, messages=[])
    full_ctx = _ctx(deps, messages=long_history)

    assert await _visible_tool_names(toolset, empty_ctx) == await _visible_tool_names(
        toolset, full_ctx
    )
    assert "skill_create" in await _visible_tool_names(toolset, empty_ctx)
