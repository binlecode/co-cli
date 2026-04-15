"""Tests for _finalize_turn post-turn lifecycle in main.py."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from tests._frontend import SilentFrontend
from tests._settings import make_settings

from co_cli.context.orchestrate import TurnResult
from co_cli.context.session import new_session_path
from co_cli.deps import CoDeps, CoRuntimeState
from co_cli.main import _finalize_turn
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(tmp_path: Path, *, history_compaction_applied: bool = False) -> CoDeps:
    config = make_settings()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        runtime=CoRuntimeState(history_compaction_applied=history_compaction_applied),
        sessions_dir=sessions_dir,
    )
    deps.session.session_path = new_session_path(sessions_dir)
    return deps


@pytest.mark.asyncio
async def test_finalize_compaction_resets_cursor(tmp_path: Path) -> None:
    """When history_compaction_applied=True, last_extracted_message_idx resets to len(next_history)."""
    # ToolReturnPart starting with the read-tool prefix is skipped by _build_window,
    # so no LLM extraction runs — cursor reset is purely synchronous.
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    content=f"1\u2192 line {idx}",
                    tool_call_id=f"c{idx}",
                )
            ]
        )
        for idx in range(5)
    ]
    deps = _make_deps(tmp_path, history_compaction_applied=True)
    # Stale cursor — simulates pre-compaction drift
    deps.session.last_extracted_message_idx = 100

    turn_result = TurnResult(
        interrupted=False,
        outcome="continue",
        messages=messages,
    )
    async with asyncio.timeout(10):
        await _finalize_turn(turn_result, [], deps, SilentFrontend())

    # Cursor must be reset to len(messages) = 5, not the stale 100
    assert deps.session.last_extracted_message_idx == len(messages)


@pytest.mark.asyncio
async def test_finalize_normal_turn_fires_extraction(tmp_path: Path) -> None:
    """When history_compaction_applied=False and cadence fires, extraction updates the cursor."""
    config = make_settings()
    n = config.memory.extract_every_n_turns

    # ToolReturnPart starting with the read-tool prefix is skipped by _build_window,
    # so the empty-window fast-path updates the cursor without making an LLM call.
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    content=f"1\u2192 line {idx}",
                    tool_call_id=f"c{idx}",
                )
            ]
        )
        for idx in range(3)
    ]
    deps = _make_deps(tmp_path, history_compaction_applied=False)
    deps.session.last_extracted_message_idx = 0
    if n > 0:
        # Position counter so cadence fires on this turn
        deps.session.last_extracted_turn_idx = n - 1

    turn_result = TurnResult(
        interrupted=False,
        outcome="continue",
        messages=messages,
    )
    async with asyncio.timeout(10):
        await _finalize_turn(turn_result, [], deps, SilentFrontend())
        # Yield to let the fire-and-forget background task advance to cursor update
        await asyncio.sleep(0)

    if n == 0:
        # Extraction disabled: cursor must stay at its initial value
        assert deps.session.last_extracted_message_idx == 0
    else:
        # Cadence fired: empty-window fast-path advances cursor to end of delta
        assert deps.session.last_extracted_message_idx == len(messages)
