"""Verify deferred tools surface for expected keyword queries after prompt updates.

Tests that first-line descriptions (extracted by agent._register_tool) contain
keywords the SDK's DeferredLoadingToolset would match on. The SDK uses lowercased
substring matching: each search term is checked against 'name + description'.
We test the description side to catch regressions where a docstring update
accidentally removes a keyword the SDK needs for discovery.
"""

from co_cli.agent._native_toolset import _build_native_toolset
from co_cli.config._core import settings
from co_cli.deps import VisibilityPolicyEnum

_NATIVE_TOOLSET, _NATIVE_INDEX = _build_native_toolset(settings)


def _deferred_descriptions() -> dict[str, str]:
    """Return {tool_name: first_line_description} for all deferred tools."""
    return {
        name: info.description.lower()
        for name, info in _NATIVE_INDEX.items()
        if info.visibility == VisibilityPolicyEnum.DEFERRED
    }


# ---------------------------------------------------------------------------
# Deferred tool keyword discovery
# ---------------------------------------------------------------------------


def test_file_write_tools_discoverable_by_keywords() -> None:
    """write_file and edit_file surface for file-writing keyword queries."""
    descs = _deferred_descriptions()
    assert "write" in descs["write_file"]
    assert "file" in descs["write_file"]
    assert "edit" in descs["edit_file"]
    assert "file" in descs["edit_file"]
    assert "replace" in descs["edit_file"]


def test_background_task_tools_discoverable_by_keywords() -> None:
    """Background task tools surface for background/long-running queries."""
    descs = _deferred_descriptions()
    assert "background" in descs["start_background_task"]
    assert "long-running" in descs["start_background_task"]
    assert "background" in descs["check_task_status"]
    assert "status" in descs["check_task_status"]
    assert "cancel" in descs["cancel_background_task"]
    assert "background" in descs["cancel_background_task"]
    assert "background" in descs["list_background_tasks"]


def test_delegation_tools_discoverable_by_keywords() -> None:
    """Delegation tools surface for delegation/analysis/research queries."""
    descs = _deferred_descriptions()
    assert "coder" in descs["delegate_coder"] or "coding" in descs["delegate_coder"]
    assert "analysis" in descs["delegate_coder"] or "codebase" in descs["delegate_coder"]
    assert "research" in descs["delegate_researcher"]
    assert "web" in descs["delegate_researcher"]
    assert "analysis" in descs["delegate_analyst"] or "knowledge" in descs["delegate_analyst"]
    assert "reasoning" in descs["delegate_reasoner"] or "thinking" in descs["delegate_reasoner"]


def test_memory_write_tools_not_in_agent() -> None:
    """Memory write tools must not be registered in the agent at all."""
    descs = _deferred_descriptions()
    # Write tools removed from agent in P1 refactor
    assert "save_memory" not in descs, "save_memory must not be registered in agent"
    assert "update_memory" not in descs, "update_memory must not be registered in agent"
    assert "append_memory" not in descs, "append_memory must not be registered in agent"
    # save_insight is extractor-only — must never appear in the main agent tool_index
    assert "save_insight" not in _NATIVE_INDEX, (
        "save_insight must not be registered in main agent — extractor-only tool"
    )
    # Read tools are always-visible (not deferred), so present in tool_index but not deferred
    assert "search_memories" in _NATIVE_INDEX
    assert "list_memories" in _NATIVE_INDEX


def test_article_write_tool_discoverable_by_keywords() -> None:
    """Article save tool surfaces for save/reference queries."""
    descs = _deferred_descriptions()
    assert "save" in descs["save_article"]
    assert "reference" in descs["save_article"]


def test_all_deferred_tools_have_nonempty_descriptions() -> None:
    """Every deferred tool must have a non-empty first-line description."""
    descs = _deferred_descriptions()
    for name, desc in descs.items():
        assert desc.strip(), f"Deferred tool {name!r} has empty description"
