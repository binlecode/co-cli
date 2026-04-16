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
from co_cli.prompts._assembly import build_static_instructions
from co_cli.tools.shell import run_shell_command

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
    """write_file and patch surface for file-writing keyword queries."""
    descs = _deferred_descriptions()
    assert "write" in descs["write_file"]
    assert "file" in descs["write_file"]
    assert "edit" in descs["patch"]
    assert "file" in descs["patch"]


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


def test_execute_code_tool_discoverable_by_keywords() -> None:
    """execute_code surfaces for code execution queries."""
    descs = _deferred_descriptions()
    assert "execute_code" in descs
    assert "run" in descs["execute_code"] or "interpreter" in descs["execute_code"]


def test_delegation_tools_discoverable_by_keywords() -> None:
    """Delegation tools surface for delegation/analysis/research queries."""
    descs = _deferred_descriptions()
    assert "analyze_code" not in descs
    assert "research" in descs["research_web"]
    assert "web" in descs["research_web"]
    assert "analysis" in descs["analyze_knowledge"] or "knowledge" in descs["analyze_knowledge"]
    assert "reasoning" in descs["reason_about"] or "thinking" in descs["reason_about"]


def test_memory_write_tools_not_in_agent() -> None:
    """Memory write tools must not be registered in the agent at all."""
    descs = _deferred_descriptions()
    # Write tools removed from agent in P1 refactor
    assert "save_memory" not in descs, "save_memory must not be registered in agent"
    assert "update_memory" not in descs, "update_memory must not be registered in agent"
    assert "append_memory" not in descs, "append_memory must not be registered in agent"
    # save_memory is extractor-only — must never appear in the main agent tool_index
    assert "save_memory" not in _NATIVE_INDEX, (
        "save_memory must not be registered in main agent — extractor-only tool"
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


# ---------------------------------------------------------------------------
# Assembled static instructions: search_tools guidance present
# ---------------------------------------------------------------------------


def test_static_instructions_contain_search_tools_guidance() -> None:
    """Assembled static instructions must reference search_tools for deferred discovery.

    Regression: if 04_tool_protocol.md loses its Deferred discovery section,
    the model gets no instruction to call search_tools and reverts to shell.
    """
    text = build_static_instructions(settings)
    assert "search_tools" in text


# ---------------------------------------------------------------------------
# Shell tool description: explicit redirects for write/edit and background tasks
# ---------------------------------------------------------------------------


def test_shell_tool_description_redirects_file_operations() -> None:
    """Shell tool docstring must redirect file creation/editing to write_file / patch.

    Regression: if these redirects are dropped, the model uses shell redirection
    (echo >>, cat <<EOF) instead of the dedicated write/edit tools.
    """
    doc = (run_shell_command.__doc__ or "").lower()
    assert "write_file" in doc
    assert "patch" in doc


def test_shell_tool_description_redirects_background_tasks() -> None:
    """Shell tool docstring must redirect detached long-running work to start_background_task.

    Regression: if this redirect is dropped, the model backgrounds processes with
    & or nohup via shell instead of using the managed background task tool.
    """
    doc = run_shell_command.__doc__ or ""
    assert "start_background_task" in doc
