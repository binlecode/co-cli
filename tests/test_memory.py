"""Tests for memory gravity — touch and dedup on recall."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.memory import (
    recall_memory,
    list_memories,
    update_memory,
    append_memory,
    search_memories,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd())).agent


def _make_ctx(
    *,
    memory_dir: Path | None = None,
    knowledge_index: Any = None,
    knowledge_search_backend: str = "grep",
) -> RunContext:
    """Return a real RunContext with real CoDeps for memory tool tests."""
    config = CoConfig(knowledge_search_backend=knowledge_search_backend)
    if memory_dir is not None:
        from dataclasses import replace
        config = replace(config, memory_dir=memory_dir)
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), knowledge_index=knowledge_index),
        config=config,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())



def _write_memory(memory_dir: Path, memory_id: int, content: str,
                   tags: list[str] | None = None,
                   created: str | None = None,
                   updated: str | None = None) -> Path:
    """Write a memory file for testing."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    if created is None:
        created = datetime.now(timezone.utc).isoformat()
    slug = content[:30].lower().replace(" ", "-")
    filename = f"{memory_id:03d}-{slug}.md"
    fm: dict[str, Any] = {
        "id": memory_id,
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
# recall_memory read-only invariant
# ---------------------------------------------------------------------------


def test_recall_does_not_mutate_files(tmp_path: Path):
    """recall_memory must not change any file's mtime (read-only path)."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    _write_memory(memory_dir, 1, "User prefers dark theme", tags=["preference"])

    before = {str(p): p.stat().st_mtime for p in memory_dir.glob("*.md")}
    result = asyncio.run(
        recall_memory(_make_ctx(memory_dir=memory_dir), "dark theme")
    )
    assert result["count"] >= 1
    after = {str(p): p.stat().st_mtime for p in memory_dir.glob("*.md")}
    assert before == after, "recall_memory must not modify any file's mtime"


# ---------------------------------------------------------------------------
# list_memories pagination
# ---------------------------------------------------------------------------


def test_list_memories_pagination(tmp_path: Path):
    """list_memories returns correct pages with offset/limit."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    for i in range(1, 6):
        _write_memory(memory_dir, i, f"Memory content number {i}",
                      tags=["test"])

    ctx = _make_ctx(memory_dir=memory_dir)

    # Page 1: offset=0, limit=2
    r1 = asyncio.run(
        list_memories(ctx, offset=0, limit=2)
    )
    assert r1["count"] == 2
    assert r1["total"] == 5
    assert r1["offset"] == 0
    assert r1["limit"] == 2
    assert r1["has_more"] is True
    assert "capacity" in r1
    assert r1["memories"][0]["id"] == 1
    assert r1["memories"][1]["id"] == 2

    # Page 2: offset=2, limit=2
    r2 = asyncio.run(
        list_memories(ctx, offset=2, limit=2)
    )
    assert r2["count"] == 2
    assert r2["total"] == 5
    assert r2["has_more"] is True

    # Page 3: offset=4, limit=2 — partial last page
    r3 = asyncio.run(
        list_memories(ctx, offset=4, limit=2)
    )
    assert r3["count"] == 1
    assert r3["total"] == 5
    assert r3["has_more"] is False


def test_fts_freshness_after_consolidation(tmp_path: Path):
    """FTS returns updated content after a near-duplicate memory is consolidated."""
    import asyncio
    from co_cli.knowledge._index_store import KnowledgeIndex
    from co_cli.tools.memory import save_memory
    from tests._timeouts import FILE_DB_TIMEOUT_SECS

    memory_dir = tmp_path / ".co-cli" / "memory"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _make_ctx(memory_dir=memory_dir, knowledge_index=idx, knowledge_search_backend="fts5")

    async def _run() -> None:
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            # Save initial memory
            await save_memory(ctx, "User prefers zygomorphic-consolidation-test widget",
                              tags=["preference"])
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            # Save near-duplicate — should consolidate (update) rather than create new
            await save_memory(ctx, "User prefers zygomorphic-consolidation-test widget v2",
                              tags=["preference", "updated"])

    asyncio.run(_run())

    # FTS must return the consolidated (updated) content
    results = idx.search("zygomorphic-consolidation-test", source="memory")
    assert len(results) >= 1, "FTS must find the consolidated memory"
    # The consolidated entry must carry the updated tag
    assert any(r.tags and "updated" in r.tags for r in results), (
        "FTS result must reflect consolidated tags"
    )
    idx.close()


# ---------------------------------------------------------------------------
# update_memory — surgical text replacement
# ---------------------------------------------------------------------------


def test_update_memory_replaces_exact_match(tmp_path: Path):
    """update_memory replaces old_content with new_content in the body."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest over unittest", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    slug = path.stem
    asyncio.run(update_memory(ctx, slug, "pytest over unittest", "pytest over all others"))

    updated_text = path.read_text(encoding="utf-8")
    assert "pytest over all others" in updated_text
    assert "pytest over unittest" not in updated_text


def test_update_memory_raises_not_found(tmp_path: Path):
    """update_memory raises FileNotFoundError for an unknown slug."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)
    ctx = _make_ctx(memory_dir=memory_dir)

    import pytest
    with pytest.raises(FileNotFoundError, match="not found"):
        asyncio.run(update_memory(ctx, "999-nonexistent", "old", "new"))


def test_update_memory_raises_zero_occurrences(tmp_path: Path):
    """update_memory raises ValueError when old_content is not in the body."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    slug = path.stem
    import pytest
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(update_memory(ctx, slug, "unittest", "mocha"))


def test_update_memory_raises_ambiguous(tmp_path: Path):
    """update_memory raises ValueError when old_content appears more than once."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    path = _write_memory(memory_dir, 1, "User uses pytest. Also uses pytest.", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    slug = path.stem
    import pytest
    with pytest.raises(ValueError, match="2 times"):
        asyncio.run(update_memory(ctx, slug, "pytest", "mocha"))


def test_update_memory_rejects_line_prefix(tmp_path: Path):
    """update_memory raises ValueError when old_content contains Read-tool line prefixes."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    slug = path.stem
    import pytest
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
    memory_dir = tmp_path / ".co-cli" / "memory"
    # 5 spaces at column 3: what expandtabs() produces for "foo\tbar"
    path = _write_memory(memory_dir, 1, "foo     bar", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    slug = path.stem
    # old_content uses a tab; after expandtabs() it matches the body
    asyncio.run(update_memory(ctx, slug, "foo\tbar", "replaced"))

    assert "replaced" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# append_memory — add content to end of body
# ---------------------------------------------------------------------------


def test_append_memory_adds_to_end(tmp_path: Path):
    """append_memory appends content as a new line at the end of the body."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    slug = path.stem
    asyncio.run(append_memory(ctx, slug, "Also uses coverage reports."))

    updated_text = path.read_text(encoding="utf-8")
    assert updated_text.rstrip("\n").endswith("Also uses coverage reports.")
    assert "User prefers pytest" in updated_text


def test_append_memory_missing_slug_raises(tmp_path: Path):
    """append_memory raises FileNotFoundError for an unknown slug."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)
    ctx = _make_ctx(memory_dir=memory_dir)

    import pytest
    with pytest.raises(FileNotFoundError, match="not found"):
        asyncio.run(append_memory(ctx, "999-nonexistent", "extra line"))



def test_composite_bm25_decay_scoring(tmp_path: Path):
    """High-relevance older memory outranks low-relevance newer memory via composite scoring.

    Uses a background corpus (10 docs) to ensure positive IDF so BM25 TF differences
    between M1 (15 occurrences of query term) and M2 (1 occurrence) are meaningful.
    With only a 1-day age gap, the BM25 advantage of M1 overcomes the small recency
    edge of M2, demonstrating that composite scoring is BM25-driven, not decay-only.
    """
    from co_cli.knowledge._index_store import KnowledgeIndex

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Background corpus — no query term; makes IDF positive so TF differences matter
    for i in range(3, 13):
        _write_memory(
            memory_dir, i,
            f"Background note about software development entry number {i}",
        )

    # Memory 1: 1 day old, high BM25 — query term repeated 15 times
    old_time = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _write_memory(
        memory_dir, 1,
        "xylobm25score " * 15 + "development",
        tags=["preference"],
        created=old_time,
    )
    # Memory 2: created today, low BM25 — query term appears once
    new_time = datetime.now(timezone.utc).isoformat()
    _write_memory(
        memory_dir, 2,
        "User occasionally uses xylobm25score for reference",
        tags=["context"],
        created=new_time,
    )

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="fts5"))
    idx.sync_dir("memory", memory_dir)

    ctx = _make_ctx(memory_dir=memory_dir, knowledge_index=idx, knowledge_search_backend="fts5")
    result = asyncio.run(recall_memory(ctx, "xylobm25score", max_results=5))

    assert result["count"] >= 2, "Both memories should match the query"
    # Memory 1 (id=1) must rank first: high BM25 (15 occurrences) outweighs
    # the small recency advantage of M2 (1-day newer, decay diff ≈ 0.977 vs 1.0)
    first_result = result["results"][0]
    assert first_result["id"] == 1, (
        f"High-relevance older memory (id=1) should rank first; got id={first_result['id']}"
    )
    idx.close()


def test_forget_evicts_from_fts(tmp_path: Path):
    """KnowledgeIndex.remove() evicts a deleted memory from FTS results."""
    from co_cli.knowledge._index_store import KnowledgeIndex

    memory_dir = tmp_path / ".co-cli" / "memory"

    # Write a uniquely-worded memory file
    path = _write_memory(memory_dir, 1, "xyloquartz memory for forget eviction test")

    # Index it
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.sync_dir("memory", memory_dir)

    # Verify it's searchable
    results = idx.search("xyloquartz")
    assert any(str(path) in r.path for r in results), "Memory should be findable before forget"

    # Simulate /forget: delete the file then remove from index
    path.unlink()
    idx.remove("memory", str(path))

    # Must no longer appear
    results_after = idx.search("xyloquartz")
    assert not any(str(path) in r.path for r in results_after), "Memory should be evicted after remove()"

    idx.close()


# ---------------------------------------------------------------------------
# search_memories — dedicated memory search tool
# ---------------------------------------------------------------------------


def test_search_memories_finds_saved_memories(tmp_path: Path):
    """search_memories returns saved memories with source='memory'."""
    from co_cli.knowledge._index_store import KnowledgeIndex

    memory_dir = tmp_path / ".co-cli" / "memory"

    _write_memory(memory_dir, 1, "User prefers xyloquartz-search-test framework",
                  tags=["preference"])
    _write_memory(memory_dir, 2, "User uses xyloquartz-search-test for all tests",
                  tags=["context"])

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.sync_dir("memory", memory_dir)

    ctx = _make_ctx(memory_dir=memory_dir, knowledge_index=idx, knowledge_search_backend="fts5")

    result = asyncio.run(search_memories(ctx, "xyloquartz-search-test"))
    assert result["count"] >= 2
    assert all(r["source"] == "memory" for r in result["results"])
    idx.close()


def test_search_memories_empty_query_returns_guard(tmp_path: Path):
    """search_memories with empty query returns guard message."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(search_memories(_make_ctx(memory_dir=memory_dir), "   "))
    assert result["count"] == 0
    assert "required" in result["display"].lower()


def test_search_memories_grep_fallback(tmp_path: Path):
    """search_memories grep fallback (no FTS index) finds memories by keyword."""
    memory_dir = tmp_path / ".co-cli" / "memory"

    _write_memory(memory_dir, 1, "User prefers xyloquartz-grep-test tools", tags=["preference"])

    result = asyncio.run(search_memories(_make_ctx(memory_dir=memory_dir), "xyloquartz-grep-test"))
    assert result["count"] >= 1
    assert all(r["source"] == "memory" for r in result["results"])


# ---------------------------------------------------------------------------
# TASK-2: artifact_type / session_summary exclusion
# ---------------------------------------------------------------------------


def _write_memory_with_artifact_type(
    memory_dir: Path,
    memory_id: int,
    content: str,
    artifact_type: str | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Write a memory file with optional artifact_type frontmatter field."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    slug = content[:30].lower().replace(" ", "-")
    filename = f"{memory_id:03d}-{slug}.md"
    fm: dict[str, Any] = {
        "id": memory_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": tags or [],
    }
    if artifact_type is not None:
        fm["artifact_type"] = artifact_type
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


def test_recall_excludes_session_summary_artifacts(tmp_path: Path):
    """recall_memory must not return entries with artifact_type == session_summary."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    keyword = "artifact-exclusion-test-recall"

    _write_memory_with_artifact_type(memory_dir, 1, f"{keyword} durable memory", artifact_type=None)
    _write_memory_with_artifact_type(memory_dir, 2, f"{keyword} session checkpoint", artifact_type="session_summary")

    ctx = _make_ctx(memory_dir=memory_dir)
    result = asyncio.run(recall_memory(ctx, keyword))

    ids_returned = [r["id"] for r in result["results"]]
    assert 1 in ids_returned, "durable memory must be returned"
    assert 2 not in ids_returned, "session_summary artifact must be excluded"


def test_search_memories_excludes_session_summary_artifacts(tmp_path: Path):
    """search_memories must not return entries with artifact_type == session_summary."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    keyword = "artifact-exclusion-test-search"

    _write_memory_with_artifact_type(memory_dir, 1, f"{keyword} durable memory", artifact_type=None)
    _write_memory_with_artifact_type(memory_dir, 2, f"{keyword} session checkpoint", artifact_type="session_summary")

    ctx = _make_ctx(memory_dir=memory_dir)
    result = asyncio.run(search_memories(ctx, keyword))

    paths_returned = [r["path"] for r in result["results"]]
    assert not any("002-" in p for p in paths_returned), "session_summary artifact must be excluded"
    assert any("001-" in p for p in paths_returned), "durable memory must be returned"


def test_list_memories_displays_artifact_type(tmp_path: Path):
    """list_memories display contains artifact_type value when present."""
    memory_dir = tmp_path / ".co-cli" / "memory"

    _write_memory_with_artifact_type(memory_dir, 1, "Session summary content", artifact_type="session_summary")

    ctx = _make_ctx(memory_dir=memory_dir)
    result = asyncio.run(list_memories(ctx))

    assert "session_summary" in result["display"]


# ---------------------------------------------------------------------------
# ArtifactTypeEnum — write-strict / read-tolerant policy
# ---------------------------------------------------------------------------


def test_validate_memory_frontmatter_rejects_unknown_artifact_type(
    tmp_path: Path, caplog: Any
):
    """persist_memory rejects unknown artifact_type on write; _load_memories tolerates it on read."""
    from dataclasses import replace as dc_replace
    from co_cli.memory._lifecycle import persist_memory
    from co_cli.tools.memory import _load_memories

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    config = dc_replace(CoConfig(), memory_dir=memory_dir)
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), knowledge_index=None),
        config=config,
    )

    # Write-strict: persist_memory must reject an unknown artifact_type
    with pytest.raises(ValueError, match="unknown artifact_type"):
        asyncio.run(
            persist_memory(
                deps,
                content="Test memory for artifact type validation",
                tags=[],
                related=[],
                artifact_type="future_unknown_type",
            )
        )

    # Read-tolerant: _load_memories must load the entry without raising, and emit a warning
    _write_memory_with_artifact_type(
        memory_dir, 1, "Memory with unknown artifact type", artifact_type="future_unknown_type"
    )
    with caplog.at_level(logging.WARNING, logger="co_cli.knowledge._frontmatter"):
        entries = _load_memories(memory_dir)

    assert any(e.artifact_type == "future_unknown_type" for e in entries), (
        "_load_memories must load entries with unknown artifact_type, not skip them"
    )
    assert any("future_unknown_type" in r.message for r in caplog.records), (
        "_load_memories must log a warning for unknown artifact_type values"
    )


