"""Functional tests for the session index module.

Uses real JSONL files written via append_messages(), real SQLite, and real FTS5.
No mocks or fakes.
"""

from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.context.session import session_filename
from co_cli.context.transcript import append_messages, write_compact_boundary
from co_cli.memory._extractor import extract_messages
from co_cli.memory._store import MemoryIndex, SessionSearchResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_session(sessions_dir: Path, *, name: str | None = None, messages=None) -> Path:
    """Write a session JSONL file with the given messages."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    if name is None:
        now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
        name = session_filename(now, "aabbccdd-0000-0000-0000-000000000000")
    path = sessions_dir / name
    if messages:
        append_messages(path, messages)
    return path


# ---------------------------------------------------------------------------
# TASK-1 test 1: extract_messages returns only user+assistant parts
# ---------------------------------------------------------------------------


def test_extract_messages_returns_only_user_and_assistant(tmp_path: Path) -> None:
    """extract_messages returns user-prompt and text parts; skips all others."""
    sessions_dir = tmp_path / "sessions"
    path = _write_session(
        sessions_dir,
        messages=[
            ModelRequest(parts=[SystemPromptPart(content="You are helpful.")]),
            ModelRequest(parts=[UserPromptPart(content="What is the capital of France?")]),
            ModelResponse(parts=[TextPart(content="The capital of France is Paris.")]),
            ModelResponse(parts=[ToolCallPart(tool_name="web_search", args='{"query": "Paris"}')]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="web_search",
                        content="Paris results...",
                        tool_call_id="tc1",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="Try again", tool_name="web_search", tool_call_id="tc1"
                    )
                ]
            ),
            ModelResponse(parts=[ThinkingPart(content="Let me think about this...", id="th1")]),
        ],
    )

    msgs = extract_messages(path)

    roles = [m.role for m in msgs]
    assert roles == ["user", "assistant"], (
        f"Only user-prompt and text parts must be extracted; got roles: {roles}"
    )
    assert msgs[0].content == "What is the capital of France?"
    assert msgs[1].content == "The capital of France is Paris."


def test_extract_messages_skips_compact_boundary(tmp_path: Path) -> None:
    """compact_boundary control lines are skipped without error."""
    sessions_dir = tmp_path / "sessions"
    path = _write_session(
        sessions_dir,
        messages=[ModelRequest(parts=[UserPromptPart(content="Before boundary")])],
    )
    write_compact_boundary(path)
    append_messages(path, [ModelResponse(parts=[TextPart(content="After boundary")])])

    msgs = extract_messages(path)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[1].role == "assistant"


def test_extract_messages_empty_file_returns_empty(tmp_path: Path) -> None:
    """Empty session file produces empty results."""
    path = tmp_path / "sessions" / "2026-04-14-T120000Z-aabbccdd.jsonl"
    path.parent.mkdir()
    path.write_text("", encoding="utf-8")

    msgs = extract_messages(path)
    assert msgs == []


def test_extract_messages_missing_file_returns_empty(tmp_path: Path) -> None:
    """Non-existent file produces empty results without raising."""
    path = tmp_path / "sessions" / "2026-04-14-T120000Z-aabbccdd.jsonl"
    msgs = extract_messages(path)
    assert msgs == []


# ---------------------------------------------------------------------------
# TASK-1 test 2: MemoryIndex.index_session populates tables; FTS row count matches
# ---------------------------------------------------------------------------


def test_index_session_populates_sessions_and_messages(tmp_path: Path) -> None:
    """index_session inserts session record and message rows; FTS count matches."""
    sessions_dir = tmp_path / "sessions"
    path = _write_session(
        sessions_dir,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="Tell me about the Rust borrow checker")]),
            ModelResponse(
                parts=[TextPart(content="The borrow checker enforces ownership rules.")]
            ),
            ModelRequest(parts=[UserPromptPart(content="Can you give an example?")]),
            ModelResponse(parts=[TextPart(content="Sure, here is a Rust ownership example.")]),
        ],
    )
    db_path = tmp_path / "session-index.db"
    store = MemoryIndex(db_path)
    try:
        store.index_session(path)

        # sessions table has one record
        sessions = store._conn.execute("SELECT * FROM sessions").fetchall()
        assert len(sessions) == 1

        # messages table has 4 records (2 user + 2 assistant)
        messages = store._conn.execute("SELECT * FROM messages").fetchall()
        assert len(messages) == 4

        # FTS table row count matches messages
        fts_count = store._conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0]
        assert fts_count == 4, f"FTS row count must equal indexed message count, got {fts_count}"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# TASK-1 test 3: MemoryIndex.search returns SessionSearchResult with correct session_id
# ---------------------------------------------------------------------------


def test_search_returns_results_with_correct_session_id(tmp_path: Path) -> None:
    """search() returns SessionSearchResult list with correct session_id and non-empty snippet."""
    sessions_dir = tmp_path / "sessions"
    now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    name = session_filename(now, "xxyyzz11-0000-0000-0000-000000000000")
    path = _write_session(
        sessions_dir,
        name=name,
        messages=[
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content="Explain the concept of monads in functional programming"
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    TextPart(
                        content="A monad is a design pattern for chaining operations "
                        "on wrapped values in functional programming."
                    )
                ]
            ),
        ],
    )

    db_path = tmp_path / "session-index.db"
    store = MemoryIndex(db_path)
    try:
        store.index_session(path)
        results = store.search("monad functional programming")

        assert len(results) >= 1, "search must return at least one result"
        result = results[0]
        assert isinstance(result, SessionSearchResult)
        assert result.session_id == "xxyyzz11", (
            f"session_id must be uuid8 prefix from filename, got {result.session_id!r}"
        )
        assert result.snippet, "snippet must be non-empty"
        assert result.score > 0.0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# TASK-1 test 4: sync_sessions skips unchanged; re-indexes on size increase
# ---------------------------------------------------------------------------


def test_sync_sessions_skips_unchanged_and_reindexes_on_growth(tmp_path: Path) -> None:
    """sync_sessions skips sessions with unchanged file_size; re-indexes on growth."""
    sessions_dir = tmp_path / "sessions"
    path = _write_session(
        sessions_dir,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="First message about Python decorators")])
        ],
    )

    db_path = tmp_path / "session-index.db"
    store = MemoryIndex(db_path)
    try:
        store.sync_sessions(sessions_dir)
        count_after_first = store._conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        assert count_after_first == 1

        # Run sync again without changing the file — must stay at 1
        store.sync_sessions(sessions_dir)
        count_after_skip = store._conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        assert count_after_skip == 1, "Second sync must be a no-op when file size unchanged"

        # Append a new message to grow the file
        append_messages(
            path,
            [
                ModelResponse(
                    parts=[TextPart(content="A decorator wraps a callable to add behavior.")]
                )
            ],
        )

        # Sync again — must re-index and pick up the new message
        store.sync_sessions(sessions_dir)
        count_after_growth = store._conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        assert count_after_growth == 2, (
            f"After file growth, sync must re-index the session; got {count_after_growth} messages"
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# TASK-1 test 5: sync_sessions(exclude=path) omits the excluded path
# ---------------------------------------------------------------------------


def test_sync_sessions_exclude_omits_active_session(tmp_path: Path) -> None:
    """sync_sessions with exclude= skips the active session file."""
    sessions_dir = tmp_path / "sessions"

    now_a = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    now_b = datetime(2026, 4, 14, 13, 0, 0, tzinfo=UTC)
    name_a = session_filename(now_a, "aaaabbcc-0000-0000-0000-000000000000")
    name_b = session_filename(now_b, "ddddeeff-0000-0000-0000-000000000000")

    _write_session(
        sessions_dir,
        name=name_a,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="Session A content about Kubernetes pods")])
        ],
    )
    path_b = _write_session(
        sessions_dir,
        name=name_b,
        messages=[
            ModelRequest(
                parts=[UserPromptPart(content="Session B content about Docker containers")]
            )
        ],
    )

    db_path = tmp_path / "session-index.db"
    store = MemoryIndex(db_path)
    try:
        store.sync_sessions(sessions_dir, exclude=path_b)

        sessions = store._conn.execute("SELECT session_id FROM sessions").fetchall()
        indexed_ids = {row["session_id"] for row in sessions}

        assert "aaaabbcc" in indexed_ids, "Non-excluded session A must be indexed"
        assert "ddddeeff" not in indexed_ids, "Excluded session B must not be indexed"
    finally:
        store.close()
