"""Functional tests for KnowledgeIndex — FTS5 search, sync, rebuild."""

import hashlib
import struct
from pathlib import Path

import pytest
import yaml
from pydantic_ai._run_context import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings, ModelEntry
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.knowledge._index_store import KnowledgeIndex, SearchResult
from co_cli.tools._shell_backend import ShellBackend

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_md(directory: Path, filename: str, content: str, frontmatter: dict) -> Path:
    """Write a markdown file with YAML frontmatter for testing."""
    directory.mkdir(parents=True, exist_ok=True)
    fm_str = yaml.dump(frontmatter, default_flow_style=False)
    full = f"---\n{fm_str}---\n\n{content}\n"
    path = directory / filename
    path.write_text(full, encoding="utf-8")
    return path


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_knowledge_index_creates_schema(tmp_path):
    """KnowledgeIndex creates docs table and docs_fts virtual table on init."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    conn = idx._conn
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','shadow')")}
    assert "docs" in tables
    assert "docs_fts" in tables
    idx.close()


# ---------------------------------------------------------------------------
# index()
# ---------------------------------------------------------------------------


def test_index_inserts_doc(tmp_path):
    """index() inserts a document and it appears in the docs table."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(
        source="memory",
        kind="memory",
        path="/test/001-foo.md",
        title="Foo Memory",
        content="User prefers pytest for testing",
        mtime=1234567890.0,
        hash=_sha256("User prefers pytest for testing"),
        tags="preference testing",
        created="2026-01-01T00:00:00+00:00",
    )
    row = idx._conn.execute("SELECT * FROM docs WHERE path = '/test/001-foo.md'").fetchone()
    assert row is not None
    assert row["title"] == "Foo Memory"
    assert row["source"] == "memory"
    assert row["kind"] == "memory"
    idx.close()


def test_index_skips_unchanged_hash(tmp_path):
    """index() skips re-indexing when hash matches (no duplicate inserts)."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    content = "User prefers pytest"
    h = _sha256(content)
    idx.index(source="memory", path="/test/001.md", title="T1", content=content, hash=h)
    idx.index(source="memory", path="/test/001.md", title="T2", content=content, hash=h)
    # Title should still be T1 since second call was skipped
    row = idx._conn.execute("SELECT title FROM docs WHERE path = '/test/001.md'").fetchone()
    assert row["title"] == "T1"
    idx.close()


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


def test_search_returns_bm25_ranked_results(tmp_path):
    """search() returns results ordered by BM25 score."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="memory", kind="memory", path="/p1.md", title="pytest testing",
              content="User prefers pytest for unit testing frameworks extensively",
              hash=_sha256("pytest testing"), mtime=0.0)
    idx.index(source="memory", kind="memory", path="/p2.md", title="unrelated",
              content="Something completely different about cooking",
              hash=_sha256("cooking"), mtime=0.0)
    idx.index(source="memory", kind="memory", path="/p3.md", title="pytest config",
              content="pytest configuration options are stored here",
              hash=_sha256("pytest config"), mtime=0.0)

    results = idx.search("pytest", source="memory")
    assert len(results) >= 1
    # pytest-related docs should rank higher
    assert any("pytest" in (r.title or "").lower() or "pytest" in (r.snippet or "").lower()
               for r in results[:2])
    idx.close()


def test_search_stopword_only_returns_empty(tmp_path):
    """search() returns [] when query contains only stopwords."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="memory", path="/p1.md", title="test", content="hello world",
              hash=_sha256("hello world"), mtime=0.0)
    results = idx.search("the a an")
    assert results == []
    idx.close()


def test_search_filters_by_source(tmp_path):
    """search() with source= returns only matching source."""
    from co_cli.knowledge._chunker import Chunk
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="memory", path="/mem.md", title="memo", content="pytest memory item",
              hash=_sha256("pytest memory"), mtime=0.0)
    idx.index(source="obsidian", path="/obs.md", title="obsidian", content="pytest obsidian note",
              hash=_sha256("pytest obsidian"), mtime=0.0)
    # Non-memory sources must also have chunks for routing to find them
    idx.index_chunks("obsidian", "/obs.md", [Chunk(index=0, content="pytest obsidian note", start_line=0, end_line=0)])

    mem_results = idx.search("pytest", source="memory")
    assert all(r.source == "memory" for r in mem_results)
    obs_results = idx.search("pytest", source="obsidian")
    assert all(r.source == "obsidian" for r in obs_results)
    idx.close()


# ---------------------------------------------------------------------------
# sync_dir()
# ---------------------------------------------------------------------------


def test_sync_dir_indexes_new_files(tmp_path):
    """sync_dir() indexes new markdown files."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    knowledge_dir = tmp_path / "knowledge"
    _write_md(knowledge_dir, "001-foo.md", "User prefers dark mode",
              {"id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": ["preference"]})

    count = idx.sync_dir("memory", knowledge_dir)
    assert count == 1

    results = idx.search("dark mode", source="memory")
    assert len(results) >= 1
    idx.close()


