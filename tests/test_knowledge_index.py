"""Functional tests for KnowledgeIndex — FTS5 search, sync, rebuild."""

import hashlib
from pathlib import Path

import pytest
import yaml

from co_cli.knowledge_index import KnowledgeIndex, SearchResult


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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(source="memory", path="/p1.md", title="test", content="hello world",
              hash=_sha256("hello world"), mtime=0.0)
    results = idx.search("the a an")
    assert results == []
    idx.close()


def test_search_filters_by_source(tmp_path):
    """search() with source= returns only matching source."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(source="memory", path="/mem.md", title="memo", content="pytest memory item",
              hash=_sha256("pytest memory"), mtime=0.0)
    idx.index(source="obsidian", path="/obs.md", title="obsidian", content="pytest obsidian note",
              hash=_sha256("pytest obsidian"), mtime=0.0)

    mem_results = idx.search("pytest", source="memory")
    assert all(r.source == "memory" for r in mem_results)
    obs_results = idx.search("pytest", source="obsidian")
    assert all(r.source == "obsidian" for r in obs_results)
    idx.close()


def test_search_filters_by_tags(tmp_path):
    """search() with tags= returns only docs whose tags field matches."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(source="memory", kind="memory", path="/p/python.md", title="Python Guide",
              content="Guide to Python asyncio", mtime=1.0, hash="a", tags="python asyncio")
    idx.index(source="memory", kind="memory", path="/p/rust.md", title="Rust Guide",
              content="Guide to Rust ownership", mtime=1.0, hash="b", tags="rust systems")
    python_results = idx.search("guide", tags=["python"])
    rust_results = idx.search("guide", tags=["rust"])
    assert len(python_results) == 1 and Path(python_results[0].path).name == "python.md"
    assert len(rust_results) == 1 and Path(rust_results[0].path).name == "rust.md"
    idx.close()


def test_search_filters_by_kind(tmp_path):
    """search() with kind= returns only matching kind."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(source="memory", kind="memory", path="/mem.md", title="memo",
              content="pytest preference", hash=_sha256("mem"), mtime=0.0)
    idx.index(source="memory", kind="article", path="/art.md", title="article",
              content="pytest reference guide", hash=_sha256("art"), mtime=0.0)

    memory_results = idx.search("pytest", kind="memory")
    assert all(r.kind == "memory" for r in memory_results)
    article_results = idx.search("pytest", kind="article")
    assert all(r.kind == "article" for r in article_results)
    idx.close()


# ---------------------------------------------------------------------------
# sync_dir()
# ---------------------------------------------------------------------------


def test_sync_dir_indexes_new_files(tmp_path):
    """sync_dir() indexes new markdown files."""
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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


def test_sync_dir_folder_scoped_does_not_evict_other_folders(tmp_path):
    """Syncing a subfolder does not evict FTS entries from sibling folders."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    vault = tmp_path / "vault"
    personal = vault / "Personal"
    work = vault / "Work"

    # Index the full vault first
    _write_md(personal, "diary.md", "Personal diary entry about hiking",
              {"id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": []})
    _write_md(work, "report.md", "Work quarterly report for zygomorphic-scoped project",
              {"id": 2, "kind": "memory", "created": "2026-01-01T00:00:00+00:00", "tags": []})
    idx.sync_dir("obsidian", vault)

    count_before = idx._conn.execute(
        "SELECT COUNT(*) FROM docs WHERE source='obsidian'"
    ).fetchone()[0]
    assert count_before == 2

    # Now sync only the Work subfolder (simulates a folder-scoped search_notes call)
    idx.sync_dir("obsidian", work)

    # Personal entry must still be in the index
    row = idx._conn.execute(
        "SELECT * FROM docs WHERE path LIKE '%diary.md'"
    ).fetchone()
    assert row is not None, "Personal folder entry must survive a Work-only sync"
    idx.close()


# ---------------------------------------------------------------------------
# FTS round-trip with save_memory
# ---------------------------------------------------------------------------


