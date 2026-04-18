"""Functional tests for SDK-native deferred tool loading and discovery."""

import pytest
from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent, build_tool_registry
from co_cli.agent._native_toolset import _approval_resume_filter, _build_native_toolset
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
    assert "start_background_task" in prompt
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
    assert _approval_resume_filter(ctx, ToolDefinition(name="patch", description="")) is True
    # Unknown tool
    assert _approval_resume_filter(ctx, ToolDefinition(name="unknown_xyz", description="")) is True


def test_approval_resume_filter_narrows_to_approved_plus_always() -> None:
    """During resume, only resume_tool_names + ALWAYS tools are visible (BC-4)."""
    deps = _make_deps(resume_tool_names=frozenset({"patch"}))
    ctx = _make_ctx(deps)
    # Approved deferred tool — visible
    assert _approval_resume_filter(ctx, ToolDefinition(name="patch", description="")) is True
    # Always tool — visible even without being in resume set
    assert _approval_resume_filter(ctx, ToolDefinition(name="read_file", description="")) is True
    # Deferred tool NOT in resume set — hidden
    assert _approval_resume_filter(ctx, ToolDefinition(name="write_file", description="")) is False
    # Another deferred tool NOT in resume set — hidden
    assert (
        _approval_resume_filter(ctx, ToolDefinition(name="save_article", description="")) is False
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


# ---------------------------------------------------------------------------
# Sequential flag: write_file and patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_tools_are_sequential() -> None:
    """write_file and patch must carry sequential=True; read_file must not."""
    from co_cli.agent._native_toolset import _build_native_toolset

    toolset, native_index = _build_native_toolset(_CONFIG)
    ctx = _make_ctx(_make_deps())
    tools = await toolset.get_tools(ctx)
    assert tools["write_file"].tool_def.sequential is True
    assert tools["patch"].tool_def.sequential is True
    assert tools["read_file"].tool_def.sequential is False
    assert native_index["write_file"].is_concurrent_safe is False
    assert native_index["write_file"].is_read_only is False
    assert native_index["patch"].is_concurrent_safe is False
    assert native_index["patch"].is_read_only is False
    assert native_index["read_file"].is_read_only is True
    assert native_index["read_file"].is_concurrent_safe is True


@pytest.mark.asyncio
async def test_excluded_tools_are_not_sequential() -> None:
    """Tools explicitly excluded from sequential=True in the plan must not be marked sequential.

    These tools (save_article, run_shell_command, write_todos) were excluded because:
    - save_article: UUID keys — no shared-path conflict possible
    - run_shell_command: each call spawns an independent subprocess; serializing would
      unnecessarily block shell+read batches
    - write_todos: writes session-scoped in-memory state, not filesystem paths
    """
    from co_cli.agent._native_toolset import _build_native_toolset

    toolset, native_index = _build_native_toolset(_CONFIG)
    ctx = _make_ctx(_make_deps())
    tools = await toolset.get_tools(ctx)
    assert tools["save_article"].tool_def.sequential is False
    assert tools["run_shell_command"].tool_def.sequential is False
    assert tools["write_todos"].tool_def.sequential is False
    assert native_index["save_article"].is_concurrent_safe is True
    assert native_index["run_shell_command"].is_concurrent_safe is True
    assert native_index["write_todos"].is_concurrent_safe is True


def test_toolinfo_read_only_tools() -> None:
    """Read-only tools must have is_read_only=True and is_concurrent_safe=True."""
    from co_cli.agent._native_toolset import _build_native_toolset

    _, native_index = _build_native_toolset(_CONFIG)
    assert native_index["read_file"].is_read_only is True
    assert native_index["read_file"].is_concurrent_safe is True
    assert native_index["glob"].is_read_only is True
    assert native_index["glob"].is_concurrent_safe is True
    assert native_index["grep"].is_read_only is True
    assert native_index["grep"].is_concurrent_safe is True


@pytest.mark.asyncio
async def test_sequential_tool_count() -> None:
    """Exactly 3 tools in the native toolset must have sequential=True: write_file, patch, execute_code."""
    from co_cli.agent._native_toolset import _build_native_toolset

    toolset, _ = _build_native_toolset(_CONFIG)
    ctx = _make_ctx(_make_deps())
    tools = await toolset.get_tools(ctx)
    sequential_names = {name for name, t in tools.items() if t.tool_def.sequential}
    assert sequential_names == {"write_file", "patch", "execute_code"}


def test_toolinfo_retries() -> None:
    """retries field in ToolInfo mirrors the value passed to _register_tool()."""
    from co_cli.agent._native_toolset import _build_native_toolset

    _, native_index = _build_native_toolset(_CONFIG)
    assert native_index["web_search"].retries == 3
    assert native_index["web_fetch"].retries == 3
    assert native_index["write_file"].retries == 1
    assert native_index["patch"].retries == 1
    assert native_index["save_article"].retries == 1
    assert native_index["check_capabilities"].retries is None


def test_approval_resume_filter_hides_previously_discovered_deferred() -> None:
    """During resume, deferred tools not in resume_tool_names are hidden even if previously discovered."""
    # This validates BC-4: discovery state doesn't grant resume visibility
    deps = _make_deps(resume_tool_names=frozenset({"patch"}))
    ctx = _make_ctx(deps)
    # save_article is deferred, not in resume set — must be hidden
    # regardless of whether it was previously discovered via search_tools
    assert (
        _approval_resume_filter(ctx, ToolDefinition(name="save_article", description="")) is False
    )


# ---------------------------------------------------------------------------
# TASK-6: requires_config gating + policy parity
# ---------------------------------------------------------------------------


def test_requires_config_gates_integration_tools() -> None:
    """requires_config gates register/unregister integration tools correctly."""
    # Permutation 1: no integrations — both obsidian and google absent
    config_none = make_settings(obsidian_vault_path=None, google_credentials_path=None)
    _, index_none = _build_native_toolset(config_none)
    assert "list_notes" not in index_none
    assert "search_notes" not in index_none
    assert "read_note" not in index_none
    assert "search_drive_files" not in index_none
    assert "list_gmail_emails" not in index_none
    assert "list_calendar_events" not in index_none

    # Permutation 2: obsidian present, google absent
    config_obs = make_settings(obsidian_vault_path="/tmp/vault", google_credentials_path=None)
    _, index_obs = _build_native_toolset(config_obs)
    assert "list_notes" in index_obs
    assert "search_notes" in index_obs
    assert "read_note" in index_obs
    assert "search_drive_files" not in index_obs
    assert "list_gmail_emails" not in index_obs

    # Permutation 3: google present, obsidian absent
    config_google = make_settings(
        obsidian_vault_path=None, google_credentials_path="/tmp/creds.json"
    )
    _, index_google = _build_native_toolset(config_google)
    assert "list_notes" not in index_google
    assert "search_drive_files" in index_google
    assert "list_gmail_emails" in index_google
    assert "list_calendar_events" in index_google


def test_tool_index_policies_match_expectation() -> None:
    """Tool index contains correct non-default policy fields for representative tools."""
    config = make_settings(obsidian_vault_path="/x", google_credentials_path="/y")
    _, index = _build_native_toolset(config)

    assert index["read_file"].max_result_size == 80_000
    assert index["run_shell_command"].max_result_size == 30_000
    assert index["write_file"].approval is True
    assert index["write_file"].retries == 1
    assert index["patch"].approval is True
    assert index["patch"].retries == 1
    assert index["web_search"].retries == 3
    assert index["web_fetch"].retries == 3
    assert index["create_gmail_draft"].approval is True
    assert index["create_gmail_draft"].retries == 1
    assert index["search_drive_files"].retries == 3


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
