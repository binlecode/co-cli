"""Functional tests for agent factory — tool registration, approval wiring, and loading policy."""

from pathlib import Path

from co_cli.agent import build_agent, build_tool_registry
from co_cli._model_factory import ModelRegistry
from co_cli.config._core import settings
from co_cli.context._tool_lifecycle import CoToolLifecycle
from tests._settings import test_settings
from co_cli.deps import LoadPolicy, ToolSource


# Config with fake integration paths so domain tools are always registered in tests,
# regardless of whether the developer's local settings have these paths configured.
_CONFIG_WITH_INTEGRATIONS = settings.model_copy(update={
    "obsidian_vault_path": "/fake/vault",
    "google_credentials_path": "/fake/creds.json",
})


def test_build_agent_registers_all_tools():
    """build_agent() registers core tools with no duplicates, and conditionally registers sub-agent tools."""
    result = build_tool_registry(_CONFIG_WITH_INTEGRATIONS)
    tool_names = list(result.tool_index.keys())
    assert len(tool_names) == len(set(tool_names)), "Duplicate tool registration"

    # Core tools always present
    for tool in ("run_shell_command", "check_capabilities", "web_search", "save_memory"):
        assert tool in result.tool_index, f"Expected core tool '{tool}' to be registered"

    # Sub-agent conditional: run_coding_subagent present iff coding role model is set
    if settings.llm.role_models.get("coding"):
        assert "run_coding_subagent" in result.tool_index
    else:
        assert "run_coding_subagent" not in result.tool_index

    # Verify with empty role_models: run_coding_subagent must be absent
    bare_llm = test_settings().llm.model_copy(update={"role_models": {}})
    bare_result = build_tool_registry(test_settings(llm=bare_llm))
    assert "run_coding_subagent" not in bare_result.tool_index


def test_approval_tools_flagged():
    """Side-effectful tools require approval; read-only and intra-tool-approval tools do not."""
    result = build_tool_registry(_CONFIG_WITH_INTEGRATIONS)

    # These tools must require approval at the agent layer
    for name in ("start_background_task", "save_memory", "write_file", "edit_file"):
        assert result.tool_index[name].approval is True, (
            f"Tool '{name}' should require approval but doesn't"
        )

    # Shell approval is intra-tool (raises ApprovalRequired); agent layer must be False
    assert result.tool_index["run_shell_command"].approval is False, (
        "run_shell_command agent-layer approval must be False (approval handled inside tool)"
    )

    # Read-only tools must not require approval
    for name in ("check_capabilities", "read_file", "search_knowledge"):
        assert result.tool_index[name].approval is False, (
            f"Tool '{name}' should NOT require approval but does"
        )


def test_web_tools_do_not_require_approval():
    """web_search and web_fetch follow the common read-only approval path."""
    result = build_tool_registry(settings)
    assert result.tool_index["web_search"].approval is False
    assert result.tool_index["web_fetch"].approval is False


def test_tool_registry_is_shared_across_agent_types():
    """Main and task agents share the same tool_index — both built from the same config."""
    reg = build_tool_registry(_CONFIG_WITH_INTEGRATIONS)
    assert len(reg.tool_index) > 0, "tool_index must be populated"
    # Verify the registry is deterministic: same config → same tools
    reg2 = build_tool_registry(_CONFIG_WITH_INTEGRATIONS)
    assert set(reg.tool_index.keys()) == set(reg2.tool_index.keys())


def test_build_agent_excludes_domain_tools_when_config_absent():
    """Domain tools absent from tool_index when config paths are not set."""
    result = build_tool_registry(test_settings(obsidian_vault_path=None, google_credentials_path=None))
    assert "list_notes" not in result.tool_index
    assert "list_gmail_emails" not in result.tool_index
    assert "search_drive_files" not in result.tool_index
    # Core tools always registered
    assert "check_capabilities" in result.tool_index
    assert "run_shell_command" in result.tool_index
    assert "web_search" in result.tool_index


