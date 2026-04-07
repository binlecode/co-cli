"""Functional tests for JSONL transcript persistence."""

from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

from co_cli.context.session_browser import format_file_size, list_sessions
from co_cli.context.transcript import (
    append_messages,
    load_transcript,
    write_compact_boundary,
    SKIP_PRECOMPACT_THRESHOLD,
    MAX_TRANSCRIPT_READ_BYTES,
)


def test_round_trip_various_part_types(tmp_path: Path) -> None:
    """Write messages with various part types, read back, assert equality."""
    sessions_dir = tmp_path / "sessions"
    session_id = "test-round-trip-001"

    original_messages = [
        ModelRequest(parts=[UserPromptPart(content="fix the bug in main.py")]),
        ModelResponse(
            parts=[TextPart(content="I'll look at main.py and fix the bug.")],
            model_name="test-model",
        ),
        ModelRequest(parts=[UserPromptPart(content="also check utils.py")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args='{"path": "utils.py"}',
                    tool_call_id="call-1",
                ),
            ],
            model_name="test-model",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    content="def helper(): pass",
                    tool_call_id="call-1",
                ),
            ],
        ),
    ]

    append_messages(sessions_dir, session_id, original_messages)
    loaded = load_transcript(sessions_dir, session_id)

    assert len(loaded) == len(original_messages)
    for orig, back in zip(original_messages, loaded):
        assert type(orig) is type(back)
        assert len(orig.parts) == len(back.parts)
        for op, bp in zip(orig.parts, back.parts):
            assert type(op) is type(bp)
            if hasattr(op, "content"):
                assert op.content == bp.content


def test_append_incremental(tmp_path: Path) -> None:
    """Multiple appends accumulate in the same JSONL file."""
    sessions_dir = tmp_path / "sessions"
    session_id = "test-incremental-001"

    batch1 = [ModelRequest(parts=[UserPromptPart(content="hello")])]
    batch2 = [
        ModelResponse(parts=[TextPart(content="hi")], model_name="m"),
        ModelRequest(parts=[UserPromptPart(content="bye")]),
    ]

    append_messages(sessions_dir, session_id, batch1)
    append_messages(sessions_dir, session_id, batch2)

    loaded = load_transcript(sessions_dir, session_id)
    assert len(loaded) == 3


def test_load_empty_session(tmp_path: Path) -> None:
    """load_transcript returns empty list for nonexistent session."""
    sessions_dir = tmp_path / "sessions"
    loaded = load_transcript(sessions_dir, "nonexistent")
    assert loaded == []