def test_sync_dir_skips_unchanged_files(tmp_path):
    """sync_dir() does not re-index files that haven't changed."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    knowledge_dir = tmp_path / "knowledge"
    _write_md(knowledge_dir, "001-foo.md", "User prefers dark mode",
              {"id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": []})

    count1 = idx.sync_dir("memory", knowledge_dir)
    assert count1 == 1

    # Second sync — same content, same hash → nothing re-indexed
    count2 = idx.sync_dir("memory", knowledge_dir)
    assert count2 == 0
    idx.close()


# ---------------------------------------------------------------------------
# remove_stale()
# ---------------------------------------------------------------------------


def test_remove_stale_removes_deleted_paths(tmp_path):
    """remove_stale() removes index entries for deleted files."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="memory", path="/keep.md", title="keep", content="keep this",
              hash=_sha256("keep"), mtime=0.0)
    idx.index(source="memory", path="/delete.md", title="delete", content="delete this",
              hash=_sha256("delete"), mtime=0.0)

    current_paths = {"/keep.md"}
    removed = idx.remove_stale("memory", current_paths)
    assert removed == 1

    row = idx._conn.execute("SELECT * FROM docs WHERE path = '/delete.md'").fetchone()
    assert row is None
    row_keep = idx._conn.execute("SELECT * FROM docs WHERE path = '/keep.md'").fetchone()
    assert row_keep is not None
    idx.close()


# ---------------------------------------------------------------------------
# rebuild()
# ---------------------------------------------------------------------------


def test_rebuild_wipes_and_reindexes(tmp_path):
    """rebuild() deletes all source entries and re-indexes from directory."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    knowledge_dir = tmp_path / "knowledge"
    _write_md(knowledge_dir, "001-foo.md", "User prefers dark theme",
              {"id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": ["preference"]})

    # Initial index
    idx.sync_dir("memory", knowledge_dir)
    count_before = idx._conn.execute("SELECT COUNT(*) FROM docs WHERE source='memory'").fetchone()[0]
    assert count_before == 1

    # Wipe and rebuild
    count_rebuilt = idx.rebuild("memory", knowledge_dir)
    assert count_rebuilt == 1

    count_after = idx._conn.execute("SELECT COUNT(*) FROM docs WHERE source='memory'").fetchone()[0]
    assert count_after == 1
    idx.close()


# ---------------------------------------------------------------------------
# Recursive sync + scoped remove_stale
# ---------------------------------------------------------------------------


def test_sync_dir_recursive(tmp_path):
    """sync_dir() with default glob indexes files in nested subdirectories."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    vault = tmp_path / "vault"
    subfolder = vault / "Work"
    _write_md(vault, "top.md", "Top-level note about productivity",
              {"id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": []})
    _write_md(subfolder, "nested.md", "Nested note about zygomorphic-recursive widget",
              {"id": 2, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": []})

    count = idx.sync_dir("obsidian", vault)
    assert count == 2, f"Expected 2 files indexed, got {count}"

    results = idx.search("zygomorphic-recursive", source="obsidian")
    assert len(results) >= 1, "Nested file should be indexed and searchable"
    idx.close()


def test_remove_stale_scoped_to_directory(tmp_path):
    """remove_stale() with directory= only evicts entries within that directory."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="obsidian", path="/vault/Work/note1.md", title="work note",
              content="work content", hash=_sha256("work"), mtime=0.0)
    idx.index(source="obsidian", path="/vault/Personal/note2.md", title="personal note",
              content="personal content", hash=_sha256("personal"), mtime=0.0)

    # Simulate sync of Work/ only — Personal/ entry must survive
    current_in_work: set[str] = set()
    removed = idx.remove_stale("obsidian", current_in_work,
                               directory=Path("/vault/Work"))
    assert removed == 1

    # Personal entry untouched
    row = idx._conn.execute(
        "SELECT * FROM docs WHERE path = '/vault/Personal/note2.md'"
    ).fetchone()
    assert row is not None, "Personal directory entry must not be evicted"
    idx.close()


def test_remove_stale_does_not_evict_common_prefix_sibling(tmp_path):
    """Sibling dirs with a common prefix are not evicted by a scoped sync."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="obsidian", path="/vault/Work/note.md", title="work",
              content="work content", hash=_sha256("work"), mtime=0.0)
    idx.index(source="obsidian", path="/vault/Workbench/note.md", title="bench",
              content="bench content", hash=_sha256("bench"), mtime=0.0)

    # Sync Work only — Workbench must survive
    removed = idx.remove_stale("obsidian", set(), directory=Path("/vault/Work"))
    assert removed == 1  # only Work/note.md removed

    row = idx._conn.execute(
        "SELECT * FROM docs WHERE path = '/vault/Workbench/note.md'"
    ).fetchone()
    assert row is not None, "Workbench entry must not be evicted by a Work-only sync"
    idx.close()


# ---------------------------------------------------------------------------
# FTS round-trip with save_memory
# ---------------------------------------------------------------------------


def test_fts_roundtrip_save_and_recall(tmp_path):
    """Save a memory with FTS index, then recall it via search()."""
    import asyncio
    from co_cli.tools.memory import save_memory

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), knowledge_index=idx),
        config=CoConfig(
            knowledge_search_backend="fts5",
            memory_dir=tmp_path / ".co-cli" / "memory",
        ),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    asyncio.run(save_memory(ctx, "User loves zygomorphic-fts5-test widget framework",
                            tags=["preference"]))
    results = idx.search("zygomorphic-fts5-test", source="memory")
    assert len(results) >= 1, "FTS should find the saved memory"
    idx.close()


