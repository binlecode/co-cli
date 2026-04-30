"""Tests for session_chunker — flatten, chunk, and determinism."""

from __future__ import annotations

import itertools
import json
import linecache
from pathlib import Path

from co_cli.memory.indexer import ExtractedMessage
from co_cli.memory.session_chunker import (
    SESSION_CHUNK_TOKENS,
    SESSION_LINE_WRAP_CHARS,
    chunk_flattened,
    chunk_session,
    flatten_session,
)

FIXTURE = Path(__file__).parent / "fixtures" / "session_with_tool_turns.jsonl"


def _make_msg(
    role: str,
    content: str,
    line_index: int = 0,
    tool_name: str | None = None,
) -> ExtractedMessage:
    return ExtractedMessage(
        line_index=line_index,
        part_index=0,
        role=role,
        content=content,
        timestamp=None,
        tool_name=tool_name,
    )


# ---------------------------------------------------------------------------
# flatten_session — prefix rules
# ---------------------------------------------------------------------------


def test_flatten_user_prefix() -> None:
    msgs = [_make_msg("user", "hello world", line_index=0)]
    lines, lmap = flatten_session(msgs)
    assert len(lines) == 1
    assert lines[0] == "User: hello world"
    assert lmap[0] == 1  # 1-indexed


def test_flatten_assistant_prefix() -> None:
    msgs = [_make_msg("assistant", "here is my answer", line_index=2)]
    lines, lmap = flatten_session(msgs)
    assert len(lines) == 1
    assert lines[0].startswith("Assistant: ")
    assert lmap[0] == 3


def test_flatten_tool_call_prefix() -> None:
    msgs = [_make_msg("tool-call", "bash", line_index=4, tool_name="bash")]
    lines, lmap = flatten_session(msgs)
    assert len(lines) == 1
    assert lines[0] == "Tool[bash](call)"
    assert lmap[0] == 5


def test_flatten_tool_return_prefix() -> None:
    msgs = [_make_msg("tool-return", "output text here", line_index=6, tool_name="bash")]
    lines, lmap = flatten_session(msgs)
    assert len(lines) == 1
    assert lines[0] == "Tool[bash](return): output text here"
    assert lmap[0] == 7


# ---------------------------------------------------------------------------
# flatten_session — line_map is monotone non-decreasing
# ---------------------------------------------------------------------------


def test_line_map_monotone() -> None:
    msgs = [
        _make_msg("user", "first question", line_index=0),
        _make_msg("assistant", "first answer", line_index=1),
        _make_msg("tool-call", "bash", line_index=2, tool_name="bash"),
        _make_msg("tool-return", "some output data", line_index=3, tool_name="bash"),
    ]
    lines, lmap = flatten_session(msgs)
    assert len(lmap) == len(lines)
    for a, b in itertools.pairwise(lmap):
        assert a <= b, f"line_map not monotone: {lmap}"


# ---------------------------------------------------------------------------
# flatten_session — long content wraps, preserves prefix, replicates line_map
# ---------------------------------------------------------------------------


def test_long_content_wraps_multiple_lines() -> None:
    long_content = "x" * (SESSION_LINE_WRAP_CHARS + 50)
    msgs = [_make_msg("user", long_content, line_index=5)]
    lines, lmap = flatten_session(msgs)
    assert len(lines) > 1, "Expected wrapping into multiple lines"
    for line in lines:
        assert line.startswith("User: ")
    # All slices map to the same JSONL line
    assert all(v == 6 for v in lmap)


def test_long_tool_return_wraps() -> None:
    long_content = "A" * (SESSION_LINE_WRAP_CHARS * 3)
    msgs = [_make_msg("tool-return", long_content, line_index=3, tool_name="search")]
    lines, lmap = flatten_session(msgs)
    assert len(lines) > 1
    for line in lines:
        assert line.startswith("Tool[search](return): ")
    assert all(v == 4 for v in lmap)


# ---------------------------------------------------------------------------
# flatten_session — sanitization
# ---------------------------------------------------------------------------


def test_heartbeat_assistant_dropped() -> None:
    """Short assistant message with no following tool-call is dropped."""
    msgs = [
        _make_msg("user", "hello", line_index=0),
        _make_msg("assistant", "ok", line_index=1),  # heartbeat: len=2, next is user
        _make_msg("user", "follow up", line_index=2),
    ]
    lines, _ = flatten_session(msgs)
    assistant_lines = [l for l in lines if l.startswith("Assistant:")]
    assert assistant_lines == [], f"Heartbeat should be dropped, got: {assistant_lines}"


def test_heartbeat_assistant_kept_before_tool_call() -> None:
    """Short assistant message immediately before a tool-call is NOT a heartbeat."""
    msgs = [
        _make_msg("assistant", "ok", line_index=0),
        _make_msg("tool-call", "bash", line_index=1, tool_name="bash"),
    ]
    lines, _ = flatten_session(msgs)
    assert any(l.startswith("Assistant:") for l in lines)


