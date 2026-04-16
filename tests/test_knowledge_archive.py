"""Tests for archive/restore of knowledge artifacts."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from tests._settings import make_settings

from co_cli.knowledge._archive import archive_artifacts, restore_artifact
from co_cli.knowledge._artifact import (
    ArtifactKindEnum,
    KnowledgeArtifact,
    load_knowledge_artifact,
)
from co_cli.knowledge._store import KnowledgeStore


def _write_artifact(
    knowledge_dir: Path,
    artifact_id: str,
    slug: str,
    content: str,
    artifact_kind: str = ArtifactKindEnum.PREFERENCE.value,
) -> Path:
    """Write a canonical knowledge artifact .md and return its path."""
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}.md"
    frontmatter = {
        "id": artifact_id,
        "kind": "knowledge",
        "artifact_kind": artifact_kind,
        "created": "2026-04-16T10:00:00Z",
        "title": slug,
    }
    raw = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{content}\n"
    path = knowledge_dir / filename
    path.write_text(raw, encoding="utf-8")
    return path


def _make_store(tmp_path: Path) -> KnowledgeStore:
    """Build a real KnowledgeStore with a scoped DB under tmp_path."""
    return KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")


def test_archive_artifacts_moves_file_and_removes_from_fts(tmp_path: Path) -> None:
    """archive_artifacts relocates the file into _archive/ and strips it from FTS."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(
        knowledge_dir, "a-1", "prefers-pytest", "User prefers pytest zzzunique over unittest"
    )

    store = _make_store(tmp_path)
    try:
        store.sync_dir("knowledge", knowledge_dir)
        before = store.search("zzzunique", source="knowledge", limit=5)
        assert any("zzzunique" in (r.snippet or "") for r in before), (
            "Sanity: content must be searchable before archive"
        )

        artifact = load_knowledge_artifact(path)
        count = archive_artifacts([artifact], knowledge_dir, store)
        assert count == 1
        assert not path.exists(), "Original file must be moved out of knowledge_dir"
        assert (knowledge_dir / "_archive" / "prefers-pytest.md").exists(), (
            "Archived file must land in _archive/"
        )

        after = store.search("zzzunique", source="knowledge", limit=5)
        assert after == [], "FTS must no longer return the archived content"
    finally:
        store.close()


def test_restore_artifact_moves_back_and_reindexes(tmp_path: Path) -> None:
    """restore_artifact puts the file back in knowledge_dir and re-indexes it."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(
        knowledge_dir,
        "r-1",
        "restore-me",
        "Distinctive restorekeyword phrase used only once in the corpus",
    )

    store = _make_store(tmp_path)
    try:
        store.sync_dir("knowledge", knowledge_dir)
        artifact = load_knowledge_artifact(path)
        archive_artifacts([artifact], knowledge_dir, store)
        assert store.search("restorekeyword", source="knowledge", limit=5) == []

        restored = restore_artifact("restore-me", knowledge_dir, store)
        assert restored is True
        assert (knowledge_dir / "restore-me.md").exists(), (
            "Restored file must be back at the top level of knowledge_dir"
        )
        assert not (knowledge_dir / "_archive" / "restore-me.md").exists(), (
            "Archive copy must be gone after restore"
        )

        after = store.search("restorekeyword", source="knowledge", limit=5)
        assert any("restorekeyword" in (r.snippet or "") for r in after), (
            "FTS must return the restored content again after re-index"
        )
    finally:
        store.close()


def test_archive_artifacts_skips_missing_source_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Entries whose file does not exist are logged and excluded from the count."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    real_path = _write_artifact(
        knowledge_dir, "ok-1", "real-one", "Content that will actually be archived"
    )
    real_artifact = load_knowledge_artifact(real_path)

    ghost_artifact = KnowledgeArtifact(
        id="ghost-1",
        path=knowledge_dir / "does-not-exist.md",
        artifact_kind=ArtifactKindEnum.PREFERENCE.value,
        title="ghost",
        content="ghost content",
        created="2026-04-16T10:00:00Z",
    )

    store = _make_store(tmp_path)
    try:
        store.sync_dir("knowledge", knowledge_dir)
        with caplog.at_level(logging.WARNING, logger="co_cli.knowledge._archive"):
            count = archive_artifacts([real_artifact, ghost_artifact], knowledge_dir, store)

        assert count == 1, "Missing files must not be counted as archived"
        assert (knowledge_dir / "_archive" / "real-one.md").exists()
        assert any("source missing" in rec.getMessage() for rec in caplog.records), (
            "Missing source must emit a warning"
        )
    finally:
        store.close()


def test_restore_artifact_returns_false_for_unknown_slug(tmp_path: Path) -> None:
    """An unknown slug resolves to zero matches and returns False."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "_archive").mkdir()

    store = _make_store(tmp_path)
    try:
        result = restore_artifact("nonexistent-slug", knowledge_dir, store)
        assert result is False
    finally:
        store.close()


