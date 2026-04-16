"""Tests for transcript mining (TASK-5.4).

Covers ``_build_dream_window`` and ``_chunk_dream_window`` pure logic plus
``_mine_transcripts`` end-to-end behavior. The integration test at the end
runs a real LLM call through the dream miner agent (``@pytest.mark.local``).
"""

from __future__ import annotations

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
from tests._settings import make_settings
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.context.transcript import append_messages
from co_cli.deps import CoDeps
from co_cli.knowledge._dream import (
    DreamState,
    _build_dream_window,
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


def _tool_resp(name: str, content: str, model_name: str = "test-model") -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=name, args={"arg": "value"})], model_name=model_name
    )


def _tool_return(name: str, content: str) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id="x")])


# ---------------------------------------------------------------------------
# _build_dream_window — pure logic
# ---------------------------------------------------------------------------


def test_build_dream_window_interleaves_text_and_tool_in_order() -> None:
    messages = [
        _req("First user line"),
        _resp("First assistant line"),
        _tool_resp("search", "query"),
        _tool_return("search", "short result."),
        _resp("Follow-up assistant line"),
    ]

    window = _build_dream_window(messages)

    lines = window.split("\n")
    assert lines[0].startswith("User:")
    assert lines[1].startswith("Co:")
    assert lines[2].startswith("Tool(search):")
    assert lines[3].startswith("Tool result (search):")
    assert lines[4].startswith("Co:")


def test_build_dream_window_applies_independent_caps() -> None:
    messages: list = []
    for i in range(60):
        messages.append(_req(f"user-line-{i}"))
    for i in range(60):
        messages.append(_tool_resp(f"tool-{i}", "args"))

    window = _build_dream_window(messages, max_text=50, max_tool=50)
    lines = window.split("\n")

    assert len(lines) == 100
    assert sum(1 for line in lines if line.startswith("User:")) == 50
    assert sum(1 for line in lines if line.startswith("Tool(")) == 50


def test_build_dream_window_empty_messages_returns_empty_string() -> None:
    assert _build_dream_window([]) == ""


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


def test_mine_session_save_cap_is_programmatic_five() -> None:
    """Plan TASK-5.4 mandates a programmatic (not prompt-only) save cap.

    The counter lives inside ``_mine_transcripts``; this tripwire guards against
    accidental drift in either the constant value or the source-line location of
    the between-chunks break.
    """
    import inspect

    from co_cli.knowledge import _dream

    assert _dream._MAX_MINE_SAVES_PER_SESSION == 5

    source = inspect.getsource(_dream._mine_transcripts)
    assert "_MAX_MINE_SAVES_PER_SESSION" in source, (
        "_mine_transcripts must reference the per-session save cap constant"
    )
    assert "break" in source, "_mine_transcripts must break out of the chunk loop at cap"


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