# ---------------------------------------------------------------------------
# doc_tags junction table — tag_match_mode + temporal filtering
# ---------------------------------------------------------------------------


def test_tag_match_mode_all_returns_only_items_with_all_tags(tmp_path):
    """search(tag_match_mode='all') returns only docs that have every requested tag."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="memory", kind="memory", path="/a.md", title="Doc A",
              content="doc about junction table filtering",
              hash="a", mtime=0.0, tags="python async")
    idx.index(source="memory", kind="memory", path="/b.md", title="Doc B",
              content="doc about junction table filtering",
              hash="b", mtime=0.0, tags="python")
    results = idx.search("junction", tags=["python", "async"], tag_match_mode="all")
    assert len(results) == 1
    assert Path(results[0].path).name == "a.md"
    idx.close()


def test_created_after_filters_older_items(tmp_path):
    """search(created_after=) excludes docs created before that date."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="memory", kind="memory", path="/a.md", title="Doc A",
              content="doc about temporal range filtering",
              hash="a", mtime=0.0, created="2024-01-01T00:00:00+00:00")
    idx.index(source="memory", kind="memory", path="/b.md", title="Doc B",
              content="doc about temporal range filtering",
              hash="b", mtime=0.0, created="2025-01-01T00:00:00+00:00")
    results = idx.search("temporal", created_after="2024-06-01")
    assert len(results) == 1
    assert Path(results[0].path).name == "b.md"
    idx.close()


# ---------------------------------------------------------------------------
# Phase 2 — Hybrid search (sqlite-vec)
# ---------------------------------------------------------------------------


def test_hybrid_schema_creates_vec_table(tmp_path):
    """KnowledgeIndex with backend='hybrid' creates the docs_vec virtual table."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none"))
    tables = {
        row[0]
        for row in idx._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
        ).fetchall()
    }
    assert "docs_vec" in tables or any("vec" in t for t in tables), (
        f"docs_vec not found in schema. Tables: {tables}"
    )
    idx.close()


def test_hybrid_provider_none_uses_fts_leg_in_hybrid(tmp_path):
    """provider='none' in hybrid backend still returns lexical FTS results."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none"))
    idx.index(
        source="memory", kind="memory", path="/xyloquartz.md",
        title="Xyloquartz note", content="xyloquartz hybrid fallback test content",
        hash="h1", mtime=0.0,
    )
    results = idx.search("xyloquartz")
    assert len(results) >= 1
    assert results[0].path == "/xyloquartz.md"
    idx.close()


def test_hybrid_remove_cleans_docs_vec(tmp_path):
    """remove() in hybrid mode evicts from docs/docs_fts/docs_vec."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none"))
    idx.index(
        source="memory", kind="memory", path="/rem.md",
        title="Remove test", content="xyloquartz-remove-hybrid unique content",
        hash="h2", mtime=0.0,
    )
    # Verify indexed
    results_before = idx.search("xyloquartz-remove-hybrid")
    assert len(results_before) >= 1

    # Remove and check not found
    idx.remove("memory", "/rem.md")
    results_after = idx.search("xyloquartz-remove-hybrid")
    assert len(results_after) == 0
    idx.close()


# ---------------------------------------------------------------------------
# Phase 3 — Reranking
# ---------------------------------------------------------------------------


def test_reranker_provider_computed_none_when_nothing_configured(tmp_path):
    """Both URL and LLM reranker absent → _reranker_provider is 'none'."""
    idx = KnowledgeIndex(config=CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_cross_encoder_reranker_url=None,
        knowledge_llm_reranker=None,
    ))
    assert idx._reranker_provider == "none"
    idx.close()


def test_reranker_provider_tei_when_url_set(tmp_path):
    """Cross-encoder URL set (any URL) → _reranker_provider is 'tei'."""
    idx = KnowledgeIndex(config=CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_cross_encoder_reranker_url="http://127.0.0.1:19999",
        knowledge_llm_reranker=None,
    ))
    assert idx._reranker_provider == "tei"
    idx.close()


def test_reranker_provider_tei_takes_priority_over_llm(tmp_path):
    """Cross-encoder URL takes priority when both cross-encoder and LLM reranker are configured."""
    idx = KnowledgeIndex(config=CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_cross_encoder_reranker_url="http://127.0.0.1:19999",
        knowledge_llm_reranker=ModelEntry(provider="ollama-openai", model="qwen2.5:3b"),
    ))
    assert idx._reranker_provider == "tei"
    idx.close()


def test_reranker_provider_ollama_openai_maps_to_ollama(tmp_path):
    """ModelEntry.provider='ollama-openai' must map _reranker_provider to 'ollama' for build_llm_reranker."""
    idx = KnowledgeIndex(config=CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_cross_encoder_reranker_url=None,
        knowledge_llm_reranker=ModelEntry(provider="ollama-openai", model="qwen2.5:3b"),
    ))
    assert idx._reranker_provider == "ollama"
    idx.close()


def test_reranker_provider_none_is_passthrough(tmp_path):
    """With reranker_provider='none', search() respects limit and returns BM25 results without reranking."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_cross_encoder_reranker_url=None))
    for i in range(5):
        idx.index(source="memory", kind="memory", path=f"/passthrough{i}.md",
                  title=f"Passthrough {i}", content=f"xyloquartz-passthrough unique content document {i}",
                  hash=f"h{i}", mtime=float(i))
    results = idx.search("xyloquartz-passthrough", limit=3)
    assert len(results) == 3
    idx.close()


