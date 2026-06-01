"""Structural tests for build_task_agent — tool resolution + validation.

Verifies that spec.tool_names resolves correctly against TOOL_REGISTRY_BY_NAME,
fails loud on unknown names, drops integration tools when credentials are
absent, and registers all resolved tools with requires_approval=False.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from tests._settings import SETTINGS_NO_MCP, make_settings

from co_cli.agent.build import build_task_agent
from co_cli.agent.spec import TaskAgentSpec
from co_cli.agent.toolset import _build_native_toolset
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend

_, _TOOL_INDEX = _build_native_toolset(SETTINGS_NO_MCP)


class _Output(BaseModel):
    result: str


def _instructions(_deps: CoDeps) -> str:
    return "test"


def _deps(settings) -> CoDeps:
    return CoDeps(shell=ShellBackend(), config=settings)


def test_resolves_named_tool_to_single_registration() -> None:
    spec = TaskAgentSpec(
        name="test_agent",
        instructions=_instructions,
        tool_names=("web_search",),
        output_type=_Output,
        default_budget=1,
        error_message="",
    )
    agent = build_task_agent(spec, _deps(SETTINGS_NO_MCP), model=None)
    tool_names = list(agent._function_toolset.tools.keys())
    assert tool_names == ["web_search"]


def test_unknown_tool_name_raises() -> None:
    spec = TaskAgentSpec(
        name="test_agent",
        instructions=_instructions,
        tool_names=("nonexistent_tool",),
        output_type=_Output,
        default_budget=1,
        error_message="",
    )
    with pytest.raises(ValueError, match="test_agent: unknown tool 'nonexistent_tool'"):
        build_task_agent(spec, _deps(SETTINGS_NO_MCP), model=None)


def test_google_tool_drops_out_without_credentials() -> None:
    settings = make_settings(mcp_servers={}, google_credentials_path=None)
    spec = TaskAgentSpec(
        name="test_agent",
        instructions=_instructions,
        tool_names=("google_drive_search", "web_search"),
        output_type=_Output,
        default_budget=1,
        error_message="",
    )
    agent = build_task_agent(spec, _deps(settings), model=None)
    tool_names = list(agent._function_toolset.tools.keys())
    assert "google_drive_search" not in tool_names
    assert "web_search" in tool_names


def test_all_tools_registered_with_no_approval() -> None:
    spec = TaskAgentSpec(
        name="test_agent",
        instructions=_instructions,
        tool_names=("web_search", "web_fetch"),
        output_type=_Output,
        default_budget=1,
        error_message="",
    )
    agent = build_task_agent(spec, _deps(SETTINGS_NO_MCP), model=None)
    for tool in agent._function_toolset.tools.values():
        assert tool.requires_approval is False, (
            f"{tool.name} has requires_approval={tool.requires_approval}"
        )