def test_tool_registry_excludes_domain_tools_when_config_absent():
    """Domain tools absent from tool_index when config paths are not set (any agent type)."""
    result = build_tool_registry(test_settings(obsidian_vault_path=None, google_credentials_path=None))
    assert "list_notes" not in result.tool_index
    assert "list_gmail_emails" not in result.tool_index


def test_tool_index_loading_policy_metadata():
    """Native tools carry loading-policy flags and source metadata in tool_index."""
    result = build_tool_registry(_CONFIG_WITH_INTEGRATIONS)

    idx = result.tool_index
    assert len(idx) > 0, "tool_index must be populated"

    # Every entry must be native source with valid loading policy
    for name, tc in idx.items():
        assert tc.source == ToolSource.NATIVE, f"{name}: source must be NATIVE, got {tc.source!r}"
        assert tc.load in (LoadPolicy.ALWAYS, LoadPolicy.DEFERRED), (
            f"{name}: load must be a LoadPolicy enum value"
        )
        assert tc.name == name, f"index key {name!r} mismatches ToolInfo.name {tc.name!r}"

    # Spot-check always-loaded tools
    for name in ("check_capabilities", "search_tools", "read_file", "web_search",
                  "run_shell_command", "list_memories", "search_knowledge"):
        assert idx[name].load == LoadPolicy.ALWAYS, f"{name} should be ALWAYS"

    # Spot-check deferred tools
    for name in ("edit_file", "write_file", "save_memory", "start_background_task"):
        assert idx[name].load == LoadPolicy.DEFERRED, f"{name} should be DEFERRED"

    # Connector integration metadata
    assert idx["list_notes"].integration == "obsidian"
    assert idx["list_gmail_emails"].integration == "google_gmail"
    assert idx["search_drive_files"].integration == "google_drive"

    # Search hints on deferred tools
    assert idx["edit_file"].search_hint is not None
    assert idx["save_memory"].search_hint is not None

    # Per-tool max_result_size overrides
    assert idx["run_shell_command"].max_result_size == 30_000
    assert idx["read_file"].max_result_size == 80_000
    # All others should have the default (50,000)
    for name, tc in idx.items():
        if name not in ("run_shell_command", "read_file"):
            assert tc.max_result_size == 50_000, (
                f"{name}: expected default max_result_size=50000, got {tc.max_result_size}"
            )


def test_tool_index_source_axis_native_only():
    """All entries in tool_index from build_agent() are native source (MCP not yet discovered)."""
    result = build_tool_registry(test_settings())
    for name, tc in result.tool_index.items():
        assert tc.source == ToolSource.NATIVE, f"{name}: expected NATIVE source before MCP discovery"


def test_toolinfo_enum_construction():
    """ToolInfo accepts LoadPolicy/ToolSource enums; old boolean kwargs raise TypeError."""
    from co_cli.deps import ToolInfo, LoadPolicy, ToolSource

    # New enum API works
    info = ToolInfo(
        name="x", description="x", approval=False,
        source=ToolSource.NATIVE, load=LoadPolicy.ALWAYS,
    )
    assert info.load == LoadPolicy.ALWAYS
    assert info.source == ToolSource.NATIVE

    # Old boolean kwargs are rejected
    import pytest
    with pytest.raises(TypeError):
        ToolInfo(
            name="x", description="x", approval=False,
            source=ToolSource.NATIVE, always_load=True,
        )
    with pytest.raises(TypeError):
        ToolInfo(
            name="x", description="x", approval=False,
            source=ToolSource.NATIVE, should_defer=True,
        )


def test_build_agent_registers_tool_lifecycle_capability():
    """build_agent() registers CoToolLifecycle as a capability on the agent."""
    config = settings
    agent = build_agent(config=config)
    # _root_capability.capabilities holds the user-provided capability list
    children = agent._root_capability.capabilities
    lifecycle_caps = [c for c in children if isinstance(c, CoToolLifecycle)]
    assert len(lifecycle_caps) == 1, (
        f"Expected exactly one CoToolLifecycle capability, found {len(lifecycle_caps)}"
    )
