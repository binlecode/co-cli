"""Functional tests for SDK-native deferred tool loading and discovery."""

import pytest
from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

from co_cli.agent import _approval_resume_filter, build_agent, build_tool_registry
from co_cli.config._core import settings
from co_cli.context._deferred_tool_prompt import build_category_awareness_prompt
from co_cli.deps import CoDeps, CoRuntimeState, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.tools.shell_backend import ShellBackend

_CONFIG = settings

# Config with integrations so domain tools are registered in tests
_CONFIG_WITH_INTEGRATIONS = settings.model_copy(
    update={
        "obsidian_vault_path": "/fake/vault",
        "google_credentials_path": "/fake/creds.json",
    }
)

_TOOL_REG = build_tool_registry(_CONFIG)
_AGENT = build_agent(config=_CONFIG)


def _make_deps(**runtime_overrides) -> CoDeps:
    """Build real CoDeps with tool_index from build_tool_registry()."""
    runtime = CoRuntimeState(**runtime_overrides)
    return CoDeps(
        shell=ShellBackend(),
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG,
        runtime=runtime,
    )


def _make_ctx(deps: CoDeps) -> RunContext:
    """Build a real RunContext bound to the given deps."""
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# ---------------------------------------------------------------------------
# BC-6: search_tools name reservation
# ---------------------------------------------------------------------------


def test_search_tools_not_in_tool_index() -> None:
    """co-cli must not register search_tools — the SDK reserves that name (BC-6)."""
    result = build_tool_registry(_CONFIG)
    assert "search_tools" not in result.tool_index


# ---------------------------------------------------------------------------
# Tool registry: deferred and always-visible
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Category awareness prompt
# ---------------------------------------------------------------------------


def test_category_awareness_prompt_includes_native_categories() -> None:
    """Category awareness prompt lists native deferred groups."""
    result = build_tool_registry(_CONFIG)
    prompt = build_category_awareness_prompt(result.tool_index)
    assert "file editing" in prompt
    assert "memory management" in prompt
    assert "background tasks" in prompt
    assert "sub-agents" in prompt


def test_category_awareness_prompt_includes_integration_categories() -> None:
    """Category awareness prompt lists integration categories when config is present."""
    result = build_tool_registry(_CONFIG_WITH_INTEGRATIONS)
    prompt = build_category_awareness_prompt(result.tool_index)
    assert "Gmail" in prompt
    assert "Google Calendar" in prompt
    assert "Google Drive" in prompt
    assert "Obsidian notes" in prompt


def test_category_awareness_prompt_excludes_absent_integrations() -> None:
    """Category awareness prompt omits integrations when their config is absent."""
    from tests._settings import make_settings

    config_no_integrations = make_settings(
        obsidian_vault_path=None,
        google_credentials_path=None,
    )
    result = build_tool_registry(config_no_integrations)
    prompt = build_category_awareness_prompt(result.tool_index)
    assert "Gmail" not in prompt
    assert "Obsidian" not in prompt


def test_category_awareness_prompt_empty_when_no_deferred() -> None:
    """Category awareness prompt returns empty string when no deferred tools exist."""
    idx = {
        "tool_a": ToolInfo(
            name="tool_a",
            description="does stuff",
            approval=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.ALWAYS,
        ),
    }
    prompt = build_category_awareness_prompt(idx)
    assert prompt == ""


# ---------------------------------------------------------------------------
# ToolInfo field removal
# ---------------------------------------------------------------------------


def test_toolinfo_no_search_hint_field() -> None:
    """ToolInfo no longer accepts search_hint — field was removed."""
    with pytest.raises(TypeError):
        ToolInfo(
            name="x",
            description="x",
            approval=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.ALWAYS,
            search_hint="should fail",
        )


# ---------------------------------------------------------------------------
# BC-4: Approval-resume narrowing (native and MCP)
# ---------------------------------------------------------------------------


def test_approval_resume_filter_normal_turn_passes_all() -> None:
    """During normal turns (resume_tool_names=None), filter passes all tools."""
    deps = _make_deps()
    ctx = _make_ctx(deps)
    # Always tool
    assert _approval_resume_filter(ctx, ToolDefinition(name="read_file", description="")) is True
    # Deferred tool
    assert _approval_resume_filter(ctx, ToolDefinition(name="edit_file", description="")) is True
    # Unknown tool
    assert _approval_resume_filter(ctx, ToolDefinition(name="unknown_xyz", description="")) is True


def test_approval_resume_filter_narrows_to_approved_plus_always() -> None:
    """During resume, only resume_tool_names + ALWAYS tools are visible (BC-4)."""
    deps = _make_deps(resume_tool_names=frozenset({"edit_file"}))
    ctx = _make_ctx(deps)
    # Approved deferred tool — visible
    assert _approval_resume_filter(ctx, ToolDefinition(name="edit_file", description="")) is True
    # Always tool — visible even without being in resume set
    assert _approval_resume_filter(ctx, ToolDefinition(name="read_file", description="")) is True
    # Deferred tool NOT in resume set — hidden
    assert _approval_resume_filter(ctx, ToolDefinition(name="write_file", description="")) is False
    # Another deferred tool NOT in resume set — hidden
    assert (
        _approval_resume_filter(ctx, ToolDefinition(name="save_memory", description="")) is False
    )


def test_approval_resume_filter_applies_to_mcp_tools() -> None:
    """Approval-resume narrowing applies uniformly to MCP tools (BC-4)."""
    deps = _make_deps(resume_tool_names=frozenset({"mcp_approved_tool"}))
    # Add MCP tool entries to tool_index
    deps.tool_index["mcp_approved_tool"] = ToolInfo(
        name="mcp_approved_tool",
        description="mcp tool",
        approval=True,
        source=ToolSourceEnum.MCP,
        visibility=VisibilityPolicyEnum.DEFERRED,
        integration="test_mcp",
    )
    deps.tool_index["mcp_other_tool"] = ToolInfo(
        name="mcp_other_tool",
        description="other mcp tool",
        approval=False,
        source=ToolSourceEnum.MCP,
        visibility=VisibilityPolicyEnum.DEFERRED,
        integration="test_mcp",
    )
    ctx = _make_ctx(deps)
    # MCP tool in resume set — visible
    assert (
        _approval_resume_filter(ctx, ToolDefinition(name="mcp_approved_tool", description=""))
        is True
    )
    # MCP tool NOT in resume set — hidden
    assert (
        _approval_resume_filter(ctx, ToolDefinition(name="mcp_other_tool", description=""))
        is False
    )


def test_approval_resume_filter_hides_previously_discovered_deferred() -> None:
    """During resume, deferred tools not in resume_tool_names are hidden even if previously discovered."""
    # This validates BC-4: discovery state doesn't grant resume visibility
    deps = _make_deps(resume_tool_names=frozenset({"edit_file"}))
    ctx = _make_ctx(deps)
    # save_memory is deferred, not in resume set — must be hidden
    # regardless of whether it was previously discovered via search_tools
    assert (
        _approval_resume_filter(ctx, ToolDefinition(name="save_memory", description="")) is False
    )
