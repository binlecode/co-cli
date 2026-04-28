"""Tests for co_cli/knowledge/service.py — save_artifact and mutate_artifact."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from co_cli.memory.service import MutateResult, mutate_artifact, save_artifact
from co_cli.tools.resource_lock import ResourceBusyError, ResourceLockStore

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_artifact(
    knowledge_dir: Path,
    artifact_id: str,
    content: str,
    *,
    artifact_kind: str = "preference",
    source_ref: str | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Write a minimal canonical knowledge artifact for testing."""
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", content[:20].lower()).strip("-")
    filename = f"{slug}-{artifact_id[:8]}.md"
    fm: dict[str, Any] = {
        "id": artifact_id,
        "kind": "knowledge",
        "artifact_kind": artifact_kind,
        "created": datetime.now(UTC).isoformat(),
        "tags": tags or [],
    }
    if source_ref:
        fm["source_ref"] = source_ref
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = knowledge_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


def _active_artifact_count(knowledge_dir: Path) -> int:
    return sum(1 for p in knowledge_dir.glob("*.md") if p.is_file())


# ---------------------------------------------------------------------------
# save_artifact — URL-keyed dedup path
# ---------------------------------------------------------------------------


def test_save_artifact_url_no_match_creates_new_file(tmp_path: Path) -> None:
    """save_artifact with source_url and no existing match writes a new file."""
    knowledge_dir = tmp_path / "knowledge"
    result = save_artifact(
        knowledge_dir,
        content="Python asyncio patterns and pitfalls",
        artifact_kind="article",
        title="Asyncio Guide",
        source_url="https://example.com/asyncio",
    )
    assert result.action == "saved"
    assert result.path.exists()
    assert result.artifact_id
    assert result.fm_dict["source_ref"] == "https://example.com/asyncio"
    assert _active_artifact_count(knowledge_dir) == 1


def test_save_artifact_url_match_consolidates(tmp_path: Path) -> None:
    """save_artifact with source_url matching an existing article merges instead of creating."""
    knowledge_dir = tmp_path / "knowledge"
    _write_artifact(
        knowledge_dir,
        "art-001",
        "Old asyncio content",
        artifact_kind="article",
        source_ref="https://example.com/asyncio",
    )
    before_count = _active_artifact_count(knowledge_dir)

    result = save_artifact(
        knowledge_dir,
        content="Updated asyncio content with new sections",
        artifact_kind="article",
        title="Asyncio Guide Updated",
        source_url="https://example.com/asyncio",
        tags=["python", "async"],
    )

    assert result.action == "merged"
    assert _active_artifact_count(knowledge_dir) == before_count, (
        "consolidation must not create a new file"
    )
    updated_text = result.path.read_text(encoding="utf-8")
    assert "Updated asyncio content" in updated_text


def test_save_artifact_url_sets_decay_protected_true(tmp_path: Path) -> None:
    """save_artifact with source_url always writes decay_protected: true."""
    knowledge_dir = tmp_path / "knowledge"
    result = save_artifact(
        knowledge_dir,
        content="Some article content",
        artifact_kind="article",
        source_url="https://example.com/article",
        decay_protected=False,  # caller explicitly passes False — must be overridden
    )
    file_text = result.path.read_text(encoding="utf-8")
    assert "decay_protected: true" in file_text


# ---------------------------------------------------------------------------
# save_artifact — Jaccard dedup path
# ---------------------------------------------------------------------------


def test_save_artifact_jaccard_near_identical_skips_no_file_written(tmp_path: Path) -> None:
    """Jaccard > 0.9 returns action='skipped' and no new file is written."""
    knowledge_dir = tmp_path / "knowledge"
    _write_artifact(
        knowledge_dir, "pref-001", "user prefers pytest over unittest", artifact_kind="preference"
    )
    before_count = _active_artifact_count(knowledge_dir)

    result = save_artifact(
        knowledge_dir,
        content="user prefers pytest over unittest",
        artifact_kind="preference",
        consolidation_enabled=True,
        consolidation_similarity_threshold=0.5,
    )

    assert result.action == "skipped"
    assert _active_artifact_count(knowledge_dir) == before_count, (
        "skipped must not write a new file"
    )


