"""Tests for session_search — file-based lexical (ripgrep) recall over transcripts.

The store is file-based: no IndexStore, no chunk pipeline, no embeddings. These
tests exercise the real ripgrep path (or its Python fallback) over JSONL files
written to a temp CO_HOME, asserting the agent-facing tool contract is stable.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.session._search import search_sessions
from co_cli.session.filename import session_filename
from co_cli.session.store import SessionStore
from co_cli.tools.session.recall import _SESSIONS_CHANNEL_CAP, session_search
from co_cli.tools.shell_backend import ShellBackend

_SESSION_CREATED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_session_file(sessions_dir: Path, uuid8: str, content: str) -> Path:
    """Create a minimal pydantic-ai JSONL session file with a user-prompt turn."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / session_filename(_SESSION_CREATED_AT, uuid8)
    line = json.dumps([{"parts": [{"part_kind": "user-prompt", "content": content}]}])
    path.write_text(line + "\n", encoding="utf-8")
    return path


def _make_store(sessions_dir: Path) -> SessionStore:
    return SessionStore(config=SETTINGS, sessions_dir=sessions_dir)


def _make_deps(
    sessions_dir: Path, store: SessionStore, current_path: Path | None = None
) -> CoDeps:
    session_state = CoSessionState()
    if current_path is not None:
        session_state.session_path = current_path
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=session_state,
        sessions_dir=sessions_dir,
        session_store=store,
    )


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


@pytest.mark.asyncio
async def test_tool_keyword_search_returns_readable_line_cited_hit(tmp_path: Path) -> None:
    """Keyword search returns the right session id, the matched line, and a readable snippet.

    Failure mode: a filesystem path instead of the uuid8 session_id, a wrong/absent
    line citation, or a JSON-escaped snippet breaks the session_view follow-up.
    """
    sessions_dir = tmp_path / "sessions"
    phrase = "the rate limiter regression we discussed"
    _make_session_file(sessions_dir, "aabb1122", f"Earlier note: {phrase} yesterday.")
    _make_session_file(sessions_dir, "ccdd3344", "an unrelated session about deploy config")
    store = _make_store(sessions_dir)
    deps = _make_deps(sessions_dir, store)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_search(ctx, query=phrase)

    results = result.metadata.get("results", [])
    assert results, "expected a keyword hit for the seeded phrase"
    hit = results[0]
    assert hit["session_id"] == "aabb1122"
    assert hit["start_line"] == hit["end_line"] == 1
    assert phrase in hit["chunk_text"]
    assert "\\u" not in hit["chunk_text"], f"snippet must be readable, not JSON-escaped: {hit!r}"


@pytest.mark.asyncio
async def test_tool_skips_structural_json_key_match(tmp_path: Path) -> None:
    """A query occurring only as a transcript JSON key (part_kind) returns no session.

    Failure mode: structural-key noise surfaces arbitrary JSON as a hit instead of
    being dropped — the agent gets a meaningless session with no readable snippet.
    """
    sessions_dir = tmp_path / "sessions"
    _make_session_file(sessions_dir, "aabb1122", "a normal conversation about caching")
    store = _make_store(sessions_dir)
    deps = _make_deps(sessions_dir, store)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_search(ctx, query="part_kind")

    assert result.metadata.get("results", []) == []


