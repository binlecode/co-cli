"""Tests for unified memory_search — artifact and session result channels."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS as _CONFIG
from tests._settings import make_settings

from co_cli.agent.core import build_agent
from co_cli.deps import CoDeps
from co_cli.memory.knowledge_store import KnowledgeStore
from co_cli.memory.session import session_filename
from co_cli.memory.transcript import append_messages
from co_cli.tools.memory.recall import memory_search
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=_CONFIG)
_MODEL = _AGENT.model

# Unique phrase only present in fixture sessions — used across session tests.
_SESSION_PHRASE = "zyloquartz-session-unique-probe"


def _make_ctx(
    tmp_path: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
    knowledge_dir: Path | None = None,
    sessions_dir: Path | None = None,
) -> RunContext:
    return RunContext(
        deps=CoDeps(
            shell=ShellBackend(),
            config=_CONFIG,
            knowledge_store=knowledge_store,
            knowledge_dir=knowledge_dir or tmp_path / "knowledge",
            sessions_dir=sessions_dir or tmp_path / "sessions",
            tool_results_dir=tmp_path / "tool-results",
        ),
        model=_MODEL,
        usage=RunUsage(),
        tool_name="memory_search",
    )


def _make_fts_store(tmp_path: Path) -> KnowledgeStore:
    fts_cfg = make_settings(
        knowledge=make_settings().knowledge.model_copy(update={"search_backend": "fts5"})
    )
    return KnowledgeStore(config=fts_cfg, knowledge_db_path=tmp_path / "search.db")


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
    }
    path = knowledge_dir / filename
    path.write_text(f"---\n{yaml.dump(fm)}---\n\n{content}\n", encoding="utf-8")
    return path


def _write_indexed_session(
    sessions_dir: Path,
    store: KnowledgeStore,
    *,
    name_suffix: str,
    content: str,
    response: str,
    offset_days: int = 0,
) -> str:
    """Write a session JSONL and index it into the KnowledgeStore; return uuid8."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime(2026, 4, 14 + offset_days, 10, 0, 0, tzinfo=UTC)
    name = session_filename(ts, f"{name_suffix}-0000-0000-0000-000000000000")
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


