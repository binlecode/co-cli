"""Workflow tests for reusable knowledge tools and recall bookkeeping."""

import asyncio
import sqlite3
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
from co_cli.deps import CoDeps, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.knowledge._chunker import chunk_text
from co_cli.knowledge._store import KnowledgeStore, SearchResult
from co_cli.tools.knowledge.read import knowledge_list, knowledge_search
from co_cli.tools.knowledge.write import (
    knowledge_append,
    knowledge_article_save,
    knowledge_save,
    knowledge_update,
)
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG

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
# list_knowledge pagination
# ---------------------------------------------------------------------------


def test_list_knowledge_pagination(tmp_path: Path):
    """list_knowledge returns correct pages with offset/limit."""
    memory_dir = tmp_path / "memory"
    for i in range(1, 6):
        _write_memory(memory_dir, i, f"Memory content number {i}", tags=["test"])

    ctx = _make_ctx(knowledge_dir=memory_dir)

    # Page 1: offset=0, limit=2
    r1 = asyncio.run(knowledge_list(ctx, offset=0, limit=2))
    assert r1.metadata["count"] == 2
    assert r1.metadata["total"] == 5
    assert r1.metadata["offset"] == 0
    assert r1.metadata["limit"] == 2
    assert r1.metadata["has_more"] is True
    assert r1.metadata["memories"][0]["id"] == "1"
    assert r1.metadata["memories"][1]["id"] == "2"

    # Page 2: offset=2, limit=2
    r2 = asyncio.run(knowledge_list(ctx, offset=2, limit=2))
    assert r2.metadata["count"] == 2
    assert r2.metadata["total"] == 5
    assert r2.metadata["has_more"] is True

    # Page 3: offset=4, limit=2 — partial last page
    r3 = asyncio.run(knowledge_list(ctx, offset=4, limit=2))
    assert r3.metadata["count"] == 1
    assert r3.metadata["total"] == 5
    assert r3.metadata["has_more"] is False


# ---------------------------------------------------------------------------
# update_knowledge — surgical text replacement
# ---------------------------------------------------------------------------


def test_update_knowledge_replaces_exact_match(tmp_path: Path):
    """update_knowledge replaces old_content with new_content in the body."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest over unittest", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    asyncio.run(knowledge_update(ctx, slug, "pytest over unittest", "pytest over all others"))

    updated_text = path.read_text(encoding="utf-8")
    assert "pytest over all others" in updated_text
    assert "pytest over unittest" not in updated_text


def test_update_knowledge_raises_not_found(tmp_path: Path):
    """update_knowledge raises FileNotFoundError for an unknown slug."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    ctx = _make_ctx(knowledge_dir=memory_dir)

    with pytest.raises(FileNotFoundError, match="not found"):
        asyncio.run(knowledge_update(ctx, "999-nonexistent", "old", "new"))


def test_update_knowledge_raises_zero_occurrences(tmp_path: Path):
    """update_knowledge raises ValueError when old_content is not in the body."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem

    with pytest.raises(ValueError, match="not found"):
        asyncio.run(knowledge_update(ctx, slug, "unittest", "mocha"))


def test_update_knowledge_raises_ambiguous(tmp_path: Path):
    """update_knowledge raises ValueError when old_content appears more than once."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User uses pytest. Also uses pytest.", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem

    with pytest.raises(ValueError, match="2 times"):
        asyncio.run(knowledge_update(ctx, slug, "pytest", "mocha"))


def test_update_knowledge_rejects_line_prefix(tmp_path: Path):
    """update_knowledge raises ValueError when old_content contains Read-tool line prefixes."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem

    # Simulate Read tool artifact: "1→ User prefers pytest"
    with pytest.raises(ValueError, match="line-number prefixes"):
        asyncio.run(knowledge_update(ctx, slug, "1\u2192 User prefers pytest", "new"))


def test_update_knowledge_tab_normalization(tmp_path: Path):
    """update_knowledge matches when the body uses spaces where old_content has a tab.

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
    asyncio.run(knowledge_update(ctx, slug, "foo\tbar", "replaced"))

    assert "replaced" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# append_knowledge — add content to end of body
# ---------------------------------------------------------------------------


def test_append_knowledge_adds_to_end(tmp_path: Path):
    """append_knowledge appends content as a new line at the end of the body."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest", tags=["preference"])
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    asyncio.run(knowledge_append(ctx, slug, "Also uses coverage reports."))

    updated_text = path.read_text(encoding="utf-8")
    assert updated_text.rstrip("\n").endswith("Also uses coverage reports.")
    assert "User prefers pytest" in updated_text


def test_append_knowledge_missing_slug_raises(tmp_path: Path):
    """append_knowledge raises FileNotFoundError for an unknown slug."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    ctx = _make_ctx(knowledge_dir=memory_dir)

    with pytest.raises(FileNotFoundError, match="not found"):
        asyncio.run(knowledge_append(ctx, "999-nonexistent", "extra line"))


