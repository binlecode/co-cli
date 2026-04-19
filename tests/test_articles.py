"""Functional tests for article tools (save_article, search_knowledge article-index, read_article)."""

from pathlib import Path

import pytest
import yaml
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.knowledge._store import KnowledgeStore
from co_cli.tools.knowledge.read import read_article, search_knowledge
from co_cli.tools.knowledge.write import save_article
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=settings)


def _make_ctx(
    tmp_path: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
    knowledge_search_backend: str = "grep",
) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_store=knowledge_store,
        config=make_settings(
            knowledge=make_settings().knowledge.model_copy(
                update={"search_backend": knowledge_search_backend}
            )
        ),
        knowledge_dir=tmp_path / "library",
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

    assert result.metadata["action"] == "saved"
    assert isinstance(result.metadata["article_id"], str)
    assert len(result.metadata["article_id"]) == 36  # standard UUID with dashes
    knowledge_dir = tmp_path / "library"
    files = list(knowledge_dir.glob("*.md"))
    assert len(files) == 1

    raw = files[0].read_text(encoding="utf-8")
    fm = yaml.safe_load(raw.split("---")[1])
    assert fm["kind"] == "knowledge"
    assert fm["artifact_kind"] == "article"
    assert fm["decay_protected"] is True
    assert fm["source_ref"] == "https://docs.python.org/3/library/asyncio.html"
    assert fm["source_type"] == "web_fetch"
    assert "python" in fm["tags"]
    assert "Python asyncio" in raw


@pytest.mark.asyncio
async def test_save_article_dedup_by_url(tmp_path: Path):
    """Saving same origin_url twice consolidates instead of duplicating."""
    ctx = _make_ctx(tmp_path)
    url = "https://example.com/unique-article"

    await save_article(ctx, content="Version 1", title="Article", origin_url=url, tags=["v1"])
    result = await save_article(
        ctx, content="Version 2", title="Article Updated", origin_url=url, tags=["v2"]
    )

    assert result.metadata["action"] == "consolidated"
    knowledge_dir = tmp_path / "library"
    files = list(knowledge_dir.glob("*.md"))
    assert len(files) == 1, "Consolidation must not create a second file"

    raw = files[0].read_text(encoding="utf-8")
    assert "Version 2" in raw, "Consolidated file must have new content"
    fm = yaml.safe_load(raw.split("---")[1])
    assert "v1" in fm["tags"], "Tags must be merged"
    assert "v2" in fm["tags"], "Tags must be merged"


@pytest.mark.asyncio
async def test_save_article_indexes_into_fts(tmp_path: Path):
    """save_article indexes the article into the FTS knowledge index."""
    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    ctx = _make_ctx(tmp_path, knowledge_store=idx, knowledge_search_backend="fts5")

    await save_article(
        ctx,
        content="xyloquartz-article-fts-unique content for indexing test",
        title="FTS Test Article",
        origin_url="https://example.com/fts-test",
        tags=["test"],
    )

    results = idx.search("xyloquartz-article-fts-unique", source="knowledge")
    assert len(results) >= 1, "Article must be findable via FTS after save"
    idx.close()


# --- search_knowledge article-index mode ---


@pytest.mark.asyncio
async def test_search_knowledge_article_grep_fallback(tmp_path: Path):
    """search_knowledge(kind='article') grep path returns article-index schema."""
    ctx = _make_ctx(tmp_path)
    await save_article(
        ctx,
        content="xyloquartz-article-grep-unique reference material",
        title="Grep Test",
        origin_url="https://example.com/grep",
        tags=["reference"],
    )

    result = await search_knowledge(
        ctx, "xyloquartz-article-grep-unique", kind="article", source="knowledge"
    )
    assert result.metadata["count"] >= 1
    hit = result.metadata["results"][0]
    assert hit["origin_url"] == "https://example.com/grep"
    assert "slug" in hit
    assert "article_id" in hit
    assert "title" in hit
    assert "tags" in hit
    assert "snippet" in hit


@pytest.mark.asyncio
async def test_search_knowledge_article_no_match(tmp_path: Path):
    """search_knowledge(kind='article') returns zero count when nothing matches."""
    ctx = _make_ctx(tmp_path)
    result = await search_knowledge(
        ctx, "zzz_no_match_ever_xyz", kind="article", source="knowledge"
    )
    assert result.metadata["count"] == 0
    assert result.metadata["results"] == []


@pytest.mark.asyncio
async def test_search_knowledge_article_fts_path(tmp_path: Path):
    """search_knowledge(kind='article') FTS path returns article-index schema."""
    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    ctx = _make_ctx(tmp_path, knowledge_store=idx, knowledge_search_backend="fts5")

    await save_article(
        ctx,
        content="xyloquartz-article-fts-index-unique content for article-index test",
        title="FTS Index Article",
        origin_url="https://example.com/fts-index",
        tags=["fts"],
    )

    result = await search_knowledge(
        ctx, "xyloquartz-article-fts-index-unique", kind="article", source="knowledge"
    )
    assert result.metadata["count"] >= 1
    hit = result.metadata["results"][0]
    assert hit["origin_url"] == "https://example.com/fts-index"
    assert "slug" in hit
    assert hit["article_id"] is not None
    idx.close()


@pytest.mark.asyncio
async def test_search_knowledge_non_article_kind_returns_generic_schema(tmp_path: Path):
    """search_knowledge with a non-article kind returns the generic cross-source schema."""
    ctx = _make_ctx(tmp_path)
    await save_article(
        ctx,
        content="xyloquartz-crosssource-schema-guard content",
        title="Schema Guard",
        origin_url="https://example.com/schema-guard",
    )

    result = await search_knowledge(ctx, "xyloquartz-crosssource-schema-guard")
    assert result.metadata["count"] >= 1
    hit = result.metadata["results"][0]
    # Generic schema must have source/kind/score/path — not article-index fields
    assert "source" in hit
    assert "kind" in hit
    assert "score" in hit
    assert "path" in hit
    assert "slug" not in hit


# --- read_article ---


@pytest.mark.asyncio
async def test_read_article_returns_full_body(tmp_path: Path):
    """read_article returns the full markdown body and metadata."""
    ctx = _make_ctx(tmp_path)
    await save_article(
        ctx,
        content="Full body content for read test.\n\nSecond paragraph.",
        title="Read Test Article",
        origin_url="https://example.com/read",
    )

    knowledge_dir = tmp_path / "library"
    slug = next(iter(knowledge_dir.glob("*.md"))).stem

    result = await read_article(ctx, slug)
    assert result.metadata["title"] == "Read Test Article"
    assert result.metadata["origin_url"] == "https://example.com/read"
    assert "Full body content" in result.metadata["content"]
    assert "Second paragraph" in result.metadata["content"]


@pytest.mark.asyncio
async def test_read_article_not_found(tmp_path: Path):
    """read_article returns error-like result for missing slug."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    result = await read_article(ctx, "999-nonexistent-article")
    assert result.metadata["article_id"] is None
    assert "not found" in result.return_value.lower()


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
    assert result.metadata["count"] >= 1
    assert all(r["source"] == "knowledge" for r in result.metadata["results"])