def test_rerank_falls_back_on_error(tmp_path):
    """search() with a dead Ollama reranker host falls back gracefully — no exception, returns results."""
    idx = KnowledgeIndex(config=CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_cross_encoder_reranker_url=None,
        knowledge_llm_reranker=ModelEntry(provider="ollama-openai", model="qwen2.5:3b"),
        llm_host="http://localhost:19999",
    ))
    for i in range(5):
        idx.index(source="memory", kind="memory", path=f"/fallback{i}.md",
                  title=f"Fallback {i}", content=f"xyloquartz-fallback-error unique content {i}",
                  hash=f"h{i}", mtime=float(i))
    results = idx.search("xyloquartz-fallback-error", limit=3)
    assert len(results) == 3
    idx.close()




# ---------------------------------------------------------------------------
# TASK-2: provenance + certainty schema extension
# ---------------------------------------------------------------------------


def test_sync_dir_propagates_provenance_and_certainty(tmp_path):
    """sync_dir() reads provenance/certainty from frontmatter and stores them."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    knowledge_dir = tmp_path / "knowledge"
    _write_md(knowledge_dir, "001-prov.md", "sync provenance certainty test content",
              {"id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00",
               "tags": [], "provenance": "planted", "certainty": "medium"})
    idx.sync_dir("memory", knowledge_dir)
    row = idx._conn.execute("SELECT provenance, certainty FROM docs").fetchone()
    assert row["provenance"] == "planted"
    assert row["certainty"] == "medium"
    idx.close()


def test_sync_dir_kind_filter_skips_non_matching(tmp_path):
    """sync_dir(kind_filter='memory') skips files whose frontmatter kind != 'memory'."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    knowledge_dir = tmp_path / "knowledge"
    _write_md(knowledge_dir, "001-mem.md", "User prefers dark mode",
              {"id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": []})
    _write_md(knowledge_dir, "002-art.md", "Python asyncio reference guide",
              {"id": 2, "kind": "article", "created": "2026-01-01T00:00:00+00:00", "tags": []})

    # Sync memories only
    count = idx.sync_dir("memory", knowledge_dir, kind_filter="memory")
    assert count == 1

    rows = idx._conn.execute("SELECT path FROM docs WHERE source='memory'").fetchall()
    assert len(rows) == 1
    assert "001-mem" in rows[0]["path"]
    idx.close()


def test_sync_dir_kind_filter_article(tmp_path):
    """sync_dir(kind_filter='article') indexes articles under 'library' source."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    knowledge_dir = tmp_path / "knowledge"
    _write_md(knowledge_dir, "001-mem.md", "User prefers dark mode",
              {"id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": []})
    _write_md(knowledge_dir, "002-art.md", "Python asyncio reference guide",
              {"id": 2, "kind": "article", "created": "2026-01-01T00:00:00+00:00", "tags": []})

    count = idx.sync_dir("library", knowledge_dir, kind_filter="article")
    assert count == 1

    rows = idx._conn.execute("SELECT path FROM docs WHERE source='library'").fetchall()
    assert len(rows) == 1
    assert "002-art" in rows[0]["path"]
    idx.close()





# ---------------------------------------------------------------------------
# Chunking scenarios (scenarios 8–15)
# ---------------------------------------------------------------------------


def test_chunks_and_chunks_fts_tables_exist(tmp_path):
    """Scenario 8: chunks and chunks_fts tables are created on KnowledgeIndex init."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    tables = {
        row[0]
        for row in idx._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow', 'view')"
        ).fetchall()
    }
    assert "chunks" in tables, f"chunks table missing. Tables: {tables}"
    assert "chunks_fts" in tables, f"chunks_fts table missing. Tables: {tables}"
    idx.close()