# ---------------------------------------------------------------------------
# rag.backend OTel annotation
# ---------------------------------------------------------------------------


def test_rag_backend_annotation_on_search_spans(tmp_path: Path):
    """search_knowledge stamps rag.backend on the active OTel span."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from co_cli.tools.knowledge.read import knowledge_search

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
            # (1) search_knowledge FTS path
            asyncio.run(knowledge_search(fts_know_ctx, "rag-backend-annotation-fts-test"))
            assert parent_span.attributes.get("rag.backend") in ("fts5", "hybrid")

            # (2) search_knowledge grep path
            asyncio.run(knowledge_search(grep_know_ctx, "rag-backend-annotation-fts-test"))
            assert parent_span.attributes.get("rag.backend") == "grep"
    finally:
        idx.close()

    assert otel_trace.get_tracer_provider() is _orig


# ---------------------------------------------------------------------------
# update_knowledge / append_knowledge DB re-index round-trip
# ---------------------------------------------------------------------------


def test_update_knowledge_reindexes_in_db(tmp_path: Path):
    """update_knowledge must update the DB index so the new content is findable."""

    memory_dir = tmp_path / "memory"
    _write_memory(memory_dir, 1, "original-content-for-update-test")
    file_path = next(memory_dir.glob("*.md"))
    slug = file_path.stem

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", memory_dir)
        ctx = _make_ctx(knowledge_dir=memory_dir, knowledge_store=idx)

        asyncio.run(
            knowledge_update(ctx, slug, "original-content-for-update-test", "updated-content-xyz")
        )

        results = idx.search("updated-content-xyz", source="knowledge", limit=5)
        assert any("updated-content-xyz" in r.snippet for r in results), (
            "update_knowledge must re-index so the updated content is searchable"
        )
    finally:
        idx.close()


def test_append_knowledge_reindexes_in_db(tmp_path: Path):
    """append_knowledge must update the DB index so the appended content is findable."""

    memory_dir = tmp_path / "memory"
    _write_memory(memory_dir, 1, "base-content-for-append-test")
    file_path = next(memory_dir.glob("*.md"))
    slug = file_path.stem

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", memory_dir)
        ctx = _make_ctx(knowledge_dir=memory_dir, knowledge_store=idx)

        asyncio.run(knowledge_append(ctx, slug, "appended-unique-content-abc"))

        results = idx.search("appended-unique-content-abc", source="knowledge", limit=5)
        assert any("appended-unique-content-abc" in r.snippet for r in results), (
            "append_knowledge must re-index so the appended content is searchable"
        )
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# save_knowledge dedup (TASK-4.2)
# ---------------------------------------------------------------------------


def _make_dedup_ctx(knowledge_dir: Path, *, threshold: float = 0.75) -> RunContext:
    """Return a RunContext with consolidation_enabled=True at given threshold."""
    base = make_settings()
    knowledge_cfg = base.knowledge.model_copy(
        update={"consolidation_enabled": True, "consolidation_similarity_threshold": threshold}
    )
    config = base.model_copy(update={"knowledge": knowledge_cfg})
    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_store=None,
        config=config,
        knowledge_dir=knowledge_dir,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


class TestSaveKnowledgeDedup:
    def test_distinct_content_creates_new_file(self, tmp_path: Path) -> None:
        """Unrelated content always writes a new artifact regardless of dedup setting."""
        knowledge_dir = tmp_path / "knowledge"
        _write_memory(knowledge_dir, 1, "completely unrelated existing entry", tags=[])
        ctx = _make_dedup_ctx(knowledge_dir)
        result = asyncio.run(knowledge_save(ctx, "user prefers pytest for testing", "preference"))
        files = list(knowledge_dir.glob("*.md"))
        assert len(files) == 2, "distinct content must produce a second file"
        assert result.metadata["action"] == "saved"

    def test_near_identical_content_skips_write(self, tmp_path: Path) -> None:
        """Score > 0.9 must skip writing and return action='skipped'."""
        knowledge_dir = tmp_path / "knowledge"
        _write_memory(knowledge_dir, 1, "user prefers pytest over unittest", tags=[])
        ctx = _make_dedup_ctx(knowledge_dir, threshold=0.5)
        result = asyncio.run(
            knowledge_save(ctx, "user prefers pytest over unittest", "preference")
        )
        files = list(knowledge_dir.glob("*.md"))
        assert len(files) == 1, "no new file must be written on skip"
        assert result.metadata["action"] == "skipped"

    def test_superset_content_replaces_existing_body(self, tmp_path: Path) -> None:
        """New content whose tokens are a strict superset of existing content triggers merge."""
        knowledge_dir = tmp_path / "knowledge"
        existing = _write_memory(knowledge_dir, 1, "user prefers pytest", tags=[])
        ctx = _make_dedup_ctx(knowledge_dir, threshold=0.3)
        result = asyncio.run(
            knowledge_save(
                ctx, "user prefers pytest over unittest framework for testing", "preference"
            )
        )
        files = list(knowledge_dir.glob("*.md"))
        assert len(files) == 1, "merge must not create a new file"
        assert result.metadata["action"] == "merged"
        updated_body = existing.read_text(encoding="utf-8")
        assert "over unittest framework for testing" in updated_body

    def test_overlapping_content_appends_to_existing(self, tmp_path: Path) -> None:
        """Partially-overlapping content that is not a superset appends to the existing artifact."""
        knowledge_dir = tmp_path / "knowledge"
        existing = _write_memory(knowledge_dir, 1, "user prefers pytest ruff", tags=[])
        ctx = _make_dedup_ctx(knowledge_dir, threshold=0.2)
        result = asyncio.run(knowledge_save(ctx, "user prefers pytest mypy checks", "preference"))
        files = list(knowledge_dir.glob("*.md"))
        assert len(files) == 1, "append must not create a new file"
        assert result.metadata["action"] == "appended"
        updated_body = existing.read_text(encoding="utf-8")
        # Both the original and appended content must be present
        assert "ruff" in updated_body
        assert "mypy checks" in updated_body

    def test_dedup_bypassed_when_consolidation_disabled(self, tmp_path: Path) -> None:
        """When consolidation_enabled=False, identical content always writes a new file."""
        knowledge_dir = tmp_path / "knowledge"
        _write_memory(knowledge_dir, 1, "user prefers pytest over unittest", tags=[])
        ctx = _make_ctx(knowledge_dir=knowledge_dir)  # consolidation_enabled defaults to False
        result = asyncio.run(
            knowledge_save(ctx, "user prefers pytest over unittest", "preference")
        )
        files = list(knowledge_dir.glob("*.md"))
        assert len(files) == 2, "disabled dedup must allow duplicate writes"
        assert result.metadata["action"] == "saved"


# ---------------------------------------------------------------------------
# ctx-path regression tests — verify error/success returns go through tool_output(ctx=ctx)
# ---------------------------------------------------------------------------


def _make_ctx_sized_knowledge(
    knowledge_dir: Path,
    tool_results_dir: Path,
    tool_name: str,
    max_result_size: int = 10,
) -> RunContext:
    """Return a RunContext with tool_name registered at max_result_size in tool_index."""
    info = ToolInfo(
        name=tool_name,
        description="test tool",
        approval=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.ALWAYS,
        max_result_size=max_result_size,
    )
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        knowledge_dir=knowledge_dir,
        tool_results_dir=tool_results_dir,
        tool_index={tool_name: info},
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage(), tool_name=tool_name)


@pytest.mark.asyncio
async def test_append_knowledge_busy_error_uses_ctx_path(tmp_path: Path) -> None:
    """Oversized append_knowledge busy error is persisted through the ctx-aware path."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_memory(knowledge_dir, 1, "test content for lock", tags=["test"])
    slug = path.stem
    ctx = _make_ctx_sized_knowledge(knowledge_dir, tmp_path / "tool-results", "knowledge_append")

    acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock() -> None:
        async with ctx.deps.resource_locks.try_acquire(slug):
            acquired.set()
            await release.wait()

    task = asyncio.create_task(hold_lock())
    await acquired.wait()

    result = await knowledge_append(ctx, slug, "extra content")
    assert PERSISTED_OUTPUT_TAG in result.return_value

    release.set()
    await task


