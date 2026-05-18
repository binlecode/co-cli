"""Tests for session_search — ranked FTS over past session transcripts."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.index.store import IndexStore
from co_cli.session.store import SessionStore
from co_cli.tools.session.recall import _SESSIONS_CHANNEL_CAP, session_search
from co_cli.tools.shell_backend import ShellBackend

_FTS5_CONFIG = SETTINGS.memory.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_TEST_SETTINGS = SETTINGS.model_copy(update={"memory": _FTS5_CONFIG})

_SESSION_TIMESTAMP = "2026-01-01-T120000Z"


def _make_stores(tmp_path: Path) -> tuple[IndexStore, SessionStore]:
    index = IndexStore(config=_TEST_SETTINGS, db_path=tmp_path / "search.db")
    return index, SessionStore(index=index, config=_TEST_SETTINGS)


def _make_session_file(sessions_dir: Path, uuid8: str, content: str) -> Path:
    """Create a minimal pydantic-ai JSONL session file with searchable content."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{_SESSION_TIMESTAMP}-{uuid8}.jsonl"
    line = json.dumps([{"parts": [{"part_kind": "user-prompt", "content": content}]}])
    path.write_text(line + "\n", encoding="utf-8")
    return path


def _make_deps(
    tmp_path: Path, index: IndexStore, store: SessionStore, sessions_dir: Path
) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=_TEST_SETTINGS,
        session=CoSessionState(),
        sessions_dir=sessions_dir,
        index_store=index,
        session_store=store,
    )


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


@pytest.mark.asyncio
async def test_session_search_returns_chunk_cited_hit(tmp_path: Path) -> None:
    """session_search must return chunk-cited hits with the expected field set.

    Failure mode: missing start_line/end_line breaks agents that follow up with
    session_view(session_id, start_line, end_line) to read verbatim turns.
    """
    sessions_dir = tmp_path / "sessions"
    index, store = _make_stores(tmp_path)
    try:
        uuid8 = "aabb1122"
        path = _make_session_file(sessions_dir, uuid8, "sesquniquetoken8z discussion here")
        store.index_session(path)

        deps = _make_deps(tmp_path, index, store, sessions_dir)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await session_search(ctx, query="sesquniquetoken8z")

        results = result.metadata.get("results", [])
        assert results, "expected at least one session hit"
        r = results[0]
        for field in (
            "session_id",
            "when",
            "source",
            "chunk_text",
            "start_line",
            "end_line",
            "score",
        ):
            assert field in r, f"missing field {field!r} in hit: {r}"
        assert "channel" not in r, f"hit must not carry 'channel' field: {r}"
    finally:
        index.close()


@pytest.mark.asyncio
async def test_session_search_empty_query_returns_browse_metadata(tmp_path: Path) -> None:
    """Empty-query session_search must return session metadata without running FTS.

    Failure mode: empty query falls through to FTS and returns empty or errors
    when there are sessions to browse.
    """
    sessions_dir = tmp_path / "sessions"
    index, store = _make_stores(tmp_path)
    try:
        path = _make_session_file(sessions_dir, "ccdd3344", "some past session content")
        store.index_session(path)

        deps = _make_deps(tmp_path, index, store, sessions_dir)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await session_search(ctx, query="")

        results = result.metadata.get("results", [])
        assert results, "expected at least one browse result"
        r = results[0]
        assert "session_id" in r, f"browse result must include session_id: {r}"
        assert "when" in r, f"browse result must include when: {r}"
        assert "channel" not in r, f"browse result must not carry 'channel': {r}"
    finally:
        index.close()


@pytest.mark.asyncio
async def test_session_search_excludes_current_session(tmp_path: Path) -> None:
    """session_search must not return the current session in results.

    Failure mode: current session appears in recall results → agent recalls its
    own in-progress conversation as a past session hit.
    """
    sessions_dir = tmp_path / "sessions"
    index, store = _make_stores(tmp_path)
    try:
        current_uuid8 = "current1"
        other_uuid8 = "other111"
        shared_token = "sharedtokenxq7v"

        current_path = _make_session_file(
            sessions_dir, current_uuid8, f"{shared_token} current session"
        )
        other_path = _make_session_file(sessions_dir, other_uuid8, f"{shared_token} other session")
        store.index_session(current_path)
        store.index_session(other_path)

        # CoSessionState with current session path set
        session_state = CoSessionState()
        session_state.session_path = current_path

        deps = CoDeps(
            shell=ShellBackend(),
            config=_TEST_SETTINGS,
            session=session_state,
            sessions_dir=sessions_dir,
            index_store=index,
            session_store=store,
        )
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await session_search(ctx, query=shared_token)

        results = result.metadata.get("results", [])
        returned_ids = [r["session_id"] for r in results]
        assert current_uuid8 not in returned_ids, (
            f"current session {current_uuid8!r} must be excluded from results: {returned_ids}"
        )
    finally:
        index.close()


@pytest.mark.asyncio
async def test_session_search_per_query_cap_honoured(tmp_path: Path) -> None:
    """session_search must cap unique sessions at _SESSIONS_CHANNEL_CAP.

    Failure mode: unbounded session results flood the context and exceed
    the cap that keeps recall focused.
    """
    sessions_dir = tmp_path / "sessions"
    index, store = _make_stores(tmp_path)
    try:
        cap_token = "captoken_mk9r"
        for i in range(_SESSIONS_CHANNEL_CAP + 2):
            uuid8 = f"sess{i:04d}"
            path = _make_session_file(sessions_dir, uuid8, f"{cap_token} session number {i}")
            store.index_session(path)

        deps = _make_deps(tmp_path, index, store, sessions_dir)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await session_search(ctx, query=cap_token)

        results = result.metadata.get("results", [])
        unique_ids = {r["session_id"] for r in results}
        assert len(unique_ids) <= _SESSIONS_CHANNEL_CAP, (
            f"unique sessions must be capped at {_SESSIONS_CHANNEL_CAP}, got {len(unique_ids)}"
        )
    finally:
        index.close()