def test_save_artifact_jaccard_superset_merges(tmp_path: Path) -> None:
    """New content whose tokens are a strict superset of existing triggers merge."""
    knowledge_dir = tmp_path / "knowledge"
    existing = _write_artifact(
        knowledge_dir, "pref-001", "user prefers pytest", artifact_kind="preference"
    )
    before_count = _active_artifact_count(knowledge_dir)

    result = save_artifact(
        knowledge_dir,
        content="user prefers pytest over unittest for testing",
        artifact_kind="preference",
        consolidation_enabled=True,
        consolidation_similarity_threshold=0.3,
    )

    assert result.action == "merged"
    assert _active_artifact_count(knowledge_dir) == before_count, (
        "merge must not create a new file"
    )
    updated_body = existing.read_text(encoding="utf-8")
    assert "over unittest for testing" in updated_body


def test_save_artifact_jaccard_overlap_appends(tmp_path: Path) -> None:
    """Partially-overlapping content (not superset) appends to existing artifact."""
    knowledge_dir = tmp_path / "knowledge"
    existing = _write_artifact(
        knowledge_dir, "pref-001", "user prefers pytest ruff", artifact_kind="preference"
    )
    before_count = _active_artifact_count(knowledge_dir)

    result = save_artifact(
        knowledge_dir,
        content="user prefers pytest mypy checks",
        artifact_kind="preference",
        consolidation_enabled=True,
        consolidation_similarity_threshold=0.2,
    )

    assert result.action == "appended"
    assert _active_artifact_count(knowledge_dir) == before_count, (
        "append must not create a new file"
    )
    updated_body = existing.read_text(encoding="utf-8")
    assert "ruff" in updated_body
    assert "mypy checks" in updated_body


# ---------------------------------------------------------------------------
# save_artifact — straight create path
# ---------------------------------------------------------------------------


def test_save_artifact_no_consolidation_creates_new_file(tmp_path: Path) -> None:
    """consolidation_enabled=False always writes a new artifact."""
    knowledge_dir = tmp_path / "knowledge"
    _write_artifact(
        knowledge_dir, "pref-001", "user prefers pytest over unittest", artifact_kind="preference"
    )

    result = save_artifact(
        knowledge_dir,
        content="user prefers pytest over unittest",
        artifact_kind="preference",
        consolidation_enabled=False,
    )

    assert result.action == "saved"
    assert _active_artifact_count(knowledge_dir) == 2, "disabled dedup must allow duplicate writes"


# ---------------------------------------------------------------------------
# mutate_artifact — append round-trip
# ---------------------------------------------------------------------------


def test_mutate_artifact_append_round_trip(tmp_path: Path) -> None:
    """mutate_artifact action='append' adds content at end of body."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(
        knowledge_dir, "pref-001", "User prefers pytest", artifact_kind="preference"
    )
    slug = path.stem

    result = mutate_artifact(
        knowledge_dir, slug=slug, action="append", content="Also uses coverage reports."
    )

    assert isinstance(result, MutateResult)
    assert result.action == "appended"
    assert result.slug == slug
    updated_text = path.read_text(encoding="utf-8")
    assert updated_text.rstrip("\n").endswith("Also uses coverage reports.")
    assert "User prefers pytest" in updated_text


# ---------------------------------------------------------------------------
# mutate_artifact — replace round-trip
# ---------------------------------------------------------------------------


def test_mutate_artifact_replace_round_trip(tmp_path: Path) -> None:
    """mutate_artifact action='replace' substitutes an exact passage."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(
        knowledge_dir, "pref-001", "User prefers pytest over unittest", artifact_kind="preference"
    )
    slug = path.stem

    result = mutate_artifact(
        knowledge_dir,
        slug=slug,
        action="replace",
        content="pytest over all others",
        target="pytest over unittest",
    )

    assert isinstance(result, MutateResult)
    assert result.action == "replaced"
    updated_text = path.read_text(encoding="utf-8")
    assert "pytest over all others" in updated_text
    assert "pytest over unittest" not in updated_text


