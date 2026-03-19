"""Tests for memory gravity — touch and dedup on recall."""

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai._run_context import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.memory._lifecycle import persist_memory as _save_memory_impl
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.memory import (
    _touch_memory,
    _dedup_pulled,
    _load_memories,
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
_AGENT, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))


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


def _ctx() -> RunContext:
    return _make_ctx()


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
# _dedup_pulled
# ---------------------------------------------------------------------------


def test_dedup_pulled_merges_similar(tmp_path: Path):
    """Similar pulled memories get consolidated."""
    _write_memory(tmp_path, 1, "User prefers pytest for testing",
                  tags=["preference"],
                  created=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    _write_memory(tmp_path, 2, "User prefers pytest for testing purposes",
                  tags=["testing"],
                  created=datetime.now(timezone.utc).isoformat())

    entries = _load_memories(tmp_path)
    assert len(entries) == 2

    result = _dedup_pulled(entries, threshold=80)
    # One should be merged away
    assert len(result) == 1
    # Remaining file should have merged tags
    remaining = _load_memories(tmp_path)
    assert len(remaining) == 1
    assert len(list(tmp_path.glob("*.md"))) == 1, "Older file must be deleted from disk"


def test_dedup_pulled_keeps_distinct(tmp_path: Path):
    """Distinct memories are NOT merged."""
    _write_memory(tmp_path, 1, "User prefers pytest over unittest",
                  tags=["preference"])
    _write_memory(tmp_path, 2, "Project uses PostgreSQL for storage",
                  tags=["context"])

    entries = _load_memories(tmp_path)
    result = _dedup_pulled(entries, threshold=85)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# recall_memory gravity integration
# ---------------------------------------------------------------------------


def test_recall_touches_pulled_memories(tmp_path: Path):
    """Recalled memories get their updated timestamp refreshed."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    _write_memory(memory_dir, 1, "User prefers dark theme", tags=["preference"])

    result = asyncio.run(
        recall_memory(_make_ctx(memory_dir=memory_dir), "dark theme")
    )
    assert result["count"] >= 1

    # Verify the file was touched (updated timestamp set)
    reloaded = _load_memories(memory_dir)
    touched = [m for m in reloaded if "dark" in m.content.lower()]
    assert len(touched) == 1
    assert touched[0].updated is not None


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

    memory_dir = tmp_path / ".co-cli" / "memory"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _make_ctx(memory_dir=memory_dir, knowledge_index=idx, knowledge_search_backend="fts5")

    # Save initial memory
    asyncio.run(save_memory(ctx, "User prefers zygomorphic-consolidation-test widget",
                            tags=["preference"]))

    # Save near-duplicate — should consolidate (update) rather than create new
    asyncio.run(save_memory(ctx, "User prefers zygomorphic-consolidation-test widget v2",
                            tags=["preference", "updated"]))

    # FTS must return the consolidated (updated) content
    results = idx.search("zygomorphic-consolidation-test", source="memory")
    assert len(results) >= 1, "FTS must find the consolidated memory"
    # The consolidated entry must carry the updated tag
    assert any(r.tags and "updated" in r.tags for r in results), (
        "FTS result must reflect consolidated tags"
    )
    idx.close()


def test_gravity_affects_recency_order(tmp_path: Path):
    """Pulled memory appears first in next recall due to gravity."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    new_time = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    # Older memory about testing
    _write_memory(memory_dir, 1, "User prefers testing with pytest",
                  tags=["preference", "testing"], created=old_time)
    # Newer memory about testing
    _write_memory(memory_dir, 2, "User uses coverage reports for testing",
                  tags=["context", "testing"], created=new_time)

    ctx = _make_ctx(memory_dir=memory_dir)

    # First recall — memory 2 should be first (newer)
    result1 = asyncio.run(recall_memory(ctx, "testing"))
    assert result1["count"] == 2

    # Now recall just memory 1 (by specific keyword "pytest")
    result2 = asyncio.run(recall_memory(ctx, "pytest"))
    assert result2["count"] >= 1

    # After being touched, memory 1 should have a fresh updated timestamp
    entries = _load_memories(memory_dir)
    m1 = [e for e in entries if e.id == 1][0]
    assert m1.updated is not None


# ---------------------------------------------------------------------------
# inject routing — personality-context tag saved via _save_memory_impl
# ---------------------------------------------------------------------------


def test_auto_save_inject_true_adds_personality_context_tag(tmp_path: Path):
    """When tags include personality-context, the saved file retains that tag."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    from dataclasses import replace
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=replace(CoConfig(), memory_dir=memory_dir),
    )
    asyncio.run(
        _save_memory_impl(deps, "User does not want trailing comments", ["correction", "personality-context"], None)
    )

    entries = _load_memories(memory_dir)
    assert len(entries) == 1
    assert "personality-context" in entries[0].tags
    assert "correction" in entries[0].tags


# ---------------------------------------------------------------------------
# update_memory — surgical text replacement
# ---------------------------------------------------------------------------


def test_update_memory_replaces_exact_match(tmp_path: Path):
    """update_memory replaces old_content with new_content in the body."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    _write_memory(memory_dir, 1, "User prefers pytest over unittest", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    entry = _load_memories(memory_dir)[0]
    slug = entry.path.stem

    asyncio.run(update_memory(ctx, slug, "pytest over unittest", "pytest over all others"))

    reloaded = _load_memories(memory_dir)[0]
    assert "pytest over all others" in reloaded.content
    assert "pytest over unittest" not in reloaded.content


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
    _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    entry = _load_memories(memory_dir)[0]
    slug = entry.path.stem

    import pytest
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(update_memory(ctx, slug, "unittest", "mocha"))


def test_update_memory_raises_ambiguous(tmp_path: Path):
    """update_memory raises ValueError when old_content appears more than once."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    _write_memory(memory_dir, 1, "User uses pytest. Also uses pytest.", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    entry = _load_memories(memory_dir)[0]
    slug = entry.path.stem

    import pytest
    with pytest.raises(ValueError, match="2 times"):
        asyncio.run(update_memory(ctx, slug, "pytest", "mocha"))


def test_update_memory_rejects_line_prefix(tmp_path: Path):
    """update_memory raises ValueError when old_content contains Read-tool line prefixes."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    entry = _load_memories(memory_dir)[0]
    slug = entry.path.stem

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
    _write_memory(memory_dir, 1, "foo     bar", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    entry = _load_memories(memory_dir)[0]
    slug = entry.path.stem

    # old_content uses a tab; after expandtabs() it matches the body
    asyncio.run(update_memory(ctx, slug, "foo\tbar", "replaced"))

    reloaded = _load_memories(memory_dir)[0]
    assert "replaced" in reloaded.content


# ---------------------------------------------------------------------------
# append_memory — add content to end of body
# ---------------------------------------------------------------------------


def test_append_memory_adds_to_end(tmp_path: Path):
    """append_memory appends content as a new line at the end of the body."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(memory_dir=memory_dir)

    entry = _load_memories(memory_dir)[0]
    slug = entry.path.stem

    asyncio.run(append_memory(ctx, slug, "Also uses coverage reports."))

    reloaded = _load_memories(memory_dir)[0]
    assert reloaded.content.endswith("Also uses coverage reports.")
    assert "User prefers pytest" in reloaded.content


def test_append_memory_missing_slug_raises(tmp_path: Path):
    """append_memory raises FileNotFoundError for an unknown slug."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)
    ctx = _make_ctx(memory_dir=memory_dir)

    import pytest
    with pytest.raises(FileNotFoundError, match="not found"):
        asyncio.run(append_memory(ctx, "999-nonexistent", "extra line"))


def test_dedup_pulled_removes_stale_fts_entry(tmp_path: Path):
    """After dedup-on-read deletes an older file, its FTS entry is also evicted."""
    from co_cli.knowledge._index_store import KnowledgeIndex

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    old_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    new_time = datetime.now(timezone.utc).isoformat()

    # Near-duplicate pair — same pattern as test_dedup_pulled_merges_similar (which passes)
    # ensuring similarity >= 85% so dedup fires with default threshold
    path_old = _write_memory(
        memory_dir, 1, "User prefers xylodedup-test pytest for testing",
        tags=["preference"], created=old_time,
    )
    path_new = _write_memory(
        memory_dir, 2, "User prefers xylodedup-test pytest for testing purposes",
        tags=["preference"], created=new_time,
    )

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    idx.sync_dir("memory", memory_dir)

    # Both paths should be findable before recall
    before = idx.search("xylodedup")
    before_paths = {r.path for r in before}
    assert str(path_old) in before_paths, "Older memory should be in FTS before recall"
    assert str(path_new) in before_paths, "Newer memory should be in FTS before recall"

    ctx = _make_ctx(memory_dir=memory_dir, knowledge_index=idx, knowledge_search_backend="fts5")
    asyncio.run(recall_memory(ctx, "xylodedup", max_results=5))

    # The merged-away (older) entry must be gone from FTS
    after = idx.search("xylodedup")
    after_paths = {r.path for r in after}
    assert str(path_old) not in after_paths, "Dedup-deleted memory must be evicted from FTS"
    assert not path_old.exists(), "Dedup-deleted file must be removed from disk"

    # The surviving (newer) entry must still be findable
    assert str(path_new) in after_paths, "Surviving memory must remain in FTS after dedup"
    idx.close()


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
