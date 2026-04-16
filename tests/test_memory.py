"""Tests for memory gravity — touch and dedup on recall."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.knowledge._store import KnowledgeStore
from co_cli.tools.memory import (
    _recall_for_context,
    append_memory,
    list_memories,
    search_memories,
    update_memory,
)
from co_cli.tools.shell_backend import ShellBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=settings)


def _make_ctx(
    *,
    knowledge_dir: Path | None = None,
    knowledge_store: Any = None,
) -> RunContext:
    """Return a real RunContext with real CoDeps for memory tool tests."""
    config = make_settings()
    deps_kwargs: dict[str, Any] = {
        "shell": ShellBackend(),
        "knowledge_store": knowledge_store,
        "config": config,
    }
    if knowledge_dir is not None:
        deps_kwargs["knowledge_dir"] = knowledge_dir
    deps = CoDeps(**deps_kwargs)
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _write_memory(
    memory_dir: Path,
    memory_id: int,
    content: str,
    tags: list[str] | None = None,
    created: str | None = None,
    updated: str | None = None,
    artifact_kind: str = "preference",
) -> Path:
    """Write a canonical knowledge artifact file for testing."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    if created is None:
        created = datetime.now(UTC).isoformat()
    slug = content[:30].lower().replace(" ", "-")
    filename = f"{memory_id:03d}-{slug}.md"
    fm: dict[str, Any] = {
        "id": str(memory_id),
        "kind": "knowledge",
        "artifact_kind": artifact_kind,
        "created": created,
        "tags": tags or [],
    }
    if updated:
        fm["updated"] = updated
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _recall_for_context read-only invariant
# ---------------------------------------------------------------------------


def test_recall_does_not_mutate_files(tmp_path: Path):
    """_recall_for_context must not change any file's mtime (read-only path)."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    _write_memory(memory_dir, 1, "User prefers dark theme", tags=["preference"])

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", memory_dir)
        before = {str(p): p.stat().st_mtime for p in memory_dir.glob("*.md")}
        result = asyncio.run(
            _recall_for_context(
                _make_ctx(knowledge_dir=memory_dir, knowledge_store=idx), "dark theme"
            )
        )
        assert result.metadata["count"] >= 1
        after = {str(p): p.stat().st_mtime for p in memory_dir.glob("*.md")}
        assert before == after, "_recall_for_context must not modify any file's mtime"
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# list_memories pagination
# ---------------------------------------------------------------------------


def test_list_memories_pagination(tmp_path: Path):
    """list_memories returns correct pages with offset/limit."""
    memory_dir = tmp_path / "memory"
    for i in range(1, 6):
        _write_memory(memory_dir, i, f"Memory content number {i}", tags=["test"])

    ctx = _make_ctx(knowledge_dir=memory_dir)

    # Page 1: offset=0, limit=2
    r1 = asyncio.run(list_memories(ctx, offset=0, limit=2))
    assert r1.metadata["count"] == 2
    assert r1.metadata["total"] == 5
    assert r1.metadata["offset"] == 0
    assert r1.metadata["limit"] == 2
    assert r1.metadata["has_more"] is True
    assert r1.metadata["memories"][0]["id"] == "1"
    assert r1.metadata["memories"][1]["id"] == "2"

    # Page 2: offset=2, limit=2
    r2 = asyncio.run(list_memories(ctx, offset=2, limit=2))
    assert r2.metadata["count"] == 2
    assert r2.metadata["total"] == 5
    assert r2.metadata["has_more"] is True

    # Page 3: offset=4, limit=2 — partial last page
    r3 = asyncio.run(list_memories(ctx, offset=4, limit=2))
    assert r3.metadata["count"] == 1
    assert r3.metadata["total"] == 5
    assert r3.metadata["has_more"] is False


# ---------------------------------------------------------------------------
# update_memory — surgical text replacement
# ---------------------------------------------------------------------------


def test_update_memory_replaces_exact_match(tmp_path: Path):
    """update_memory replaces old_content with new_content in the body."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest over unittest", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    asyncio.run(update_memory(ctx, slug, "pytest over unittest", "pytest over all others"))

    updated_text = path.read_text(encoding="utf-8")
    assert "pytest over all others" in updated_text
    assert "pytest over unittest" not in updated_text


