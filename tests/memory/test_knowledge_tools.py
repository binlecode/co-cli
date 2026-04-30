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

from co_cli.agent.core import build_agent
from co_cli.config.core import settings
from co_cli.deps import CoDeps, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.memory.knowledge_store import KnowledgeStore, SearchResult
from co_cli.tools.memory.read import memory_list
from co_cli.tools.memory.write import (
    memory_create,
    memory_modify,
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
        _write_memory(memory_dir, i, f"Memory content number {i}")

    ctx = _make_ctx(knowledge_dir=memory_dir)

    # Page 1: offset=0, limit=2
    r1 = asyncio.run(memory_list(ctx, offset=0, limit=2))
    assert r1.metadata["count"] == 2
    assert r1.metadata["total"] == 5
    assert r1.metadata["offset"] == 0
    assert r1.metadata["limit"] == 2
    assert r1.metadata["has_more"] is True
    assert r1.metadata["memories"][0]["id"] == "1"
    assert r1.metadata["memories"][1]["id"] == "2"

    # Page 2: offset=2, limit=2
    r2 = asyncio.run(memory_list(ctx, offset=2, limit=2))
    assert r2.metadata["count"] == 2
    assert r2.metadata["total"] == 5
    assert r2.metadata["has_more"] is True

    # Page 3: offset=4, limit=2 — partial last page
    r3 = asyncio.run(memory_list(ctx, offset=4, limit=2))
    assert r3.metadata["count"] == 1
    assert r3.metadata["total"] == 5
    assert r3.metadata["has_more"] is False


# ---------------------------------------------------------------------------
# memory_modify action="replace" — surgical text replacement
# ---------------------------------------------------------------------------


def test_memory_modify_replace_exact_match(tmp_path: Path):
    """memory_modify replace substitutes old passage with new in the body."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest over unittest")
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    asyncio.run(
        memory_modify(
            ctx, slug, "replace", "pytest over all others", target="pytest over unittest"
        )
    )

    updated_text = path.read_text(encoding="utf-8")
    assert "pytest over all others" in updated_text
    assert "pytest over unittest" not in updated_text


def test_memory_modify_replace_not_found(tmp_path: Path):
    """memory_modify replace returns tool_error for an unknown slug."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    ctx = _make_ctx(knowledge_dir=memory_dir)

    result = asyncio.run(memory_modify(ctx, "999-nonexistent", "replace", "new", target="old"))
    assert "not found" in result.return_value.lower()


def test_memory_modify_replace_zero_occurrences(tmp_path: Path):
    """memory_modify replace returns tool_error when target is not in the body."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest")
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    result = asyncio.run(memory_modify(ctx, slug, "replace", "mocha", target="unittest"))
    assert "not found" in result.return_value.lower()


def test_memory_modify_replace_ambiguous(tmp_path: Path):
    """memory_modify replace returns tool_error when target appears more than once."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User uses pytest. Also uses pytest.")
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    result = asyncio.run(memory_modify(ctx, slug, "replace", "mocha", target="pytest"))
    assert "2 times" in result.return_value


def test_memory_modify_replace_rejects_line_prefix(tmp_path: Path):
    """memory_modify replace returns tool_error when target contains Read-tool line prefixes."""
    memory_dir = tmp_path / "memory"
    path = _write_memory(memory_dir, 1, "User prefers pytest")
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    result = asyncio.run(
        memory_modify(ctx, slug, "replace", "new", target="1→ User prefers pytest")
    )
    assert "line-number prefixes" in result.return_value


def test_memory_modify_replace_tab_normalization(tmp_path: Path):
    """memory_modify replace matches when the body uses spaces where target has a tab.

    expandtabs() normalises both sides before matching. "foo" is 3 chars so a tab
    at column 3 advances to column 8 (5 spaces): "foo\tbar" → "foo     bar".
    Body is written with those literal spaces; target uses the raw tab — they
    compare equal after normalisation.
    """
    memory_dir = tmp_path / "memory"
    # 5 spaces at column 3: what expandtabs() produces for "foo\tbar"
    path = _write_memory(memory_dir, 1, "foo     bar")
    ctx = _make_ctx(knowledge_dir=memory_dir)

    slug = path.stem
    asyncio.run(memory_modify(ctx, slug, "replace", "replaced", target="foo\tbar"))

    assert "replaced" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# memory_modify action="append" — add content to end of body
