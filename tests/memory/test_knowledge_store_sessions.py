"""Tests for KnowledgeStore.index_session and sync_sessions.

Real SQLite, real FTS5, real JSONL fixtures. No mocks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.memory.knowledge_store import KnowledgeStore
from co_cli.memory.session import session_filename
from co_cli.memory.transcript import append_messages

FIXTURE = Path(__file__).parent / "fixtures" / "session_with_tool_turns.jsonl"


def _make_store(tmp_path: Path) -> KnowledgeStore:
    """Build a minimal FTS5-only KnowledgeStore pointing at tmp_path."""
    from co_cli.config.core import Settings

    db = tmp_path / "search.db"
    store = KnowledgeStore(config=Settings(), knowledge_db_path=db)
    return store


def _write_session(
    sessions_dir: Path,
    name: str,
    messages: list,
) -> Path:
    """Write a session JSONL file and return its path."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / name
    append_messages(path, messages)
    return path


def _rich_messages() -> list:
    """A set of messages with user, assistant, and tool turns."""
    return [
        ModelRequest(parts=[UserPromptPart(content="What is the latest Python release?")]),
        ModelResponse(
            parts=[
                TextPart(content="Let me search for that."),
                ToolCallPart(
                    tool_name="web_search",
                    args='{"query": "latest Python release"}',
                    tool_call_id="tc1",
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="web_search",
                    content="Python 3.13 was released in October 2024 with improved performance.",
                    tool_call_id="tc1",
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Python 3.13 is the latest release.")]),
    ]


def _session_name(offset_days: int = 0) -> str:
    ts = datetime(2026, 4, 14 + offset_days, 12, 0, 0, tzinfo=UTC)
    return session_filename(ts, f"aabbcc{offset_days:02d}-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# index_session — docs row
# ---------------------------------------------------------------------------


def test_index_session_writes_docs_row(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    name = _session_name(0)
    path = _write_session(sessions_dir, name, _rich_messages())

    store = _make_store(tmp_path)
    try:
        store.index_session(path)
        row = store._conn.execute(
            "SELECT source, kind, created FROM docs WHERE source='session' AND chunk_id=0"
        ).fetchone()
        assert row is not None, "Expected a docs row after index_session"
        assert row["source"] == "session"
        assert row["kind"] == "session"
        assert row["created"] is not None
    finally:
        store.close()


def test_index_session_writes_chunk_rows(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    name = _session_name(0)
    path = _write_session(sessions_dir, name, _rich_messages())
    expected_uuid8 = name[: 19 + 1 + 8].split("-")[-1]  # last segment of stem

    store = _make_store(tmp_path)
    try:
        store.index_session(path)
        rows = store._conn.execute(
            "SELECT chunk_index, start_line, end_line FROM chunks"
            " WHERE source='session' AND doc_path=?",
            (expected_uuid8,),
        ).fetchall()
        assert len(rows) >= 1, "Expected at least one chunk row"
        chunk_indices = [r["chunk_index"] for r in rows]
        assert chunk_indices == sorted(chunk_indices), "chunk_index not monotone"
        for r in rows:
            assert r["start_line"] >= 1
            assert r["end_line"] >= r["start_line"]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# index_session — idempotency (hash-skip)
# ---------------------------------------------------------------------------


def test_index_session_idempotent_no_re_embed(tmp_path: Path) -> None:
    """Second call on unchanged file produces no new embedding_cache rows and no chunk rewrites."""
    sessions_dir = tmp_path / "sessions"
    name = _session_name(0)
    path = _write_session(sessions_dir, name, _rich_messages())

    store = _make_store(tmp_path)
    try:
        store.index_session(path)
        uuid8 = store._conn.execute(
            "SELECT path FROM docs WHERE source='session' AND chunk_id=0"
        ).fetchone()["path"]

        before_emb = store._conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]
        before_chunk_count = store._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE source='session' AND doc_path=?",
            (uuid8,),
        ).fetchone()[0]
        before_max_rowid = store._conn.execute(
            "SELECT MAX(rowid) FROM chunks WHERE source='session' AND doc_path=?",
            (uuid8,),
        ).fetchone()[0]

        store.index_session(path)  # second call — should hash-skip

        after_emb = store._conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]
        after_chunk_count = store._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE source='session' AND doc_path=?",
            (uuid8,),
        ).fetchone()[0]
        after_max_rowid = store._conn.execute(
            "SELECT MAX(rowid) FROM chunks WHERE source='session' AND doc_path=?",
            (uuid8,),
        ).fetchone()[0]

        assert after_emb == before_emb, "Unexpected new embedding_cache rows on second call"
        assert after_chunk_count == before_chunk_count, "Chunk count changed on second call"
        assert after_max_rowid == before_max_rowid, "max rowid bumped — indicates re-insert"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# index_session — partial-write recovery
# ---------------------------------------------------------------------------


def test_index_session_partial_write_recovery(tmp_path: Path) -> None:
    """If docs hash is set but chunks are absent (crash simulation), re-indexes cleanly."""
    sessions_dir = tmp_path / "sessions"
    name = _session_name(0)
    path = _write_session(sessions_dir, name, _rich_messages())

    store = _make_store(tmp_path)
    try:
        store.index_session(path)
        uuid8 = store._conn.execute(
            "SELECT path FROM docs WHERE source='session' AND chunk_id=0"
        ).fetchone()["path"]

        # Simulate crash between index() and index_chunks(): docs hash committed,
        # chunks and vec cleaned (as index_chunks would do at the start of a re-index).
        # Use remove_chunks so both chunks and chunks_vec are cleared consistently.
        store.remove_chunks("session", uuid8)

        count_before = store._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE source='session' AND doc_path=?", (uuid8,)
        ).fetchone()[0]
        assert count_before == 0, "Chunks should be empty before recovery call"

        store.index_session(path)  # should fall through and re-index

        count_after = store._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE source='session' AND doc_path=?", (uuid8,)
        ).fetchone()[0]
        assert count_after > 0, "Chunks should be re-populated after partial-write recovery"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# index_session — file grows → chunks replaced
