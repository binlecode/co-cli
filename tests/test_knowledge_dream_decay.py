"""Tests for the dream-cycle decay sweep (TASK-5.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from tests._settings import make_settings

from co_cli.deps import CoDeps
from co_cli.knowledge._artifact import (
    ArtifactKindEnum,
    KnowledgeArtifact,
    PinModeEnum,
    SourceTypeEnum,
)
from co_cli.knowledge._dream import _MAX_DECAY_PER_CYCLE, _decay_sweep
from co_cli.knowledge._frontmatter import render_knowledge_file
from co_cli.knowledge._store import KnowledgeStore
from co_cli.tools.shell_backend import ShellBackend


def _write_aged_artifact(
    knowledge_dir: Path,
    *,
    created_days_ago: int,
    last_recalled_days_ago: int | None,
    pin_mode: str = PinModeEnum.NONE.value,
    decay_protected: bool = False,
    content: str = "forgotten artifact body",
) -> Path:
    """Write an artifact whose ``created`` is back-dated so it can be swept."""
    now = datetime.now(UTC)
    created = (now - timedelta(days=created_days_ago)).isoformat()
    last_recalled = (
        (now - timedelta(days=last_recalled_days_ago)).isoformat()
        if last_recalled_days_ago is not None
        else None
    )
    artifact_id = str(uuid4())
    path = knowledge_dir / f"decay-{artifact_id[:8]}.md"
    artifact = KnowledgeArtifact(
        id=artifact_id,
        path=path,
        artifact_kind=ArtifactKindEnum.PREFERENCE.value,
        title="aged entry",
        content=content,
        created=created,
        last_recalled=last_recalled,
        pin_mode=pin_mode,
        decay_protected=decay_protected,
        source_type=SourceTypeEnum.DETECTED.value,
    )
    path.write_text(render_knowledge_file(artifact), encoding="utf-8")
    return path


def _make_deps(tmp_path: Path) -> tuple[CoDeps, KnowledgeStore]:
    knowledge_dir = tmp_path / "knowledge"
    sessions_dir = tmp_path / "sessions"
    knowledge_dir.mkdir()
    sessions_dir.mkdir()
    config = make_settings()
    store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        knowledge_dir=knowledge_dir,
        sessions_dir=sessions_dir,
        knowledge_store=store,
    )
    return deps, store


def test_decay_sweep_archives_old_unrecalled_artifacts(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path)
    try:
        stale_path = _write_aged_artifact(
            deps.knowledge_dir,
            created_days_ago=365,
            last_recalled_days_ago=None,
            content="stalezyx artifact body",
        )
        fresh_path = _write_aged_artifact(
            deps.knowledge_dir,
            created_days_ago=3,
            last_recalled_days_ago=None,
            content="freshzyx artifact body",
        )
        store.sync_dir("knowledge", deps.knowledge_dir, glob="*.md")
        assert store.search("stalezyx", source="knowledge", limit=5)
        assert store.search("freshzyx", source="knowledge", limit=5)

        archived = _decay_sweep(deps)

        assert archived == 1
        assert not stale_path.exists()
        assert fresh_path.exists()
        archive_dir = deps.knowledge_dir / "_archive"
        assert archive_dir.is_dir()
        archived_names = {p.name for p in archive_dir.glob("*.md")}
        assert stale_path.name in archived_names
        # FTS no longer surfaces the archived stale artifact, but fresh remains
        assert not store.search("stalezyx", source="knowledge", limit=5)
        assert store.search("freshzyx", source="knowledge", limit=5)
    finally:
        store.close()


def test_decay_sweep_skips_pinned_and_decay_protected(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path)
    try:
        _write_aged_artifact(
            deps.knowledge_dir,
            created_days_ago=365,
            last_recalled_days_ago=None,
            pin_mode=PinModeEnum.STANDING.value,
        )
        _write_aged_artifact(
            deps.knowledge_dir,
            created_days_ago=365,
            last_recalled_days_ago=None,
            decay_protected=True,
        )

        archived = _decay_sweep(deps)

        assert archived == 0
        assert len(list(deps.knowledge_dir.glob("*.md"))) == 2
        assert not (deps.knowledge_dir / "_archive").exists()
    finally:
        store.close()


def test_decay_sweep_returns_zero_when_no_artifacts_present(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path)
    try:
        archived = _decay_sweep(deps)
        assert archived == 0
        assert not (deps.knowledge_dir / "_archive").exists()
    finally:
        store.close()


def test_decay_sweep_caps_archival_at_per_cycle_limit(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path)
    try:
        for _ in range(_MAX_DECAY_PER_CYCLE + 5):
            _write_aged_artifact(
                deps.knowledge_dir,
                created_days_ago=365,
                last_recalled_days_ago=None,
            )
        store.sync_dir("knowledge", deps.knowledge_dir, glob="*.md")

        archived = _decay_sweep(deps)

        assert archived == _MAX_DECAY_PER_CYCLE
        remaining = list(deps.knowledge_dir.glob("*.md"))
        assert len(remaining) == 5
    finally:
        store.close()


@pytest.mark.asyncio
async def test_decay_sweep_leaves_recently_recalled_artifacts(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path)
    try:
        _write_aged_artifact(
            deps.knowledge_dir,
            created_days_ago=365,
            last_recalled_days_ago=5,
        )

        archived = _decay_sweep(deps)

        assert archived == 0
    finally:
        store.close()
