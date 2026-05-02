"""Behavioral tests for save_artifact and mutate_artifact write paths.

Exercises: dedup (URL-keyed, Jaccard), straight save, indexing, append, and
replace-uniqueness guard. No LLM — real filesystem + real FTS5 only.
"""

import pytest
from tests._settings import SETTINGS

from co_cli.memory.memory_store import MemoryStore
from co_cli.memory.service import mutate_artifact, reindex, save_artifact

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path, name="test-search.db") -> MemoryStore:
    return MemoryStore(config=SETTINGS, memory_db_path=tmp_path / name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_artifact_straight_save_creates_file_and_indexes(tmp_path):
    """save_artifact (straight path) must write a file AND index it in FTS5.

    Failure mode: file written but not indexed → memory_search misses newly
    created artifacts on the next turn.
    """
    knowledge_dir = tmp_path / "knowledge"
    store = _make_store(tmp_path)
    try:
        result = save_artifact(
            knowledge_dir,
            content="pytest is a testing framework",
            artifact_kind="note",
            title="pytest note",
        )

        assert result.action == "saved"
        assert result.path.exists(), "artifact file was not written to disk"
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
            chunk_size=600,
            chunk_overlap=80,
        )

        hits = store.search("pytest testing")
        paths = [h.path for h in hits]
        assert any(str(result.path) in p for p in paths), (
            f"artifact not found in FTS5 index after save; indexed paths: {paths}"
        )
    finally:
        store.close()


def test_save_artifact_url_keyed_dedup_updates_existing(tmp_path):
    """save_artifact with the same source_url must update, not create a duplicate.

    Failure mode: duplicate articles accumulate silently → user gets stale
    content in search results.
    """
    knowledge_dir = tmp_path / "knowledge"
    url = "https://example.com/test-page"

    save_artifact(
        knowledge_dir,
        content="original content",
        artifact_kind="article",
        title="test article",
        source_url=url,
    )

    second = save_artifact(
        knowledge_dir,
        content="updated content",
        artifact_kind="article",
        title="test article",
        source_url=url,
    )

    assert second.action in ("appended", "merged"), f"expected dedup action, got {second.action!r}"
    md_files = list(knowledge_dir.glob("*.md"))
    assert len(md_files) == 1, (
        f"expected exactly 1 .md file after URL dedup, found {len(md_files)}: {md_files}"
    )


def test_save_artifact_jaccard_dedup_skips_near_identical(tmp_path):
    """save_artifact with consolidation_enabled must skip near-identical content.

    Failure mode: near-duplicate artifacts pile up → search returns noisy,
    redundant results.

    Uses a 20-word vocabulary repeated to form the base; adding one word
    gives Jaccard = 20/21 ≈ 0.95, which exceeds the > 0.9 skip threshold.
    The code checks best_score > 0.9 before the superset path, so 'skipped'
    fires regardless of superset status.
    """
    knowledge_dir = tmp_path / "knowledge"
    # 20 distinct meaningful tokens (no stopwords, all len > 1).
    base = (
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet "
        "kilo lima mike november oscar papa quebec romeo sierra tango "
    ) * 3

    save_artifact(
        knowledge_dir,
        content=base,
        artifact_kind="note",
        title="nato note",
        consolidation_enabled=True,
    )

    # Adding one word: Jaccard = 20/21 ≈ 0.95 > 0.9 → triggers 'skipped'.
    second = save_artifact(
        knowledge_dir,
        content=base + " ultraviolet",
        artifact_kind="note",
        title="nato note",
        consolidation_enabled=True,
    )

    assert second.action == "skipped", (
        f"expected 'skipped' for near-identical content (Jaccard > 0.9), got {second.action!r}"
    )


def test_mutate_artifact_append_adds_content_at_end(tmp_path):
    """mutate_artifact append must add new content to the end of the artifact body.

    Failure mode: append silently no-ops or overwrites → memory modification
    is lost on the next read.
    """
    knowledge_dir = tmp_path / "knowledge"
    saved = save_artifact(
        knowledge_dir,
        content="initial body",
        artifact_kind="note",
        title="my note",
    )

    mutate_result = mutate_artifact(
        knowledge_dir,
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


def test_mutate_artifact_replace_rejects_non_unique_target(tmp_path):
    """mutate_artifact replace must raise ValueError when the target appears more than once.

    Failure mode: replace picks wrong occurrence → artifact body silently
    corrupted with no error surfaced to the caller.
    """
    knowledge_dir = tmp_path / "knowledge"
    saved = save_artifact(
        knowledge_dir,
        content="same line\nsame line\nother content",
        artifact_kind="note",
        title="dupe note",
    )

    with pytest.raises(ValueError, match="appears"):
        mutate_artifact(
            knowledge_dir,
            filename_stem=saved.filename_stem,
            action="replace",
            target="same line",
            content="replacement",
        )


def test_save_artifact_url_dedup_uses_index_when_store_provided(tmp_path):
    """Second save_artifact with same source_url uses O(1) index path when memory_store is set.

    Failure mode: without memory_store, dedup relies on O(n) file scan; with it, a single
    SQL lookup replaces the scan — this test confirms the index path produces action='merged'.
    """
    knowledge_dir = tmp_path / "knowledge"
    store = _make_store(tmp_path)
    url = "https://example.com/index-dedup"

    try:
        first = save_artifact(
            knowledge_dir,
            content="first version",
            artifact_kind="article",
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
            chunk_size=600,
            chunk_overlap=80,
        )

        second = save_artifact(
            knowledge_dir,
            content="updated version",
            artifact_kind="article",
            title="index dedup test",
            source_url=url,
            memory_store=store,
        )

        assert second.action == "merged", (
            f"Expected 'merged' when index path is used for dedup, got {second.action!r}"
        )
        md_files = list(knowledge_dir.glob("*.md"))
        assert len(md_files) == 1, (
            f"Expected 1 .md file after index-path dedup, found {len(md_files)}"
        )
    finally:
        store.close()


def test_find_article_by_url_file_scan_fallback(tmp_path):
    """_find_article_by_url with memory_store=None falls back to file scan.

    Failure mode: if the file-scan fallback is removed, saves without a warm index
    (e.g. first-run bootstrap) would create duplicate articles.
    """
    from co_cli.memory.service import _find_article_by_url

    knowledge_dir = tmp_path / "knowledge"
    url = "https://example.com/fallback-scan"

    saved = save_artifact(
        knowledge_dir,
        content="content for fallback test",
        artifact_kind="article",
        title="fallback test",
        source_url=url,
    )

    result = _find_article_by_url(knowledge_dir, url, memory_store=None)

    assert result is not None, "file-scan fallback returned None for a known URL"
    assert result == saved.path, f"Expected {saved.path}, got {result}"
