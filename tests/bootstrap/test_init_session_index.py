"""Tests for init_session_index — bootstrap rewire for unified session chunking."""

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._settings import make_settings

from co_cli.bootstrap.core import init_session_index
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.display.core import TerminalFrontend
from co_cli.memory.knowledge_store import KnowledgeStore
from co_cli.memory.session import session_filename
from co_cli.memory.transcript import append_messages
from co_cli.tools.shell_backend import ShellBackend


def _make_store(tmp_path: Path) -> KnowledgeStore:
    from co_cli.config.core import Settings

    db = tmp_path / "search.db"
    return KnowledgeStore(config=Settings(), knowledge_db_path=db)


def _make_deps(tmp_path: Path, knowledge_store: KnowledgeStore | None) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        knowledge_store=knowledge_store,
        config=make_settings(),
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        sessions_dir=tmp_path / "sessions",
        knowledge_dir=tmp_path / "knowledge",
    )


def _make_frontend() -> TerminalFrontend:
    return TerminalFrontend()


def _write_session(sessions_dir: Path, offset_days: int = 0) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime(2026, 4, 14 + offset_days, 12, 0, 0, tzinfo=UTC)
    name = session_filename(ts, f"aabbcc{offset_days:02d}-0000-0000-0000-000000000000")
    path = sessions_dir / name
    append_messages(
        path,
        [
            ModelRequest(parts=[UserPromptPart(content=f"Test question {offset_days}")]),
            ModelResponse(parts=[TextPart(content=f"Test answer {offset_days}")]),
        ],
    )
    return path


# ---------------------------------------------------------------------------
# init_session_index indexes non-current sessions
# ---------------------------------------------------------------------------


def test_init_session_index_indexes_other_sessions(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    deps = _make_deps(tmp_path, knowledge_store=store)
    frontend = _make_frontend()

    current = _write_session(deps.sessions_dir, offset_days=0)
    _write_session(deps.sessions_dir, offset_days=1)
    _write_session(deps.sessions_dir, offset_days=2)

    try:
        init_session_index(deps, current, frontend)

        count = store._conn.execute(
            "SELECT COUNT(*) FROM docs WHERE source='session' AND chunk_id=0"
        ).fetchone()[0]
        assert count == 2, f"Expected 2 indexed sessions, got {count}"
    finally:
        store.close()


def test_init_session_index_excludes_current(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    deps = _make_deps(tmp_path, knowledge_store=store)
    frontend = _make_frontend()

    current = _write_session(deps.sessions_dir, offset_days=0)

    try:
        init_session_index(deps, current, frontend)

        # Current session uuid8 is "aabbcc00"
        current_uuid8 = "aabbcc00"
        row = store._conn.execute(
            "SELECT path FROM docs WHERE source='session' AND path=?",
            (current_uuid8,),
        ).fetchone()
        assert row is None, "Current session must not be indexed"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Legacy session-index.db is removed on first run
# ---------------------------------------------------------------------------


def test_init_session_index_removes_legacy_db(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    deps = _make_deps(tmp_path, knowledge_store=store)
    frontend = _make_frontend()

    current = _write_session(deps.sessions_dir, offset_days=0)

    # Place a fake legacy session-index.db at the expected location
    legacy_db = deps.sessions_dir.parent / "session-index.db"
    legacy_db.parent.mkdir(parents=True, exist_ok=True)
    legacy_db.write_bytes(b"SQLite format 3")

    try:
        init_session_index(deps, current, frontend)
        assert not legacy_db.exists(), "Legacy session-index.db should have been removed"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Second call does not re-embed (idempotency)
# ---------------------------------------------------------------------------


def test_init_session_index_no_re_embed_on_second_call(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    deps = _make_deps(tmp_path, knowledge_store=store)
    frontend = _make_frontend()

    current = _write_session(deps.sessions_dir, offset_days=0)
    _write_session(deps.sessions_dir, offset_days=1)

    try:
        init_session_index(deps, current, frontend)

        before_emb = store._conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]

        init_session_index(deps, current, frontend)  # second call

        after_emb = store._conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]
        assert after_emb == before_emb, (
            "Embedding cache grew on second call — re-embedding occurred"
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# CoDeps no longer has session_store field
# ---------------------------------------------------------------------------


def test_codeps_has_no_session_store_field() -> None:
    field_names = {f.name for f in dataclasses.fields(CoDeps)}
    assert "session_store" not in field_names, (
        f"session_store should not be a field of CoDeps; found: {field_names}"
    )


# ---------------------------------------------------------------------------
# Graceful degradation when knowledge_store is None
# ---------------------------------------------------------------------------


def test_init_session_index_graceful_when_no_knowledge_store(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, knowledge_store=None)
    frontend = _make_frontend()

    current = _write_session(deps.sessions_dir, offset_days=0)

    # Must not raise
    init_session_index(deps, current, frontend)