# ---------------------------------------------------------------------------
# mutate_artifact — guard: zero target occurrences
# ---------------------------------------------------------------------------


def test_mutate_artifact_replace_zero_matches_raises(tmp_path: Path) -> None:
    """replace with target not in body raises ValueError."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(
        knowledge_dir, "pref-001", "User prefers pytest", artifact_kind="preference"
    )
    slug = path.stem

    with pytest.raises(ValueError, match="not found"):
        mutate_artifact(
            knowledge_dir, slug=slug, action="replace", content="new", target="unittest"
        )


# ---------------------------------------------------------------------------
# mutate_artifact — guard: multiple target occurrences
# ---------------------------------------------------------------------------


def test_mutate_artifact_replace_multiple_matches_raises(tmp_path: Path) -> None:
    """replace with target appearing more than once raises ValueError."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(
        knowledge_dir,
        "pref-001",
        "User uses pytest. Also uses pytest.",
        artifact_kind="preference",
    )
    slug = path.stem

    with pytest.raises(ValueError, match="2 times"):
        mutate_artifact(
            knowledge_dir, slug=slug, action="replace", content="mocha", target="pytest"
        )


# ---------------------------------------------------------------------------
# mutate_artifact — guard: empty target for replace
# ---------------------------------------------------------------------------


def test_mutate_artifact_replace_empty_target_raises(tmp_path: Path) -> None:
    """replace with empty target raises ValueError."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(knowledge_dir, "pref-001", "Some content", artifact_kind="preference")
    slug = path.stem

    with pytest.raises(ValueError, match="non-empty target"):
        mutate_artifact(
            knowledge_dir, slug=slug, action="replace", content="new content", target=""
        )


# ---------------------------------------------------------------------------
# mutate_artifact — guard: line-number prefix rejection
# ---------------------------------------------------------------------------


def test_mutate_artifact_line_prefix_in_content_raises(tmp_path: Path) -> None:
    """content containing Read-tool line-number prefix raises ValueError."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(
        knowledge_dir, "pref-001", "User prefers pytest", artifact_kind="preference"
    )
    slug = path.stem

    with pytest.raises(ValueError, match="line-number prefixes"):
        mutate_artifact(
            knowledge_dir, slug=slug, action="append", content="1→ User prefers pytest"
        )


def test_mutate_artifact_line_prefix_in_target_raises(tmp_path: Path) -> None:
    """target containing Read-tool line-number prefix raises ValueError."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(
        knowledge_dir, "pref-001", "User prefers pytest", artifact_kind="preference"
    )
    slug = path.stem

    with pytest.raises(ValueError, match="line-number prefixes"):
        mutate_artifact(
            knowledge_dir,
            slug=slug,
            action="replace",
            content="new",
            target="1→ User prefers pytest",
        )


# ---------------------------------------------------------------------------
# Lock contention — simulates tool wrapper's try_acquire pattern
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_contention_raises_resource_busy_error(tmp_path: Path) -> None:
    """Two async tasks racing on the same slug — the second gets ResourceBusyError."""
    knowledge_dir = tmp_path / "knowledge"
    path = _write_artifact(
        knowledge_dir, "pref-001", "initial content", artifact_kind="preference"
    )
    slug = path.stem
    lock_store = ResourceLockStore()

    acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock() -> None:
        async with lock_store.try_acquire(slug):
            acquired.set()
            await release.wait()
            mutate_artifact(knowledge_dir, slug=slug, action="append", content="from holder")

    task = asyncio.create_task(hold_lock())
    await acquired.wait()

    with pytest.raises(ResourceBusyError):
        async with lock_store.try_acquire(slug):
            mutate_artifact(knowledge_dir, slug=slug, action="append", content="from racer")

    release.set()
    await task