def test_index_chunks_inserts_correct_row_count(tmp_path):
    """Scenario 9: index_chunks inserts correct number of rows into chunks table."""
    from co_cli.knowledge._chunker import Chunk
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="library", path="/art.md", title="Test Article", content="body", hash="h", mtime=0.0)
    chunks = [
        Chunk(index=0, content="First paragraph content", start_line=0, end_line=2),
        Chunk(index=1, content="Second paragraph content", start_line=4, end_line=6),
        Chunk(index=2, content="Third paragraph content", start_line=8, end_line=10),
    ]
    idx.index_chunks("library", "/art.md", chunks)
    count = idx._conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE source='library' AND doc_path='/art.md'"
    ).fetchone()[0]
    assert count == 3
    idx.close()


def test_chunks_fts_finds_phrase_in_second_half_of_article(tmp_path):
    """Scenario 10: FTS search on non-memory source retrieves phrase only in second half."""
    from co_cli.knowledge._chunker import Chunk
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="library", path="/long.md", title="Long Article", content="intro text", hash="h1", mtime=0.0)
    idx.index_chunks("library", "/long.md", [
        Chunk(index=0, content="The first half discusses general concepts", start_line=0, end_line=5),
        Chunk(index=1, content="The second half covers zygomorphic-deep-phrase retrieval patterns", start_line=7, end_line=12),
    ])
    results = idx.search("zygomorphic-deep-phrase", source="library")
    assert len(results) >= 1, "Phrase in second chunk must be retrievable via chunks_fts"
    assert results[0].path == "/long.md"
    idx.close()


def test_remove_also_removes_chunk_rows(tmp_path):
    """Scenario 11: remove() on a library article also removes its chunk rows."""
    from co_cli.knowledge._chunker import Chunk
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="library", path="/del.md", title="Delete Test", content="content", hash="h2", mtime=0.0)
    idx.index_chunks("library", "/del.md", [
        Chunk(index=0, content="chunk content zygomorphic-remove-cascade", start_line=0, end_line=0),
    ])

    count_before = idx._conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE doc_path='/del.md'"
    ).fetchone()[0]
    assert count_before == 1

    idx.remove("library", "/del.md")

    count_after = idx._conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE doc_path='/del.md'"
    ).fetchone()[0]
    assert count_after == 0, "Chunk rows must be removed when the doc is removed"
    idx.close()


def test_sync_dir_library_emits_chunk_rows(tmp_path):
    """Scenario 12a: sync_dir for library source writes rows into chunks table."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    knowledge_dir = tmp_path / "library"
    _write_md(knowledge_dir, "001-art.md",
              "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.",
              {"id": 1, "kind": "article", "created": "2026-01-01T00:00:00+00:00", "tags": []})

    idx.sync_dir("library", knowledge_dir)

    count = idx._conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE source='library'"
    ).fetchone()[0]
    assert count >= 1, "sync_dir for library must produce at least one chunk row"
    idx.close()


def test_sync_dir_memory_emits_no_chunk_rows(tmp_path):
    """Scenario 12b: sync_dir for memory source must not write any chunk rows."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    knowledge_dir = tmp_path / "memory"
    _write_md(knowledge_dir, "001-mem.md", "User preference note content",
              {"id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": []})

    idx.sync_dir("memory", knowledge_dir)

    count = idx._conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE source='memory'"
    ).fetchone()[0]
    assert count == 0, "Memory source must never produce chunk rows"
    idx.close()


