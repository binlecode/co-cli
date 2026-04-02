"""Functional tests for agent factory — tool registration, approval wiring, and loading policy."""

import dataclasses
from pathlib import Path

from co_cli.agent import build_agent, build_task_agent
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import settings, ROLE_TASK
from co_cli.deps import CoConfig


# Config with fake integration paths so domain tools are always registered in tests,
# regardless of whether the developer's local settings have these paths configured.
_CONFIG_WITH_INTEGRATIONS = dataclasses.replace(
    CoConfig.from_settings(settings, cwd=Path.cwd()),
    obsidian_vault_path=Path("/fake/vault"),
    google_credentials_path="/fake/creds.json",
)


def test_build_agent_registers_all_tools():
    """build_agent() registers core tools with no duplicates, and conditionally registers sub-agent tools."""
    result = build_agent(config=_CONFIG_WITH_INTEGRATIONS)
    tool_names = list(result.tool_index.keys())
    assert len(tool_names) == len(set(tool_names)), "Duplicate tool registration"

    # Core tools always present
    for tool in ("run_shell_command", "check_capabilities", "web_search", "save_memory"):
        assert tool in result.tool_index, f"Expected core tool '{tool}' to be registered"

    # Sub-agent conditional: run_coding_subagent present iff coding role model is set
    if settings.role_models.get("coding"):
        assert "run_coding_subagent" in result.tool_index
    else:
        assert "run_coding_subagent" not in result.tool_index

    # Verify with CoConfig() (no role models): run_coding_subagent must be absent
    bare_result = build_agent(config=CoConfig())
    assert "run_coding_subagent" not in bare_result.tool_index


def test_approval_tools_flagged():
    """Side-effectful tools require approval; read-only and intra-tool-approval tools do not."""
    result = build_agent(config=_CONFIG_WITH_INTEGRATIONS)

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
    result = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
    assert result.tool_index["web_search"].approval is False
    assert result.tool_index["web_fetch"].approval is False


def test_build_task_agent_registers_same_tools_as_main_agent():
    """build_task_agent() registers the same tools and approval flags as build_agent()."""
    config = _CONFIG_WITH_INTEGRATIONS
    registry = ModelRegistry.from_config(config)
    task_resolved = registry.get(ROLE_TASK, ResolvedModel(model=None, settings=None))

    main_result = build_agent(config=config)
    task_result = build_task_agent(config=config, role_model=task_resolved)

    assert set(task_result.tool_index.keys()) == set(main_result.tool_index.keys())
    main_approvals = {name: tc.approval for name, tc in main_result.tool_index.items()}
    task_approvals = {name: tc.approval for name, tc in task_result.tool_index.items()}
    assert task_approvals == main_approvals


def test_build_agent_excludes_domain_tools_when_config_absent():
    """Domain tools absent from tool_index when config paths are not set."""
    result = build_agent(config=CoConfig())
    assert "list_notes" not in result.tool_index
    assert "list_gmail_emails" not in result.tool_index
    assert "search_drive_files" not in result.tool_index
    # Core tools always registered
    assert "check_capabilities" in result.tool_index
    assert "run_shell_command" in result.tool_index
    assert "web_search" in result.tool_index


def test_build_task_agent_excludes_domain_tools_when_config_absent():
    """build_task_agent() excludes domain tools when config paths are absent."""
    result = build_task_agent(
        config=CoConfig(),
        role_model=ResolvedModel(model=None, settings=None),
    )
    assert "list_notes" not in result.tool_index
    assert "list_gmail_emails" not in result.tool_index


def test_tool_index_loading_policy_metadata():
    """Native tools carry loading-policy flags and source metadata in tool_index."""
    result = build_agent(config=_CONFIG_WITH_INTEGRATIONS)

    idx = result.tool_index
    assert len(idx) > 0, "tool_index must be populated"

    # Every entry must be native source with valid loading policy
    for name, tc in idx.items():
        assert tc.source == "native", f"{name}: source must be 'native', got {tc.source!r}"
        assert tc.always_load != tc.should_defer, (
            f"{name}: exactly one of always_load/should_defer must be True"
        )
        assert tc.name == name, f"index key {name!r} mismatches ToolConfig.name {tc.name!r}"

    # Spot-check always-loaded tools
    for name in ("check_capabilities", "search_tools", "read_file", "web_search",
                  "run_shell_command", "list_memories", "search_knowledge"):
        assert idx[name].always_load is True, f"{name} should be always_load"
        assert idx[name].should_defer is False, f"{name} should not be deferred"

    # Spot-check deferred tools
    for name in ("edit_file", "write_file", "save_memory", "start_background_task"):
        assert idx[name].should_defer is True, f"{name} should be deferred"
        assert idx[name].always_load is False, f"{name} should not be always_load"

    # Connector integration metadata
    assert idx["list_notes"].integration == "obsidian"
    assert idx["list_gmail_emails"].integration == "google_gmail"
    assert idx["search_drive_files"].integration == "google_drive"

    # Search hints on deferred tools
    assert idx["edit_file"].search_hint is not None
    assert idx["save_memory"].search_hint is not None


def test_tool_index_source_axis_native_only():
    """All entries in tool_index from build_agent() are native source (MCP not yet discovered)."""
    result = build_agent(config=CoConfig())
    for name, tc in result.tool_index.items():
        assert tc.source == "native", f"{name}: expected native source before MCP discovery"
