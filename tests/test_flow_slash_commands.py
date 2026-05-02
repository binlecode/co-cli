"""Tests for slash command handlers."""

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._settings import SETTINGS_NO_MCP

from co_cli.commands.clear import _cmd_clear
from co_cli.commands.types import CommandContext
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.display.core import TerminalFrontend
from co_cli.tools.shell_backend import ShellBackend


@pytest.mark.asyncio
async def test_cmd_clear_wipes_history_and_resets_compaction_state() -> None:
    """/clear must return empty history and reset all compaction runtime fields."""
    runtime = CoRuntimeState()
    runtime.previous_compaction_summary = "old summary"
    runtime.post_compaction_token_estimate = 42_000
    runtime.message_count_at_last_compaction = 10

    deps = CoDeps(
        shell=ShellBackend(), config=SETTINGS_NO_MCP, session=CoSessionState(), runtime=runtime
    )
    history = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(parts=[TextPart(content="hi")]),
    ]
    ctx = CommandContext(
        message_history=history, deps=deps, agent=None, frontend=TerminalFrontend()
    )
    result = await _cmd_clear(ctx, "")

    assert result == []
    assert deps.runtime.previous_compaction_summary is None
    assert deps.runtime.post_compaction_token_estimate is None
    assert deps.runtime.message_count_at_last_compaction is None
