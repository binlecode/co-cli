"""Functional tests for article tools (save_article, search_articles, read_article)."""

import asyncio
from pathlib import Path

import pytest
import yaml
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoConfig
from co_cli.knowledge._index_store import KnowledgeIndex
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.articles import save_article, search_articles, read_article, search_knowledge

_AGENT = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))


def _make_ctx(
    tmp_path: Path,
    *,
    knowledge_index: KnowledgeIndex | None = None,
    knowledge_search_backend: str = "grep",
) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(), knowledge_index=knowledge_index,
        config=CoConfig(
            library_dir=tmp_path / "library",
            knowledge_search_backend=knowledge_search_backend,
        ),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# --- save_article ---


@pytest.mark.asyncio
async def test_save_article_creates_file(tmp_path: Path):
    """save_article writes a markdown file with correct frontmatter."""
    ctx = _make_ctx(tmp_path)
    result = await save_article(
        ctx,
        content="Python asyncio is a concurrent framework.",
        title="Python Asyncio Guide",
        origin_url="https://docs.python.org/3/library/asyncio.html",
        tags=["python", "async"],
    )

    assert result["action"] == "saved"
    assert result["article_id"] == 1
    library_dir = tmp_path / "library"
    files = list(library_dir.glob("*.md"))
    assert len(files) == 1

    raw = files[0].read_text(encoding="utf-8")
    fm = yaml.safe_load(raw.split("---")[1])
    assert fm["kind"] == "article"
    assert fm["decay_protected"] is True
    assert fm["origin_url"] == "https://docs.python.org/3/library/asyncio.html"
    assert "python" in fm["tags"]
    assert "Python asyncio" in raw


@pytest.mark.asyncio
async def test_save_article_dedup_by_url(tmp_path: Path):
    """Saving same origin_url twice consolidates instead of duplicating."""
    ctx = _make_ctx(tmp_path)
    url = "https://example.com/unique-article"

    await save_article(ctx, content="Version 1", title="Article", origin_url=url, tags=["v1"])
    result = await save_article(ctx, content="Version 2", title="Article Updated", origin_url=url, tags=["v2"])

    assert result["action"] == "consolidated"
    library_dir = tmp_path / "library"
    files = list(library_dir.glob("*.md"))
    assert len(files) == 1, "Consolidation must not create a second file"

    raw = files[0].read_text(encoding="utf-8")
    assert "Version 2" in raw, "Consolidated file must have new content"
    fm = yaml.safe_load(raw.split("---")[1])
    assert "v1" in fm["tags"] and "v2" in fm["tags"], "Tags must be merged"


@pytest.mark.asyncio
async def test_save_article_indexes_into_fts(tmp_path: Path):
    """save_article indexes the article into the FTS knowledge index."""
    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db"))
    ctx = _make_ctx(tmp_path, knowledge_index=idx, knowledge_search_backend="fts5")

    await save_article(
        ctx,
        content="xyloquartz-article-fts-unique content for indexing test",
        title="FTS Test Article",
        origin_url="https://example.com/fts-test",
        tags=["test"],
    )

    results = idx.search("xyloquartz-article-fts-unique", source="library")
    assert len(results) >= 1, "Article must be findable via FTS after save"
    idx.close()


# --- search_articles ---


@pytest.mark.asyncio
async def test_search_articles_grep_fallback(tmp_path: Path):
    """search_articles grep fallback finds articles by keyword."""
    ctx = _make_ctx(tmp_path)
    await save_article(
        ctx,
        content="xyloquartz-article-grep-unique reference material",
        title="Grep Test",
        origin_url="https://example.com/grep",
        tags=["reference"],
    )

    result = await search_articles(ctx, "xyloquartz-article-grep-unique")
    assert result["count"] >= 1
    assert result["results"][0]["origin_url"] == "https://example.com/grep"


@pytest.mark.asyncio
async def test_search_articles_no_match(tmp_path: Path):
    """search_articles returns zero count when nothing matches."""
    ctx = _make_ctx(tmp_path)
    result = await search_articles(ctx, "zzz_no_match_ever_xyz")
    assert result["count"] == 0
    assert result["results"] == []


# --- read_article ---


@pytest.mark.asyncio
async def test_read_article_returns_full_body(tmp_path: Path):
    """read_article returns the full markdown body and metadata."""
    ctx = _make_ctx(tmp_path)
    save_result = await save_article(
        ctx,
        content="Full body content for read test.\n\nSecond paragraph.",
        title="Read Test Article",
        origin_url="https://example.com/read",
    )

    library_dir = tmp_path / "library"
    slug = list(library_dir.glob("*.md"))[0].stem

    result = await read_article(ctx, slug)
    assert result["title"] == "Read Test Article"
    assert result["origin_url"] == "https://example.com/read"
    assert "Full body content" in result["content"]
    assert "Second paragraph" in result["content"]


@pytest.mark.asyncio
async def test_read_article_not_found(tmp_path: Path):
    """read_article returns error-like result for missing slug."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    result = await read_article(ctx, "999-nonexistent-article")
    assert result["article_id"] is None
    assert "not found" in result["display"].lower()


# --- search_knowledge (cross-source) ---


@pytest.mark.asyncio
async def test_search_knowledge_grep_finds_articles(tmp_path: Path):
    """search_knowledge grep fallback returns articles from library dir."""
    ctx = _make_ctx(tmp_path)
    await save_article(
        ctx,
        content="xyloquartz-crosssource-unique knowledge content",
        title="Cross Source",
        origin_url="https://example.com/cross",
    )

    result = await search_knowledge(ctx, "xyloquartz-crosssource-unique")
    assert result["count"] >= 1
    assert all(r["source"] == "library" for r in result["results"])