@pytest.mark.asyncio
async def test_update_knowledge_busy_error_uses_ctx_path(tmp_path: Path) -> None:
    """Oversized update_knowledge busy error is persisted through the ctx-aware path."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_memory(knowledge_dir, 1, "test content for update lock", tags=["test"])
    slug = path.stem
    ctx = _make_ctx_sized_knowledge(knowledge_dir, tmp_path / "tool-results", "knowledge_update")

    acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock() -> None:
        async with ctx.deps.resource_locks.try_acquire(slug):
            acquired.set()
            await release.wait()

    task = asyncio.create_task(hold_lock())
    await acquired.wait()

    result = await knowledge_update(ctx, slug, "test content for update lock", "new content")
    assert PERSISTED_OUTPUT_TAG in result.return_value

    release.set()
    await task


def test_save_knowledge_success_uses_ctx_path(tmp_path: Path) -> None:
    """Oversized save_knowledge success message is persisted through the ctx-aware path."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    # consolidation_enabled=False by default — isolates the final-save return path
    ctx = _make_ctx_sized_knowledge(knowledge_dir, tmp_path / "tool-results", "knowledge_save")
    result = asyncio.run(knowledge_save(ctx, "some knowledge content to save", "preference"))
    assert PERSISTED_OUTPUT_TAG in result.return_value