def test_recall_memory_queries_docs_fts_not_chunks(tmp_path):
    """Scenario 13: memory search queries docs_fts; chunks table remains empty for memory."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="memory", kind="memory", path="/m.md", title="mem",
              content="zygomorphic-recall-memory-test content", hash="hm", mtime=0.0)

    results = idx.search("zygomorphic-recall-memory-test", source="memory")
    assert len(results) >= 1, "Memory search must find content via docs_fts"

    chunk_count = idx._conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE source='memory'"
    ).fetchone()[0]
    assert chunk_count == 0, "Memory content must not appear in chunks table"
    idx.close()


def test_index_chunks_memory_raises_value_error(tmp_path):
    """Scenario 14: index_chunks with source='memory' raises ValueError."""
    from co_cli.knowledge._chunker import Chunk
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    with pytest.raises(ValueError, match="memory"):
        idx.index_chunks("memory", "/m.md", [Chunk(index=0, content="x", start_line=0, end_line=0)])
    idx.close()


def test_global_search_returns_both_memory_and_nonmemory(tmp_path):
    """Scenario 15: source=None search returns results from both memory and non-memory."""
    from co_cli.knowledge._chunker import Chunk
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    # Index a memory item
    idx.index(source="memory", kind="memory", path="/mem15.md", title="Global search memory",
              content="zygomorphic-global-union memory entry", hash="hg1", mtime=0.0)
    # Index a library article with chunk
    idx.index(source="library", kind="article", path="/lib15.md", title="Global search library",
              content="intro", hash="hg2", mtime=0.0)
    idx.index_chunks("library", "/lib15.md", [
        Chunk(index=0, content="zygomorphic-global-union library chunk content", start_line=0, end_line=0),
    ])

    results = idx.search("zygomorphic-global-union")
    sources_found = {r.source for r in results}
    assert "memory" in sources_found, "Global search must include memory results"
    assert "library" in sources_found, "Global search must include library/chunk results"
    idx.close()


def test_chunks_fts_multi_document_crowding(tmp_path):
    """Scenario 16: a long article (many chunks) must not crowd out a second matching article.

    Before the fix, _run_chunks_fts used LIMIT at chunk-row granularity, so a single
    article with N matching chunks consumed the full limit and suppressed other documents.
    """
    from co_cli.knowledge._chunker import Chunk

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_chunk_size=50, knowledge_chunk_overlap=5))

    # Article A: 10 matching chunks — more than the search limit of 5.
    idx.index(source="library", kind="article", path="/crowding-a.md",
              title="Crowding Article A", content="intro", hash="hca1", mtime=0.0)
    idx.index_chunks("library", "/crowding-a.md", [
        Chunk(index=i, content=f"zygomorphic-crowding-token repeated text segment number {i}",
              start_line=i, end_line=i)
        for i in range(10)
    ])

    # Article B: 1 matching chunk — should still appear despite Article A's 10 chunks.
    idx.index(source="library", kind="article", path="/crowding-b.md",
              title="Crowding Article B", content="intro", hash="hcb1", mtime=0.0)
    idx.index_chunks("library", "/crowding-b.md", [
        Chunk(index=0, content="zygomorphic-crowding-token second document entry",
              start_line=0, end_line=0),
    ])

    results = idx.search("zygomorphic-crowding-token", source="library", limit=5)
    paths_found = {r.path for r in results}
    assert "/crowding-a.md" in paths_found, "High-chunk article must appear in results"
    assert "/crowding-b.md" in paths_found, (
        "Second document must not be crowded out by the first article's many chunks"
    )
    idx.close()


# ---------------------------------------------------------------------------
# Chunk-level RRF fix tests (Divergence 1, 2, 3)
# ---------------------------------------------------------------------------


def test_searchresult_has_chunk_fields(tmp_path):
    """SearchResult has chunk_index, start_line, end_line fields defaulting to None."""
    r = SearchResult(
        source="memory", kind="memory", path="/x.md",
        title=None, snippet=None, score=0.5,
        tags=None, category=None, created=None, updated=None,
    )
    assert hasattr(r, "chunk_index")
    assert hasattr(r, "start_line")
    assert hasattr(r, "end_line")
    assert r.chunk_index is None
    assert r.start_line is None
    assert r.end_line is None


def test_fts_search_chunk_result_carries_chunk_index(tmp_path):
    """FTS search on non-memory source populates chunk_index, start_line, end_line."""
    from co_cli.knowledge._chunker import Chunk

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="library", kind="article", path="/art.md",
              title="Article", content="intro", hash="h1", mtime=0.0)
    idx.index_chunks("library", "/art.md", [
        Chunk(index=0, content="first chunk content placeholder", start_line=0, end_line=2),
        Chunk(index=1, content="zygomorphic-chunk-index-test phrase here", start_line=4, end_line=6),
    ])

    results = idx.search("zygomorphic-chunk-index-test", source="library")
    assert len(results) >= 1
    r = results[0]
    assert r.chunk_index is not None, "chunk_index must be set for non-memory FTS result"
    assert r.start_line is not None, "start_line must be set for non-memory FTS result"
    assert r.end_line is not None, "end_line must be set for non-memory FTS result"
    idx.close()


def test_fts_search_memory_result_chunk_fields_none(tmp_path):
    """FTS search on memory source leaves chunk fields as None."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.index(source="memory", kind="memory", path="/mem.md",
              title="Memory", content="zygomorphic-mem-chunk-fields content",
              hash="hm", mtime=0.0)

    results = idx.search("zygomorphic-mem-chunk-fields", source="memory")
    assert len(results) >= 1
    r = results[0]
    assert r.chunk_index is None, "chunk_index must be None for memory results"
    assert r.start_line is None, "start_line must be None for memory results"
    assert r.end_line is None, "end_line must be None for memory results"
    idx.close()


