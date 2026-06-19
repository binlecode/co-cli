"""Tests for memory_view — full-body artifact reader by filename_stem."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.index.store import IndexStore
from co_cli.memory.service import reindex, save_memory_item
from co_cli.tools.memory.view import memory_view
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import VIEW_MAX_BODY_CHARS

_FTS5_CONFIG = SETTINGS.memory.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_TEST_SETTINGS = SETTINGS.model_copy(update={"memory": _FTS5_CONFIG})


def _make_store(tmp_path: Path) -> IndexStore:
    return IndexStore(config=_TEST_SETTINGS, db_path=tmp_path / "search.db")


def _make_deps(tmp_path: Path, store: IndexStore | None = None) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=_TEST_SETTINGS,
        session=CoSessionState(),
        memory_dir=tmp_path / "memory",
        index_store=store,
    )


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _seed(
    knowledge_dir: Path,
    store: IndexStore,
    *,
    content: str,
    kind: str,
    title: str,
) -> str:
    """Seed an artifact and return its filename_stem."""
    r = save_memory_item(knowledge_dir, content=content, memory_kind=kind, title=title)
    reindex(
        store,
        r.path,
        r.content,
        r.markdown_content,
        r.frontmatter_dict,
        r.filename_stem,
        chunk_tokens=600,
        chunk_overlap_tokens=80,
    )
    return r.filename_stem


@pytest.mark.asyncio
async def test_memory_view_returns_body_after_create(tmp_path: Path) -> None:
    """memory_view must return the artifact body (post-frontmatter) after creation.

    Failure mode: returning frontmatter or full file contents rather than just the body
    causes the agent to see metadata noise instead of the actual artifact text.
    """
    store = _make_store(tmp_path)
    try:
        stem = _seed(
            tmp_path / "memory",
            store,
            content="This is the body content for viewing.",
            kind="note",
            title="view test",
        )
        deps = _make_deps(tmp_path, store)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await memory_view(ctx, name=stem)

        assert result.metadata is None or result.metadata.get("error") is not True, (
            f"memory_view must succeed for existing artifact: {result.return_value!r}"
        )
        assert "This is the body content for viewing." in result.return_value, (
            f"body content missing from result: {result.return_value!r}"
        )
        # Frontmatter keys must not appear in the body
        assert "memory_kind:" not in result.return_value, (
            f"frontmatter must be stripped from body: {result.return_value!r}"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_view_clamps_oversized_body(tmp_path: Path) -> None:
    """memory_view must clamp an oversized artifact body and append a truncation marker.

    Failure mode: returning the full multi-megabyte body floods the agent's context
    instead of nudging it to refine toward a narrower artifact.
    """
    store = _make_store(tmp_path)
    try:
        oversized = "lorem ipsum dolor sit amet. " * 2_000
        assert len(oversized) > VIEW_MAX_BODY_CHARS
        stem = _seed(
            tmp_path / "memory",
            store,
            content=oversized,
            kind="note",
            title="oversized artifact",
        )
        deps = _make_deps(tmp_path, store)
        from co_cli.tools.agent_tool import AGENT_TOOL_ATTR

        info = getattr(memory_view, AGENT_TOOL_ATTR)
        deps.tool_catalog[info.name] = info
        ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=info.name)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await memory_view(ctx, name=stem)

        assert "(truncated — refine with a narrower artifact)" in result.return_value, (
            f"oversized body must carry the truncation marker: {result.return_value[-200:]!r}"
        )
        assert len(result.return_value) < VIEW_MAX_BODY_CHARS + 200, (
            f"clamped body must be bounded near VIEW_MAX_BODY_CHARS: {len(result.return_value)}"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_view_unknown_name_returns_tool_error(tmp_path: Path) -> None:
    """memory_view must return tool_error for an unknown artifact name.

    Failure mode: raising an exception instead of returning tool_error causes
    pydantic-ai to retry rather than surface the miss to the agent gracefully.
    """
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await memory_view(ctx, name="nonexistent_artifact_99zz")

    assert result.metadata is not None, "tool_error must populate metadata"
    assert result.metadata.get("error") is True, (
        f"unknown name must return tool_error: {result.return_value!r}"
    )
    assert "nonexistent_artifact_99zz" in result.return_value, (
        f"error message must name the missing artifact: {result.return_value!r}"
    )


@pytest.mark.asyncio
async def test_memory_view_metadata_includes_kind_name_path(tmp_path: Path) -> None:
    """memory_view result metadata must include kind, name, and path fields.

    Failure mode: missing metadata fields break callers that extract kind for
    display or path for subsequent file operations.
    """
    store = _make_store(tmp_path)
    try:
        stem = _seed(
            tmp_path / "memory",
            store,
            content="metadata check content here",
            kind="note",
            title="metadata test",
        )
        deps = _make_deps(tmp_path, store)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await memory_view(ctx, name=stem)

        meta = result.metadata or {}
        assert meta.get("kind") == "note", f"metadata.kind must be 'note': {meta}"
        assert meta.get("name") == stem, f"metadata.name must be {stem!r}: {meta}"
        assert "path" in meta, f"metadata must include path: {meta}"
        assert meta["path"].endswith(".md"), f"path must end with .md: {meta}"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_view_body_matches_after_append(tmp_path: Path) -> None:
    """memory_view must return updated body after memory_append appends content.

    Failure mode: view returning stale cached content rather than the current on-disk
    body causes the agent to work with out-of-date artifact state.
    """
    from co_cli.tools.memory.manage import memory_append

    store = _make_store(tmp_path)
    try:
        stem = _seed(
            tmp_path / "memory",
            store,
            content="initial body line",
            kind="note",
            title="append test",
        )
        deps = _make_deps(tmp_path, store)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            await memory_append(
                ctx,
                filename_stem=stem,
                content="appended extra line here",
            )

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await memory_view(ctx, name=stem)

        assert "appended extra line here" in result.return_value, (
            f"view must return updated body after append: {result.return_value!r}"
        )
    finally:
        store.close()
