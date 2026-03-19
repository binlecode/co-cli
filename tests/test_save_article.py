"""Functional tests for article tools — save, recall, read, list filter."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai._run_context import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.articles import save_article, recall_article, read_article_detail, search_knowledge
from co_cli.tools.memory import list_memories, save_memory
from co_cli.knowledge._frontmatter import parse_frontmatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))


def _make_ctx(
    *,
    library_dir: Path | None = None,
    memory_dir: Path | None = None,
    knowledge_index: Any = None,
    knowledge_search_backend: str = "grep",
) -> RunContext:
    """Return a real RunContext with real CoDeps for article tool tests."""
    from dataclasses import replace
    config = CoConfig(knowledge_search_backend=knowledge_search_backend)
    if library_dir is not None:
        config = replace(config, library_dir=library_dir)
    if memory_dir is not None:
        config = replace(config, memory_dir=memory_dir)
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), knowledge_index=knowledge_index),
        config=config,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _ctx_with_idx(idx: Any, *, library_dir: Path | None = None, memory_dir: Path | None = None) -> RunContext:
    """Return a real RunContext with FTS5 backend and the given KnowledgeIndex."""
    return _make_ctx(
        library_dir=library_dir,
        memory_dir=memory_dir,
        knowledge_index=idx,
        knowledge_search_backend="fts5",
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# save_article
# ---------------------------------------------------------------------------


def test_save_article_writes_correct_frontmatter(tmp_path):
    """save_article writes kind:article, origin_url, provenance:web-fetch, decay_protected:true."""
    library_dir = tmp_path / ".co-cli" / "library"
    ctx = _make_ctx(library_dir=library_dir)

    result = _run(save_article(
        ctx,
        content="## Python Asyncio Guide\n\nAsyncio is a library...",
        title="Python Asyncio Guide",
        origin_url="https://docs.python.org/3/library/asyncio.html",
        tags=["python", "asyncio", "reference"],
    ))

    assert result["action"] == "saved"
    assert result["article_id"] is not None

    library_dir = tmp_path / ".co-cli" / "library"
    files = list(library_dir.glob("*.md"))
    assert len(files) == 1

    raw = files[0].read_text(encoding="utf-8")
    fm, body = parse_frontmatter(raw)

    assert fm["kind"] == "article"
    assert fm["origin_url"] == "https://docs.python.org/3/library/asyncio.html"
    assert fm["provenance"] == "web-fetch"
    assert fm["decay_protected"] is True
    assert fm["title"] == "Python Asyncio Guide"
    assert "python" in fm["tags"]
    assert "asyncio" in fm["tags"]
    assert "Asyncio is a library" in body


def test_save_article_dedup_by_origin_url(tmp_path):
    """Saving the same origin_url twice consolidates rather than creating duplicate."""
    library_dir = tmp_path / ".co-cli" / "library"
    ctx = _make_ctx(library_dir=library_dir)

    result1 = _run(save_article(
        ctx,
        content="First version of content",
        title="My Article",
        origin_url="https://example.com/article",
        tags=["python"],
    ))
    assert result1["action"] == "saved"

    result2 = _run(save_article(
        ctx,
        content="Updated version of content",
        title="My Article v2",
        origin_url="https://example.com/article",
        tags=["python", "updated"],
    ))
    assert result2["action"] == "consolidated"
    assert result2["article_id"] == result1["article_id"]

    # Only one file should exist
    library_dir = tmp_path / ".co-cli" / "library"
    files = list(library_dir.glob("*.md"))
    assert len(files) == 1

    raw = files[0].read_text(encoding="utf-8")
    fm, body = parse_frontmatter(raw)
    # Tags merged
    assert "updated" in fm["tags"]
    # Content updated
    assert "Updated version" in body


# ---------------------------------------------------------------------------
# recall_article
# ---------------------------------------------------------------------------


def test_recall_article_returns_summary_only(tmp_path):
    """recall_article returns first paragraph, not full body."""
    library_dir = tmp_path / ".co-cli" / "library"
    ctx = _make_ctx(library_dir=library_dir)

    long_content = (
        "First paragraph about asyncio basics.\n\n"
        "Second paragraph with much more detail that should not appear in summary. " * 20
    )
    _run(save_article(
        ctx,
        content=long_content,
        title="Asyncio Deep Dive",
        origin_url="https://example.com/asyncio",
        tags=["asyncio"],
    ))

    result = _run(recall_article(ctx, "asyncio"))
    assert result["count"] >= 1
    # Snippet is first paragraph only, not the full 20-repetition content
    snippet = result["results"][0]["snippet"]
    assert len(snippet) <= 210  # 200 chars + "..."
    assert "Second paragraph" not in snippet


def test_recall_article_no_match(tmp_path):
    """recall_article returns count=0 when no match found."""
    library_dir = tmp_path / ".co-cli" / "library"
    library_dir.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(library_dir=library_dir)

    result = _run(recall_article(ctx, "xyzunmatchable999"))
    assert result["count"] == 0
    assert result["results"] == []


# ---------------------------------------------------------------------------
# read_article_detail
# ---------------------------------------------------------------------------


def test_read_article_detail_returns_full_body(tmp_path):
    """read_article_detail returns full markdown body."""
    library_dir = tmp_path / ".co-cli" / "library"
    ctx = _make_ctx(library_dir=library_dir)

    long_body = "# Full Content\n\n" + ("Detailed paragraph. " * 100)
    _run(save_article(
        ctx,
        content=long_body,
        title="Full Article",
        origin_url="https://example.com/full",
        tags=["full"],
    ))

    files = list(library_dir.glob("*.md"))
    slug = files[0].stem

    result = _run(read_article_detail(ctx, slug))
    assert result["content"] is not None
    assert len(result["content"]) > 200
    assert "Detailed paragraph." in result["content"]
    assert result["origin_url"] == "https://example.com/full"
    assert result["title"] == "Full Article"


def test_read_article_detail_prefix_match(tmp_path):
    """read_article_detail finds article via prefix glob when exact slug not given."""
    library_dir = tmp_path / ".co-cli" / "library"
    ctx = _make_ctx(library_dir=library_dir)
    _run(save_article(
        ctx,
        content="Full content here.",
        title="Prefix Match Article",
        origin_url="https://example.com/prefix-match",
        tags=[],
    ))
    full_slug = list(library_dir.glob("*.md"))[0].stem
    partial = full_slug[:3]
    result = _run(read_article_detail(ctx, partial))
    assert result["content"] is not None
    assert result["title"] == "Prefix Match Article"


def test_read_article_detail_not_found(tmp_path):
    """read_article_detail returns None content for unknown slug."""
    library_dir = tmp_path / ".co-cli" / "library"
    library_dir.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(library_dir=library_dir)

    result = _run(read_article_detail(ctx, "999-nonexistent-slug"))
    assert result["content"] is None
    assert result["article_id"] is None


# ---------------------------------------------------------------------------
# FTS metadata parity
# ---------------------------------------------------------------------------


def test_recall_article_fts_metadata_parity(tmp_path):
    """recall_article via FTS returns article_id and origin_url (not None)."""
    from co_cli.knowledge._index_store import KnowledgeIndex

    library_dir = tmp_path / ".co-cli" / "library"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _ctx_with_idx(idx, library_dir=library_dir)

    _run(save_article(
        ctx,
        content="Comprehensive guide to zygomorphic-fts-article widget API",
        title="Zygomorphic Widget Guide",
        origin_url="https://example.com/zygomorphic-guide",
        tags=["reference", "widget"],
    ))

    result = _run(recall_article(ctx, "zygomorphic-fts-article"))
    assert result["count"] >= 1, "FTS must find the saved article"

    first = result["results"][0]
    assert first["article_id"] is not None, "article_id must not be None in FTS mode"
    assert first["origin_url"] == "https://example.com/zygomorphic-guide", (
        "origin_url must be populated from frontmatter in FTS mode"
    )
    idx.close()


# ---------------------------------------------------------------------------
# FTS integration tests
# ---------------------------------------------------------------------------


def test_fts_article_consolidated_tags_indexed(tmp_path):
    """Consolidated article reindex carries merged tags (Bug #1 regression guard).

    Saving the same origin_url twice merges tags on disk AND in the FTS index.
    Both the retained tag and the new tag must be discoverable via tag search.
    """
    from co_cli.knowledge._index_store import KnowledgeIndex

    library_dir = tmp_path / ".co-cli" / "library"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _ctx_with_idx(idx, library_dir=library_dir)

    _run(save_article(
        ctx,
        content="Guide to zygomorphic-consol asyncio patterns",
        title="Zygomorphic Consol Guide",
        origin_url="https://example.com/zygomorphic-consol",
        tags=["python"],
    ))

    _run(save_article(
        ctx,
        content="Updated guide to zygomorphic-consol asyncio patterns",
        title="Zygomorphic Consol Guide v2",
        origin_url="https://example.com/zygomorphic-consol",
        tags=["async"],
    ))

    results_python = idx.search("zygomorphic-consol", tags=["python"])
    results_async = idx.search("zygomorphic-consol", tags=["async"])

    assert len(results_python) >= 1, "Retained tag 'python' must remain indexed after consolidation"
    assert len(results_async) >= 1, "New tag 'async' must be indexed after consolidation"
    idx.close()


def test_recall_article_fts_return_contract(tmp_path):
    """recall_article FTS path returns correct field types.

    Validates schema parity: article_id (int), origin_url (str),
    tags (list[str]), snippet (str), slug (str).
    """
    from co_cli.knowledge._index_store import KnowledgeIndex

    library_dir = tmp_path / ".co-cli" / "library"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _ctx_with_idx(idx, library_dir=library_dir)

    _run(save_article(
        ctx,
        content="Comprehensive reference on zygomorphic-contract widget patterns",
        title="Zygomorphic Contract Guide",
        origin_url="https://example.com/zygomorphic-contract",
        tags=["reference"],
    ))

    result = _run(recall_article(ctx, "zygomorphic-contract"))
    assert result["count"] >= 1

    first = result["results"][0]
    assert isinstance(first["article_id"], int), "article_id must be int"
    assert isinstance(first["origin_url"], str), "origin_url must be str"
    assert isinstance(first["tags"], list), "tags must be list"
    assert all(isinstance(t, str) for t in first["tags"]), "tags must be list[str]"
    assert isinstance(first["snippet"], str), "snippet must be str"
    assert isinstance(first["slug"], str), "slug must be str"
    idx.close()


def test_search_knowledge_fts_kind_filter(tmp_path):
    """search_knowledge FTS path respects kind filter.

    Saves one article and one memory both matching the query keyword.
    kind='article' returns only articles; kind='memory' returns only memories.
    """
    from co_cli.knowledge._index_store import KnowledgeIndex

    library_dir = tmp_path / ".co-cli" / "library"
    memory_dir = tmp_path / ".co-cli" / "memory"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _ctx_with_idx(idx, library_dir=library_dir, memory_dir=memory_dir)

    _run(save_article(
        ctx,
        content="Reference on zygomorphic-kindfilter patterns",
        title="Zygomorphic Kindfilter Article",
        origin_url="https://example.com/zygomorphic-kindfilter",
        tags=["reference"],
    ))
    _run(save_memory(
        ctx,
        "User worked with zygomorphic-kindfilter tools extensively",
        tags=["context"],
    ))

    # Default scope covers local articles — kind="article" finds without source override
    article_result = _run(search_knowledge(ctx, "zygomorphic-kindfilter", kind="article"))
    assert article_result["count"] >= 1
    assert all(r["kind"] == "article" for r in article_result["results"])

    # Memories are excluded from default scope — must specify source="memory" explicitly
    memory_result = _run(search_knowledge(ctx, "zygomorphic-kindfilter", source="memory", kind="memory"))
    assert memory_result["count"] >= 1
    assert all(r["kind"] == "memory" for r in memory_result["results"])
    idx.close()


def test_search_knowledge_fallback_grep_kind_filter(tmp_path):
    """search_knowledge grep fallback (no FTS index) respects kind filter.

    Saves one article. kind='article' must find it; kind='memory' must return 0.
    """
    library_dir = tmp_path / ".co-cli" / "library"
    ctx = _make_ctx(library_dir=library_dir)

    _run(save_article(
        ctx,
        content="Guide to zygomorphic-grepkind asyncio patterns",
        title="Zygomorphic Grepkind Guide",
        origin_url="https://example.com/zygomorphic-grepkind",
        tags=["reference"],
    ))

    article_result = _run(search_knowledge(ctx, "zygomorphic-grepkind", kind="article"))
    assert article_result["count"] >= 1
    first = article_result["results"][0]
    assert isinstance(first["title"], str) and first["title"]

    memory_result = _run(search_knowledge(ctx, "zygomorphic-grepkind", kind="memory"))
    assert memory_result["count"] == 0


# ---------------------------------------------------------------------------
# TASK-4: confidence field in search_knowledge results
# ---------------------------------------------------------------------------


def test_search_knowledge_result_dicts_contain_confidence(tmp_path):
    """search_knowledge FTS path populates confidence in each result dict."""
    from co_cli.knowledge._index_store import KnowledgeIndex

    library_dir = tmp_path / ".co-cli" / "library"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _ctx_with_idx(idx, library_dir=library_dir)

    # Use save_article — search_knowledge default scope covers local articles, not memories
    _run(save_article(
        ctx,
        content="Reference on zygomorphic-confidence-test framework for testing",
        title="Zygomorphic Confidence Test",
        origin_url="https://example.com/zygomorphic-confidence-test",
        tags=["reference"],
    ))

    result = _run(search_knowledge(ctx, "zygomorphic-confidence-test"))
    assert result["count"] >= 1
    first = result["results"][0]
    assert "confidence" in first
    assert first["confidence"] is not None
    assert isinstance(first["confidence"], float)
    assert 0.0 <= first["confidence"] <= 1.5  # formula can exceed 1 in theory, bounded by inputs
    idx.close()


def test_search_knowledge_display_contains_conf_label(tmp_path):
    """search_knowledge display string shows 'conf:' for FTS results."""
    from co_cli.knowledge._index_store import KnowledgeIndex

    library_dir = tmp_path / ".co-cli" / "library"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _ctx_with_idx(idx, library_dir=library_dir)

    # Use save_article — search_knowledge default scope covers local articles, not memories
    _run(save_article(
        ctx,
        content="Reference on zygomorphic-conf-display-test style guides",
        title="Zygomorphic Conf Display Test",
        origin_url="https://example.com/zygomorphic-conf-display-test",
        tags=["reference"],
    ))

    result = _run(search_knowledge(ctx, "zygomorphic-conf-display-test"))
    assert result["count"] >= 1
    assert "conf:" in result["display"]
    idx.close()


def test_compute_confidence_user_told_high_outscores_detected_medium(tmp_path):
    """_compute_confidence: user-told+high scores higher than detected+medium at same base score."""
    from co_cli.knowledge._index_store import SearchResult
    from co_cli.tools.articles import _compute_confidence

    created = datetime.now(timezone.utc).isoformat()

    r_high = SearchResult(
        source="memory", kind="memory", path="/a.md",
        title=None, snippet=None, score=0.5,
        tags=None, category=None, created=created, updated=None,
        provenance="user-told", certainty="high",
    )
    r_low = SearchResult(
        source="memory", kind="memory", path="/b.md",
        title=None, snippet=None, score=0.5,
        tags=None, category=None, created=created, updated=None,
        provenance="detected", certainty="medium",
    )
    conf_high = _compute_confidence(r_high, half_life_days=30)
    conf_low = _compute_confidence(r_low, half_life_days=30)
    assert conf_high > conf_low, (
        f"user-told+high ({conf_high:.4f}) should outscore detected+medium ({conf_low:.4f})"
    )


# ---------------------------------------------------------------------------
# TASK-5: contradiction detection in search_knowledge
# ---------------------------------------------------------------------------


def test_search_knowledge_flags_contradictions(tmp_path):
    """search_knowledge marks both memories conflict:True when same category has opposing polarity."""
    from co_cli.knowledge._index_store import KnowledgeIndex
    import yaml as _yaml

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _ctx_with_idx(idx, memory_dir=memory_dir)

    # Write two memories in the same category with opposing content

    fm_a = {
        "id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00",
        "tags": ["preference"], "provenance": "user-told",
        "auto_category": "preference", "certainty": "high",
    }
    fm_b = {
        "id": 2, "kind": "memory", "created": "2026-01-02T00:00:00+00:00",
        "tags": ["preference"], "provenance": "user-told",
        "auto_category": "preference", "certainty": "high",
    }
    path_a = memory_dir / "001-prefer-dark.md"
    path_b = memory_dir / "002-no-dark.md"
    path_a.write_text(
        f"---\n{_yaml.dump(fm_a, default_flow_style=False)}---\n\nI prefer dark mode\n",
        encoding="utf-8",
    )
    path_b.write_text(
        f"---\n{_yaml.dump(fm_b, default_flow_style=False)}---\n\nI don't prefer dark mode\n",
        encoding="utf-8",
    )

    idx.sync_dir("memory", memory_dir)

    # These are kind:memory files — must search with source="memory" (excluded from default scope)
    result = _run(search_knowledge(ctx, "dark mode prefer", source="memory"))
    assert result["count"] >= 2

    # Both results in same category with opposing polarity should be flagged
    flagged = [r for r in result["results"] if r.get("conflict") is True]
    assert len(flagged) >= 2, (
        f"Expected 2 conflict flags, got {len(flagged)}: {result['results']}"
    )
    idx.close()


def test_search_knowledge_no_conflict_when_no_opposition(tmp_path):
    """search_knowledge returns conflict:False when results are compatible."""
    from co_cli.knowledge._index_store import KnowledgeIndex
    import yaml as _yaml

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _ctx_with_idx(idx, memory_dir=memory_dir)

    fm_a = {
        "id": 1, "kind": "memory", "created": "2026-01-01T00:00:00+00:00",
        "tags": ["preference"], "provenance": "user-told",
        "auto_category": "preference", "certainty": "high",
    }
    fm_b = {
        "id": 2, "kind": "memory", "created": "2026-01-02T00:00:00+00:00",
        "tags": ["preference"], "provenance": "user-told",
        "auto_category": "preference", "certainty": "high",
    }
    path_a = memory_dir / "001-dark.md"
    path_b = memory_dir / "002-dark2.md"
    path_a.write_text(
        f"---\n{_yaml.dump(fm_a, default_flow_style=False)}---\n\nI prefer dark mode\n",
        encoding="utf-8",
    )
    path_b.write_text(
        f"---\n{_yaml.dump(fm_b, default_flow_style=False)}---\n\nI also enjoy dark themes\n",
        encoding="utf-8",
    )

    idx.sync_dir("memory", memory_dir)

    # source="memory" to search memories (excluded from default search_knowledge scope)
    result = _run(search_knowledge(ctx, "dark mode", source="memory"))
    # All results should have conflict:False (compatible statements)
    assert all(not r.get("conflict") for r in result["results"]), (
        f"No conflicts expected for compatible memories: {result['results']}"
    )
    idx.close()


def test_search_knowledge_grep_fallback_honors_source_filter(tmp_path):
    """Grep fallback returns empty for non-memory sources; normal for source='memory'."""
    # Write a memory file so grep has something to find
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "001-xylozygote-memory.md").write_text(
        "---\nid: 1\nkind: memory\ntags: []\ncreated: '2026-01-01T00:00:00+00:00'\n---\n\nxylozygote keyword memory\n",
        encoding="utf-8",
    )

    ctx = _make_ctx(memory_dir=memory_dir)

    # source='obsidian' with no FTS index → must return empty, not memory results
    obsidian_result = _run(search_knowledge(ctx, "xylozygote", source="obsidian"))
    assert obsidian_result["count"] == 0

    # source='memory' with no FTS index → grep works, must find the file
    memory_result = _run(search_knowledge(ctx, "xylozygote", source="memory"))
    assert memory_result["count"] >= 1


# ---------------------------------------------------------------------------
# TASK-5: search_knowledge default scope excludes memories
# ---------------------------------------------------------------------------


def test_search_knowledge_default_excludes_memories(tmp_path):
    """search_knowledge with no source filter returns articles but not memories."""
    from co_cli.knowledge._index_store import KnowledgeIndex

    library_dir = tmp_path / ".co-cli" / "library"
    memory_dir = tmp_path / ".co-cli" / "memory"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _ctx_with_idx(idx, library_dir=library_dir, memory_dir=memory_dir)

    # Save an article (indexed at source="library") and a memory (source="memory")
    _run(save_article(
        ctx,
        content="xylozygote-partition-test reference article content",
        title="Partition Test Article",
        origin_url="https://example.com/xylozygote-partition",
        tags=["reference"],
    ))
    _run(save_memory(ctx, "xylozygote-partition-test memory entry", tags=["preference"]))

    # Default scope: should find article, not memory
    result = _run(search_knowledge(ctx, "xylozygote-partition-test"))
    sources = {r["source"] for r in result["results"]}
    assert "memory" not in sources, "Memories must be excluded from default search_knowledge scope"
    assert result["count"] >= 1, "Article must be in default search results"

    # Explicit source="memory" escape hatch must still work
    mem_result = _run(search_knowledge(ctx, "xylozygote-partition-test", source="memory"))
    assert mem_result["count"] >= 1, "Explicit source='memory' must still find memories"
    idx.close()


# ---------------------------------------------------------------------------
# Scenario 20: chunks FTS end-to-end via save_article
# ---------------------------------------------------------------------------


def test_save_article_long_article_second_half_retrievable(tmp_path):
    """Scenario 20: save a long article; phrase only in second half must be retrievable via chunks FTS."""
    from co_cli.knowledge._index_store import KnowledgeIndex

    library_dir = tmp_path / ".co-cli" / "library"
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_chunk_size=100, knowledge_chunk_overlap=10))
    ctx = _ctx_with_idx(idx, library_dir=library_dir)

    # Construct article >2 x chunk_size tokens: ~100 tokens = 400 chars
    # First half has no target phrase; second half has it
    first_half = "This is the opening section. " * 20
    second_half = "This section discusses zygomorphic-second-half-retrieval patterns in depth. " * 20
    long_content = first_half + "\n\n" + second_half

    _run(save_article(
        ctx,
        content=long_content,
        title="Long Article Chunking Test",
        origin_url="https://example.com/zygomorphic-second-half-retrieval",
        tags=["test"],
    ))

    results = idx.search("zygomorphic-second-half-retrieval", source="library")
    assert len(results) >= 1, (
        "Phrase only in second half of article must be retrievable via chunks FTS"
    )
    idx.close()


def test_search_knowledge_hybrid_whole_flow_real_embedder_populates_vec_rows(tmp_path):
    """Real whole-flow hybrid retrieval must populate vec rows using configured embedder settings."""
    from dataclasses import replace

    from co_cli.knowledge._index_store import KnowledgeIndex
    from co_cli.bootstrap._check import check_tei
    from co_cli.config import settings

    if not check_tei(settings.knowledge_embed_api_url).ok:
        pytest.fail("TEI embed service not reachable — start the service before running hybrid tests")

    library_dir = tmp_path / ".co-cli" / "library"
    config = replace(
        CoConfig(),
        library_dir=library_dir,
        knowledge_search_backend="hybrid",
    )
    deps = CoDeps(
        services=CoServices(
            shell=ShellBackend(),
            knowledge_index=KnowledgeIndex(config=replace(config, knowledge_db_path=tmp_path / "search.db")),
        ),
        config=config,
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    idx = ctx.deps.services.knowledge_index

    _run(save_article(
        ctx,
        content="wholeflow-real-embedder-token alpha content for configured provider",
        title="Alpha Real Embedder Doc",
        origin_url="https://example.com/real-embedder-alpha",
        tags=["reference"],
    ))
    _run(save_article(
        ctx,
        content="wholeflow-real-embedder-token beta content for configured provider",
        title="Beta Real Embedder Doc",
        origin_url="https://example.com/real-embedder-beta",
        tags=["reference"],
    ))

    docs_vec_count = idx._conn.execute("SELECT COUNT(*) FROM docs_vec").fetchone()[0]
    chunks_vec_count = idx._conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0]

    assert docs_vec_count >= 2, "Configured embedder must populate docs_vec for hybrid retrieval"
    assert chunks_vec_count >= 2, "Configured embedder must populate chunks_vec for hybrid retrieval"

    result = _run(search_knowledge(ctx, "wholeflow-real-embedder-token"))
    assert result["count"] >= 2
    idx.close()


def test_search_knowledge_hybrid_whole_flow_real_reranker_changes_scores(tmp_path):
    """Real whole-flow hybrid retrieval must apply the configured reranker, not fallback passthrough."""
    from dataclasses import replace

    from co_cli.knowledge._index_store import KnowledgeIndex
    from co_cli.bootstrap._check import check_tei
    from co_cli.config import settings

    if not check_tei(settings.knowledge_rerank_api_url).ok:
        pytest.fail("TEI rerank service not reachable — start the service before running hybrid tests")

    library_dir = tmp_path / ".co-cli" / "library"
    config = replace(
        CoConfig(),
        library_dir=library_dir,
        knowledge_search_backend="hybrid",
    )
    deps = CoDeps(
        services=CoServices(
            shell=ShellBackend(),
            knowledge_index=KnowledgeIndex(config=replace(config, knowledge_db_path=tmp_path / "search.db", knowledge_reranker_provider="none")),
        ),
        config=config,
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    idx_none = ctx.deps.services.knowledge_index

    _run(save_article(
        ctx,
        content="wholeflow-real-reranker-token alpha content for configured reranker",
        title="Alpha Real Reranker Doc",
        origin_url="https://example.com/real-reranker-alpha",
        tags=["reference"],
    ))
    _run(save_article(
        ctx,
        content="wholeflow-real-reranker-token beta content for configured reranker",
        title="Beta Real Reranker Doc",
        origin_url="https://example.com/real-reranker-beta",
        tags=["reference"],
    ))

    baseline = _run(search_knowledge(ctx, "wholeflow-real-reranker-token"))
    assert baseline["count"] >= 2
    idx_none.close()

    # Fresh index on the same db with the real reranker — no private state mutation
    idx_real = KnowledgeIndex(config=replace(config, knowledge_db_path=tmp_path / "search.db"))
    deps_real = CoDeps(
        services=CoServices(shell=ShellBackend(), knowledge_index=idx_real),
        config=config,
    )
    ctx_real = RunContext(deps=deps_real, model=_AGENT.model, usage=RunUsage())
    reranked = _run(search_knowledge(ctx_real, "wholeflow-real-reranker-token"))

    baseline_scores = [r["score"] for r in baseline["results"]]
    reranked_scores = [r["score"] for r in reranked["results"]]

    assert reranked_scores != baseline_scores, (
        "Configured reranker must change final retrieval scores; identical scores indicate fallback passthrough"
    )
    idx_real.close()