def test_malformed_lines_skipped(tmp_path: Path) -> None:
    """Malformed JSONL lines are skipped, valid lines are loaded."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_id = "test-malformed-001"

    valid_msg = ModelRequest(parts=[UserPromptPart(content="valid")])
    append_messages(sessions_dir, session_id, [valid_msg])

    path = sessions_dir / f"{session_id}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write("not valid json{{{{\n")

    append_messages(sessions_dir, session_id, [valid_msg])

    loaded = load_transcript(sessions_dir, session_id)
    assert len(loaded) == 2, "Two valid messages expected, malformed line skipped"


def test_append_empty_list_is_noop(tmp_path: Path) -> None:
    """Appending an empty list does not create a file."""
    sessions_dir = tmp_path / "sessions"
    append_messages(sessions_dir, "test-empty-001", [])
    assert not (sessions_dir / "test-empty-001.jsonl").exists()


def test_list_sessions_sorted_by_mtime(tmp_path: Path) -> None:
    """list_sessions returns entries sorted by mtime descending with titles and file sizes."""
    import time
    sessions_dir = tmp_path / "sessions"

    msgs_a = [ModelRequest(parts=[UserPromptPart(content="fix the auth bug")])]
    append_messages(sessions_dir, "test-session-a", msgs_a)
    time.sleep(0.05)
    msgs_b = [
        ModelRequest(parts=[UserPromptPart(content="refactor the database layer")]),
        ModelResponse(parts=[TextPart(content="Sure, I'll refactor.")], model_name="m"),
    ]
    append_messages(sessions_dir, "test-session-b", msgs_b)

    summaries = list_sessions(sessions_dir)

    assert len(summaries) == 2
    # Most recent first
    assert summaries[0].session_id == "test-session-b"
    assert summaries[1].session_id == "test-session-a"
    # Title extraction
    assert "refactor the database layer" in summaries[0].title
    assert "fix the auth bug" in summaries[1].title
    # File size from stat (larger file has more messages)
    assert summaries[0].file_size > summaries[1].file_size
    assert summaries[0].file_size > 0
    assert summaries[1].file_size > 0


def test_list_sessions_empty_dir(tmp_path: Path) -> None:
    """list_sessions returns empty list when no sessions exist."""
    sessions_dir = tmp_path / "sessions"
    assert list_sessions(sessions_dir) == []


def test_list_sessions_title_truncation(tmp_path: Path) -> None:
    """Titles longer than 80 chars are truncated with ellipsis."""
    sessions_dir = tmp_path / "sessions"
    long_prompt = "x" * 120
    msgs = [ModelRequest(parts=[UserPromptPart(content=long_prompt)])]
    append_messages(sessions_dir, "test-long-title", msgs)

    summaries = list_sessions(sessions_dir)
    assert len(summaries) == 1
    assert len(summaries[0].title) == 83  # 80 chars + "..."
    assert summaries[0].title.endswith("...")


def test_compact_boundary_skips_pre_boundary_messages_large_file(tmp_path: Path) -> None:
    """For files > 5MB, messages before the last compact boundary are skipped on load."""
    sessions_dir = tmp_path / "sessions"
    session_id = "test-compact-large"

    # Write enough pre-boundary messages to exceed SKIP_PRECOMPACT_THRESHOLD
    pre_msg = ModelRequest(parts=[UserPromptPart(content="A" * 2000)])
    for _ in range(2600):
        append_messages(sessions_dir, session_id, [pre_msg])

    path = sessions_dir / f"{session_id}.jsonl"
    assert path.stat().st_size > SKIP_PRECOMPACT_THRESHOLD

    write_compact_boundary(sessions_dir, session_id)

    post_msg = ModelRequest(parts=[UserPromptPart(content="post-compact")])
    append_messages(sessions_dir, session_id, [post_msg, post_msg])

    loaded = load_transcript(sessions_dir, session_id)
    assert len(loaded) == 2
    assert loaded[0].parts[0].content == "post-compact"


def test_compact_boundary_ignored_for_small_files(tmp_path: Path) -> None:
    """For files < 5MB, compact boundary markers are ignored — all messages loaded."""
    sessions_dir = tmp_path / "sessions"
    session_id = "test-compact-small"

    pre_msg = ModelRequest(parts=[UserPromptPart(content="before")])
    append_messages(sessions_dir, session_id, [pre_msg])
    write_compact_boundary(sessions_dir, session_id)
    post_msg = ModelRequest(parts=[UserPromptPart(content="after")])
    append_messages(sessions_dir, session_id, [post_msg])

    path = sessions_dir / f"{session_id}.jsonl"
    assert path.stat().st_size < SKIP_PRECOMPACT_THRESHOLD

    loaded = load_transcript(sessions_dir, session_id)
    assert len(loaded) == 2
    assert loaded[0].parts[0].content == "before"
    assert loaded[1].parts[0].content == "after"


def test_load_transcript_rejects_oversized_file(tmp_path: Path) -> None:
    """Files exceeding MAX_TRANSCRIPT_READ_BYTES return empty list."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_id = "test-oversized"
    path = sessions_dir / f"{session_id}.jsonl"
    with path.open("w") as f:
        f.seek(MAX_TRANSCRIPT_READ_BYTES + 1)
        f.write("\n")

    loaded = load_transcript(sessions_dir, session_id)
    assert loaded == []


def test_format_file_size() -> None:
    """format_file_size produces human-readable output."""
    assert format_file_size(500) == "500 B"
    assert format_file_size(2048) == "2 KB"
    assert format_file_size(1536 * 1024) == "1.5 MB"
