"""Behavioral tests for build_task_agent error handling."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.build import build_task_agent
from co_cli.agent.spec import TaskAgentSpec
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend


class _Output(BaseModel):
    result: str


def _instructions(_deps: CoDeps) -> str:
    return "test"


def _deps(settings) -> CoDeps:
    return CoDeps(shell=ShellBackend(), config=settings)


def test_unknown_tool_name_raises() -> None:
    spec = TaskAgentSpec(
        name="test_agent",
        instructions=_instructions,
        tool_names=("nonexistent_tool",),
        output_type=_Output,
        default_budget=1,
    )
    with pytest.raises(ValueError, match="test_agent: unknown tool 'nonexistent_tool'"):
        build_task_agent(spec, _deps(SETTINGS_NO_MCP), model=None)
