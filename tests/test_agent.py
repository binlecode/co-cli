"""Functional tests for agent factory — tool registration and approval wiring."""

import dataclasses
from pathlib import Path

import pytest

from co_cli.agent import build_agent, build_task_agent
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import WebPolicy, settings, ROLE_TASK
from co_cli.deps import CoConfig


# Canonical non-delegation tool inventory. Update when adding/removing/renaming tools.
# Domain tools (obsidian + google) are always included here; tests that assert on this
# set use fake config paths to force registration regardless of local settings.
EXPECTED_TOOLS_CORE = {
    # Side-effectful (requires_approval=True)
    "run_shell_command",
    "create_email_draft",
    "save_memory",
    "save_article",
    "update_memory",
    "append_memory",
    "write_file",
    "edit_file",
    # Read-only
    "search_memories",
    "list_memories",
    "read_article_detail",
    "search_knowledge",
    "list_notes",
    "read_note",
    "search_notes",
    "recall_article",
    "search_drive_files",
    "read_drive_file",
    "list_emails",
    "search_emails",
    "list_calendar_events",
    "search_calendar_events",
    "web_search",
    "web_fetch",
    # File system read-only
    "read_file",
    "list_directory",
    "find_in_files",
    # Session task tracking
    "todo_write",
    "todo_read",
    # Background task control
    "start_background_task",
    "check_task_status",
    "cancel_background_task",
    "list_background_tasks",
    # Capability introspection
    "check_capabilities",
}

# Delegation tools are registered iff their role model is configured
_ROLE_TO_TOOL = {
    "coding": "run_coder_subagent",
    "research": "run_research_subagent",
    "analysis": "run_analysis_subagent",
    "reasoning": "run_thinking_subagent",
}

EXPECTED_TOOLS = EXPECTED_TOOLS_CORE | {
    tool
    for role, tool in _ROLE_TO_TOOL.items()
    if settings.role_models.get(role)
}

EXPECTED_APPROVAL_TOOLS = {
    "create_email_draft",
    "save_memory",
    "save_article",
    "update_memory",
    "append_memory",
    "write_file",
    "edit_file",
    "start_background_task",
}

# Config with fake integration paths so domain tools are always registered in tests,
# regardless of whether the developer's local settings have these paths configured.
_CONFIG_WITH_INTEGRATIONS = dataclasses.replace(
    CoConfig.from_settings(settings, cwd=Path.cwd()),
    obsidian_vault_path=Path("/fake/vault"),
    google_credentials_path="/fake/creds.json",
)


def test_build_agent_registers_all_tools():
    """build_agent() registers exactly the expected tools with no duplicates."""
    result = build_agent(config=_CONFIG_WITH_INTEGRATIONS)
    assert len(result.tool_names) == len(set(result.tool_names)), "Duplicate tool registration"
    assert set(result.tool_names) == EXPECTED_TOOLS


def test_approval_tools_flagged():
    """Side-effectful tools require approval; read-only tools do not."""
    result = build_agent(config=_CONFIG_WITH_INTEGRATIONS)
    for name, requires_approval in result.tool_approvals.items():
        if name in EXPECTED_APPROVAL_TOOLS:
            assert requires_approval, (
                f"Tool '{name}' should require approval but doesn't"
            )
        else:
            assert not requires_approval, (
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


def test_build_agent_wires_system_prompt_as_base_instruction():
    """config.system_prompt becomes the static first instruction on the built agent.

    Catches accidental removal of instructions=config.system_prompt from the Agent
    constructor — without it the agent loses its role/rules/personality base silently.
    """
    config = dataclasses.replace(CoConfig(), system_prompt="co-cli-sentinel-system-prompt")
    agent = build_agent(config=config).agent
    assert agent._instructions[0] == "co-cli-sentinel-system-prompt"