def _make_tool_call_session_file(
    sessions_dir: Path, uuid8: str, tool_name: str, args: dict
) -> Path:
    """Create a session JSONL file whose only content is one tool-call part."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / session_filename(_SESSION_CREATED_AT, uuid8)
    line = json.dumps(
        [{"parts": [{"part_kind": "tool-call", "tool_name": tool_name, "args": json.dumps(args)}]}]
    )
    path.write_text(line + "\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_tool_recalls_term_only_in_tool_call_args(tmp_path: Path) -> None:
    """session_search recalls a session when the term occurs only in tool-call args.

    Behavior: the agent stored a fact via a tool (knowledge_manage) and never typed
    it in prose. Searching that term must still surface the past session with a line
    citation — agent-synthesized tool inputs are recallable, not lost.
    """
    sessions_dir = tmp_path / "sessions"
    _make_tool_call_session_file(
        sessions_dir,
        "aabb1122",
        "knowledge_manage",
        {"action": "create", "content": "User's deploy ID is DEPLOY_77."},
    )
    store = _make_store(sessions_dir)
    deps = _make_deps(sessions_dir, store)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_search(ctx, query="DEPLOY_77")

    results = result.metadata.get("results", [])
    assert results, "term occurring only in tool-call args must be recalled"
    hit = results[0]
    assert hit["session_id"] == "aabb1122"
    assert hit["start_line"] == hit["end_line"] == 1
    assert "DEPLOY_77" in hit["chunk_text"], f"snippet must surface the arg value: {hit!r}"


@pytest.mark.asyncio
async def test_tool_empty_query_browses_recent_sessions(tmp_path: Path) -> None:
    """Empty-query session_search returns recent-session metadata."""
    sessions_dir = tmp_path / "sessions"
    _make_session_file(sessions_dir, "ccdd3344", "some past session content")
    store = _make_store(sessions_dir)
    deps = _make_deps(sessions_dir, store)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_search(ctx, query="")

    results = result.metadata.get("results", [])
    assert results, "expected at least one browse result"
    assert results[0]["session_id"] == "ccdd3344"
    assert results[0]["when"] == "2026-01-01"


@pytest.mark.asyncio
async def test_tool_excludes_current_session(tmp_path: Path) -> None:
    """session_search must not return the current in-progress session."""
    sessions_dir = tmp_path / "sessions"
    shared_token = "sharedtokenxq7v"
    current_path = _make_session_file(sessions_dir, "current1", f"{shared_token} current session")
    _make_session_file(sessions_dir, "other111", f"{shared_token} other session")
    store = _make_store(sessions_dir)
    deps = _make_deps(sessions_dir, store, current_path=current_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_search(ctx, query=shared_token)

    returned_ids = [r["session_id"] for r in result.metadata.get("results", [])]
    assert "current1" not in returned_ids, f"current session must be excluded, got {returned_ids}"
    assert "other111" in returned_ids, "the other session should still be recalled"


@pytest.mark.asyncio
async def test_tool_keyword_search_caps_unique_sessions(tmp_path: Path) -> None:
    """session_search caps unique sessions at _SESSIONS_CHANNEL_CAP."""
    sessions_dir = tmp_path / "sessions"
    cap_token = "captoken_mk9r"
    for i in range(_SESSIONS_CHANNEL_CAP + 2):
        _make_session_file(sessions_dir, f"sess{i:04d}", f"{cap_token} session number {i}")
    store = _make_store(sessions_dir)
    deps = _make_deps(sessions_dir, store)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_search(ctx, query=cap_token)

    unique_ids = {r["session_id"] for r in result.metadata.get("results", [])}
    assert len(unique_ids) == _SESSIONS_CHANNEL_CAP


@pytest.mark.asyncio
async def test_tool_no_match_returns_empty_message(tmp_path: Path) -> None:
    """A keyword with no match returns the empty-result message, not an error."""
    sessions_dir = tmp_path / "sessions"
    _make_session_file(sessions_dir, "aabb1122", "a session about something else entirely")
    store = _make_store(sessions_dir)
    deps = _make_deps(sessions_dir, store)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_search(ctx, query="nonexistent_phrase_zzz9")

    assert result.metadata.get("results", []) == []


_FLIGHT_CODE_PATTERN = r"\b[A-Z]{2}\d{2,4}\b"


def test_engine_regex_bridges_vocabulary_mismatch(tmp_path: Path) -> None:
    """Regex mode finds a shaped entity the literal user-vocabulary query misses.

    The session records only "AA890 delayed" — never the word "flight". A literal
    `flight` query is a false negative; the structural pattern bridges the gap.
    A malformed pattern returns an explicit error, never an empty "no results"
    and never a raise or a fallthrough into the line-scan.
    """
    sessions_dir = tmp_path / "sessions"
    _make_session_file(sessions_dir, "aabb1122", "Booking confirmed: AA890 delayed two hours.")

    regex = search_sessions(sessions_dir, _FLIGHT_CODE_PATTERN, limit=3, is_regex=True)
    assert regex.error is None
    assert regex.hits, "regex pattern must surface the shaped flight code"
    assert "AA890" in regex.hits[0].snippet

    literal = search_sessions(sessions_dir, "flight", limit=3)
    assert literal.error is None
    assert literal.hits == [], "literal query on absent vocabulary must miss"

    malformed = search_sessions(sessions_dir, "[unterminated", limit=3, is_regex=True)
    assert malformed.hits == []
    assert malformed.error is not None, "malformed regex must yield an explicit error"


@pytest.mark.asyncio
async def test_tool_pattern_mode_returns_flight_code_hit(tmp_path: Path) -> None:
    """Tool-level pattern= mode returns the shaped entity as a line-cited hit."""
    sessions_dir = tmp_path / "sessions"
    _make_session_file(sessions_dir, "aabb1122", "Booking confirmed: AA890 delayed two hours.")
    store = _make_store(sessions_dir)
    deps = _make_deps(sessions_dir, store)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_search(ctx, pattern=_FLIGHT_CODE_PATTERN)

    results = result.metadata.get("results", [])
    assert results, "pattern mode must surface the flight-code session"
    assert results[0]["session_id"] == "aabb1122"
    assert "AA890" in results[0]["chunk_text"]


@pytest.mark.asyncio
async def test_tool_query_and_pattern_mutually_exclusive(tmp_path: Path) -> None:
    """Supplying both query and pattern returns a tool_error, not an implicit choice."""
    sessions_dir = tmp_path / "sessions"
    _make_session_file(sessions_dir, "aabb1122", "some content")
    store = _make_store(sessions_dir)
    deps = _make_deps(sessions_dir, store)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await session_search(ctx, query="x", pattern="y")

    assert result.metadata.get("error") is True
