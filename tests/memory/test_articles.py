"""Functional tests for article save/search/read via unified memory_* tools."""

from pathlib import Path

import pytest
import yaml
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent.core import build_agent
from co_cli.config.core import settings
from co_cli.deps import CoDeps
from co_cli.memory.knowledge_store import KnowledgeStore
from co_cli.tools.memory.read import memory_read
from co_cli.tools.memory.recall import memory_search
from co_cli.tools.memory.write import memory_create
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
    """memory_create with source_url writes a markdown file with correct frontmatter."""
    ctx = _make_ctx(tmp_path)
    result = await memory_create(
        ctx,
        content="Python asyncio is a concurrent framework.",
        artifact_kind="article",
        title="Python Asyncio Guide",
        source_url="https://docs.python.org/3/library/asyncio.html",
        tags=["python", "async"],
    )

    assert result.metadata["action"] == "saved"
    assert isinstance(result.metadata["artifact_id"], str)
    assert len(result.metadata["artifact_id"]) == 36  # standard UUID with dashes
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
    """Saving same source_url twice merges instead of duplicating."""
    ctx = _make_ctx(tmp_path)
    url = "https://example.com/unique-article"

    await memory_create(
        ctx,
        content="Version 1",
        artifact_kind="article",
        title="Article",
        source_url=url,
        tags=["v1"],
    )
    result = await memory_create(
        ctx,
        content="Version 2",
        artifact_kind="article",
        title="Article Updated",
        source_url=url,
        tags=["v2"],
    )

    assert result.metadata["action"] == "merged"
    knowledge_dir = tmp_path / "library"
    files = list(knowledge_dir.glob("*.md"))
    assert len(files) == 1, "Merge must not create a second file"

    raw = files[0].read_text(encoding="utf-8")
    assert "Version 2" in raw, "Merged file must have new content"
    fm = yaml.safe_load(raw.split("---")[1])
    assert "v1" in fm["tags"], "Tags must be merged"
    assert "v2" in fm["tags"], "Tags must be merged"


@pytest.mark.asyncio
async def test_save_article_indexes_into_fts(tmp_path: Path):
    """memory_create indexes the article into the FTS knowledge index."""
    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    ctx = _make_ctx(tmp_path, knowledge_store=idx, knowledge_search_backend="fts5")

    await memory_create(
        ctx,
        content="xyloquartz-article-fts-unique content for indexing test",
        artifact_kind="article",
        title="FTS Test Article",
        source_url="https://example.com/fts-test",
        tags=["test"],
    )

    results = idx.search("xyloquartz-article-fts-unique", source="knowledge")
    assert len(results) >= 1, "Article must be findable via FTS after save"
    idx.close()


# --- memory_search article mode ---


@pytest.mark.asyncio
async def test_memory_search_article_grep_finds_article(tmp_path: Path):
    """memory_search grep path returns T2 artifact result for article."""
    ctx = _make_ctx(tmp_path)
    await memory_create(
        ctx,
        content="xyloquartz-article-grep-unique reference material",
        artifact_kind="article",
        title="Grep Test",
        source_url="https://example.com/grep",
        tags=["reference"],
    )

    result = await memory_search(ctx, "xyloquartz-article-grep-unique", kind="article")
    assert result.metadata["count"] >= 1
    hit = result.metadata["results"][0]
    assert hit["tier"] == "artifacts"
    assert hit["kind"] == "article"
    assert "slug" in hit
    assert "title" in hit
    assert "snippet" in hit


@pytest.mark.asyncio
async def test_memory_search_article_no_match(tmp_path: Path):
    """memory_search returns zero count when nothing matches."""
    ctx = _make_ctx(tmp_path)
    result = await memory_search(ctx, "zzz_no_match_ever_xyz", kind="article")
    assert result.metadata["count"] == 0
    assert result.metadata["results"] == []


@pytest.mark.asyncio
async def test_memory_search_article_fts_path(tmp_path: Path):
    """memory_search FTS path returns T2 artifact result for article."""
    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    ctx = _make_ctx(tmp_path, knowledge_store=idx, knowledge_search_backend="fts5")

    await memory_create(
        ctx,
        content="xyloquartz-article-fts-index-unique content for article-index test",
        artifact_kind="article",
        title="FTS Index Article",
        source_url="https://example.com/fts-index",
        tags=["fts"],
    )

    result = await memory_search(ctx, "xyloquartz-article-fts-index-unique", kind="article")
    assert result.metadata["count"] >= 1
    hit = result.metadata["results"][0]
    assert hit["tier"] == "artifacts"
    assert hit["kind"] == "article"
    assert "slug" in hit
    idx.close()


# --- memory_read ---


@pytest.mark.asyncio
async def test_read_article_returns_full_body(tmp_path: Path):
    """memory_read returns the full markdown body and metadata."""
    ctx = _make_ctx(tmp_path)
    await memory_create(
        ctx,
        content="Full body content for read test.\n\nSecond paragraph.",
        artifact_kind="article",
        title="Read Test Article",
        source_url="https://example.com/read",
    )

    knowledge_dir = tmp_path / "library"
    slug = next(iter(knowledge_dir.glob("*.md"))).stem

    result = await memory_read(ctx, slug)
    assert result.metadata["title"] == "Read Test Article"
    assert result.metadata["source_ref"] == "https://example.com/read"
    assert "Full body content" in result.metadata["content"]
    assert "Second paragraph" in result.metadata["content"]


@pytest.mark.asyncio
async def test_read_article_not_found(tmp_path: Path):
    """memory_read returns error-like result for missing slug."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    result = await memory_read(ctx, "999-nonexistent-article")
    assert result.metadata["artifact_id"] is None
    assert "not found" in result.return_value.lower()


# --- memory_search cross-source ---


@pytest.mark.asyncio
async def test_memory_search_grep_finds_articles(tmp_path: Path):
    """memory_search grep fallback returns articles from library dir."""
    ctx = _make_ctx(tmp_path)
    await memory_create(
        ctx,
        content="xyloquartz-crosssource-unique knowledge content",
        artifact_kind="article",
        title="Cross Source",
        source_url="https://example.com/cross",
    )

    result = await memory_search(ctx, "xyloquartz-crosssource-unique")
    assert result.metadata["count"] >= 1
    assert all(r["tier"] == "artifacts" for r in result.metadata["results"])