def test_fts_roundtrip_save_and_recall(tmp_path):
    """Save a memory with FTS index, then recall it via search()."""
    from dataclasses import dataclass, field
    from typing import Any
    import asyncio

    idx = KnowledgeIndex(tmp_path / "search.db")

    @dataclass
    class _Deps:
        memory_max_count: int = 200
        memory_dedup_window_days: int = 7
        memory_dedup_threshold: int = 85
        memory_decay_strategy: str = "summarize"
        memory_decay_percentage: float = 0.2
        knowledge_index: Any = None
        knowledge_search_backend: str = "fts5"
        memory_dir: Path = field(default_factory=lambda: Path(".co-cli/memory"))
        library_dir: Path = field(default_factory=lambda: Path(".co-cli/library"))

    class _Ctx:
        def __init__(self, deps):
            self._deps = deps
        @property
        def deps(self):
            return self._deps
        @property
        def model(self):
            return None

    deps = _Deps(knowledge_index=idx)
    ctx = _Ctx(deps)

    import os
    orig = os.getcwd()
    os.chdir(tmp_path)
    try:
        from co_cli.tools.memory import save_memory
        asyncio.run(save_memory(ctx, "User loves zygomorphic-fts5-test widget framework",
                                tags=["preference"]))
        results = idx.search("zygomorphic-fts5-test", source="memory")
        assert len(results) >= 1, "FTS should find the saved memory"
    finally:
        os.chdir(orig)
        idx.close()


# ---------------------------------------------------------------------------
# doc_tags junction table — tag_match_mode + temporal filtering
# ---------------------------------------------------------------------------


def test_tag_match_mode_all_returns_only_items_with_all_tags(tmp_path):
    """search(tag_match_mode='all') returns only docs that have every requested tag."""
    idx = KnowledgeIndex(tmp_path / "search.db")
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


def test_tag_match_mode_any_returns_items_with_any_tag(tmp_path):
    """search(tag_match_mode='any') returns docs that have at least one requested tag."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(source="memory", kind="memory", path="/a.md", title="Doc A",
              content="doc about anytag filtering",
              hash="a", mtime=0.0, tags="python")
    idx.index(source="memory", kind="memory", path="/b.md", title="Doc B",
              content="doc about anytag filtering",
              hash="b", mtime=0.0, tags="async")
    results = idx.search("anytag", tags=["python", "async"], tag_match_mode="any")
    assert len(results) == 2
    idx.close()



def test_created_after_filters_older_items(tmp_path):
    """search(created_after=) excludes docs created before that date."""
    idx = KnowledgeIndex(tmp_path / "search.db")
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


def test_created_before_filters_newer_items(tmp_path):
    """search(created_before=) excludes docs created after that date."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(source="memory", kind="memory", path="/a.md", title="Doc A",
              content="doc about temporal range filtering",
              hash="a", mtime=0.0, created="2024-01-01T00:00:00+00:00")
    idx.index(source="memory", kind="memory", path="/b.md", title="Doc B",
              content="doc about temporal range filtering",
              hash="b", mtime=0.0, created="2025-01-01T00:00:00+00:00")
    results = idx.search("temporal", created_before="2024-06-01")
    assert len(results) == 1
    assert Path(results[0].path).name == "a.md"
    idx.close()



def test_search_tag_mode_all_with_duplicate_tags_returns_match(tmp_path):
    """search() with duplicate tags in filter list must not silently return 0 results."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(source="memory", kind="memory", path="/a.md", title="Doc A",
              content="doc about duplicate tag filter test",
              hash="a", mtime=0.0, tags="python")
    results = idx.search("duplicate", tags=["python", "python"], tag_match_mode="all")
    assert len(results) == 1
    idx.close()


def test_combined_tags_tag_match_mode_and_created_after(tmp_path):
    """search() with tags (all mode) + created_after returns only the matching doc."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(source="memory", kind="memory", path="/a.md", title="Doc A",
              content="doc about combined junction filtering",
              hash="a", mtime=0.0, tags="python async", created="2025-01-01T00:00:00+00:00")
    idx.index(source="memory", kind="memory", path="/b.md", title="Doc B",
              content="doc about combined junction filtering",
              hash="b", mtime=0.0, tags="python", created="2024-01-01T00:00:00+00:00")
    results = idx.search(
        "combined",
        tags=["python", "async"],
        tag_match_mode="all",
        created_after="2024-06-01",
    )
    assert len(results) == 1
    assert Path(results[0].path).name == "a.md"
    idx.close()