# ---------------------------------------------------------------------------


def test_memory_modify_append_missing_slug_returns_error(tmp_path: Path):
    """memory_modify append returns tool_error for an unknown slug."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    ctx = _make_ctx(knowledge_dir=memory_dir)

    result = asyncio.run(memory_modify(ctx, "999-nonexistent", "append", "extra line"))
    assert "not found" in result.return_value.lower()


# ---------------------------------------------------------------------------
# memory_modify DB re-index round-trip
# ---------------------------------------------------------------------------


def test_memory_modify_replace_reindexes_in_db(tmp_path: Path):
    """memory_modify replace must update the DB index so the new content is findable."""

    memory_dir = tmp_path / "memory"
    _write_memory(memory_dir, 1, "original-content-for-update-test")
    file_path = next(memory_dir.glob("*.md"))
    slug = file_path.stem

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", memory_dir)
        ctx = _make_ctx(knowledge_dir=memory_dir, knowledge_store=idx)

        asyncio.run(
            memory_modify(
                ctx,
                slug,
                "replace",
                "updated-content-xyz",
                target="original-content-for-update-test",
            )
        )

        results = idx.search("updated-content-xyz", source="knowledge", limit=5)
        assert any("updated-content-xyz" in r.snippet for r in results), (
            "memory_modify replace must re-index so the updated content is searchable"
        )
    finally:
        idx.close()


def test_memory_modify_append_reindexes_in_db(tmp_path: Path):
    """memory_modify append must update the DB index so the appended content is findable."""

    memory_dir = tmp_path / "memory"
    _write_memory(memory_dir, 1, "base-content-for-append-test")
    file_path = next(memory_dir.glob("*.md"))
    slug = file_path.stem

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", memory_dir)
        ctx = _make_ctx(knowledge_dir=memory_dir, knowledge_store=idx)

        asyncio.run(memory_modify(ctx, slug, "append", "appended-unique-content-abc"))

        results = idx.search("appended-unique-content-abc", source="knowledge", limit=5)
        assert any("appended-unique-content-abc" in r.snippet for r in results), (
            "memory_modify append must re-index so the appended content is searchable"
        )
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# memory_create dedup
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


class TestMemoryCreateDedup:
    def test_distinct_content_creates_new_file(self, tmp_path: Path) -> None:
        """Unrelated content always writes a new artifact regardless of dedup setting."""
        knowledge_dir = tmp_path / "knowledge"
        _write_memory(knowledge_dir, 1, "completely unrelated existing entry")
        ctx = _make_dedup_ctx(knowledge_dir)
        result = asyncio.run(memory_create(ctx, "user prefers pytest for testing", "preference"))
        files = list(knowledge_dir.glob("*.md"))
        assert len(files) == 2, "distinct content must produce a second file"
        assert result.metadata["action"] == "saved"

    def test_near_identical_content_skips_write(self, tmp_path: Path) -> None:
        """Score > 0.9 must skip writing and return action='skipped'."""
        knowledge_dir = tmp_path / "knowledge"
        _write_memory(knowledge_dir, 1, "user prefers pytest over unittest")
        ctx = _make_dedup_ctx(knowledge_dir, threshold=0.5)
        result = asyncio.run(memory_create(ctx, "user prefers pytest over unittest", "preference"))
        files = list(knowledge_dir.glob("*.md"))
        assert len(files) == 1, "no new file must be written on skip"
        assert result.metadata["action"] == "skipped"

    def test_superset_content_replaces_existing_body(self, tmp_path: Path) -> None:
        """New content whose tokens are a strict superset of existing content triggers merge."""
        knowledge_dir = tmp_path / "knowledge"
        existing = _write_memory(knowledge_dir, 1, "user prefers pytest")
        ctx = _make_dedup_ctx(knowledge_dir, threshold=0.3)
        result = asyncio.run(
            memory_create(
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
        existing = _write_memory(knowledge_dir, 1, "user prefers pytest ruff")
        ctx = _make_dedup_ctx(knowledge_dir, threshold=0.2)
        result = asyncio.run(memory_create(ctx, "user prefers pytest mypy checks", "preference"))
        files = list(knowledge_dir.glob("*.md"))
        assert len(files) == 1, "append must not create a new file"
        assert result.metadata["action"] == "appended"
        updated_body = existing.read_text(encoding="utf-8")
        assert "ruff" in updated_body
        assert "mypy checks" in updated_body

    def test_dedup_bypassed_when_consolidation_disabled(self, tmp_path: Path) -> None:
        """When consolidation_enabled=False, identical content always writes a new file."""
        knowledge_dir = tmp_path / "knowledge"
        _write_memory(knowledge_dir, 1, "user prefers pytest over unittest")
        ctx = _make_ctx(knowledge_dir=knowledge_dir)  # consolidation_enabled defaults to False
        result = asyncio.run(memory_create(ctx, "user prefers pytest over unittest", "preference"))
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
async def test_memory_modify_append_busy_error_uses_ctx_path(tmp_path: Path) -> None:
    """Oversized memory_modify append busy error is persisted through the ctx-aware path."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_memory(knowledge_dir, 1, "test content for lock")
    slug = path.stem
    ctx = _make_ctx_sized_knowledge(knowledge_dir, tmp_path / "tool-results", "memory_modify")

    acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock() -> None:
        async with ctx.deps.resource_locks.try_acquire(slug):
            acquired.set()
            await release.wait()

    task = asyncio.create_task(hold_lock())
    await acquired.wait()

    result = await memory_modify(ctx, slug, "append", "extra content")
    assert PERSISTED_OUTPUT_TAG in result.return_value

    release.set()
    await task


