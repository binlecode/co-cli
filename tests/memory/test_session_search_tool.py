"""Tests for unified memory_search — T2 artifact and T1 session result tiers."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS as _CONFIG
from tests._settings import make_settings

from co_cli.agent.core import build_agent
from co_cli.deps import CoDeps
from co_cli.memory.knowledge_store import KnowledgeStore
from co_cli.memory.session import session_filename
from co_cli.memory.session_store import SessionStore
from co_cli.memory.transcript import append_messages
from co_cli.tools.memory.recall import memory_search
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=_CONFIG)
_MODEL = _AGENT.model


def _make_ctx(
    tmp_path: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
    session_store: SessionStore | None = None,
    knowledge_dir: Path | None = None,
) -> RunContext:
    return RunContext(
        deps=CoDeps(
            shell=ShellBackend(),
            config=_CONFIG,
            knowledge_store=knowledge_store,
            session_store=session_store,
            knowledge_dir=knowledge_dir or tmp_path / "knowledge",
            sessions_dir=tmp_path / "sessions",
            tool_results_dir=tmp_path / "tool-results",
        ),
        model=_MODEL,
        usage=RunUsage(),
        tool_name="memory_search",
    )


def _write_artifact(knowledge_dir: Path, idx: int, content: str, kind: str = "preference") -> Path:
    """Write a minimal knowledge artifact file."""
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    created = datetime.now(UTC).isoformat()
    slug = content[:30].lower().replace(" ", "-")
    filename = f"{idx:03d}-{slug}.md"
    fm = {
        "id": str(idx),
        "kind": "knowledge",
        "artifact_kind": kind,
        "created": created,
        "tags": [],
    }
    path = knowledge_dir / filename
    path.write_text(f"---\n{yaml.dump(fm)}---\n\n{content}\n", encoding="utf-8")
    return path


def _write_indexed_session(
    sessions_dir: Path,
    store: SessionStore,
    *,
    name_suffix: str,
    content: str,
    response: str,
) -> str:
    """Write a session JSONL and index it; return the session_id prefix."""
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
async def test_browse_mode_results_carry_tier_sessions(tmp_path: Path) -> None:
    """Browse mode (empty query) results each carry tier='sessions'."""
    db_path = tmp_path / "session-index.db"
    store = SessionStore(db_path)
    try:
        _write_indexed_session(
            tmp_path / "sessions",
            store,
            name_suffix="tierbrowse1",
            content="python async context manager patterns",
            response="Here is how to use async context managers",
        )
        ctx = _make_ctx(tmp_path, session_store=store)
        result = await memory_search(ctx, query="")

        assert result.metadata is not None
        assert result.metadata["count"] >= 1
        first = result.metadata["results"][0]
        assert first["tier"] == "sessions"
        assert "summary" not in first
    finally:
        store.close()


@pytest.mark.asyncio
async def test_artifact_query_returns_tier_artifacts_via_grep(tmp_path: Path) -> None:
    """Non-empty query returns T2 artifact results with tier='artifacts' via grep fallback."""
    knowledge_dir = tmp_path / "knowledge"
    _write_artifact(knowledge_dir, 1, "xyloquartz-t2-grep-unique preferred test runner pytest")
    ctx = _make_ctx(tmp_path, knowledge_dir=knowledge_dir)

    result = await memory_search(ctx, query="xyloquartz-t2-grep-unique")

    assert result.metadata is not None
    assert result.metadata["count"] >= 1
    artifact_results = [r for r in result.metadata["results"] if r["tier"] == "artifacts"]
    assert len(artifact_results) >= 1
    first = artifact_results[0]
    assert first["tier"] == "artifacts"
    assert "slug" in first
    assert "path" in first


@pytest.mark.asyncio
async def test_artifact_query_returns_tier_artifacts_via_fts(tmp_path: Path) -> None:
    """Non-empty query returns T2 artifact results with tier='artifacts' via FTS5."""
    fts_cfg = make_settings(
        knowledge=make_settings().knowledge.model_copy(update={"search_backend": "fts5"})
    )
    idx = KnowledgeStore(config=fts_cfg, knowledge_db_path=tmp_path / "search.db")
    try:
        knowledge_dir = tmp_path / "knowledge"
        _write_artifact(knowledge_dir, 1, "xyloquartz-t2-fts-unique preferred runner pytest")
        idx.sync_dir("knowledge", knowledge_dir)

        ctx = RunContext(
            deps=CoDeps(
                shell=ShellBackend(),
                config=fts_cfg,
                knowledge_store=idx,
                knowledge_dir=knowledge_dir,
                sessions_dir=tmp_path / "sessions",
                tool_results_dir=tmp_path / "tool-results",
            ),
            model=_MODEL,
            usage=RunUsage(),
            tool_name="memory_search",
        )

        result = await memory_search(ctx, query="xyloquartz-t2-fts-unique")

        assert result.metadata is not None
        artifact_results = [r for r in result.metadata["results"] if r["tier"] == "artifacts"]
        assert len(artifact_results) >= 1
        first = artifact_results[0]
        assert first["tier"] == "artifacts"
        assert "slug" in first
        assert first["score"] > 0.0, "FTS results must have non-zero score"
    finally:
        idx.close()


@pytest.mark.asyncio
async def test_kind_filter_narrows_artifact_results(tmp_path: Path) -> None:
    """kind parameter filters T2 artifact results — wrong kind returns empty."""
    knowledge_dir = tmp_path / "knowledge"
    _write_artifact(knowledge_dir, 1, "xyloquartz-kind-filter-unique value", kind="preference")
    ctx = _make_ctx(tmp_path, knowledge_dir=knowledge_dir)

    result = await memory_search(ctx, query="xyloquartz-kind-filter-unique", kind="rule")

    artifact_results = [r for r in (result.metadata["results"] or []) if r["tier"] == "artifacts"]
    assert len(artifact_results) == 0, "kind='rule' must filter out preference artifacts"


@pytest.mark.asyncio
async def test_no_matching_results_returns_count_zero(tmp_path: Path) -> None:
    """No matching T1 or T2 results returns count=0 and empty results list."""
    ctx = _make_ctx(tmp_path)
    result = await memory_search(ctx, query="zzz-no-match-ever-xyloquartz-unique-9999")

    assert result.metadata is not None
    assert result.metadata["count"] == 0
    assert result.metadata["results"] == []


def test_memory_search_is_always_visible() -> None:
    """memory_search must be registered as ALWAYS visibility in the native toolset."""
    from co_cli.agent._native_toolset import _build_native_toolset
    from co_cli.config.core import settings

    _, index = _build_native_toolset(settings)
    assert "memory_search" in index, "memory_search must be in native tool index"
    assert "knowledge_search" not in index, "knowledge_search must not exist in tool index"
