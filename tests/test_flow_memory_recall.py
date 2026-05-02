"""Tests for the memory recall tools — session read and grep_recall paths."""

import asyncio

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.memory.read import memory_read_session_turn
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(tmp_path, sessions_dir=None):
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        sessions_dir=sessions_dir or tmp_path / "sessions",
    )


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


@pytest.mark.asyncio
async def test_memory_read_session_turn_targeted_glob_locates_correct_file(
    tmp_path,
) -> None:
    """Targeted glob must locate the session matching the given session_id and not confuse it.

    Failure mode: glob `f'*-{session_id}.jsonl'` wrong → every session lookup returns
    'Unknown session_id', breaking agents that try to read past session turns.

    Creates two JSONL session files with distinct 8-char IDs. Calls
    memory_read_session_turn with ID_A and verifies:
    - The call succeeds (no "Unknown session_id" error).
    - Metadata carries the correct session_id back to the caller.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    # Two files with distinct session IDs in the suffix
    id_a = "aaaaaaaa"
    id_b = "bbbbbbbb"
    (sessions_dir / f"2026-01-01-T120000Z-{id_a}.jsonl").touch()
    (sessions_dir / f"2026-01-01-T120000Z-{id_b}.jsonl").touch()

    deps = _make_deps(tmp_path, sessions_dir=sessions_dir)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await memory_read_session_turn(ctx, id_a, 1, 5)

    assert "Unknown session_id" not in result.return_value, (
        f"Targeted glob failed to locate session '{id_a}': {result.return_value!r}"
    )
    assert result.metadata.get("session_id") == id_a, (
        f"Expected session_id={id_a!r} in metadata, got {result.metadata!r}"
    )


@pytest.mark.asyncio
async def test_memory_read_session_turn_unknown_id_returns_error(tmp_path) -> None:
    """A session_id with no matching file must return a 'Unknown session_id' error.

    Failure mode: glob matches wrong file (e.g. prefix instead of suffix) → agent
    reads wrong session data silently.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "2026-01-01-T120000Z-aaaaaaaa.jsonl").touch()

    deps = _make_deps(tmp_path, sessions_dir=sessions_dir)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await memory_read_session_turn(ctx, "cccccccc", 1, 5)

    assert "Unknown session_id" in result.return_value, (
        f"Expected 'Unknown session_id' error for missing ID, got: {result.return_value!r}"
    )
