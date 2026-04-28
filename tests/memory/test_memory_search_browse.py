"""Tests for memory_search empty-query browse mode — zero LLM cost."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS as _CONFIG

from co_cli.agent.core import build_agent
from co_cli.deps import CoDeps
from co_cli.memory.session import session_filename
from co_cli.memory.session_store import SessionStore
from co_cli.memory.transcript import append_messages
from co_cli.tools.memory.recall import memory_search
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=_CONFIG)
_MODEL = _AGENT.model


def _make_ctx(deps: CoDeps) -> RunContext:
    return RunContext(
        deps=deps,
        model=_MODEL,
        usage=RunUsage(),
        tool_name="memory_search",
    )


def _make_deps(tmp_path: Path, *, session_store: SessionStore | None = None) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=_CONFIG,
        session_store=session_store,
        sessions_dir=tmp_path / "sessions",
        tool_results_dir=tmp_path / "tool-results",
    )


def _write_indexed_session(
    sessions_dir: Path,
    store: SessionStore,
    *,
    name_suffix: str,
    content: str,
    response: str,
) -> str:
    """Write a session JSONL and index it; return the uuid8 session_id."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
    name = session_filename(now, f"{name_suffix}-0000-0000-0000-000000000000")
    path = sessions_dir / name
    append_messages(
        path,
        [
            ModelRequest(parts=[UserPromptPart(content=content)]),
            ModelResponse(parts=[TextPart(content=response)], model_name="m"),
        ],
    )
    store.index_session(path)
    return name_suffix[:8]


@pytest.mark.asyncio
async def test_browse_mode_returns_recent_sessions(tmp_path: Path) -> None:
    """Empty-query browse returns recent session metadata without making LLM calls."""
    db_path = tmp_path / "session-index.db"
    store = SessionStore(db_path)
    try:
        sessions_dir = tmp_path / "sessions"
        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="browse001",
            content="We worked on docker networking setup",
            response="Here are the docker networking steps",
        )

        deps = _make_deps(tmp_path, session_store=store)
        ctx = _make_ctx(deps)

        result = await memory_search(ctx, query="")

        assert result.return_value, "ToolReturn must have non-empty return_value"
        assert result.metadata is not None
        assert result.metadata["count"] >= 1, (
            f"Browse mode must return at least one session, got {result.metadata['count']}"
        )

        results_list = result.metadata["results"]
        assert len(results_list) >= 1

        first = results_list[0]
        assert "session_id" in first, "Browse results must include session_id"
        assert "when" in first, "Browse results must include when"
        assert "title" in first, "Browse results must include title"
        assert "file_size" in first, "Browse results must include file_size"
        assert first["tier"] == "sessions", "Browse results must carry tier='sessions'"

        # Browse mode does NOT produce LLM summaries
        assert "summary" not in first, (
            "Browse mode must not include 'summary' — that is a search-mode field"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_browse_mode_zero_llm_cost(tmp_path: Path) -> None:
    """Browse mode with deps.model=None succeeds — confirms no LLM is invoked."""
    db_path = tmp_path / "session-index.db"
    store = SessionStore(db_path)
    try:
        sessions_dir = tmp_path / "sessions"
        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="browse002",
            content="pytest fixture setup patterns",
            response="Here are common pytest patterns",
        )

        # deps.model is None — any LLM call would raise or return None
        deps = CoDeps(
            shell=ShellBackend(),
            config=_CONFIG,
            session_store=store,
            model=None,
            sessions_dir=tmp_path / "sessions",
            tool_results_dir=tmp_path / "tool-results",
        )
        ctx = _make_ctx(deps)

        result = await memory_search(ctx, query="")

        # Must succeed even with no model configured
        assert result.metadata is not None
        assert result.metadata["count"] >= 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_browse_mode_empty_sessions_returns_zero(tmp_path: Path) -> None:
    """Browse mode with no sessions returns count=0 and empty results list."""
    db_path = tmp_path / "session-index.db"
    store = SessionStore(db_path)
    try:
        deps = _make_deps(tmp_path, session_store=store)
        ctx = _make_ctx(deps)

        result = await memory_search(ctx, query="")

        assert result.metadata is not None
        assert result.metadata["count"] == 0
        assert result.metadata["results"] == []
    finally:
        store.close()
