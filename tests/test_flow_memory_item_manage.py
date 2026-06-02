"""Behavioral tests for the memory write tools — delete, create, URL-keyed dedup.

Exercises: memory_delete removes artifact file + index entry, delete on missing file →
tool_error, memory_create rejects canon kind,
and the URL-keyed branch lit by `source_url` (web_fetch stamp, consolidation
on re-save, Jaccard/manual fallback when absent).
No LLM — real filesystem + real FTS5 only.
"""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.index.store import IndexStore
from co_cli.memory.item import load_memory_item
from co_cli.memory.service import reindex, save_memory_item
from co_cli.memory.store import MemoryStore
from co_cli.tools.memory.manage import (
    memory_create,
    memory_delete,
)
from co_cli.tools.shell_backend import ShellBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path, name: str = "test-search.db") -> IndexStore:
    return IndexStore(config=SETTINGS, db_path=tmp_path / name)


def _make_deps(tmp_path: Path, store: IndexStore | None = None) -> CoDeps:
    memory = MemoryStore(index=store, config=SETTINGS) if store is not None else None
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        memory_dir=tmp_path / "memory",
        index_store=store,
        memory_store=memory,
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


# ---------------------------------------------------------------------------
# Tests — delete action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_manage_delete_removes_file(tmp_path: Path) -> None:
    """memory_delete must remove the artifact file from disk.

    Regression guard: if delete is a no-op or uses the wrong path, the file
    persists and the artifact continues to appear in searches.
    """
    knowledge_dir = tmp_path / "memory"
    saved = save_memory_item(
        knowledge_dir,
        content="content to be deleted",
        memory_kind="note",
        title="delete me",
    )
    assert saved.path.exists(), "precondition: memory item file must exist before delete"

    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = await memory_delete(ctx, filename_stem=saved.filename_stem)

    assert not saved.path.exists(), "memory item file must be removed after delete"
    assert result.metadata is not None
    assert result.metadata.get("error") is not True, "successful delete must not set error flag"
    assert result.metadata.get("action") == "deleted"


@pytest.mark.asyncio
async def test_artifact_manage_delete_missing_artifact_returns_error(tmp_path: Path) -> None:
    """memory_delete on a non-existent name must return tool_error.

    Regression guard: a silent no-op on missing names would mask typos in
    filename_stem and leave the caller thinking the delete succeeded.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = await memory_delete(ctx, filename_stem="nonexistent-artifact-xyz")

    assert result.metadata is not None, "tool_error must populate metadata"
    assert result.metadata.get("error") is True, "tool_error must set error=True in metadata"
    assert "nonexistent-artifact-xyz" in result.return_value, (
        "error message must include the bad name so the caller can diagnose it"
    )


@pytest.mark.asyncio
async def test_artifact_manage_delete_removes_from_index(tmp_path: Path) -> None:
    """memory_delete must remove the artifact from the FTS5 index.

    Regression guard: if the index entry is not removed, memory_search would
    continue returning a result whose file no longer exists.
    """
    knowledge_dir = tmp_path / "memory"
    store = _make_store(tmp_path)
    try:
        saved = save_memory_item(
            knowledge_dir,
            content="uniquetoken_to_find_in_index",
            memory_kind="note",
            title="indexed note",
        )
        reindex(
            store,
            saved.path,
            saved.content,
            saved.markdown_content,
            saved.frontmatter_dict,
            saved.filename_stem,
            chunk_tokens=600,
            chunk_overlap_tokens=80,
        )

        hits_before = store.search("uniquetoken_to_find_in_index")
        assert any(saved.filename_stem in h.path for h in hits_before), (
            "precondition: memory item must be findable in index before delete"
        )

        deps = _make_deps(tmp_path, store=store)
        ctx = _make_ctx(deps)
        await memory_delete(ctx, filename_stem=saved.filename_stem)

        hits_after = store.search("uniquetoken_to_find_in_index")
        assert not any(saved.filename_stem in h.path for h in hits_after), (
            "memory item must not appear in FTS5 index after delete"
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Tests — create action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_manage_create_rejects_canon_memory_kind(tmp_path: Path) -> None:
    """memory_create must reject kind='canon' — canon is read-only.

    Regression guard: adding CANON to MemoryKindEnum would silently admit it as a writable
    kind without this check.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        memory_dir=tmp_path / "memory",
    )
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    result = await memory_create(ctx, name_title="test", content="test content", kind="canon")

    assert "canon" in result.return_value.lower(), "error message must mention the rejected kind"
    assert result.metadata is not None, "tool_error must populate metadata"
    assert result.metadata.get("error") is True, "tool_error must set error=True in metadata"


