"""Functional tests for agent factory — tool registration and approval wiring."""

import asyncio
import dataclasses
from datetime import date
from pathlib import Path

from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelResponse, TextPart, ModelMessage

from co_cli.agent import build_agent
from co_cli.config import WebPolicy, settings
from co_cli.deps import CoConfig


# Canonical non-delegation tool inventory. Update when adding/removing/renaming tools.
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
    "coding": "delegate_coder",
    "research": "delegate_research",
    "analysis": "delegate_analysis",
    "reasoning": "delegate_think",
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


def test_build_agent_registers_all_tools():
    """build_agent() registers exactly the expected tools with no duplicates."""
    _agent, tool_names, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
    assert len(tool_names) == len(set(tool_names)), "Duplicate tool registration"
    assert set(tool_names) == EXPECTED_TOOLS


def test_approval_tools_flagged():
    """Side-effectful tools require approval; read-only tools do not."""
    _agent, _tool_names, tool_approval = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
    for name, requires_approval in tool_approval.items():
        if name in EXPECTED_APPROVAL_TOOLS:
            assert requires_approval, (
                f"Tool '{name}' should require approval but doesn't"
            )
        else:
            assert not requires_approval, (
                f"Tool '{name}' should NOT require approval but does"
            )


def test_history_processors_attached():
    """Agent has all four history processors for context governance (§16)."""
    agent, _tool_names, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
    processor_names = [p.__name__ for p in agent.history_processors]
    assert "inject_opening_context" in processor_names
    assert "truncate_tool_returns" in processor_names
    assert "detect_safety_issues" in processor_names
    assert "truncate_history_window" in processor_names


def test_web_search_ask_requires_approval():
    """web_search requires approval when web_policy.search is 'ask'."""
    config = dataclasses.replace(
        CoConfig.from_settings(settings, cwd=Path.cwd()),
        web_policy=WebPolicy(search="ask", fetch="allow"),
    )
    _agent, _tool_names, tool_approval = build_agent(config=config)
    assert tool_approval["web_search"] is True
    assert tool_approval["web_fetch"] is False


def test_web_fetch_ask_requires_approval():
    """web_fetch requires approval when web_policy.fetch is 'ask'."""
    config = dataclasses.replace(
        CoConfig.from_settings(settings, cwd=Path.cwd()),
        web_policy=WebPolicy(search="allow", fetch="ask"),
    )
    _agent, _tool_names, tool_approval = build_agent(config=config)
    assert tool_approval["web_search"] is False
    assert tool_approval["web_fetch"] is True


def test_instructions_reevaluated_on_turn2():
    """@agent.instructions are freshly evaluated on every turn, not accumulated in history.

    Verifies that a turn-2 run (with non-empty message_history) receives
    current-date instructions from @agent.instructions, confirming the
    channel is not stale.
    """
    from co_cli.deps import CoDeps, CoServices
    from co_cli.tools._shell_backend import ShellBackend

    captured: list[str | None] = []

    def capture_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.append(info.instructions)
        return ModelResponse(parts=[TextPart(content="ok")])

    agent, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
    agent._model = FunctionModel(capture_model)
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    # Turn 1
    r1 = asyncio.run(agent.run("turn 1", deps=deps))

    # Turn 2 — simulated continuation with history from turn 1
    history = list(r1.all_messages())
    asyncio.run(agent.run("turn 2", deps=deps, message_history=history))

    assert len(captured) == 2, "Expected model called exactly twice"
    today = date.today().isoformat()
    for i, instructions in enumerate(captured):
        assert instructions is not None, f"Turn {i + 1}: instructions must not be None"
        assert today in instructions, (
            f"Turn {i + 1}: expected current date '{today}' in instructions, got: {instructions!r}"
        )