def test_hybrid_rrf_multi_chunk_doc_scores_higher(tmp_path):
    """Doc with 3 matching chunks ranks above a doc with 1 matching chunk in hybrid search."""
    from co_cli.knowledge._chunker import Chunk

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none"))

    # Doc A: 3 matching chunks
    idx.index(source="library", kind="article", path="/doc-a.md",
              title="Doc A", content="intro", hash="ha", mtime=0.0)
    idx.index_chunks("library", "/doc-a.md", [
        Chunk(index=0, content="zygomorphic-rrf-test alpha section content", start_line=0, end_line=2),
        Chunk(index=1, content="zygomorphic-rrf-test beta section content", start_line=4, end_line=6),
        Chunk(index=2, content="zygomorphic-rrf-test gamma section content", start_line=8, end_line=10),
    ])

    # Doc B: 1 matching chunk
    idx.index(source="library", kind="article", path="/doc-b.md",
              title="Doc B", content="intro", hash="hb", mtime=0.0)
    idx.index_chunks("library", "/doc-b.md", [
        Chunk(index=0, content="zygomorphic-rrf-test single entry only", start_line=0, end_line=2),
    ])

    results = idx.search("zygomorphic-rrf-test")
    paths = [r.path for r in results]
    assert "/doc-a.md" in paths, "Doc A must appear in results"
    assert "/doc-b.md" in paths, "Doc B must appear in results"
    assert paths.index("/doc-a.md") < paths.index("/doc-b.md"), (
        "Doc A with 3 matching chunks must rank above Doc B with 1"
    )
    idx.close()


def test_hybrid_merge_winning_chunk_metadata_propagated(tmp_path):
    """In hybrid search, the winning chunk's chunk_index and snippet are propagated to the result."""
    from co_cli.knowledge._chunker import Chunk

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none"))
    idx.index(source="library", kind="article", path="/merge-test.md",
              title="Merge Test", content="intro", hash="hmt", mtime=0.0)
    idx.index_chunks("library", "/merge-test.md", [
        Chunk(index=0, content="ordinary content not relevant here", start_line=0, end_line=2),
        Chunk(index=1, content="zygomorphic-merge-winner best matching chunk content", start_line=5, end_line=8),
    ])

    results = idx.search("zygomorphic-merge-winner")
    assert len(results) >= 1
    result = results[0]
    assert result.path == "/merge-test.md"
    assert result.chunk_index == 1, f"Expected winning chunk_index=1, got {result.chunk_index}"
    assert result.start_line == 5, f"Expected start_line=5, got {result.start_line}"
    assert result.snippet is not None, "Snippet from FTS chunk must be propagated"
    idx.close()




def test_hybrid_fallback_collapses_chunks_to_doc_level(tmp_path):
    """Hybrid search with embedding_provider=none falls back to FTS and returns one result per doc."""
    from co_cli.knowledge._chunker import Chunk

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none"))
    idx.index(source="library", kind="article", path="/fallback-doc.md",
              title="Fallback Doc", content="intro", hash="hfd", mtime=0.0)
    idx.index_chunks("library", "/fallback-doc.md", [
        Chunk(index=i, content=f"zygomorphic-fallback-collapse chunk {i} content",
              start_line=i * 3, end_line=i * 3 + 2)
        for i in range(4)
    ])

    results = idx.search("zygomorphic-fallback-collapse", source="library")
    paths = [r.path for r in results]
    assert paths.count("/fallback-doc.md") == 1, (
        "Fallback path must collapse chunks to doc level — each doc appears once"
    )
    idx.close()


# ---------------------------------------------------------------------------
# Group 4 — Vector injection tests (Stream A)
# ---------------------------------------------------------------------------


def test_vec_docs_search_closest_embedding_ranked_first(tmp_path):
    """2 docs injected at [1,0,0,0] and [0,1,0,0]; query [1,0,0,0] → doc1 first, chunk_index=None."""
    dims = 4
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none", knowledge_embedding_dims=dims))
    idx.index(source="memory", kind="memory", path="/doc1.md",
              title="Doc1", content="content one", hash="h1", mtime=0.0)
    idx.index(source="memory", kind="memory", path="/doc2.md",
              title="Doc2", content="content two", hash="h2", mtime=0.0)

    row1 = idx._conn.execute(
        "SELECT rowid FROM docs WHERE path=? AND chunk_id=0", ("/doc1.md",)
    ).fetchone()
    row2 = idx._conn.execute(
        "SELECT rowid FROM docs WHERE path=? AND chunk_id=0", ("/doc2.md",)
    ).fetchone()
    idx._conn.execute(f"INSERT INTO {idx._docs_vec_table}(rowid, embedding) VALUES (?, ?)",
                      (row1["rowid"], struct.pack(f"{dims}f", 1.0, 0.0, 0.0, 0.0)))
    idx._conn.execute(f"INSERT INTO {idx._docs_vec_table}(rowid, embedding) VALUES (?, ?)",
                      (row2["rowid"], struct.pack(f"{dims}f", 0.0, 1.0, 0.0, 0.0)))
    idx._conn.commit()

    blob = struct.pack(f"{dims}f", 1.0, 0.0, 0.0, 0.0)
    results = idx._vec_docs_search(
        blob, kind=None, tags=None, tag_match_mode="any",
        created_after=None, created_before=None, limit=10,
    )
    assert len(results) >= 2
    assert results[0].path == "/doc1.md", "Doc with closest embedding must rank first"
    assert all(r.chunk_index is None for r in results), "docs_vec results must have chunk_index=None"
    idx.close()