# ---------------------------------------------------------------------------
# Tests — create action with source_url (URL-keyed dedup tool surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_manage_create_with_source_url_stamps_web_fetch(tmp_path: Path) -> None:
    """memory_create(kind=article, source_url=…) routes to URL-keyed branch.

    Regression guard: if source_url is not threaded through to save_memory_item,
    the article is saved on the Jaccard path with source_type=manual, no URL-keyed
    dedup, no decay protection — orphaning the URL-keyed branch.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    url = "https://example.com/article-x"

    result = await memory_create(
        ctx,
        name_title="example article",
        content="first capture of the page",
        kind="article",
        source_url=url,
    )

    assert result.metadata is not None
    assert result.metadata.get("error") is not True, f"create failed: {result.return_value}"
    artifact_path = Path(result.metadata["path"])
    assert artifact_path.exists()

    item = load_memory_item(artifact_path)
    assert item.source_type == "web_fetch", (
        f"expected source_type=web_fetch, got {item.source_type!r}"
    )
    assert item.source_ref == url, f"expected source_ref={url!r}, got {item.source_ref!r}"
    assert item.decay_protected is True, "URL-keyed articles must be decay-protected"


@pytest.mark.asyncio
async def test_artifact_manage_create_with_same_source_url_consolidates(tmp_path: Path) -> None:
    """A second memory_create(…, source_url=X) consolidates onto the first.

    Regression guard: missing source_url plumbing → second call creates a duplicate
    file, fragmenting recall. The URL-keyed branch must preserve artifact_id and
    overwrite content in place.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    url = "https://example.com/article-y"

    first = await memory_create(
        ctx,
        name_title="topic",
        content="initial body",
        kind="article",
        source_url=url,
    )
    assert first.metadata is not None
    first_artifact_id = first.metadata["artifact_id"]
    first_path = Path(first.metadata["path"])

    second = await memory_create(
        ctx,
        name_title="topic",
        content="revised body",
        kind="article",
        source_url=url,
    )
    assert second.metadata is not None
    assert second.metadata.get("error") is not True, f"consolidation failed: {second.return_value}"
    assert second.metadata["artifact_id"] == first_artifact_id, (
        "consolidation must preserve the original artifact_id"
    )
    assert second.metadata["action"] == "merged", (
        f"expected action='merged' on re-save with same URL, got {second.metadata['action']!r}"
    )

    md_files = list((tmp_path / "memory").glob("*.md"))
    assert len(md_files) == 1, (
        f"expected exactly 1 .md file after URL-keyed consolidation, found {len(md_files)}: "
        f"{[p.name for p in md_files]}"
    )

    item = load_memory_item(first_path)
    assert item.content.strip() == "revised body", "content must be updated on consolidation"
    assert item.source_type == "web_fetch"
    assert item.source_ref == url
    assert item.decay_protected is True


@pytest.mark.asyncio
async def test_artifact_manage_create_without_source_url_uses_manual_path(tmp_path: Path) -> None:
    """memory_create(kind=article) without source_url stays on the Jaccard path.

    Regression guard: a bug threading source_url unconditionally (e.g., empty string
    truthiness) would silently push every article through the URL-keyed branch.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = await memory_create(
        ctx,
        name_title="manual article",
        content="agent-curated body with no URL",
        kind="article",
    )

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    item = load_memory_item(Path(result.metadata["path"]))
    assert item.source_type == "manual", (
        f"absent source_url must default to source_type=manual, got {item.source_type!r}"
    )
    assert item.source_ref is None
    assert item.decay_protected is False