def _write_session_file(
    sessions_dir: Path,
    *,
    name_suffix: str,
    content: str,
    response: str,
) -> Path:
    """Write a session JSONL file (no indexing) for browse-mode tests."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
    name = session_filename(ts, f"{name_suffix}-0000-0000-0000-000000000000")
    path = sessions_dir / name
    append_messages(
        path,
        [
            ModelRequest(parts=[UserPromptPart(content=content)]),
            ModelResponse(parts=[TextPart(content=response)], model_name="m"),
        ],
    )
    return path


# ---------------------------------------------------------------------------
# Browse mode (empty query)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_mode_results_carry_channel_sessions(tmp_path: Path) -> None:
    """Browse mode (empty query) results each carry channel='sessions'."""
    sessions_dir = tmp_path / "sessions"
    _write_session_file(
        sessions_dir,
        name_suffix="browsebrowse1",
        content="python async context manager patterns",
        response="Here is how to use async context managers",
    )
    ctx = _make_ctx(tmp_path, sessions_dir=sessions_dir)
    result = await memory_search(ctx, query="")

    assert result.metadata is not None
    assert result.metadata["count"] >= 1
    first = result.metadata["results"][0]
    assert first["channel"] == "sessions"
    assert "summary" not in first


# ---------------------------------------------------------------------------
# Artifact channel tests (unchanged from pre-delivery)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_query_returns_channel_artifacts_via_grep(tmp_path: Path) -> None:
    """Non-empty query returns artifact results with channel='artifacts' via grep fallback."""
    knowledge_dir = tmp_path / "knowledge"
    _write_artifact(knowledge_dir, 1, "xyloquartz-grep-unique preferred test runner pytest")
    ctx = _make_ctx(tmp_path, knowledge_dir=knowledge_dir)

    result = await memory_search(ctx, query="xyloquartz-grep-unique")

    assert result.metadata is not None
    assert result.metadata["count"] >= 1
    artifact_results = [r for r in result.metadata["results"] if r["channel"] == "artifacts"]
    assert len(artifact_results) >= 1
    first = artifact_results[0]
    assert first["channel"] == "artifacts"
    assert "slug" in first
    assert "path" in first


@pytest.mark.asyncio
async def test_artifact_query_returns_channel_artifacts_via_fts(tmp_path: Path) -> None:
    """Non-empty query returns artifact results with channel='artifacts' via FTS5."""
    store = _make_fts_store(tmp_path)
    try:
        knowledge_dir = tmp_path / "knowledge"
        _write_artifact(knowledge_dir, 1, "xyloquartz-fts-unique preferred runner pytest")
        store.sync_dir("knowledge", knowledge_dir)

        ctx = RunContext(
            deps=CoDeps(
                shell=ShellBackend(),
                config=make_settings(
                    knowledge=make_settings().knowledge.model_copy(
                        update={"search_backend": "fts5"}
                    )
                ),
                knowledge_store=store,
                knowledge_dir=knowledge_dir,
                sessions_dir=tmp_path / "sessions",
                tool_results_dir=tmp_path / "tool-results",
            ),
            model=_MODEL,
            usage=RunUsage(),
            tool_name="memory_search",
        )

        result = await memory_search(ctx, query="xyloquartz-fts-unique")

        assert result.metadata is not None
        artifact_results = [r for r in result.metadata["results"] if r["channel"] == "artifacts"]
        assert len(artifact_results) >= 1
        first = artifact_results[0]
        assert first["channel"] == "artifacts"
        assert "slug" in first
        assert first["score"] > 0.0, "FTS results must have non-zero score"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_kind_filter_narrows_artifact_results(tmp_path: Path) -> None:
    """kind parameter filters artifact results — wrong kind returns empty."""
    knowledge_dir = tmp_path / "knowledge"
    _write_artifact(knowledge_dir, 1, "xyloquartz-kind-filter-unique value", kind="preference")
    ctx = _make_ctx(tmp_path, knowledge_dir=knowledge_dir)

    result = await memory_search(ctx, query="xyloquartz-kind-filter-unique", kind="rule")

    artifact_results = [
        r for r in (result.metadata["results"] or []) if r["channel"] == "artifacts"
    ]
    assert len(artifact_results) == 0, "kind='rule' must filter out preference artifacts"


@pytest.mark.asyncio
async def test_no_matching_results_returns_count_zero(tmp_path: Path) -> None:
    """No matching session or artifact results returns count=0 and empty results list."""
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


# ---------------------------------------------------------------------------
# Session channel tests (chunked path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_search_returns_chunk_citations(tmp_path: Path) -> None:
    """Session search returns chunk_text, start_line, end_line, score — no summary field."""
    store = _make_fts_store(tmp_path)
    sessions_dir = tmp_path / "sessions"
    try:
        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="citphrse01",
            content=f"Discussing {_SESSION_PHRASE} in depth",
            response=f"Result about {_SESSION_PHRASE}",
        )
        ctx = _make_ctx(tmp_path, knowledge_store=store, sessions_dir=sessions_dir)

        result = await memory_search(ctx, query=_SESSION_PHRASE)

        assert result.metadata is not None
        session_hits = [r for r in result.metadata["results"] if r["channel"] == "sessions"]
        assert len(session_hits) >= 1, "Expected at least one session hit"
        hit = session_hits[0]
        assert "chunk_text" in hit, "chunk_text must be present"
        assert "start_line" in hit, "start_line must be present"
        assert "end_line" in hit, "end_line must be present"
        assert "score" in hit, "score must be present"
        assert "summary" not in hit, "summary must not be present on chunked results"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_session_search_records_span_attribute(tmp_path: Path) -> None:
    """_search_sessions records memory.sessions.count on the active OTel span."""
    store = _make_fts_store(tmp_path)
    sessions_dir = tmp_path / "sessions"
    try:
        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="spanattr01",
            content=f"Span attribute test {_SESSION_PHRASE}",
            response="ok",
        )
        ctx = _make_ctx(tmp_path, knowledge_store=store, sessions_dir=sessions_dir)

        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("test_mem_search"):
            await memory_search(ctx, query=_SESSION_PHRASE)

        spans = exporter.get_finished_spans()
        test_span = next(s for s in spans if s.name == "test_mem_search")
        attrs = dict(test_span.attributes or {})
        assert "memory.sessions.count" in attrs, f"span attrs: {attrs}"
        assert attrs["memory.sessions.count"] >= 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_session_search_no_llm_call(tmp_path: Path) -> None:
    """Session recall emits no LLM/pydantic_ai child spans — chunked path is synchronous."""
    store = _make_fts_store(tmp_path)
    sessions_dir = tmp_path / "sessions"
    try:
        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="nollmcall1",
            content=f"No LLM call test {_SESSION_PHRASE}",
            response="pure index lookup",
        )
        ctx = _make_ctx(tmp_path, knowledge_store=store, sessions_dir=sessions_dir)

        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("root_span"):
            await memory_search(ctx, query=_SESSION_PHRASE)

        span_names = [s.name for s in exporter.get_finished_spans()]
        llm_spans = [n for n in span_names if re.match(r"^pydantic_ai\.|^llm\.call", n)]
        assert llm_spans == [], f"LLM spans emitted on session recall path: {llm_spans}"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_session_cap_across_sessions(tmp_path: Path) -> None:
    """5 distinct sessions matching the query → exactly 3 results (cap=3)."""
    store = _make_fts_store(tmp_path)
    sessions_dir = tmp_path / "sessions"
    try:
        for i in range(5):
            _write_indexed_session(
                sessions_dir,
                store,
                name_suffix=f"c5xs{i:04d}",  # unique 8-char uuid8 prefix per session
                content=f"Session {i} about {_SESSION_PHRASE}",
                response="response",
                offset_days=i,
            )
        ctx = _make_ctx(tmp_path, knowledge_store=store, sessions_dir=sessions_dir)

        result = await memory_search(ctx, query=_SESSION_PHRASE)

        session_hits = [r for r in result.metadata["results"] if r["channel"] == "sessions"]
        assert len(session_hits) == 3, f"Expected 3 session hits (cap), got {len(session_hits)}"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_session_dedup_within_session(tmp_path: Path) -> None:
    """Multiple matching chunks from 1 session → exactly 1 session result."""
    store = _make_fts_store(tmp_path)
    sessions_dir = tmp_path / "sessions"
    try:
        # Long content forces multiple chunks; all mention the phrase
        long_content = f" {_SESSION_PHRASE}".join([f"paragraph {i}" for i in range(20)])
        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="dedup1sess",
            content=long_content,
            response=f"summary also {_SESSION_PHRASE}",
        )
        ctx = _make_ctx(tmp_path, knowledge_store=store, sessions_dir=sessions_dir)

        result = await memory_search(ctx, query=_SESSION_PHRASE)

        session_hits = [r for r in result.metadata["results"] if r["channel"] == "sessions"]
        assert len(session_hits) == 1, (
            f"Expected 1 deduped session result, got {len(session_hits)}"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_session_render_format(tmp_path: Path) -> None:
    """Session hits render as '[YYYY-MM-DD] <uuid8> @ L<start>-<end>' plus preview line."""
    store = _make_fts_store(tmp_path)
    sessions_dir = tmp_path / "sessions"
    try:
        _write_indexed_session(
            sessions_dir,
            store,
            name_suffix="renderfmt01",
            content=f"Render format test {_SESSION_PHRASE}",
            response="ok",
        )
        ctx = _make_ctx(tmp_path, knowledge_store=store, sessions_dir=sessions_dir)

        result = await memory_search(ctx, query=_SESSION_PHRASE)

        display = result.return_value or ""
        pattern = r"\[\d{4}-\d{2}-\d{2}\] \w{8} @ L\d+-\d+"
        assert re.search(pattern, display), (
            f"Expected '[YYYY-MM-DD] uuid8 @ L<n>-<m>' in display, got:\n{display}"
        )
    finally:
        store.close()