# ---------------------------------------------------------------------------
# Phase 2 — Hybrid search (sqlite-vec)
# ---------------------------------------------------------------------------


def test_hybrid_schema_creates_vec_table(tmp_path):
    """KnowledgeIndex with backend='hybrid' creates the docs_vec virtual table."""
    idx = KnowledgeIndex(tmp_path / "search.db", backend="hybrid", embedding_provider="none")
    tables = {
        row[0]
        for row in idx._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
        ).fetchall()
    }
    assert idx._backend == "hybrid"
    assert "docs_vec" in tables or any("vec" in t for t in tables), (
        f"docs_vec not found in schema. Tables: {tables}"
    )
    idx.close()


def test_hybrid_provider_none_uses_fts_leg_in_hybrid(tmp_path):
    """provider='none' in hybrid backend still returns lexical FTS results."""
    idx = KnowledgeIndex(tmp_path / "search.db", backend="hybrid", embedding_provider="none")
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
    idx = KnowledgeIndex(tmp_path / "search.db", backend="hybrid", embedding_provider="none")
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


def test_reranker_provider_none_is_passthrough(tmp_path):
    """_rerank_results() with provider='none' returns candidates[:limit] unchanged."""
    idx = KnowledgeIndex(tmp_path / "search.db", reranker_provider="none")
    candidates = [
        SearchResult(
            source="memory", kind="memory", path=f"/doc{i}.md",
            title=f"Doc {i}", snippet=None, score=float(i),
            tags=None, category=None, created=None, updated=None,
        )
        for i in range(5)
    ]
    result = idx._rerank_results("test query", candidates, limit=3)
    assert result == candidates[:3]
    idx.close()


def test_rerank_falls_back_on_error(tmp_path):
    """_rerank_results() with a dead Ollama host returns candidates[:limit], no exception."""
    idx = KnowledgeIndex(
        tmp_path / "search.db",
        reranker_provider="ollama",
        ollama_host="http://localhost:19999",
        reranker_model="qwen2.5:3b",
    )
    candidates = [
        SearchResult(
            source="memory", kind="memory", path=f"/doc{i}.md",
            title=f"Doc {i}", snippet=None, score=float(i),
            tags=None, category=None, created=None, updated=None,
        )
        for i in range(5)
    ]
    result = idx._rerank_results("test query", candidates, limit=3)
    assert len(result) == 3
    assert result == candidates[:3]
    idx.close()


def test_ollama_listwise_rerank_reorders_results(tmp_path):
    """_rerank_results() with Ollama reranker places the relevant doc first."""
    import httpx
    model = "qwen2.5:3b"
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        available = {m["name"] for m in resp.json().get("models", [])}
        # Match exact name or name without tag (e.g. "qwen2.5:3b" matches "qwen2.5:3b")
        if not any(m == model or m.startswith(model.split(":")[0] + ":") for m in available):
            pytest.skip(f"Ollama model {model!r} not installed")
    except Exception:
        pytest.skip("Ollama not reachable")

    idx = KnowledgeIndex(
        tmp_path / "search.db",
        reranker_provider="ollama",
        reranker_model="qwen2.5:3b",
    )
    idx.index(
        source="memory", path="/irrelevant.md",
        title="Cooking recipes",
        content="How to bake chocolate cake with flour and eggs",
        hash="h1", mtime=0.0,
    )
    idx.index(
        source="memory", path="/relevant.md",
        title="asyncio concurrency",
        content="Python asyncio event loop concurrency patterns for async programming",
        hash="h2", mtime=0.0,
    )

    # Place irrelevant doc first (higher initial FTS score) to confirm reranking flips order
    candidates = [
        SearchResult(
            source="memory", kind="memory", path="/irrelevant.md",
            title="Cooking recipes", snippet=None, score=0.9,
            tags=None, category=None, created=None, updated=None,
        ),
        SearchResult(
            source="memory", kind="memory", path="/relevant.md",
            title="asyncio concurrency", snippet=None, score=0.5,
            tags=None, category=None, created=None, updated=None,
        ),
    ]
    result = idx._rerank_results("asyncio concurrency patterns", candidates, limit=2)
    assert len(result) == 2
    assert result[0].path == "/relevant.md", "Relevant doc should rank first after reranking"
    idx.close()



