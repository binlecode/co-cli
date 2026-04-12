"""Functional tests for JSONL transcript persistence."""

from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.context.session_browser import format_file_size, list_sessions
from co_cli.context.transcript import (
    MAX_TRANSCRIPT_READ_BYTES,
    SKIP_PRECOMPACT_THRESHOLD,
    append_messages,
    load_transcript,
    write_compact_boundary,
)


def test_round_trip_various_part_types(tmp_path: Path) -> None:
    """Write messages with various part types, read back, assert equality."""
    path = tmp_path / "sessions" / "test-round-trip-001.jsonl"

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

    append_messages(path, original_messages)
    loaded = load_transcript(path)

    assert len(loaded) == len(original_messages)
    for orig, back in zip(original_messages, loaded, strict=False):
        assert type(orig) is type(back)
        assert len(orig.parts) == len(back.parts)
        for op, bp in zip(orig.parts, back.parts, strict=False):
            assert type(op) is type(bp)
            if hasattr(op, "content"):
                assert op.content == bp.content


def test_append_incremental(tmp_path: Path) -> None:
    """Multiple appends accumulate in the same JSONL file."""
    path = tmp_path / "sessions" / "test-incremental-001.jsonl"

    batch1 = [ModelRequest(parts=[UserPromptPart(content="hello")])]
    batch2 = [
        ModelResponse(parts=[TextPart(content="hi")], model_name="m"),
        ModelRequest(parts=[UserPromptPart(content="bye")]),
    ]

    append_messages(path, batch1)
    append_messages(path, batch2)

    loaded = load_transcript(path)
    assert len(loaded) == 3


def test_load_empty_session(tmp_path: Path) -> None:
    """load_transcript returns empty list for nonexistent path."""
    path = tmp_path / "sessions" / "nonexistent.jsonl"
    loaded = load_transcript(path)
    assert loaded == []


def test_malformed_lines_skipped(tmp_path: Path) -> None:
    """Malformed JSONL lines are skipped, valid lines are loaded."""
    path = tmp_path / "sessions" / "test-malformed-001.jsonl"

    valid_msg = ModelRequest(parts=[UserPromptPart(content="valid")])
    append_messages(path, [valid_msg])

    with path.open("a", encoding="utf-8") as f:
        f.write("not valid json{{{{\n")

    append_messages(path, [valid_msg])

    loaded = load_transcript(path)
    assert len(loaded) == 2, "Two valid messages expected, malformed line skipped"


def test_append_empty_list_is_noop(tmp_path: Path) -> None:
    """Appending an empty list does not create a file."""
    path = tmp_path / "sessions" / "test-empty-001.jsonl"
    append_messages(path, [])
    assert not path.exists()


def test_list_sessions_sorted_by_filename(tmp_path: Path) -> None:
    """list_sessions returns entries sorted by filename descending with titles and file sizes."""
    sessions_dir = tmp_path / "sessions"

    msgs_a = [ModelRequest(parts=[UserPromptPart(content="fix the auth bug")])]
    append_messages(sessions_dir / "test-session-a.jsonl", msgs_a)
    msgs_b = [
        ModelRequest(parts=[UserPromptPart(content="refactor the database layer")]),
        ModelResponse(parts=[TextPart(content="Sure, I'll refactor.")], model_name="m"),
    ]
    append_messages(sessions_dir / "test-session-b.jsonl", msgs_b)

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
    append_messages(sessions_dir / "test-long-title.jsonl", msgs)

    summaries = list_sessions(sessions_dir)
    assert len(summaries) == 1
    assert len(summaries[0].title) == 83  # 80 chars + "..."
    assert summaries[0].title.endswith("...")


def test_compact_boundary_skips_pre_boundary_messages_large_file(tmp_path: Path) -> None:
    """For files > 5MB, messages before the last compact boundary are skipped on load."""
    path = tmp_path / "sessions" / "test-compact-large.jsonl"

    # Write enough pre-boundary messages to exceed SKIP_PRECOMPACT_THRESHOLD
    pre_msg = ModelRequest(parts=[UserPromptPart(content="A" * 2000)])
    for _ in range(2600):
        append_messages(path, [pre_msg])

    assert path.stat().st_size > SKIP_PRECOMPACT_THRESHOLD

    write_compact_boundary(path)

    post_msg = ModelRequest(parts=[UserPromptPart(content="post-compact")])
    append_messages(path, [post_msg, post_msg])

    loaded = load_transcript(path)
    assert len(loaded) == 2
    assert loaded[0].parts[0].content == "post-compact"


def test_compact_boundary_ignored_for_small_files(tmp_path: Path) -> None:
    """For files < 5MB, compact boundary markers are ignored — all messages loaded."""
    path = tmp_path / "sessions" / "test-compact-small.jsonl"

    pre_msg = ModelRequest(parts=[UserPromptPart(content="before")])
    append_messages(path, [pre_msg])
    write_compact_boundary(path)
    post_msg = ModelRequest(parts=[UserPromptPart(content="after")])
    append_messages(path, [post_msg])

    assert path.stat().st_size < SKIP_PRECOMPACT_THRESHOLD

    loaded = load_transcript(path)
    assert len(loaded) == 2
    assert loaded[0].parts[0].content == "before"
    assert loaded[1].parts[0].content == "after"


def test_load_transcript_rejects_oversized_file(tmp_path: Path) -> None:
    """Files exceeding MAX_TRANSCRIPT_READ_BYTES return empty list."""
    path = tmp_path / "sessions" / "test-oversized.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.seek(MAX_TRANSCRIPT_READ_BYTES + 1)
        f.write("\n")

    loaded = load_transcript(path)
    assert loaded == []


def test_format_file_size() -> None:
    """format_file_size produces human-readable output."""
    assert format_file_size(500) == "500 B"
    assert format_file_size(2048) == "2 KB"
    assert format_file_size(1536 * 1024) == "1.5 MB"
