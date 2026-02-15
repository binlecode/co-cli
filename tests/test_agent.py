"""Functional tests for agent factory — tool registration and approval wiring."""

from co_cli.agent import get_agent
from co_cli.config import WebPolicy


# Canonical tool inventory. Update this set when adding/removing/renaming tools.
EXPECTED_TOOLS = {
    # Side-effectful (requires_approval=True)
    "run_shell_command",
    "create_email_draft",
    "save_memory",
    # Read-only
    "recall_memory",
    "list_memories",
    "search_notes",
    "list_notes",
    "read_note",
    "search_drive_files",
    "read_drive_file",
    "list_emails",
    "search_emails",
    "list_calendar_events",
    "search_calendar_events",
    "web_search",
    "web_fetch",
}

EXPECTED_APPROVAL_TOOLS = {
    "run_shell_command",
    "create_email_draft",
    "save_memory",
}


def test_get_agent_registers_all_tools():
    """get_agent() registers exactly the expected tools with no duplicates."""
    _agent, _model_settings, tool_names = get_agent()
    assert len(tool_names) == len(set(tool_names)), "Duplicate tool registration"
    assert set(tool_names) == EXPECTED_TOOLS


def test_approval_tools_flagged():
    """Side-effectful tools require approval; read-only tools do not."""
    agent, _model_settings, _tool_names = get_agent()
    for tool_def in agent._function_toolset.tools.values():
        name = tool_def.name
        if name in EXPECTED_APPROVAL_TOOLS:
            assert tool_def.requires_approval, (
                f"Tool '{name}' should require approval but doesn't"
            )
        else:
            assert not tool_def.requires_approval, (
                f"Tool '{name}' should NOT require approval but does"
            )


def test_history_processors_attached():
    """Agent has all four history processors for context governance (§16)."""
    agent, _model_settings, _tool_names = get_agent()
    processor_names = [p.__name__ for p in agent.history_processors]
    assert "inject_opening_context" in processor_names
    assert "truncate_tool_returns" in processor_names
    assert "detect_safety_issues" in processor_names
    assert "truncate_history_window" in processor_names


def test_web_search_ask_requires_approval():
    """web_search requires approval when web_policy.search is 'ask'."""
    agent, _model_settings, _tool_names = get_agent(
        web_policy=WebPolicy(search="ask", fetch="allow"),
    )
    tool_map = {t.name: t for t in agent._function_toolset.tools.values()}
    assert tool_map["web_search"].requires_approval is True
    assert tool_map["web_fetch"].requires_approval is False


def test_web_fetch_ask_requires_approval():
    """web_fetch requires approval when web_policy.fetch is 'ask'."""
    agent, _model_settings, _tool_names = get_agent(
        web_policy=WebPolicy(search="allow", fetch="ask"),
    )
    tool_map = {t.name: t for t in agent._function_toolset.tools.values()}
    assert tool_map["web_search"].requires_approval is False
    assert tool_map["web_fetch"].requires_approval is True