# ---------------------------------------------------------------------------
# rag.backend OTel annotation
# ---------------------------------------------------------------------------


def test_rag_backend_annotation_on_search_spans(tmp_path: Path):
    """search_memories and search_knowledge stamp rag.backend on the active OTel span."""
    from dataclasses import replace

    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from co_cli.knowledge._index_store import KnowledgeIndex
    from co_cli.tools.articles import search_knowledge

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    library_dir = tmp_path / "library"
    library_dir.mkdir()

    _write_memory(memory_dir, 1, "rag-backend-annotation-fts-test", tags=["test"])
    article_path = library_dir / "001-rag-backend-test.md"
    article_path.write_text(
        "---\nkind: article\ntags: [test]\n---\nrag-backend-annotation-fts-test article\n",
        encoding="utf-8",
    )

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    try:
        idx.sync_dir("memory", memory_dir)
        idx.sync_dir("library", library_dir)

        fts_mem_ctx = _make_ctx(memory_dir=memory_dir, knowledge_index=idx, knowledge_search_backend="fts5")
        grep_mem_ctx = _make_ctx(memory_dir=memory_dir, knowledge_index=None)
        fts_know_ctx = RunContext(
            deps=CoDeps(
                services=CoServices(shell=ShellBackend(), knowledge_index=idx),
                config=replace(CoConfig(), library_dir=library_dir, knowledge_search_backend="fts5"),
            ),
            model=_AGENT.model,
            usage=RunUsage(),
        )
        grep_know_ctx = RunContext(
            deps=CoDeps(
                services=CoServices(shell=ShellBackend(), knowledge_index=None),
                config=replace(CoConfig(), library_dir=library_dir),
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
            # (1) search_memories FTS path
            asyncio.run(search_memories(fts_mem_ctx, "rag-backend-annotation-fts-test"))
            assert parent_span.attributes.get("rag.backend") in ("fts5", "hybrid")

            # (2) search_memories grep path
            asyncio.run(search_memories(grep_mem_ctx, "rag-backend-annotation-fts-test"))
            assert parent_span.attributes.get("rag.backend") == "grep"

            # (3) search_knowledge FTS path
            asyncio.run(search_knowledge(fts_know_ctx, "rag-backend-annotation-fts-test"))
            assert parent_span.attributes.get("rag.backend") in ("fts5", "hybrid")

            # (4) search_knowledge grep path
            asyncio.run(search_knowledge(grep_know_ctx, "rag-backend-annotation-fts-test"))
            assert parent_span.attributes.get("rag.backend") == "grep"
    finally:
        idx.close()

    assert otel_trace.get_tracer_provider() is _orig
