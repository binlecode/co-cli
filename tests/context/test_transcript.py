"""Functional tests for JSONL transcript persistence."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from tests._frontend import SilentFrontend
from tests._settings import make_settings
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.context.orchestrate import TurnResult
from co_cli.context.session import new_session_path
from co_cli.context.session_browser import format_file_size, list_sessions
from co_cli.context.transcript import (
    MAX_TRANSCRIPT_READ_BYTES,
    SKIP_PRECOMPACT_THRESHOLD,
    append_messages,
    load_transcript,
    persist_session_history,
    write_compact_boundary,
)
from co_cli.deps import CoDeps, CoRuntimeState
from co_cli.main import _finalize_turn
from co_cli.tools.shell_backend import ShellBackend


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
                    tool_name="file_read",
                    args='{"path": "utils.py"}',
                    tool_call_id="call-1",
                ),
            ],
            model_name="test-model",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="file_read",
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
    from datetime import UTC, datetime

    from co_cli.context.session import session_filename

    sessions_dir = tmp_path / "sessions"

    # Older session
    older_dt = datetime(2026, 4, 10, 8, 0, 0, tzinfo=UTC)
    older_id = "aaaaaaaa-0000-0000-0000-000000000000"
    older_name = session_filename(older_dt, older_id)
    msgs_a = [ModelRequest(parts=[UserPromptPart(content="fix the auth bug")])]
    append_messages(sessions_dir / older_name, msgs_a)

    # Newer session — more messages = larger file
    newer_dt = datetime(2026, 4, 11, 8, 0, 0, tzinfo=UTC)
    newer_id = "bbbbbbbb-0000-0000-0000-000000000000"
    newer_name = session_filename(newer_dt, newer_id)
    msgs_b = [
        ModelRequest(parts=[UserPromptPart(content="refactor the database layer")]),
        ModelResponse(parts=[TextPart(content="Sure, I'll refactor.")], model_name="m"),
    ]
    append_messages(sessions_dir / newer_name, msgs_b)

    summaries = list_sessions(sessions_dir)

    assert len(summaries) == 2
    # Most recent first (lexicographic descending = chronological descending)
    assert summaries[0].session_id == "bbbbbbbb"
    assert summaries[1].session_id == "aaaaaaaa"
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
    from datetime import UTC, datetime

    from co_cli.context.session import session_filename

    sessions_dir = tmp_path / "sessions"
    long_prompt = "x" * 120
    msgs = [ModelRequest(parts=[UserPromptPart(content=long_prompt)])]
    name = session_filename(
        datetime(2026, 4, 11, 8, 0, 0, tzinfo=UTC), "cccccccc-0000-0000-0000-000000000000"
    )
    append_messages(sessions_dir / name, msgs)

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


def test_persist_session_history_branches_child_session_on_compaction(tmp_path: Path) -> None:
    """Compacted history is persisted in a fresh child transcript linked to the parent."""
    from co_cli.context._compaction import _SUMMARY_MARKER_PREFIX, _build_compaction_marker

    sessions_dir = tmp_path / "sessions"
    parent = sessions_dir / "2026-04-11-T080000Z-parent001.jsonl"
    original = [ModelRequest(parts=[UserPromptPart(content="original turn")])]
    append_messages(parent, original)

    marker = _build_compaction_marker(1, "Summary")
    compacted = [
        marker,
        ModelResponse(parts=[TextPart(content="Understood.")], model_name="m"),
    ]
    child = persist_session_history(
        session_path=parent,
        sessions_dir=sessions_dir,
        messages=compacted,
        persisted_message_count=len(original),
        history_compacted=True,
    )

    assert child != parent
    raw_lines = child.read_text(encoding="utf-8").splitlines()
    assert raw_lines[0].startswith('{"type":"session_meta"')
    assert parent.name in raw_lines[0]

    loaded_child = load_transcript(child)
    assert len(loaded_child) == 2
    assert loaded_child[0].parts[0].content.startswith(_SUMMARY_MARKER_PREFIX)
    assert loaded_child[1].parts[0].content == "Understood."
    assert load_transcript(parent)[0].parts[0].content == "original turn"


def test_persist_session_history_preserves_todo_snapshot_in_child(tmp_path: Path) -> None:
    """A post-compaction todo snapshot round-trips through the child transcript."""
    from co_cli.context._compaction import (
        _TODO_SNAPSHOT_PREFIX,
        _build_compaction_marker,
        _build_todo_snapshot,
    )

    sessions_dir = tmp_path / "sessions"
    parent = sessions_dir / "2026-04-23-T120000Z-parent-todo001.jsonl"
    original = [ModelRequest(parts=[UserPromptPart(content="original turn")])]
    append_messages(parent, original)

    snapshot = _build_todo_snapshot(
        [
            {"content": "survive persistence", "status": "pending", "priority": "medium"},
        ]
    )
    assert snapshot is not None

    compacted = [
        _build_compaction_marker(1, "Summary"),
        snapshot,
        ModelResponse(parts=[TextPart(content="Understood.")], model_name="m"),
    ]
    child = persist_session_history(
        session_path=parent,
        sessions_dir=sessions_dir,
        messages=compacted,
        persisted_message_count=len(original),
        history_compacted=True,
    )

    loaded_child = load_transcript(child)
    assert len(loaded_child) == 3
    snapshot_loaded = loaded_child[1]
    assert isinstance(snapshot_loaded, ModelRequest)
    assert snapshot_loaded.parts[0].content.startswith(_TODO_SNAPSHOT_PREFIX)
    assert "survive persistence" in snapshot_loaded.parts[0].content


def test_load_transcript_rejects_oversized_file(tmp_path: Path) -> None:
    """Files exceeding MAX_TRANSCRIPT_READ_BYTES return empty list."""
    path = tmp_path / "sessions" / "test-oversized.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.seek(MAX_TRANSCRIPT_READ_BYTES + 1)
        f.write("\n")

    loaded = load_transcript(path)
    assert loaded == []


def test_load_transcript_tolerates_legacy_tool_names(tmp_path: Path) -> None:
    """Sessions containing renamed tool names (e.g. update_memory) load without exception.

    After the rename-memory-tools-to-knowledge refactor, persisted sessions may
    contain ToolCallPart/ToolReturnPart with the old names update_memory and
    append_memory. load_transcript must not raise — tool_name is an opaque string
    in pydantic-ai's type adapter and is never validated against a live registry.
    """
    path = tmp_path / "sessions" / "test-legacy-tools-001.jsonl"

    legacy_messages = [
        ModelRequest(parts=[UserPromptPart(content="remember this fact")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="update_memory",
                    args='{"title": "test", "content": "legacy fact"}',
                    tool_call_id="call-1",
                ),
            ],
            model_name="test-model",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="update_memory",
                    content="saved",
                    tool_call_id="call-1",
                ),
            ],
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="append_memory",
                    args='{"title": "test", "content": "more"}',
                    tool_call_id="call-2",
                ),
            ],
            model_name="test-model",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="append_memory",
                    content="appended",
                    tool_call_id="call-2",
                ),
            ],
        ),
    ]

    append_messages(path, legacy_messages)
    loaded = load_transcript(path)

    assert len(loaded) == len(legacy_messages)
    # Legacy tool names survive the round-trip unchanged
    assert loaded[1].parts[0].tool_name == "update_memory"
    assert loaded[3].parts[0].tool_name == "append_memory"


def test_format_file_size() -> None:
    """format_file_size produces human-readable output."""
    assert format_file_size(500) == "500 B"
    assert format_file_size(2048) == "2 KB"
    assert format_file_size(1536 * 1024) == "1.5 MB"


@pytest.mark.asyncio
async def test_finalize_turn_branches_child_transcript_when_history_compacted(
    tmp_path: Path,
) -> None:
    """When history_compaction_applied is True, _finalize_turn creates a child transcript."""
    sessions_dir = tmp_path / "sessions"
    parent = sessions_dir / "2026-04-20-T170000Z-hygiene001.jsonl"
    original = [ModelRequest(parts=[UserPromptPart(content="original turn")])]
    append_messages(parent, original)

    config = make_settings()
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        runtime=CoRuntimeState(history_compaction_applied=True),
        sessions_dir=sessions_dir,
    )
    deps.session.session_path = parent
    deps.session.persisted_message_count = len(original)

    compacted = [ModelRequest(parts=[UserPromptPart(content="compacted summary")])]
    turn_result = TurnResult(
        interrupted=False,
        outcome="continue",
        messages=compacted,
    )
    frontend = SilentFrontend()
    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        await _finalize_turn(turn_result, original, deps, frontend)

    assert deps.session.session_path != parent
    loaded = load_transcript(deps.session.session_path)
    assert len(loaded) == 1
    assert loaded[0].parts[0].content == "compacted summary"


@pytest.mark.asyncio
async def test_finalize_turn_notifies_on_transcript_write_failure(tmp_path: Path) -> None:
    """When the sessions dir is read-only, _finalize_turn surfaces a write-failure status."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    # Make the sessions dir read-only so file creation fails
    sessions_dir.chmod(0o555)
    try:
        config = make_settings()
        deps = CoDeps(
            shell=ShellBackend(),
            config=config,
            runtime=CoRuntimeState(history_compaction_applied=False),
            sessions_dir=sessions_dir,
        )
        deps.session.session_path = new_session_path(sessions_dir)

        turn_result = TurnResult(
            interrupted=False,
            outcome="continue",
            messages=[ModelRequest(parts=[UserPromptPart(content="hello")])],
        )
        frontend = SilentFrontend()
        async with asyncio.timeout(10):
            await _finalize_turn(turn_result, [], deps, frontend)

        assert any("Session write failed" in status for status in frontend.statuses)
    finally:
        # Restore permissions so pytest can clean up tmp_path
        sessions_dir.chmod(0o755)