# ---------------------------------------------------------------------------


def test_index_session_replaces_chunks_on_growth(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    name = _session_name(0)
    path = _write_session(sessions_dir, name, _rich_messages())

    store = _make_store(tmp_path)
    try:
        store.index_session(path)
        uuid8 = store._conn.execute(
            "SELECT path FROM docs WHERE source='session' AND chunk_id=0"
        ).fetchone()["path"]
        first_max_idx = store._conn.execute(
            "SELECT MAX(chunk_index) FROM chunks WHERE source='session' AND doc_path=?",
            (uuid8,),
        ).fetchone()[0]

        # Grow the session by appending more messages
        extra = [
            ModelRequest(parts=[UserPromptPart(content="Tell me more about Python 3.14 plans.")]),
            ModelResponse(
                parts=[
                    TextPart(
                        content="Python 3.14 is expected to include free-threading improvements "
                        "and continued optimization of the interpreter. The release is planned "
                        "for late 2025 with several PEPs targeting performance enhancements."
                    )
                ]
            ),
        ]
        append_messages(path, extra)
        store.index_session(path)

        # All chunks should have contiguous chunk_index starting from 0
        rows = store._conn.execute(
            "SELECT chunk_index FROM chunks WHERE source='session' AND doc_path=?"
            " ORDER BY chunk_index",
            (uuid8,),
        ).fetchall()
        new_indices = [r["chunk_index"] for r in rows]
        assert new_indices == list(range(len(new_indices))), "chunk_index not contiguous 0..N"

        # Larger session should produce at least as many chunks
        assert new_indices[-1] >= first_max_idx
    finally:
        store.close()


# ---------------------------------------------------------------------------
# sync_sessions — skips exclude, indexes others, removes stale
# ---------------------------------------------------------------------------


def test_sync_sessions_excludes_current(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    current = _write_session(sessions_dir, _session_name(0), _rich_messages())
    _write_session(sessions_dir, _session_name(1), _rich_messages())
    other_uuid8 = _session_name(1).split(".")[0].split("-")[-1]  # last 8 chars of stem

    store = _make_store(tmp_path)
    try:
        store.sync_sessions(sessions_dir, exclude=current)

        current_uuid8 = _session_name(0).split(".")[0].split("-")[-1]
        current_row = store._conn.execute(
            "SELECT path FROM docs WHERE source='session' AND path=?",
            (current_uuid8,),
        ).fetchone()
        assert current_row is None, "Current session should be excluded from index"

        other_row = store._conn.execute(
            "SELECT path FROM docs WHERE source='session' AND path=?",
            (other_uuid8,),
        ).fetchone()
        assert other_row is not None, "Other session should be indexed"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# search source filter — sessions only, knowledge only, isolation
# ---------------------------------------------------------------------------


def test_search_source_session_returns_only_sessions(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    path = _write_session(sessions_dir, _session_name(0), _rich_messages())

    store = _make_store(tmp_path)
    try:
        store.index_session(path)
        results = store.search("Python release", source=["session"])
        assert all(r.source == "session" for r in results), "Expected only session results"
    finally:
        store.close()


def test_search_source_knowledge_excludes_sessions(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    path = _write_session(sessions_dir, _session_name(0), _rich_messages())

    store = _make_store(tmp_path)
    try:
        store.index_session(path)
        results = store.search("Python release", source="knowledge")
        assert all(r.source != "session" for r in results), (
            "Expected no session results when source='knowledge'"
        )
    finally:
        store.close()


def test_artifact_search_unaffected_by_sessions(tmp_path: Path) -> None:
    """Artifact-only results are byte-identical before and after sessions are indexed."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    artifact = knowledge_dir / "note.md"
    artifact.write_text(
        "---\nkind: note\ntitle: Test Note\ncreated: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\n"
        "This is a test artifact about climate change and renewable energy.\n"
    )

    sessions_dir = tmp_path / "sessions"
    session_path = _write_session(sessions_dir, _session_name(0), _rich_messages())

    store = _make_store(tmp_path)
    try:
        store.sync_dir("knowledge", knowledge_dir)
        results_before = store.search("renewable energy", source="knowledge")

        store.index_session(session_path)
        results_after = store.search("renewable energy", source="knowledge")

        assert len(results_before) == len(results_after)
        for a, b in zip(results_before, results_after, strict=True):
            assert a.path == b.path
            assert a.score == b.score
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Unrecognised filename: no crash, just warning
# ---------------------------------------------------------------------------


def test_index_session_ignores_unrecognised_filename(tmp_path: Path) -> None:
    bad_file = tmp_path / "not-a-session.jsonl"
    bad_file.write_text('{"kind": "session_meta"}\n')

    store = _make_store(tmp_path)
    try:
        store.index_session(bad_file)  # should not raise
        count = store._conn.execute("SELECT COUNT(*) FROM docs WHERE source='session'").fetchone()[
            0
        ]
        assert count == 0
    finally:
        store.close()