def test_update_memory_raises_not_found(tmp_path: Path):
    """update_memory raises FileNotFoundError for an unknown slug."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    ctx = _make_ctx(knowledge_dir=memory_dir)

    with pytest.raises(FileNotFoundError, match="not found"):
        asyncio.run(update_memory(ctx, "999-nonexistent", "old", "new"))


def test_update_memory_raises_zero_occurrences(tmp_path: Path):
    """update_memory raises ValueError when old_content is not in the body."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem

    with pytest.raises(ValueError, match="not found"):
        asyncio.run(update_memory(ctx, slug, "unittest", "mocha"))


def test_update_memory_raises_ambiguous(tmp_path: Path):
    """update_memory raises ValueError when old_content appears more than once."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User uses pytest. Also uses pytest.", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem

    with pytest.raises(ValueError, match="2 times"):
        asyncio.run(update_memory(ctx, slug, "pytest", "mocha"))


def test_update_memory_rejects_line_prefix(tmp_path: Path):
    """update_memory raises ValueError when old_content contains Read-tool line prefixes."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem

    # Simulate Read tool artifact: "1→ User prefers pytest"
    with pytest.raises(ValueError, match="line-number prefixes"):
        asyncio.run(update_memory(ctx, slug, "1\u2192 User prefers pytest", "new"))


