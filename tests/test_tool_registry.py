"""Functional tests for SDK-native deferred tool loading and discovery."""

import pytest
from tests._settings import make_settings

from co_cli.agent._core import build_tool_registry
from co_cli.config._core import settings
from co_cli.context._deferred_tool_prompt import build_category_awareness_prompt
from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

_CONFIG = settings

# Config with integrations so domain tools are registered in tests
_CONFIG_WITH_INTEGRATIONS = settings.model_copy(
    update={
        "obsidian_vault_path": "/fake/vault",
        "google_credentials_path": "/fake/creds.json",
    }
)


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
    assert "code execution" in prompt
    assert "sub-agents" in prompt


def test_category_awareness_prompt_includes_representative_tool_names() -> None:
    """Category awareness prompt includes representative tool names for native deferred categories.

    Regression: if build_category_awareness_prompt stops emitting inline tool names,
    local models lack concrete keywords to form effective search_tools queries,
    reverting to shell over dedicated tools.
    """
    result = build_tool_registry(_CONFIG)
    prompt = build_category_awareness_prompt(result.tool_index)
    assert "write_file" in prompt
    assert "patch" in prompt
    assert "task_start" in prompt
    assert "execute_code" in prompt
    assert "analyze_code" not in prompt


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


def test_native_and_mcp_both_populate_tool_index() -> None:
    """tool_index contains ToolSourceEnum.NATIVE entries from native path and accepts MCP entries."""
    result = build_tool_registry(_CONFIG)
    native_entries = [
        info for info in result.tool_index.values() if info.source == ToolSourceEnum.NATIVE
    ]
    assert len(native_entries) >= 1

    # Inject a synthetic MCP entry (mirrors the pattern from discover_mcp_tools)
    result.tool_index["mcp_synthetic"] = ToolInfo(
        name="mcp_synthetic",
        description="synthetic mcp tool",
        approval=False,
        source=ToolSourceEnum.MCP,
        visibility=VisibilityPolicyEnum.DEFERRED,
        integration="test_server",
    )
    sources = {info.source for info in result.tool_index.values()}
    assert ToolSourceEnum.NATIVE in sources
    assert ToolSourceEnum.MCP in sources