# ---------------------------------------------------------------------------
# TASK-2: provenance + certainty schema extension
# ---------------------------------------------------------------------------


def test_searchresult_has_provenance_and_certainty_fields(tmp_path):
    """SearchResult dataclass has provenance and certainty fields (both default None)."""
    r = SearchResult(
        source="memory", kind="memory", path="/x.md",
        title=None, snippet=None, score=0.5,
        tags=None, category=None, created=None, updated=None,
    )
    assert hasattr(r, "provenance")
    assert hasattr(r, "certainty")
    assert r.provenance is None
    assert r.certainty is None


def test_index_stores_provenance_and_certainty(tmp_path):
    """index() writes provenance and certainty into the docs table."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(
        source="memory", kind="memory", path="/p.md",
        title="Prov test", content="provenance certainty test content",
        hash="h1", mtime=0.0,
        provenance="user-told", certainty="high",
    )
    row = idx._conn.execute("SELECT provenance, certainty FROM docs WHERE path='/p.md'").fetchone()
    assert row["provenance"] == "user-told"
    assert row["certainty"] == "high"
    idx.close()


def test_fts_search_returns_provenance_and_certainty(tmp_path):
    """search() FTS path returns populated provenance and certainty on SearchResult."""
    idx = KnowledgeIndex(tmp_path / "search.db")
    idx.index(
        source="memory", kind="memory", path="/prov.md",
        title="Prov search", content="prov-certainty-fts unique test token",
        hash="h2", mtime=0.0,
        provenance="detected", certainty="low",
    )
    results = idx.search("prov-certainty-fts")
    assert len(results) >= 1
    r = results[0]
    assert r.provenance == "detected"
    assert r.certainty == "low"
    idx.close()


def test_sync_dir_propagates_provenance_and_certainty(tmp_path):
    """sync_dir() reads provenance/certainty from frontmatter and stores them."""
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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
    idx = KnowledgeIndex(tmp_path / "search.db")
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



def test_local_cross_encoder_reranks_correctly(tmp_path):
    """_local_cross_encoder_rerank() places the relevant doc first when fastembed is installed."""
    pytest.importorskip("fastembed")

    idx = KnowledgeIndex(
        tmp_path / "search.db",
        reranker_provider="local",
        reranker_model="BAAI/bge-reranker-base",
    )
    idx.index(
        source="memory", path="/off1.md",
        title="chocolate cake recipe",
        content="How to bake a chocolate cake with flour, sugar and eggs",
        hash="h1", mtime=0.0,
    )
    idx.index(
        source="memory", path="/off2.md",
        title="gardening tips",
        content="How to grow tomatoes in your backyard garden",
        hash="h2", mtime=0.0,
    )
    idx.index(
        source="memory", path="/relevant.md",
        title="asyncio concurrency patterns",
        content="Python asyncio event loop enables concurrent async programming with coroutines",
        hash="h3", mtime=0.0,
    )
    results = idx.search("asyncio concurrency", limit=3)
    assert len(results) >= 1
    assert results[0].path == "/relevant.md", (
        f"Expected /relevant.md first, got: {[r.path for r in results]}"
    )
    idx.close()
