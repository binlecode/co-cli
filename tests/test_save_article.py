"""Functional tests for article tools — save, recall, read, list filter."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from co_cli.tools.articles import save_article, recall_article, read_article_detail, search_knowledge
from co_cli.tools.memory import list_memories, save_memory
from co_cli._frontmatter import parse_frontmatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeDeps:
    memory_max_count: int = 200
    memory_dedup_window_days: int = 7
    memory_dedup_threshold: int = 85
    memory_decay_strategy: str = "summarize"
    memory_decay_percentage: float = 0.2
    memory_recall_half_life_days: int = 30
    knowledge_index: Any = None
    knowledge_search_backend: str = "grep"
    obsidian_vault_path: Any = None


class _FakeRunContext:
    def __init__(self, deps: Any):
        self._deps = deps

    @property
    def deps(self) -> Any:
        return self._deps


def _ctx() -> _FakeRunContext:
    return _FakeRunContext(_FakeDeps())


def _ctx_with_idx(idx: Any) -> _FakeRunContext:
    """Return a fake context with FTS5 backend and the given KnowledgeIndex."""
    return _FakeRunContext(_FakeDeps(knowledge_index=idx, knowledge_search_backend="fts5"))


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# save_article
# ---------------------------------------------------------------------------


def test_save_article_writes_correct_frontmatter(tmp_path, monkeypatch):
    """save_article writes kind:article, origin_url, provenance:web-fetch, decay_protected:true."""
    monkeypatch.chdir(tmp_path)

    result = _run(save_article(
        _ctx(),
        content="## Python Asyncio Guide\n\nAsyncio is a library...",
        title="Python Asyncio Guide",
        origin_url="https://docs.python.org/3/library/asyncio.html",
        tags=["python", "asyncio", "reference"],
    ))

    assert result["action"] == "saved"
    assert result["article_id"] is not None

    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    files = list(knowledge_dir.glob("*.md"))
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


def test_save_article_dedup_by_origin_url(tmp_path, monkeypatch):
    """Saving the same origin_url twice consolidates rather than creating duplicate."""
    monkeypatch.chdir(tmp_path)

    result1 = _run(save_article(
        _ctx(),
        content="First version of content",
        title="My Article",
        origin_url="https://example.com/article",
        tags=["python"],
    ))
    assert result1["action"] == "saved"

    result2 = _run(save_article(
        _ctx(),
        content="Updated version of content",
        title="My Article v2",
        origin_url="https://example.com/article",
        tags=["python", "updated"],
    ))
    assert result2["action"] == "consolidated"
    assert result2["article_id"] == result1["article_id"]

    # Only one file should exist
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    files = list(knowledge_dir.glob("*.md"))
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


def test_recall_article_returns_summary_only(tmp_path, monkeypatch):
    """recall_article returns first paragraph, not full body."""
    monkeypatch.chdir(tmp_path)

    long_content = (
        "First paragraph about asyncio basics.\n\n"
        "Second paragraph with much more detail that should not appear in summary. " * 20
    )
    _run(save_article(
        _ctx(),
        content=long_content,
        title="Asyncio Deep Dive",
        origin_url="https://example.com/asyncio",
        tags=["asyncio"],
    ))

    result = _run(recall_article(_ctx(), "asyncio"))
    assert result["count"] >= 1
    # Snippet is first paragraph only, not the full 20-repetition content
    snippet = result["results"][0]["snippet"]
    assert len(snippet) <= 210  # 200 chars + "..."
    assert "Second paragraph" not in snippet


def test_recall_article_no_match(tmp_path, monkeypatch):
    """recall_article returns count=0 when no match found."""
    monkeypatch.chdir(tmp_path)

    result = _run(recall_article(_ctx(), "xyzunmatchable999"))
    assert result["count"] == 0
    assert result["results"] == []


# ---------------------------------------------------------------------------
# read_article_detail
# ---------------------------------------------------------------------------


def test_read_article_detail_returns_full_body(tmp_path, monkeypatch):
    """read_article_detail returns full markdown body."""
    monkeypatch.chdir(tmp_path)

    long_body = "# Full Content\n\n" + ("Detailed paragraph. " * 100)
    _run(save_article(
        _ctx(),
        content=long_body,
        title="Full Article",
        origin_url="https://example.com/full",
        tags=["full"],
    ))

    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    files = list(knowledge_dir.glob("*.md"))
    slug = files[0].stem

    result = _run(read_article_detail(_ctx(), slug))
    assert result["content"] is not None
    assert len(result["content"]) > 200
    assert "Detailed paragraph." in result["content"]
    assert result["origin_url"] == "https://example.com/full"
    assert result["title"] == "Full Article"


def test_read_article_detail_prefix_match(tmp_path, monkeypatch):
    """read_article_detail finds article via prefix glob when exact slug not given."""
    monkeypatch.chdir(tmp_path)
    _run(save_article(
        _ctx(),
        content="Full content here.",
        title="Prefix Match Article",
        origin_url="https://example.com/prefix-match",
        tags=[],
    ))
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    full_slug = list(knowledge_dir.glob("*.md"))[0].stem
    partial = full_slug[:3]
    result = _run(read_article_detail(_ctx(), partial))
    assert result["content"] is not None
    assert result["title"] == "Prefix Match Article"


def test_read_article_detail_not_found(tmp_path, monkeypatch):
    """read_article_detail returns None content for unknown slug."""
    monkeypatch.chdir(tmp_path)

    result = _run(read_article_detail(_ctx(), "999-nonexistent-slug"))
    assert result["content"] is None
    assert result["article_id"] is None


# ---------------------------------------------------------------------------
# list_memories kind filter
# ---------------------------------------------------------------------------


def test_recall_article_fts_metadata_parity(tmp_path, monkeypatch):
    """recall_article via FTS returns article_id and origin_url (not None)."""
    from co_cli.knowledge_index import KnowledgeIndex

    idx = KnowledgeIndex(tmp_path / "search.db")
    deps = _FakeDeps(knowledge_index=idx, knowledge_search_backend="fts5")

    class _CtxWithIdx:
        def __init__(self, d):
            self._deps = d
        @property
        def deps(self):
            return self._deps

    ctx = _CtxWithIdx(deps)
    monkeypatch.chdir(tmp_path)

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


def test_list_memories_kind_article_filter(tmp_path, monkeypatch):
    """list_memories(kind='article') returns only articles."""
    monkeypatch.chdir(tmp_path)

    # Save one article and one memory
    _run(save_article(
        _ctx(),
        content="Reference content",
        title="Some Article",
        origin_url="https://example.com/ref",
        tags=["reference"],
    ))

    # Manually write a memory file
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    memory_file = knowledge_dir / "002-user-prefers-dark-mode.md"
    fm = {
        "id": 2,
        "kind": "memory",
        "created": "2026-01-15T00:00:00+00:00",
        "tags": ["preference"],
        "provenance": "user-told",
        "auto_category": "preference",
    }
    import yaml as _yaml
    memory_file.write_text(
        f"---\n{_yaml.dump(fm, default_flow_style=False)}---\n\nUser prefers dark mode\n",
        encoding="utf-8",
    )

    from co_cli.tools.memory import list_memories as _list_memories
    result = _run(_list_memories(_ctx(), kind="article"))
    assert result["total"] == 1
    assert all(m["kind"] == "article" for m in result["memories"])


def test_list_memories_kind_memory_filter(tmp_path, monkeypatch):
    """list_memories(kind='memory') returns only memories."""
    monkeypatch.chdir(tmp_path)

    # Save one article
    _run(save_article(
        _ctx(),
        content="Reference content",
        title="Some Article",
        origin_url="https://example.com/ref",
        tags=["reference"],
    ))

    # Manually write a memory file
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    memory_file = knowledge_dir / "002-user-prefers-dark-mode.md"
    import yaml as _yaml
    fm = {
        "id": 2,
        "kind": "memory",
        "created": "2026-01-15T00:00:00+00:00",
        "tags": ["preference"],
        "provenance": "user-told",
        "auto_category": "preference",
    }
    memory_file.write_text(
        f"---\n{_yaml.dump(fm, default_flow_style=False)}---\n\nUser prefers dark mode\n",
        encoding="utf-8",
    )

    from co_cli.tools.memory import list_memories as _list_memories
    result = _run(_list_memories(_ctx(), kind="memory"))
    assert result["total"] == 1
    assert all(m["kind"] == "memory" for m in result["memories"])


# ---------------------------------------------------------------------------
# FTS integration tests
# ---------------------------------------------------------------------------


def test_fts_article_consolidated_tags_indexed(tmp_path, monkeypatch):
    """Consolidated article reindex carries merged tags (Bug #1 regression guard).

    Saving the same origin_url twice merges tags on disk AND in the FTS index.
    Both the retained tag and the new tag must be discoverable via tag search.
    """
    from co_cli.knowledge_index import KnowledgeIndex

    idx = KnowledgeIndex(tmp_path / "search.db")
    ctx = _ctx_with_idx(idx)
    monkeypatch.chdir(tmp_path)

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


def test_recall_article_fts_return_contract(tmp_path, monkeypatch):
    """recall_article FTS path returns correct field types.

    Validates schema parity: article_id (int), origin_url (str),
    tags (list[str]), snippet (str), slug (str).
    """
    from co_cli.knowledge_index import KnowledgeIndex

    idx = KnowledgeIndex(tmp_path / "search.db")
    ctx = _ctx_with_idx(idx)
    monkeypatch.chdir(tmp_path)

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


def test_search_knowledge_fts_kind_filter(tmp_path, monkeypatch):
    """search_knowledge FTS path respects kind filter.

    Saves one article and one memory both matching the query keyword.
    kind='article' returns only articles; kind='memory' returns only memories.
    """
    from co_cli.knowledge_index import KnowledgeIndex

    idx = KnowledgeIndex(tmp_path / "search.db")
    ctx = _ctx_with_idx(idx)
    monkeypatch.chdir(tmp_path)

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

    article_result = _run(search_knowledge(ctx, "zygomorphic-kindfilter", kind="article"))
    assert article_result["count"] >= 1
    assert all(r["kind"] == "article" for r in article_result["results"])

    memory_result = _run(search_knowledge(ctx, "zygomorphic-kindfilter", kind="memory"))
    assert memory_result["count"] >= 1
    assert all(r["kind"] == "memory" for r in memory_result["results"])
    idx.close()


def test_search_knowledge_fallback_grep_kind_filter(tmp_path, monkeypatch):
    """search_knowledge grep fallback (no FTS index) respects kind filter.

    Saves one article. kind='article' must find it; kind='memory' must return 0.
    """
    monkeypatch.chdir(tmp_path)

    _run(save_article(
        _ctx(),
        content="Guide to zygomorphic-grepkind asyncio patterns",
        title="Zygomorphic Grepkind Guide",
        origin_url="https://example.com/zygomorphic-grepkind",
        tags=["reference"],
    ))

    article_result = _run(search_knowledge(_ctx(), "zygomorphic-grepkind", kind="article"))
    assert article_result["count"] >= 1
    first = article_result["results"][0]
    assert isinstance(first["title"], str) and first["title"]

    memory_result = _run(search_knowledge(_ctx(), "zygomorphic-grepkind", kind="memory"))
    assert memory_result["count"] == 0


# ---------------------------------------------------------------------------
# TASK-4: confidence field in search_knowledge results
# ---------------------------------------------------------------------------


def test_search_knowledge_result_dicts_contain_confidence(tmp_path, monkeypatch):
    """search_knowledge FTS path populates confidence in each result dict."""
    from co_cli.knowledge_index import KnowledgeIndex

    idx = KnowledgeIndex(tmp_path / "search.db")
    ctx = _ctx_with_idx(idx)
    monkeypatch.chdir(tmp_path)

    _run(save_memory(
        ctx,
        "User prefers zygomorphic-confidence-test framework for testing",
        tags=["preference"],
    ))

    result = _run(search_knowledge(ctx, "zygomorphic-confidence-test"))
    assert result["count"] >= 1
    first = result["results"][0]
    assert "confidence" in first
    assert first["confidence"] is not None
    assert isinstance(first["confidence"], float)
    assert 0.0 <= first["confidence"] <= 1.5  # formula can exceed 1 in theory, bounded by inputs
    idx.close()


def test_search_knowledge_display_contains_conf_label(tmp_path, monkeypatch):
    """search_knowledge display string shows 'conf:' for FTS results."""
    from co_cli.knowledge_index import KnowledgeIndex

    idx = KnowledgeIndex(tmp_path / "search.db")
    ctx = _ctx_with_idx(idx)
    monkeypatch.chdir(tmp_path)

    _run(save_memory(
        ctx,
        "User prefers zygomorphic-conf-display-test style guides",
        tags=["preference"],
    ))

    result = _run(search_knowledge(ctx, "zygomorphic-conf-display-test"))
    assert result["count"] >= 1
    assert "conf:" in result["display"]
    idx.close()


def test_compute_confidence_user_told_high_outscores_detected_medium(tmp_path):
    """_compute_confidence: user-told+high scores higher than detected+medium at same base score."""
    from co_cli.knowledge_index import SearchResult
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


def test_search_knowledge_flags_contradictions(tmp_path, monkeypatch):
    """search_knowledge marks both memories conflict:True when same category has opposing polarity."""
    from co_cli.knowledge_index import KnowledgeIndex
    import yaml as _yaml

    idx = KnowledgeIndex(tmp_path / "search.db")
    ctx = _ctx_with_idx(idx)
    monkeypatch.chdir(tmp_path)

    # Write two memories in the same category with opposing content
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

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
    path_a = knowledge_dir / "001-prefer-dark.md"
    path_b = knowledge_dir / "002-no-dark.md"
    path_a.write_text(
        f"---\n{_yaml.dump(fm_a, default_flow_style=False)}---\n\nI prefer dark mode\n",
        encoding="utf-8",
    )
    path_b.write_text(
        f"---\n{_yaml.dump(fm_b, default_flow_style=False)}---\n\nI don't prefer dark mode\n",
        encoding="utf-8",
    )

    idx.sync_dir("memory", knowledge_dir)

    result = _run(search_knowledge(ctx, "dark mode prefer"))
    assert result["count"] >= 2

    # Both results in same category with opposing polarity should be flagged
    flagged = [r for r in result["results"] if r.get("conflict") is True]
    assert len(flagged) >= 2, (
        f"Expected 2 conflict flags, got {len(flagged)}: {result['results']}"
    )
    idx.close()


def test_search_knowledge_no_conflict_when_no_opposition(tmp_path, monkeypatch):
    """search_knowledge returns conflict:False when results are compatible."""
    from co_cli.knowledge_index import KnowledgeIndex
    import yaml as _yaml

    idx = KnowledgeIndex(tmp_path / "search.db")
    ctx = _ctx_with_idx(idx)
    monkeypatch.chdir(tmp_path)

    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

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
    path_a = knowledge_dir / "001-dark.md"
    path_b = knowledge_dir / "002-dark2.md"
    path_a.write_text(
        f"---\n{_yaml.dump(fm_a, default_flow_style=False)}---\n\nI prefer dark mode\n",
        encoding="utf-8",
    )
    path_b.write_text(
        f"---\n{_yaml.dump(fm_b, default_flow_style=False)}---\n\nI also enjoy dark themes\n",
        encoding="utf-8",
    )

    idx.sync_dir("memory", knowledge_dir)

    result = _run(search_knowledge(ctx, "dark mode"))
    # All results should have conflict:False (compatible statements)
    assert all(not r.get("conflict") for r in result["results"]), (
        f"No conflicts expected for compatible memories: {result['results']}"
    )
    idx.close()


def test_search_knowledge_grep_fallback_honors_source_filter(tmp_path, monkeypatch):
    """Grep fallback returns empty for non-memory sources; normal for source='memory'."""
    monkeypatch.chdir(tmp_path)

    # Write a memory file so grep has something to find
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    (knowledge_dir / "001-xylozygote-memory.md").write_text(
        "---\nid: 1\nkind: memory\ntags: []\ncreated: '2026-01-01T00:00:00+00:00'\n---\n\nxylozygote keyword memory\n",
        encoding="utf-8",
    )

    # source='obsidian' with no FTS index → must return empty, not memory results
    obsidian_result = _run(search_knowledge(_ctx(), "xylozygote", source="obsidian"))
    assert obsidian_result["count"] == 0

    # source='memory' with no FTS index → grep works, must find the file
    memory_result = _run(search_knowledge(_ctx(), "xylozygote", source="memory"))
    assert memory_result["count"] >= 1