def test_vec_chunks_search_returns_chunk_level_results(tmp_path):
    """chunk_index=2 injected into chunks_vec; result has chunk_index=2, start_line populated."""
    from co_cli.knowledge._chunker import Chunk

    dims = 4
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none", knowledge_embedding_dims=dims))
    idx.index(source="library", kind="article", path="/art.md",
              title="Article", content="intro", hash="h1", mtime=0.0)
    idx.index_chunks("library", "/art.md", [
        Chunk(index=0, content="chunk zero", start_line=0, end_line=2),
        Chunk(index=1, content="chunk one", start_line=4, end_line=6),
        Chunk(index=2, content="chunk two zygomorphic-vec-chunk-test", start_line=8, end_line=10),
    ])

    row = idx._conn.execute(
        "SELECT rowid FROM chunks WHERE source='library' AND doc_path='/art.md' AND chunk_index=2"
    ).fetchone()
    idx._conn.execute(f"INSERT INTO {idx._chunks_vec_table}(rowid, embedding) VALUES (?, ?)",
                      (row["rowid"], struct.pack(f"{dims}f", 1.0, 0.0, 0.0, 0.0)))
    idx._conn.commit()

    blob = struct.pack(f"{dims}f", 1.0, 0.0, 0.0, 0.0)
    results = idx._vec_chunks_search(
        blob, sources=["library"], kind=None, tags=None, tag_match_mode="any",
        created_after=None, created_before=None, limit=10,
    )
    assert len(results) >= 1
    r = results[0]
    assert r.chunk_index == 2, f"Expected chunk_index=2, got {r.chunk_index}"
    assert r.start_line == 8, f"Expected start_line=8, got {r.start_line}"
    idx.close()


def test_hybrid_rrf_both_legs_boost_same_chunk(tmp_path):
    """A chunk matching both FTS and injected vec embedding appears in hybrid search results."""
    from co_cli.knowledge._chunker import Chunk

    dims = 4
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none", knowledge_embedding_dims=dims))
    idx.index(source="library", kind="article", path="/boost.md",
              title="Boost Doc", content="intro", hash="hb", mtime=0.0)
    idx.index_chunks("library", "/boost.md", [
        Chunk(index=0, content="zygomorphic-rrf-boost-test unique phrase here",
              start_line=0, end_line=2),
    ])

    row = idx._conn.execute(
        "SELECT rowid FROM chunks WHERE source='library' AND doc_path='/boost.md' AND chunk_index=0"
    ).fetchone()
    idx._conn.execute(f"INSERT INTO {idx._chunks_vec_table}(rowid, embedding) VALUES (?, ?)",
                      (row["rowid"], struct.pack(f"{dims}f", 1.0, 0.0, 0.0, 0.0)))
    idx._conn.commit()

    results = idx.search("zygomorphic-rrf-boost-test")
    assert any(r.path == "/boost.md" for r in results), (
        "Chunk matching both FTS and vec legs must appear in hybrid search results"
    )
    idx.close()


def test_hybrid_memory_uses_docs_vec_not_chunks_vec(tmp_path):
    """Memory source results in hybrid search have chunk_index=None — memory is indexed at doc level."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="hybrid", knowledge_embedding_provider="none"))
    idx.index(source="memory", kind="memory", path="/mem-vec.md",
              title="Memory Vec Doc", content="xyloquartz-memory-vec-test unique term",
              hash="hmv", mtime=0.0)

    results = idx.search("xyloquartz-memory-vec-test")
    assert len(results) >= 1
    assert all(r.chunk_index is None for r in results), (
        "Memory results must have chunk_index=None — memory is indexed at doc level, not chunked"
    )


# ---------------------------------------------------------------------------
# Group 5 — TEI provider tests (Stream B)
# ---------------------------------------------------------------------------


def test_tei_rerank_falls_back_on_connection_error(tmp_path):
    """search() with a dead TEI reranker host falls back gracefully — no exception, returns results."""
    idx = KnowledgeIndex(config=CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_cross_encoder_reranker_url="http://127.0.0.1:19999",
    ))
    for i in range(5):
        idx.index(source="memory", kind="memory", path=f"/tei-fallback{i}.md",
                  title=f"Tei Fallback {i}", content=f"xyloquartz-tei-fallback unique content {i}",
                  hash=f"h{i}", mtime=float(i))
    results = idx.search("xyloquartz-tei-fallback", limit=3)
    assert len(results) == 3
    idx.close()


