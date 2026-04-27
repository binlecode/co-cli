"""Tests for memory_search LLM summarization path — real model, real FTS5 search."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS as _CONFIG
from tests._settings import TEST_LLM
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

from co_cli.agent._core import build_agent
from co_cli.deps import CoDeps
from co_cli.llm._factory import build_model
from co_cli.memory.session import session_filename
from co_cli.memory.store import MemoryIndex
from co_cli.memory.transcript import append_messages
from co_cli.tools.memory import memory_search
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


def _write_indexed_session(
    sessions_dir: Path,
    store: MemoryIndex,
    *,
    name_suffix: str,
    content: str,
    response: str,
) -> str:
    """Write a session JSONL, index it, return the uuid8 session_id."""
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
async def test_memory_search_summarizes_matching_session(tmp_path: Path) -> None:
    """memory_search with a keyword query returns LLM-generated summaries for matching sessions."""
    db_path = tmp_path / "session-index.db"
    store = MemoryIndex(db_path)
    try:
        sessions_dir = tmp_path / "sessions"

        # Session about docker networking
        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="docker01",
            content="How do I configure docker bridge networking between containers?",
            response=(
                "You can configure docker bridge networking by creating a custom network "
                "with `docker network create mynet` and then attaching containers to it."
            ),
        )

        # Unrelated session — should not appear in results
        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="pytest01",
            content="How do pytest fixtures work with scope=session?",
            response="Session-scoped fixtures run once per test session and are shared.",
        )

        deps = CoDeps(
            shell=ShellBackend(),
            config=_CONFIG,
            memory_index=store,
            model=build_model(_CONFIG.llm),
            sessions_dir=sessions_dir,
            tool_results_dir=tmp_path / "tool-results",
        )
        ctx = _make_ctx(deps)

        await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
        async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS * len(["docker01"])):
            result = await memory_search(ctx, "docker networking", limit=1)

        assert result.metadata is not None
        assert result.metadata["count"] >= 1, (
            f"Expected at least 1 docker result, got {result.metadata['count']}"
        )

        results_list = result.metadata["results"]
        assert len(results_list) >= 1

        first = results_list[0]
        assert "summary" in first, "Search mode must return 'summary' in each result"
        summary = first["summary"]

        assert summary, "summary must be non-empty"
        assert not summary.startswith("[Raw preview"), (
            f"summary must come from LLM, not raw fallback. Got: {summary[:80]}"
        )

        summary_lower = summary.lower()
        assert "docker" in summary_lower or "network" in summary_lower, (
            f"Summary must mention docker or network. Got: {summary[:200]}"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_search_returns_session_id_and_when(tmp_path: Path) -> None:
    """Summarization results include session_id, when, source, and summary fields."""
    db_path = tmp_path / "session-index.db"
    store = MemoryIndex(db_path)
    try:
        sessions_dir = tmp_path / "sessions"

        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="meta0001",
            content="Explain how to set up docker compose networking",
            response="Docker compose creates a default network for all services in the file.",
        )

        deps = CoDeps(
            shell=ShellBackend(),
            config=_CONFIG,
            memory_index=store,
            model=build_model(_CONFIG.llm),
            sessions_dir=sessions_dir,
            tool_results_dir=tmp_path / "tool-results",
        )
        ctx = _make_ctx(deps)

        async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS * 2):
            result = await memory_search(ctx, "docker compose", limit=1)

        assert result.metadata is not None
        assert result.metadata["count"] >= 1

        entry = result.metadata["results"][0]
        assert "session_id" in entry
        assert "when" in entry
        assert "source" in entry
        assert "summary" in entry
        assert entry["session_id"], "session_id must be non-empty"
        assert entry["when"], "when must be non-empty"
    finally:
        store.close()
