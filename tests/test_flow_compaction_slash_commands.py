"""Tests for slash command handlers — /clear and /compact."""

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._settings import SETTINGS_NO_MCP

from co_cli.commands.clear import _cmd_clear
from co_cli.commands.types import CommandContext
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.display.core import TerminalFrontend
from co_cli.tools.shell_backend import ShellBackend


def _req(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _resp(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


@pytest.mark.asyncio
async def test_cmd_clear_wipes_history_and_resets_compaction_state() -> None:
    """/clear must return empty history and reset all compaction runtime fields."""
    runtime = CoRuntimeState()
    runtime.post_compaction_token_estimate = 42_000
    runtime.message_count_at_last_compaction = 10

    deps = CoDeps(
        shell=ShellBackend(), config=SETTINGS_NO_MCP, session=CoSessionState(), runtime=runtime
    )
    history = [_req("hello"), _resp("hi")]
    ctx = CommandContext(
        message_history=history, deps=deps, agent=None, frontend=TerminalFrontend()
    )
    result = await _cmd_clear(ctx, "")

    assert result == []
    assert deps.runtime.post_compaction_token_estimate is None
    assert deps.runtime.message_count_at_last_compaction is None
