"""Functional tests for agent factory — tool registration and approval wiring."""

import dataclasses
from pathlib import Path

from co_cli.agent import build_agent, build_task_agent
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import WebPolicy, settings, ROLE_TASK
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
    # No-duplicates check
    result = build_agent(config=_CONFIG_WITH_INTEGRATIONS)
    assert len(result.tool_names) == len(set(result.tool_names)), "Duplicate tool registration"

    # Core tools always present
    for tool in ("run_shell_command", "check_capabilities", "web_search", "save_memory"):
        assert tool in result.tool_names, f"Expected core tool '{tool}' to be registered"

    # Sub-agent conditional: run_coder_subagent present iff coding role model is set
    if settings.role_models.get("coding"):
        assert "run_coder_subagent" in result.tool_names
    else:
        assert "run_coder_subagent" not in result.tool_names

    # Verify with CoConfig() (no role models): run_coder_subagent must be absent
    bare_result = build_agent(config=CoConfig())
    assert "run_coder_subagent" not in bare_result.tool_names


def test_approval_tools_flagged():
    """Side-effectful tools require approval; read-only and intra-tool-approval tools do not."""
    result = build_agent(config=_CONFIG_WITH_INTEGRATIONS)

    # These tools must require approval at the agent layer
    for name in ("start_background_task", "save_memory", "write_file", "edit_file"):
        assert result.tool_approvals[name] is True, (
            f"Tool '{name}' should require approval but doesn't"
        )

    # Shell approval is intra-tool (raises ApprovalRequired); agent layer must be False
    assert result.tool_approvals["run_shell_command"] is False, (
        "run_shell_command agent-layer approval must be False (approval handled inside tool)"
    )

    # Read-only tools must not require approval
    for name in ("check_capabilities", "read_file", "search_knowledge"):
        assert result.tool_approvals[name] is False, (
            f"Tool '{name}' should NOT require approval but does"
        )


def test_web_search_ask_requires_approval():
    """web_search requires approval when web_policy.search is 'ask'."""
    config = dataclasses.replace(
        CoConfig.from_settings(settings, cwd=Path.cwd()),
        web_policy=WebPolicy(search="ask", fetch="allow"),
    )
    result = build_agent(config=config)
    assert result.tool_approvals["web_search"] is True
    assert result.tool_approvals["web_fetch"] is False


def test_web_fetch_ask_requires_approval():
    """web_fetch requires approval when web_policy.fetch is 'ask'."""
    config = dataclasses.replace(
        CoConfig.from_settings(settings, cwd=Path.cwd()),
        web_policy=WebPolicy(search="allow", fetch="ask"),
    )
    result = build_agent(config=config)
    assert result.tool_approvals["web_search"] is False
    assert result.tool_approvals["web_fetch"] is True


def test_build_task_agent_registers_same_tools_as_main_agent():
    """build_task_agent() registers the same tools and approval flags as build_agent()."""
    config = _CONFIG_WITH_INTEGRATIONS
    registry = ModelRegistry.from_config(config)
    task_resolved = registry.get(ROLE_TASK, ResolvedModel(model=None, settings=None))

    main_result = build_agent(config=config)
    task_result = build_task_agent(config=config, resolved=task_resolved)

    assert set(task_result.tool_names) == set(main_result.tool_names)
    assert task_result.tool_approvals == main_result.tool_approvals


def test_build_agent_excludes_domain_tools_when_config_absent():
    """Domain tools absent from tool_names when config paths are not set."""
    result = build_agent(config=CoConfig())
    assert "list_notes" not in result.tool_names
    assert "list_emails" not in result.tool_names
    assert "search_drive_files" not in result.tool_names
    # Core tools always registered
    assert "check_capabilities" in result.tool_names
    assert "run_shell_command" in result.tool_names
    assert "web_search" in result.tool_names


def test_build_task_agent_excludes_domain_tools_when_config_absent():
    """build_task_agent() excludes domain tools when config paths are absent."""
    result = build_task_agent(
        config=CoConfig(),
        resolved=ResolvedModel(model=None, settings=None),
    )
    assert "list_notes" not in result.tool_names
    assert "list_emails" not in result.tool_names