@pytest.mark.asyncio
async def test_memory_modify_replace_busy_error_uses_ctx_path(tmp_path: Path) -> None:
    """Oversized memory_modify replace busy error is persisted through the ctx-aware path."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_memory(knowledge_dir, 1, "test content for update lock")
    slug = path.stem
    ctx = _make_ctx_sized_knowledge(knowledge_dir, tmp_path / "tool-results", "memory_modify")

    acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock() -> None:
        async with ctx.deps.resource_locks.try_acquire(slug):
            acquired.set()
            await release.wait()

    task = asyncio.create_task(hold_lock())
    await acquired.wait()

    result = await memory_modify(
        ctx, slug, "replace", "new content", target="test content for update lock"
    )
    assert PERSISTED_OUTPUT_TAG in result.return_value

    release.set()
    await task


def test_memory_create_success_uses_ctx_path(tmp_path: Path) -> None:
    """Oversized memory_create success message is persisted through the ctx-aware path."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx_sized_knowledge(knowledge_dir, tmp_path / "tool-results", "memory_create")
    result = asyncio.run(memory_create(ctx, "some knowledge content to save", "preference"))
    assert PERSISTED_OUTPUT_TAG in result.return_value


# ---------------------------------------------------------------------------
# source_ref / artifact_id — identity fields on SearchResult
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_search_populates_source_ref_and_artifact_id(tmp_path: Path) -> None:
    """store.search() returns SearchResult with source_ref and artifact_id for articles indexed via memory_create."""
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
        await memory_create(
            ctx,
            content="xyloquartz-identity-field-unique content for identity test",
            artifact_kind="article",
            title="Identity Test Article",
            source_url="https://example.com/identity-test",
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


# ---------------------------------------------------------------------------
# memory_create tool — saves artifact, returns artifact_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_create_saves_artifact_and_returns_artifact_id(tmp_path: Path) -> None:
    """memory_create writes a file and returns artifact_id in metadata."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    ctx = _make_ctx(knowledge_dir=knowledge_dir)

    result = await memory_create(ctx, "user prefers pytest for testing", "preference")

    files = list(knowledge_dir.glob("*.md"))
    assert len(files) == 1, "memory_create must write exactly one file"
    assert result.metadata["action"] == "saved"
    assert result.metadata["artifact_id"] is not None


@pytest.mark.asyncio
async def test_memory_create_with_source_url_sets_decay_protected(tmp_path: Path) -> None:
    """memory_create with source_url always sets decay_protected=True."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    ctx = _make_ctx(knowledge_dir=knowledge_dir)

    result = await memory_create(
        ctx,
        content="Article about async patterns",
        artifact_kind="article",
        source_url="https://example.com/async-guide",
        decay_protected=False,
    )

    assert result.metadata["action"] == "saved"
    file_text = Path(result.metadata["path"]).read_text(encoding="utf-8")
    assert "decay_protected: true" in file_text


# ---------------------------------------------------------------------------
# memory_modify tool — append and replace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_modify_append_round_trips_content(tmp_path: Path) -> None:
    """memory_modify action='append' adds content to artifact body."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_memory(knowledge_dir, 1, "User prefers pytest")
    ctx = _make_ctx(knowledge_dir=knowledge_dir)

    slug = path.stem
    result = await memory_modify(ctx, slug, "append", "Also uses ruff for linting.")

    assert result.metadata["action"] == "appended"
    updated_text = path.read_text(encoding="utf-8")
    assert updated_text.rstrip("\n").endswith("Also uses ruff for linting.")
    assert "User prefers pytest" in updated_text


@pytest.mark.asyncio
async def test_memory_modify_replace_empty_target_returns_tool_error(tmp_path: Path) -> None:
    """memory_modify action='replace' with empty target returns tool_error (not raises)."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_memory(knowledge_dir, 1, "User prefers pytest")
    ctx = _make_ctx(knowledge_dir=knowledge_dir)

    slug = path.stem
    result = await memory_modify(ctx, slug, "replace", "new content", target="")

    assert "non-empty target" in result.return_value


@pytest.mark.asyncio
async def test_memory_modify_line_prefix_in_content_returns_tool_error(tmp_path: Path) -> None:
    """memory_modify rejects content with Read-tool line-number prefixes."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_memory(knowledge_dir, 1, "User prefers pytest")
    ctx = _make_ctx(knowledge_dir=knowledge_dir)

    slug = path.stem
    result = await memory_modify(ctx, slug, "append", "1→ User prefers pytest")

    assert "line-number prefixes" in result.return_value


# ---------------------------------------------------------------------------
# BM25 score ordering: more-relevant artifact must score higher
# ---------------------------------------------------------------------------


def test_fts_search_scores_higher_relevance_artifact_first(tmp_path: Path) -> None:
    """BM25 score must be monotone with relevance: more occurrences → higher score.

    Regression guard for the inverted formula bug (1/(1+abs(rank)) maps
    high-magnitude rank to LOW score; abs(rank)/(1+abs(rank)) is correct).
    """
    knowledge_dir = tmp_path / "knowledge"
    # High-relevance artifact: query term "quasar" repeated many times
    hi_content = " ".join(["quasar"] * 20)
    _write_memory(knowledge_dir, 1, hi_content)
    # Low-relevance artifact: query term appears once
    _write_memory(knowledge_dir, 2, "quasar is a distant luminous object")

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", knowledge_dir)
        results = idx.search("quasar", source="knowledge", limit=5)

        assert len(results) == 2, f"Expected 2 results, got {len(results)}"
        hi_score = results[0].score
        lo_score = results[1].score
        assert hi_score > lo_score, (
            f"High-relevance artifact must score higher: hi={hi_score:.6f} lo={lo_score:.6f}"
        )
    finally:
        idx.close()