def test_short_tool_return_dropped() -> None:
    """Tool-return content < 10 chars is dropped."""
    msgs = [
        _make_msg("tool-return", "ok", line_index=0, tool_name="tool"),
        _make_msg("tool-return", "a long enough result here", line_index=1, tool_name="tool"),
    ]
    lines, _ = flatten_session(msgs)
    assert len(lines) == 1
    assert lines[0].startswith("Tool[tool](return):")


# ---------------------------------------------------------------------------
# chunk_flattened — size and overlap assertions
# ---------------------------------------------------------------------------


def _make_long_flat_lines(n_lines: int, chars_per_line: int = 200) -> tuple[list[str], list[int]]:
    flat = [f"User: {'x' * (chars_per_line - 6)}" for _ in range(n_lines)]
    lmap = list(range(1, n_lines + 1))
    return flat, lmap


def test_chunk_sizes_within_bounds() -> None:
    """Non-last chunks should have len(text)//4 in [320, 480]."""
    flat, lmap = _make_long_flat_lines(n_lines=60, chars_per_line=120)
    chunks = chunk_flattened(flat, lmap)
    assert len(chunks) > 1, "Expected multiple chunks"
    for chunk in chunks[:-1]:
        token_est = len(chunk.text) // 4
        assert 320 <= token_est <= 480, (
            f"Chunk token estimate {token_est} outside [320, 480]: {chunk.text[:60]!r}"
        )


def test_consecutive_chunks_overlap() -> None:
    """Consecutive chunks share non-empty content."""
    flat, lmap = _make_long_flat_lines(n_lines=40, chars_per_line=150)
    chunks = chunk_flattened(flat, lmap)
    assert len(chunks) > 1
    for i in range(len(chunks) - 1):
        lines_a = set(chunks[i].text.splitlines())
        lines_b = set(chunks[i + 1].text.splitlines())
        overlap = lines_a & lines_b
        assert overlap, f"No overlap between chunk {i} and {i + 1}"


def test_chunk_bounds_one_indexed() -> None:
    """Every chunk has start_jsonl_line >= 1 and end >= start."""
    flat, lmap = _make_long_flat_lines(n_lines=30, chars_per_line=100)
    chunks = chunk_flattened(flat, lmap)
    for chunk in chunks:
        assert chunk.start_jsonl_line >= 1
        assert chunk.end_jsonl_line >= chunk.start_jsonl_line


# ---------------------------------------------------------------------------
# chunk_session — determinism and real-fixture bounds
# ---------------------------------------------------------------------------


def test_chunk_session_deterministic() -> None:
    """Two calls on the same file return byte-equal results."""
    assert FIXTURE.exists()
    first = chunk_session(FIXTURE)
    second = chunk_session(FIXTURE)
    assert len(first) == len(second)
    for a, b in zip(first, second, strict=True):
        assert a.text == b.text
        assert a.start_jsonl_line == b.start_jsonl_line
        assert a.end_jsonl_line == b.end_jsonl_line


def test_chunk_session_at_least_one_chunk() -> None:
    assert FIXTURE.exists()
    chunks = chunk_session(FIXTURE)
    assert len(chunks) >= 1


def test_chunk_session_bounds_cite_real_lines() -> None:
    """start_jsonl_line of each chunk points to a real, non-empty JSONL line."""
    assert FIXTURE.exists()
    chunks = chunk_session(FIXTURE)
    fixture_str = str(FIXTURE)
    linecache.clearcache()

    for chunk in chunks:
        raw = linecache.getline(fixture_str, chunk.start_jsonl_line).strip()
        assert raw, f"start_jsonl_line={chunk.start_jsonl_line} points to empty line"
        # The line should be parseable JSONL (list or dict)
        data = json.loads(raw)
        assert isinstance(data, (list, dict))


def test_chunk_session_text_contains_role_prefixes() -> None:
    """At least one chunk should contain a recognizable role-prefix line."""
    assert FIXTURE.exists()
    chunks = chunk_session(FIXTURE)
    all_text = "\n".join(c.text for c in chunks)
    has_user = "User: " in all_text
    has_assistant = "Assistant: " in all_text
    has_tool = "Tool[" in all_text
    assert has_user or has_assistant or has_tool, (
        "Expected at least one role-prefixed line in chunked output"
    )


def test_chunk_session_with_size_override(tmp_path: Path) -> None:
    """chunk_tokens kwarg propagates to output chunk sizes."""
    import shutil

    dest = tmp_path / FIXTURE.name
    shutil.copy(FIXTURE, dest)

    small_chunks = chunk_session(dest, chunk_tokens=100)
    large_chunks = chunk_session(dest, chunk_tokens=SESSION_CHUNK_TOKENS)
    # Smaller token limit → more (or equal) chunks
    assert len(small_chunks) >= len(large_chunks)
