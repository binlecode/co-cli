"""Tests for session_view — verbatim session turn reader by session_id + line range."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.session.view import session_view
from co_cli.tools.shell_backend import ShellBackend

_SESSION_TIMESTAMP = "2026-01-01-T120000Z"


def _make_deps(tmp_path: Path, sessions_dir: Path | None = None) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        sessions_dir=sessions_dir or tmp_path / "sessions",
    )


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _make_session_file(sessions_dir: Path, uuid8: str, lines: list[str]) -> Path:
    """Create a JSONL session file. Each string in lines becomes one user-prompt message."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{_SESSION_TIMESTAMP}-{uuid8}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for line_content in lines:
            record = [{"parts": [{"part_kind": "user-prompt", "content": line_content}]}]
            f.write(json.dumps(record) + "\n")
    return path


@pytest.mark.asyncio
async def test_session_view_targeted_glob_locates_correct_file(tmp_path: Path) -> None:
    """Targeted glob must locate the session matching the given session_id.

    Failure mode: glob `f'*-{session_id}.jsonl'` wrong → every session lookup returns
    'Unknown session_id', breaking agents that try to read past session turns.
    """
    sessions_dir = tmp_path / "sessions"
    id_a = "aaaaaaaa"
    id_b = "bbbbbbbb"
    _make_session_file(sessions_dir, id_a, ["content for session A"])
    _make_session_file(sessions_dir, id_b, ["content for session B"])

    deps = _make_deps(tmp_path, sessions_dir=sessions_dir)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_view(ctx, session_id=id_a, start_line=1, end_line=5)

    assert result.metadata is None or result.metadata.get("error") is not True, (
        f"Targeted glob failed to locate session '{id_a}': {result.return_value!r}"
    )
    assert result.metadata.get("session_id") == id_a, (
        f"Expected session_id={id_a!r} in metadata, got {result.metadata!r}"
    )


@pytest.mark.asyncio
async def test_session_view_unknown_id_returns_tool_error(tmp_path: Path) -> None:
    """A session_id with no matching file must return a tool_error.

    Failure mode: returning a generic output instead of tool_error causes the agent
    to silently proceed with missing session data rather than surfacing the miss.
    """
    sessions_dir = tmp_path / "sessions"
    _make_session_file(sessions_dir, "aaaaaaaa", ["some content"])

    deps = _make_deps(tmp_path, sessions_dir=sessions_dir)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_view(ctx, session_id="cccccccc", start_line=1, end_line=5)

    assert result.metadata is not None, "tool_error must populate metadata"
    assert result.metadata.get("error") is True, (
        f"Unknown session_id must return tool_error: {result.return_value!r}"
    )
    assert "cccccccc" in result.return_value or "Unknown session_id" in result.return_value, (
        f"error must mention the missing id: {result.return_value!r}"
    )


@pytest.mark.asyncio
async def test_session_view_line_range_returns_correct_turns(tmp_path: Path) -> None:
    """session_view must return only the turns within the requested line range.

    Failure mode: returning all turns instead of the range slice causes agents to
    receive more context than requested, wasting tokens and obscuring the hit location.
    """
    sessions_dir = tmp_path / "sessions"
    uuid8 = "llrange1"
    # Write 5 lines
    _make_session_file(
        sessions_dir,
        uuid8,
        [
            "turn one content",
            "turn two content",
            "turn three content",
            "turn four content",
            "turn five content",
        ],
    )

    deps = _make_deps(tmp_path, sessions_dir=sessions_dir)
    ctx = _ctx(deps)

    # Request only lines 2-3 (1-indexed)
    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_view(ctx, session_id=uuid8, start_line=2, end_line=3)

    assert result.metadata is None or result.metadata.get("error") is not True, (
        f"session_view must succeed for valid range: {result.return_value!r}"
    )
    output_lines = result.metadata.get("lines", [])
    line_numbers = [entry["line"] for entry in output_lines]
    assert line_numbers == [2, 3], (
        f"only the requested lines 2-3 must be returned, got {line_numbers}"
    )


@pytest.mark.asyncio
async def test_session_view_verbatim_content_preserved(tmp_path: Path) -> None:
    """session_view must return the verbatim turn content, not a summary.

    Failure mode: content truncated or paraphrased — agent loses the exact wording
    needed for extracting commands, file paths, or tool arguments from the hit.
    """
    sessions_dir = tmp_path / "sessions"
    uuid8 = "verbatm1"
    unique_text = "verbatim_exact_content_marker_zq9x_unique"
    _make_session_file(sessions_dir, uuid8, [unique_text])

    deps = _make_deps(tmp_path, sessions_dir=sessions_dir)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_view(ctx, session_id=uuid8, start_line=1, end_line=1)

    output_lines = result.metadata.get("lines", [])
    assert output_lines, "expected at least one line in output"
    content_preview = output_lines[0].get("content_preview", "")
    assert unique_text in content_preview, (
        f"verbatim content must appear in output: {content_preview!r}"
    )


@pytest.mark.asyncio
async def test_session_view_shows_tool_call_arguments(tmp_path: Path) -> None:
    """session_view surfaces a tool-call's arguments, not just the tool name.

    Failure mode: the agent reads back a past tool call and sees only the tool name,
    losing the file path / command / saved content it needs to act on.
    """
    sessions_dir = tmp_path / "sessions"
    uuid8 = "toolargs"
    args = json.dumps({"action": "create", "content": "User's deploy ID is DEPLOY_77."})
    record = [
        {"parts": [{"part_kind": "tool-call", "tool_name": "knowledge_manage", "args": args}]}
    ]
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{_SESSION_TIMESTAMP}-{uuid8}.jsonl").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )

    deps = _make_deps(tmp_path, sessions_dir=sessions_dir)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_view(ctx, session_id=uuid8, start_line=1, end_line=1)

    output_lines = result.metadata.get("lines", [])
    assert output_lines, "expected the tool-call turn in output"
    content_preview = output_lines[0].get("content_preview", "")
    assert "DEPLOY_77" in content_preview, (
        f"tool-call args must appear in the view: {content_preview!r}"
    )
