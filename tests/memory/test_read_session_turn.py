"""Tests for memory_read_session_turn — verbatim JSONL turn drill-down tool."""

import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS as _CONFIG

from co_cli.agent.core import build_agent
from co_cli.deps import CoDeps
from co_cli.tools.memory.read import (
    _SESSION_TURN_MAX_LINES,
    memory_read_session_turn,
)
from co_cli.tools.shell_backend import ShellBackend

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The fixture session uuid8 as documented in the task spec
_FIXTURE_UUID8 = "b8445d2b"
_FIXTURE_FILENAME = "2026-04-16-T235238Z-b8445d2b.jsonl"
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "session_with_tool_turns.jsonl"

# Cached agent — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=_CONFIG)
_MODEL = _AGENT.model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(sessions_dir: Path, *, tool_results_dir: Path) -> RunContext:
    """Return a real RunContext with real CoDeps for memory_read_session_turn tests."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=_CONFIG,
        sessions_dir=sessions_dir,
        tool_results_dir=tool_results_dir,
    )
    return RunContext(
        deps=deps,
        model=_MODEL,
        usage=RunUsage(),
        tool_name="memory_read_session_turn",
    )


def _install_fixture(sessions_dir: Path) -> None:
    """Copy the fixture JSONL into sessions_dir under its canonical filename."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    dest = sessions_dir / _FIXTURE_FILENAME
    shutil.copy2(_FIXTURE_PATH, dest)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_range_returns_lines_with_required_fields(tmp_path: Path) -> None:
    """A valid range returns a payload whose lines carry line, role, content_preview, tool_name."""
    sessions_dir = tmp_path / "sessions"
    _install_fixture(sessions_dir)
    ctx = _make_ctx(sessions_dir, tool_results_dir=tmp_path / "tool-results")

    result = await memory_read_session_turn(ctx, _FIXTURE_UUID8, 1, 6)

    assert result.metadata is not None
    assert result.metadata["session_id"] == _FIXTURE_UUID8
    lines: list[dict[str, Any]] = result.metadata["lines"]
    assert len(lines) <= 6
    assert len(lines) >= 1
    for entry in lines:
        assert "line" in entry
        assert "role" in entry
        assert "content_preview" in entry
        assert "tool_name" in entry
        assert isinstance(entry["line"], int)
        assert isinstance(entry["role"], str)
        assert isinstance(entry["content_preview"], str)


@pytest.mark.asyncio
async def test_over_200_lines_truncated_to_max(tmp_path: Path) -> None:
    """A range wider than 200 lines is truncated and reports truncated=True."""
    sessions_dir = tmp_path / "sessions"
    _install_fixture(sessions_dir)
    ctx = _make_ctx(sessions_dir, tool_results_dir=tmp_path / "tool-results")

    # Request a range wider than the 200-line ceiling
    result = await memory_read_session_turn(ctx, _FIXTURE_UUID8, 1, 300)

    assert result.metadata is not None
    assert result.metadata["truncated"] is True
    lines: list[dict[str, Any]] = result.metadata["lines"]
    # Must not exceed the ceiling regardless of how many messages the file has
    assert len(lines) <= _SESSION_TURN_MAX_LINES


@pytest.mark.asyncio
async def test_byte_ceiling_triggers_truncation(tmp_path: Path) -> None:
    """Content exceeding 16 KB byte budget is cut short with truncated=True.

    Writes a real session with 100 user-prompt lines each containing 204 chars.
    Accumulated previews: 100 * 200 bytes = 20 KB > _SESSION_TURN_MAX_BYTES (16 KB).
    """
    import json
    from datetime import UTC, datetime

    from co_cli.memory.session import session_filename

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    name = session_filename(ts, "badbytes0000000000000000000000000000")
    session_path = sessions_dir / name
    large_uuid8 = "badbytes"

    # 100 request lines, each with a 204-char user prompt.
    # extract_messages will emit one ExtractedMessage per line → 100 messages.
    jsonl_lines = [
        json.dumps(
            [
                {
                    "kind": "request",
                    "parts": [{"part_kind": "user-prompt", "content": f"Q{i:02d} " + "x" * 200}],
                }
            ]
        )
        for i in range(100)
    ]
    session_path.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

    ctx = _make_ctx(sessions_dir, tool_results_dir=tmp_path / "tool-results")

    result = await memory_read_session_turn(ctx, large_uuid8, 1, 100)

    assert result.metadata is not None
    assert result.metadata["truncated"] is True
    # Byte ceiling cuts off before all 100 messages are accumulated
    assert len(result.metadata["lines"]) < 100


@pytest.mark.asyncio
async def test_unknown_session_id_returns_error_display(tmp_path: Path) -> None:
    """An unknown session_id returns a ToolReturn whose display contains an error message."""
    sessions_dir = tmp_path / "sessions"
    _install_fixture(sessions_dir)
    ctx = _make_ctx(sessions_dir, tool_results_dir=tmp_path / "tool-results")

    result = await memory_read_session_turn(ctx, "deadbeef", 1, 5)

    assert result is not None
    display = result.return_value
    assert isinstance(display, str)
    assert (
        "deadbeef" in display or "unknown" in display.lower() or "no matching" in display.lower()
    )


@pytest.mark.asyncio
async def test_invalid_start_line_returns_validation_error(tmp_path: Path) -> None:
    """start_line < 1 returns a ToolReturn whose display contains a validation error."""
    sessions_dir = tmp_path / "sessions"
    _install_fixture(sessions_dir)
    ctx = _make_ctx(sessions_dir, tool_results_dir=tmp_path / "tool-results")

    result = await memory_read_session_turn(ctx, _FIXTURE_UUID8, 0, 5)

    assert result is not None
    display = result.return_value
    assert isinstance(display, str)
    assert "validation" in display.lower() or "error" in display.lower()


@pytest.mark.asyncio
async def test_end_before_start_returns_validation_error(tmp_path: Path) -> None:
    """end_line < start_line returns a ToolReturn whose display contains a validation error."""
    sessions_dir = tmp_path / "sessions"
    _install_fixture(sessions_dir)
    ctx = _make_ctx(sessions_dir, tool_results_dir=tmp_path / "tool-results")

    result = await memory_read_session_turn(ctx, _FIXTURE_UUID8, 5, 3)

    assert result is not None
    display = result.return_value
    assert isinstance(display, str)
    assert "validation" in display.lower() or "error" in display.lower()