def test_update_memory_tab_normalization(tmp_path: Path):
    """update_memory matches when the body uses spaces where old_content has a tab.

    expandtabs() normalises both sides before matching. "foo" is 3 chars so a tab
    at column 3 advances to column 8 (5 spaces): "foo\tbar" → "foo     bar".
    Body is written with those literal spaces; old_content uses the raw tab — they
    compare equal after normalisation.
    """
    memory_dir = tmp_path / "memory"
    # 5 spaces at column 3: what expandtabs() produces for "foo\tbar"
    path = _write_memory(memory_dir, 1, "foo     bar", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    # old_content uses a tab; after expandtabs() it matches the body
    asyncio.run(update_memory(ctx, slug, "foo\tbar", "replaced"))

    assert "replaced" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# append_memory — add content to end of body
# ---------------------------------------------------------------------------


def test_append_memory_adds_to_end(tmp_path: Path):
    """append_memory appends content as a new line at the end of the body."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    asyncio.run(append_memory(ctx, slug, "Also uses coverage reports."))

    updated_text = path.read_text(encoding="utf-8")
    assert updated_text.rstrip("\n").endswith("Also uses coverage reports.")
    assert "User prefers pytest" in updated_text


def test_append_memory_missing_slug_raises(tmp_path: Path):
    """append_memory raises FileNotFoundError for an unknown slug."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    ctx = _make_ctx(knowledge_dir=memory_dir)

    with pytest.raises(FileNotFoundError, match="not found"):
        asyncio.run(append_memory(ctx, "999-nonexistent", "extra line"))


# ---------------------------------------------------------------------------
# search_memories — dedicated memory search tool
# ---------------------------------------------------------------------------


def test_search_memories_finds_saved_memories(tmp_path: Path):
    """search_memories returns saved memories via FTS5 DB search."""
    memory_dir = tmp_path / "memory"

    _write_memory(
        memory_dir, 1, "User prefers xyloquartz-search-test framework", tags=["preference"]
    )
    _write_memory(
        memory_dir, 2, "User uses xyloquartz-search-test for all tests", tags=["context"]
    )

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", memory_dir)
        ctx = _make_ctx(knowledge_dir=memory_dir, knowledge_store=idx)

        result = asyncio.run(search_memories(ctx, "xyloquartz-search-test"))
        assert result.metadata["count"] >= 2
        assert all(r["source"] == "knowledge" for r in result.metadata["results"])
    finally:
        idx.close()


def test_search_memories_empty_query_returns_guard(tmp_path: Path):
    """search_memories with empty query returns guard message."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(search_memories(_make_ctx(knowledge_dir=memory_dir), "   "))
    assert result.metadata["count"] == 0
    assert "required" in result.return_value.lower()


# ---------------------------------------------------------------------------
# rag.backend OTel annotation
# ---------------------------------------------------------------------------


def test_rag_backend_annotation_on_search_spans(tmp_path: Path):
    """search_memories and search_knowledge stamp rag.backend on the active OTel span."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from co_cli.knowledge._store import KnowledgeStore
    from co_cli.tools.articles import search_knowledge

    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    _write_memory(knowledge_dir, 1, "rag-backend-annotation-fts-test", tags=["test"])
    _write_memory(
        knowledge_dir,
        2,
        "rag-backend-annotation-fts-test article",
        tags=["test"],
        artifact_kind="article",
    )

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", knowledge_dir)

        mem_ctx = _make_ctx(knowledge_dir=knowledge_dir, knowledge_store=idx)
        fts_know_ctx = RunContext(
            deps=CoDeps(
                shell=ShellBackend(),
                knowledge_store=idx,
                config=make_settings(
                    knowledge=make_settings().knowledge.model_copy(
                        update={"search_backend": "fts5"}
                    )
                ),
                knowledge_dir=knowledge_dir,
            ),
            model=_AGENT.model,
            usage=RunUsage(),
        )
        grep_know_ctx = RunContext(
            deps=CoDeps(
                shell=ShellBackend(),
                knowledge_store=None,
                config=make_settings(),
                knowledge_dir=knowledge_dir,
            ),
            model=_AGENT.model,
            usage=RunUsage(),
        )

        exporter = InMemorySpanExporter()
        _orig = otel_trace.get_tracer_provider()
        # add_span_processor has no corresponding remove in the OTel SDK; the processor
        # stays registered for the process lifetime, but the exporter goes out of scope
        # after this test so spans accumulate into a GC-eligible object only.
        _orig.add_span_processor(SimpleSpanProcessor(exporter))

        tracer = _orig.get_tracer("test.rag_backend")
        with tracer.start_as_current_span("execute_tool test") as parent_span:
            # (1) search_memories — FTS5 DB search
            asyncio.run(search_memories(mem_ctx, "rag-backend-annotation-fts-test"))
            assert parent_span.attributes.get("rag.backend") == "fts5"

            # (2) search_knowledge FTS path
            asyncio.run(search_knowledge(fts_know_ctx, "rag-backend-annotation-fts-test"))
            assert parent_span.attributes.get("rag.backend") in ("fts5", "hybrid")

            # (3) search_knowledge grep path
            asyncio.run(search_knowledge(grep_know_ctx, "rag-backend-annotation-fts-test"))
            assert parent_span.attributes.get("rag.backend") == "grep"
    finally:
        idx.close()

    assert otel_trace.get_tracer_provider() is _orig


# ---------------------------------------------------------------------------
# Self-contained personality role layout tests
# ---------------------------------------------------------------------------


def test_load_character_memories_from_system_path():
    """Character memories load from souls/{role}/memories/, not memory_dir."""
    from co_cli.prompts.personalities._loader import load_character_memories

    result = load_character_memories("finch")
    assert result.startswith("## Character")
    assert len(result) > 100


def test_load_soul_mindsets_from_role_path():
    """Mindsets load from souls/{role}/mindsets/."""
    from co_cli.prompts.personalities._loader import load_soul_mindsets

    result = load_soul_mindsets("finch")
    assert result.startswith("## Mindsets")
    assert len(result) > 100


# ---------------------------------------------------------------------------
# update_memory / append_memory DB re-index round-trip
# ---------------------------------------------------------------------------


def test_update_memory_reindexes_in_db(tmp_path: Path):
    """update_memory must update the DB index so the new content is findable."""
    from co_cli.knowledge._store import KnowledgeStore
    from co_cli.tools.memory import update_memory

    memory_dir = tmp_path / "memory"
    _write_memory(memory_dir, 1, "original-content-for-update-test")
    file_path = next(memory_dir.glob("*.md"))
    slug = file_path.stem

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", memory_dir)
        ctx = _make_ctx(knowledge_dir=memory_dir, knowledge_store=idx)

        asyncio.run(
            update_memory(ctx, slug, "original-content-for-update-test", "updated-content-xyz")
        )

        results = idx.search("updated-content-xyz", source="knowledge", limit=5)
        assert any("updated-content-xyz" in r.snippet for r in results), (
            "update_memory must re-index so the updated content is searchable"
        )
    finally:
        idx.close()


def test_append_memory_reindexes_in_db(tmp_path: Path):
    """append_memory must update the DB index so the appended content is findable."""
    from co_cli.knowledge._store import KnowledgeStore
    from co_cli.tools.memory import append_memory

    memory_dir = tmp_path / "memory"
    _write_memory(memory_dir, 1, "base-content-for-append-test")
    file_path = next(memory_dir.glob("*.md"))
    slug = file_path.stem

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", memory_dir)
        ctx = _make_ctx(knowledge_dir=memory_dir, knowledge_store=idx)

        asyncio.run(append_memory(ctx, slug, "appended-unique-content-abc"))

        results = idx.search("appended-unique-content-abc", source="knowledge", limit=5)
        assert any("appended-unique-content-abc" in r.snippet for r in results), (
            "append_memory must re-index so the appended content is searchable"
        )
    finally:
        idx.close()
