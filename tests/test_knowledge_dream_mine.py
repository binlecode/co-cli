"""Tests for transcript-mining chunking and mining behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from tests._settings import make_settings
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.context.transcript import append_messages
from co_cli.deps import CoDeps
from co_cli.knowledge._dream import (
    DreamState,
    _chunk_dream_window,
    _mine_transcripts,
)
from co_cli.knowledge._store import KnowledgeStore
from co_cli.llm._factory import build_model
from co_cli.tools.shell_backend import ShellBackend


def _req(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _resp(text: str, model_name: str = "test-model") -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)], model_name=model_name)


# ---------------------------------------------------------------------------
# _chunk_dream_window — pure logic
# ---------------------------------------------------------------------------


def test_chunk_dream_window_returns_single_chunk_when_under_soft_limit() -> None:
    window = "a" * 8_000

    chunks = _chunk_dream_window(window)

    assert chunks == [window]


def test_chunk_dream_window_splits_large_window_with_overlap() -> None:
    window = "a" * 30_000

    chunks = _chunk_dream_window(window, soft_limit=16_000, chunk_size=12_000, overlap=2_000)

    assert len(chunks) >= 3
    assert all(len(chunk) <= 12_000 for chunk in chunks)
    concatenated = "".join(chunks)
    assert len(concatenated) > len(window)


def test_chunk_dream_window_handles_soft_limit_exactly() -> None:
    window = "x" * 16_000
    assert _chunk_dream_window(window, soft_limit=16_000) == [window]


# ---------------------------------------------------------------------------
# _mine_transcripts — skip, empty, missing-dir paths (no LLM needed)
# ---------------------------------------------------------------------------


def _make_deps(tmp_path: Path, *, with_model: bool) -> tuple[CoDeps, KnowledgeStore]:
    """Build a real CoDeps rooted at tmp_path. Model optional for non-LLM tests."""
    knowledge_dir = tmp_path / "knowledge"
    sessions_dir = tmp_path / "sessions"
    knowledge_dir.mkdir()
    sessions_dir.mkdir()
    config = make_settings()
    store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")
    model = build_model(config.llm) if with_model else None
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        knowledge_dir=knowledge_dir,
        sessions_dir=sessions_dir,
        knowledge_store=store,
        model=model,
    )
    return deps, store


@pytest.mark.asyncio
async def test_mine_returns_zero_when_sessions_dir_missing(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        deps.sessions_dir.rmdir()
        result = await _mine_transcripts(deps, DreamState())
        assert result == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_mine_skips_sessions_already_processed(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        session_path = deps.sessions_dir / "2026-04-16-T120000Z-aaaaaaaa.jsonl"
        append_messages(
            session_path,
            [_req("I always prefer pytest"), _resp("Got it.")],
        )
        state = DreamState(processed_sessions=[session_path.name])

        result = await _mine_transcripts(deps, state)

        assert result == 0
        assert state.processed_sessions == [session_path.name]
        assert list(deps.knowledge_dir.glob("*.md")) == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_mine_marks_empty_transcript_processed_without_llm(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        empty_path = deps.sessions_dir / "2026-04-16-T120000Z-bbbbbbbb.jsonl"
        empty_path.touch()
        state = DreamState()

        result = await _mine_transcripts(deps, state)

        assert result == 0
        assert state.processed_sessions == [empty_path.name]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# _mine_transcripts — live LLM integration (satisfies plan done_when)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.local
async def test_mine_extracts_artifacts_and_marks_sessions_processed(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=True)
    try:
        session_a = deps.sessions_dir / "2026-04-16-T110000Z-aaaaaaaa.jsonl"
        session_b = deps.sessions_dir / "2026-04-16-T120000Z-bbbbbbbb.jsonl"

        append_messages(
            session_a,
            [
                _req(
                    "Quick note for future sessions: I always prefer pytest for testing. "
                    "No unittest, no nose, no mocks either."
                ),
                _resp("Understood — I'll default to pytest and avoid mocking libraries."),
                _req(
                    "Yes, and I consistently reject any test that uses unittest.mock — "
                    "we've been bitten by mock/prod divergence twice this quarter."
                ),
                _resp("Acknowledged. Real dependencies only in tests."),
            ],
        )
        append_messages(
            session_b,
            [
                _req(
                    "Heads up: all our pipeline bugs are tracked in the Linear project "
                    "called INGEST. Use that when you want ticket context."
                ),
                _resp("Noted — I'll reference the INGEST Linear project for pipeline work."),
            ],
        )

        state = DreamState()

        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 3):
            extracted = await _mine_transcripts(deps, state)

        assert extracted >= 1
        saved_files = list(deps.knowledge_dir.glob("*.md"))
        assert len(saved_files) >= 1

        assert session_a.name in state.processed_sessions
        assert session_b.name in state.processed_sessions

        state_snapshot = list(state.processed_sessions)
        before_files = set(deps.knowledge_dir.glob("*.md"))

        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
            second_extracted = await _mine_transcripts(deps, state)

        assert second_extracted == 0
        assert state.processed_sessions == state_snapshot
        assert set(deps.knowledge_dir.glob("*.md")) == before_files
    finally:
        store.close()