# ---------------------------------------------------------------------------
# source_ref / artifact_id — identity fields on SearchResult
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_search_populates_source_ref_and_artifact_id(tmp_path: Path) -> None:
    """store.search() returns SearchResult with source_ref and artifact_id for articles indexed via save_article."""
    fts_cfg = make_settings(
        knowledge=make_settings().knowledge.model_copy(update={"search_backend": "fts5"})
    )
    idx = KnowledgeStore(config=fts_cfg, knowledge_db_path=tmp_path / "search.db")
    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_store=idx,
        config=fts_cfg,
        knowledge_dir=tmp_path / "library",
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    try:
        await knowledge_article_save(
            ctx,
            content="xyloquartz-identity-field-unique content for identity test",
            title="Identity Test Article",
            origin_url="https://example.com/identity-test",
            tags=["identity"],
        )
        results = idx.search("xyloquartz-identity-field-unique", source="knowledge")
        assert len(results) >= 1
        hit = results[0]
        assert hit.source_ref == "https://example.com/identity-test"
        assert hit.artifact_id is not None
    finally:
        idx.close()


def test_search_result_to_tool_output_excludes_identity_fields() -> None:
    """SearchResult.to_tool_output() does not expose source_ref or artifact_id."""
    result = SearchResult(
        source="knowledge",
        kind="article",
        path="/tmp/test.md",
        title="Test",
        snippet="snippet",
        score=0.9,
        tags=None,
        category=None,
        created=None,
        updated=None,
        source_ref="https://example.com",
        artifact_id="abc-123",
    )
    output = result.to_tool_output()
    assert "source_ref" not in output
    assert "artifact_id" not in output
    assert set(output.keys()) == {
        "source",
        "kind",
        "title",
        "snippet",
        "score",
        "path",
        "confidence",
        "conflict",
    }


@pytest.mark.asyncio
async def test_fts_article_search_tolerates_null_identity_fields(tmp_path: Path) -> None:
    """Article-index FTS path emits origin_url='' and article_id=None for pre-migration rows."""
    fts_cfg = make_settings(
        knowledge=make_settings().knowledge.model_copy(update={"search_backend": "fts5"})
    )
    idx = KnowledgeStore(config=fts_cfg, knowledge_db_path=tmp_path / "search.db")
    path_str = str(tmp_path / "legacy-article.md")
    content = "xyloquartz-null-identity-unique legacy article content"
    idx.index(
        source="knowledge",
        kind="article",
        path=path_str,
        title="Legacy Article",
        content=content,
    )
    idx.index_chunks("knowledge", path_str, chunk_text(content, chunk_size=200, overlap=0))

    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_store=idx,
        config=fts_cfg,
        knowledge_dir=tmp_path / "library",
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    try:
        result = await knowledge_search(
            ctx, "xyloquartz-null-identity-unique", kind="article", source="knowledge"
        )
        assert result.metadata["count"] >= 1
        hit = result.metadata["results"][0]
        assert hit["origin_url"] == ""
        assert hit["article_id"] is None
    finally:
        idx.close()


def test_knowledge_store_migration_adds_new_columns_to_legacy_db(tmp_path: Path) -> None:
    """KnowledgeStore adds source_ref and artifact_id to a DB created without them."""
    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE docs (
            source      TEXT NOT NULL,
            kind        TEXT,
            path        TEXT NOT NULL,
            title       TEXT,
            content     TEXT,
            mtime       REAL,
            hash        TEXT,
            tags        TEXT,
            category    TEXT,
            created     TEXT,
            updated     TEXT,
            provenance  TEXT,
            certainty   TEXT,
            chunk_id    INTEGER DEFAULT 0,
            type        TEXT,
            description TEXT,
            UNIQUE(source, path, chunk_id)
        );
    """)
    con.commit()
    con.close()

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=db_path)
    idx.close()

    con2 = sqlite3.connect(str(db_path))
    col_names = {row[1] for row in con2.execute("PRAGMA table_info(docs)").fetchall()}
    con2.close()

    assert "source_ref" in col_names, "Migration must add source_ref column to legacy DB"
    assert "artifact_id" in col_names, "Migration must add artifact_id column to legacy DB"
