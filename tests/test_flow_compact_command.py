"""Tests for the /compact slash-command handler."""

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._settings import SETTINGS_NO_MCP

from co_cli.commands.compact import _cmd_compact
from co_cli.commands.types import CommandContext
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.shell_backend import ShellBackend


def _req(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _resp(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


@pytest.mark.asyncio
async def test_compact_clears_previous_summary_on_summarizer_failure() -> None:
    """/compact must clear previous_compaction_summary when the summarizer falls back to a static marker.

    Failure mode: stale summary survives into the next proactive compaction's iterative
    branch, causing the model to receive a PREVIOUS SUMMARY referencing history that no
    longer exists after the full-history replacement.
    """
    runtime = CoRuntimeState()
    runtime.previous_compaction_summary = "OLD SUMMARY — no longer valid after /compact"

    deps = CoDeps(
        shell=ShellBackend(),
        model=None,  # no-model path → gate returns (False, False) → static marker, summary=None
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=runtime,
    )
    history = [
        _req("tell me about the project"),
        _resp("here is a summary of the project"),
        _req("what should we do next?"),
        _resp("here are the next steps"),
    ]
    ctx = CommandContext(message_history=history, deps=deps, agent=None)

    await _cmd_compact(ctx, "")

    assert deps.runtime.previous_compaction_summary is None, (
        "previous_compaction_summary must be cleared when /compact uses a static marker"
    )
