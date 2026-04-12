"""Functional tests for transcript path-based API."""

from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from co_cli.context.transcript import (
    COMPACT_BOUNDARY_MARKER,
    append_messages,
    load_transcript,
    write_compact_boundary,
)


def _make_user_message(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _make_assistant_message(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def test_append_messages_creates_file(tmp_path: Path) -> None:
    """append_messages creates the JSONL file on first call."""
    path = tmp_path / "sessions" / "2026-04-11-T120000Z-550e8400.jsonl"
    assert not path.exists()
    append_messages(path, [_make_user_message("hello")])
    assert path.exists()


def test_append_messages_roundtrip(tmp_path: Path) -> None:
    """Messages appended then loaded via load_transcript are equal in count and type."""
    path = tmp_path / "sessions" / "2026-04-11-T120000Z-550e8400.jsonl"
    msgs = [
        _make_user_message("hello"),
        _make_assistant_message("world"),
    ]
    append_messages(path, msgs)
    loaded = load_transcript(path)
    assert len(loaded) == 2


def test_append_messages_empty_noop(tmp_path: Path) -> None:
    """append_messages with empty list does not create the file."""
    path = tmp_path / "sessions" / "2026-04-11-T120000Z-550e8400.jsonl"
    append_messages(path, [])
    assert not path.exists()


def test_load_transcript_missing_returns_empty(tmp_path: Path) -> None:
    """load_transcript returns [] when the file does not exist."""
    path = tmp_path / "sessions" / "2026-04-11-T120000Z-550e8400.jsonl"
    assert load_transcript(path) == []


def test_write_compact_boundary_appends_marker(tmp_path: Path) -> None:
    """write_compact_boundary appends the compact boundary marker line."""
    path = tmp_path / "sessions" / "2026-04-11-T120000Z-550e8400.jsonl"
    append_messages(path, [_make_user_message("before compaction")])
    write_compact_boundary(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == COMPACT_BOUNDARY_MARKER


def test_load_transcript_skips_pre_boundary_when_large(tmp_path: Path) -> None:
    """For files above threshold, messages before the last boundary are skipped."""
    from co_cli.context.transcript import SKIP_PRECOMPACT_THRESHOLD

    path = tmp_path / "sessions" / "2026-04-11-T120000Z-550e8400.jsonl"
    # Write messages before boundary
    pre_msgs = [_make_user_message("before")]
    append_messages(path, pre_msgs)
    write_compact_boundary(path)
    # Write messages after boundary
    post_msgs = [_make_user_message("after")]
    append_messages(path, post_msgs)

    # Inflate the file to exceed SKIP_PRECOMPACT_THRESHOLD
    with path.open("a", encoding="utf-8") as f:
        f.write(" " * (SKIP_PRECOMPACT_THRESHOLD + 1))

    loaded = load_transcript(path)
    # Only post-boundary messages should be returned
    assert len(loaded) == len(post_msgs)
