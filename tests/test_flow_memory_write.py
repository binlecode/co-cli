"""Behavioral tests for save_memory_item and mutate_memory_item write paths.

Exercises: dedup (URL-keyed, Jaccard), straight save, indexing, append,
replace-uniqueness guard, and replace-frontmatter integrity. No LLM —
real filesystem + real FTS5 only.
"""

from pathlib import Path

import pytest
import yaml
from tests._settings import SETTINGS

from co_cli.index.store import IndexStore
from co_cli.memory.item import load_memory_item
from co_cli.memory.service import mutate_memory_item, reindex, save_memory_item

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path, name="test-search.db") -> IndexStore:
    return IndexStore(config=SETTINGS, db_path=tmp_path / name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_memory_item_straight_save_creates_file_and_indexes(tmp_path):
    """save_memory_item (straight path) must write a file AND index it in FTS5.

    Failure mode: file written but not indexed → memory_search misses newly
    created memory items on the next turn.
    """
    memory_dir = tmp_path / "memory"
    store = _make_store(tmp_path)
    try:
        result = save_memory_item(
            memory_dir,
            content="pytest is a testing framework",
            memory_kind="note",
            title="pytest note",
        )

        assert result.action == "saved"
        assert result.path.exists(), "memory item file was not written to disk"
        assert len(result.artifact_id) >= 8, (
            f"artifact_id must be a non-empty slug string, got {result.artifact_id!r}"
        )

        reindex(
            store,
            result.path,
            result.content,
            result.markdown_content,
            result.frontmatter_dict,
            result.filename_stem,
            chunk_tokens=600,
            chunk_overlap_tokens=80,
        )

        hits, _ = store.search("pytest testing")
        paths = [h.path for h in hits]
        assert any(str(result.path) in p for p in paths), (
            f"memory item not found in FTS5 index after save; indexed paths: {paths}"
        )
    finally:
        store.close()


def test_save_memory_item_enum_kind_serializes_as_plain_string(tmp_path):
    """An enum memory_kind must serialize to a plain YAML string, not a python-object tag.

    Regression guard: pydantic-ai coerces the `kind: MemoryKind` Literal to a
    MemoryKindEnum member, which yaml.dump (dispatching on exact type) would
    otherwise emit as `!!python/object/apply:...MemoryKindEnum`. yaml.safe_load
    refuses to construct that, so load_memory_item parses empty frontmatter and
    the file is silently orphaned (missing 'id'/'created_at') — invisible to
    /memory list, /memory forget, and load_memory_items.
    """
    from co_cli.memory.item import MemoryKindEnum

    memory_dir = tmp_path / "memory"
    result = save_memory_item(
        memory_dir,
        content="staging deploy id is STG_DEPLOY_42",
        memory_kind=MemoryKindEnum.NOTE,
        title="enum kind note",
    )

    raw = result.path.read_text(encoding="utf-8")
    assert "!!python/object" not in raw, (
        f"frontmatter must not contain a python-object tag for memory_kind:\n{raw}"
    )
    assert "memory_kind: note" in raw, f"memory_kind must serialize as plain string:\n{raw}"

    item = load_memory_item(result.path)
    assert item.memory_kind == "note"
    assert isinstance(item.memory_kind, str)


def test_save_memory_item_url_keyed_dedup_updates_existing(tmp_path):
    """save_memory_item with the same source_url must update, not create a duplicate.

    Failure mode: duplicate articles accumulate silently → user gets stale
    content in search results.
    """
    memory_dir = tmp_path / "memory"
    url = "https://example.com/test-page"

    save_memory_item(
        memory_dir,
        content="original content",
        memory_kind="article",
        title="test article",
        source_url=url,
    )

    second = save_memory_item(
        memory_dir,
        content="updated content",
        memory_kind="article",
        title="test article",
        source_url=url,
    )

    assert second.action in ("appended", "merged"), f"expected dedup action, got {second.action!r}"
    md_files = list(memory_dir.glob("*.md"))
    assert len(md_files) == 1, (
        f"expected exactly 1 .md file after URL dedup, found {len(md_files)}: {md_files}"
    )


def test_save_memory_item_jaccard_dedup_skips_near_identical(tmp_path):
    """save_memory_item must skip near-identical content at write time.

    Failure mode: near-duplicate memory items pile up → search returns noisy,
    redundant results.

    Uses a 20-word vocabulary repeated to form the base; adding one word
    gives Jaccard = 20/21 ≈ 0.95, which exceeds the > 0.9 skip threshold.
    The code checks best_score > 0.9 before the superset path, so 'skipped'
    fires regardless of superset status.
    """
    memory_dir = tmp_path / "memory"
    base = (
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet "
        "kilo lima mike november oscar papa quebec romeo sierra tango "
    ) * 3

    save_memory_item(
        memory_dir,
        content=base,
        memory_kind="note",
        title="nato note",
    )

    second = save_memory_item(
        memory_dir,
        content=base + " ultraviolet",
        memory_kind="note",
        title="nato note",
    )

    assert second.action == "skipped", (
        f"expected 'skipped' for near-identical content (Jaccard > 0.9), got {second.action!r}"
    )


def test_mutate_memory_item_append_adds_content_at_end(tmp_path):
    """mutate_memory_item append must add new content to the end of the memory item body.

    Failure mode: append silently no-ops or overwrites → memory modification
    is lost on the next read.
    """
    memory_dir = tmp_path / "memory"
    saved = save_memory_item(
        memory_dir,
        content="initial body",
        memory_kind="note",
        title="my note",
    )

    mutate_result = mutate_memory_item(
        memory_dir,
        filename_stem=saved.filename_stem,
        action="append",
        content="new line",
    )

    assert mutate_result.action == "appended"
    file_text = mutate_result.path.read_text(encoding="utf-8")
    # The file may have a trailing newline after the appended content; strip before checking.
    assert file_text.rstrip("\n").endswith("new line"), (
        f"'new line' not found at end of file. File ends with: {file_text[-100:]!r}"
    )


def test_mutate_memory_item_replace_rejects_non_unique_target(tmp_path):
    """mutate_memory_item replace must raise ValueError when the target appears more than once.

    Failure mode: replace picks wrong occurrence → memory item body silently
    corrupted with no error surfaced to the caller.
    """
    memory_dir = tmp_path / "memory"
    saved = save_memory_item(
        memory_dir,
        content="same line\nsame line\nother content",
        memory_kind="note",
        title="dupe note",
    )

    with pytest.raises(ValueError, match="appears"):
        mutate_memory_item(
            memory_dir,
            filename_stem=saved.filename_stem,
            action="replace",
            target="same line",
            content="replacement",
        )


def test_save_memory_item_url_dedup_uses_index_when_store_provided(tmp_path):
    """Second save_memory_item with same source_url uses O(1) index path when memory_store is set.

    Failure mode: without memory_store, dedup relies on O(n) file scan; with it, a single
    SQL lookup replaces the scan — this test confirms the index path produces action='merged'.
    """
    memory_dir = tmp_path / "memory"
    store = _make_store(tmp_path)
    url = "https://example.com/index-dedup"

    try:
        first = save_memory_item(
            memory_dir,
            content="first version",
            memory_kind="article",
            title="index dedup test",
            source_url=url,
        )
        reindex(
            store,
            first.path,
            first.content,
            first.markdown_content,
            first.frontmatter_dict,
            first.filename_stem,
            chunk_tokens=600,
            chunk_overlap_tokens=80,
        )

        second = save_memory_item(
            memory_dir,
            content="updated version",
            memory_kind="article",
            title="index dedup test",
            source_url=url,
            index_store=store,
        )

        assert second.action == "merged", (
            f"Expected 'merged' when index path is used for dedup, got {second.action!r}"
        )
        md_files = list(memory_dir.glob("*.md"))
        assert len(md_files) == 1, (
            f"Expected 1 .md file after index-path dedup, found {len(md_files)}"
        )
    finally:
        store.close()


def _write_seeded_artifact(path: Path, body: str) -> None:
    frontmatter = {
        "memory_kind": "note",
        "id": "test-123",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    path.write_text(
        f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{body}\n",
        encoding="utf-8",
    )


def test_mutate_memory_item_replace_preserves_frontmatter(tmp_path: Path) -> None:
    """mutate_memory_item action='replace' must update the body without corrupting frontmatter."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    artifact_path = memory_dir / "test-art.md"
    _write_seeded_artifact(artifact_path, "original body content")

    mutate_memory_item(
        memory_dir,
        filename_stem="test-art",
        action="replace",
        content="updated body content",
        target="original body content",
    )

    item = load_memory_item(artifact_path)
    assert item.content.strip() == "updated body content"
    assert item.id == "test-123"
    assert item.created_at == "2026-01-01T00:00:00+00:00"