def test_restore_artifact_returns_false_on_ambiguous_slug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Multiple archive matches for a slug prefix must not auto-restore."""
    knowledge_dir = tmp_path / "knowledge"
    path_a = _write_artifact(knowledge_dir, "amb-a", "shared-prefix-alpha", "alpha body")
    path_b = _write_artifact(knowledge_dir, "amb-b", "shared-prefix-beta", "beta body")

    store = _make_store(tmp_path)
    try:
        store.sync_dir("knowledge", knowledge_dir)
        archive_artifacts(
            [load_knowledge_artifact(path_a), load_knowledge_artifact(path_b)],
            knowledge_dir,
            store,
        )

        with caplog.at_level(logging.WARNING, logger="co_cli.knowledge._archive"):
            result = restore_artifact("shared-prefix", knowledge_dir, store)

        assert result is False
        assert (knowledge_dir / "_archive" / "shared-prefix-alpha.md").exists(), (
            "Both archived files must stay put on ambiguous restore"
        )
        assert (knowledge_dir / "_archive" / "shared-prefix-beta.md").exists()
        assert any("ambiguous slug" in rec.getMessage() for rec in caplog.records), (
            "Ambiguous match must emit a warning"
        )
    finally:
        store.close()


def test_archive_collision_gets_numeric_suffix_and_preserves_prior_archive(
    tmp_path: Path,
) -> None:
    """Archiving two files with the same basename must not clobber the first."""
    knowledge_dir = tmp_path / "knowledge"
    archive_dir = knowledge_dir / "_archive"
    knowledge_dir.mkdir()
    archive_dir.mkdir()
    prior_archived = archive_dir / "duplicate-slug.md"
    prior_archived.write_text("earlier archived content", encoding="utf-8")

    new_path = _write_artifact(
        knowledge_dir, "new-1", "duplicate-slug", "newer body scheduled for archive"
    )

    store = _make_store(tmp_path)
    try:
        count = archive_artifacts([load_knowledge_artifact(new_path)], knowledge_dir, store)

        assert count == 1
        assert prior_archived.exists(), "Prior archive must not be overwritten"
        assert prior_archived.read_text(encoding="utf-8") == "earlier archived content"
        assert (archive_dir / "duplicate-slug-1.md").exists(), (
            "Collision must land under a numerically suffixed filename"
        )
        assert not new_path.exists(), "Source must still be moved out of knowledge_dir"
    finally:
        store.close()


def test_restore_collision_gets_numeric_suffix_and_preserves_active_file(
    tmp_path: Path,
) -> None:
    """Restoring into a directory with an existing same-name file must not clobber it."""
    knowledge_dir = tmp_path / "knowledge"
    archive_dir = knowledge_dir / "_archive"
    knowledge_dir.mkdir()
    archive_dir.mkdir()
    active_path = knowledge_dir / "collide-slug.md"
    active_path.write_text("active body that must not be overwritten", encoding="utf-8")

    archived_path = archive_dir / "collide-slug.md"
    archived_path.write_text(
        "---\nid: r-coll\nkind: knowledge\nartifact_kind: preference\n"
        "created: 2026-04-16T10:00:00Z\ntitle: collide-slug\n---\n\n"
        "archived body ready for restore\n",
        encoding="utf-8",
    )

    store = _make_store(tmp_path)
    try:
        result = restore_artifact("collide-slug", knowledge_dir, store)

        assert result is True
        assert active_path.exists()
        assert active_path.read_text(encoding="utf-8") == (
            "active body that must not be overwritten"
        ), "Active file must remain untouched"
        assert (knowledge_dir / "collide-slug-1.md").exists(), (
            "Restored file must land under a numerically suffixed filename"
        )
        assert not archived_path.exists(), "Archive copy must be gone after restore"
    finally:
        store.close()
