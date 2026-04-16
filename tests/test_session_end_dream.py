"""Tests for the session-end dream trigger in main._drain_and_cleanup (TASK-5.8)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from tests._settings import make_settings

from co_cli.deps import CoDeps
from co_cli.knowledge._artifact import ArtifactKindEnum, KnowledgeArtifact, SourceTypeEnum
from co_cli.knowledge._dream import dream_state_path, load_dream_state
from co_cli.knowledge._frontmatter import render_knowledge_file
from co_cli.knowledge._store import KnowledgeStore
from co_cli.main import _maybe_run_dream_cycle
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(tmp_path: Path, *, consolidation_enabled: bool) -> tuple[CoDeps, KnowledgeStore]:
    knowledge_dir = tmp_path / "knowledge"
    sessions_dir = tmp_path / "sessions"
    knowledge_dir.mkdir()
    sessions_dir.mkdir()
    config = make_settings()
    config = config.model_copy(deep=True)
    config.knowledge.consolidation_enabled = consolidation_enabled
    config.knowledge.consolidation_trigger = "session_end"
    store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        knowledge_dir=knowledge_dir,
        sessions_dir=sessions_dir,
        knowledge_store=store,
    )
    return deps, store


def _seed_stale_artifact(knowledge_dir: Path) -> Path:
    artifact_id = str(uuid4())
    path = knowledge_dir / f"stale-{artifact_id[:8]}.md"
    artifact = KnowledgeArtifact(
        id=artifact_id,
        path=path,
        artifact_kind=ArtifactKindEnum.NOTE.value,
        title="stale",
        content="a stale note nobody has recalled in a year",
        created=(datetime.now(UTC) - timedelta(days=365)).isoformat(),
        source_type=SourceTypeEnum.DETECTED.value,
    )
    path.write_text(render_knowledge_file(artifact), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_feature_gate_off_skips_dream_cycle(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, consolidation_enabled=False)
    try:
        _seed_stale_artifact(deps.knowledge_dir)

        await _maybe_run_dream_cycle(deps)

        assert not dream_state_path(deps.knowledge_dir).exists()
        assert not (deps.knowledge_dir / "_archive").exists()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_feature_gate_on_runs_dream_cycle(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, consolidation_enabled=True)
    try:
        stale_path = _seed_stale_artifact(deps.knowledge_dir)
        store.sync_dir("knowledge", deps.knowledge_dir, glob="*.md")

        await _maybe_run_dream_cycle(deps)

        assert dream_state_path(deps.knowledge_dir).exists()
        state = load_dream_state(deps.knowledge_dir)
        assert state.last_dream_at is not None
        assert state.stats.total_cycles == 1
        assert state.stats.total_decayed == 1
        assert not stale_path.exists()
        archive_dir = deps.knowledge_dir / "_archive"
        assert archive_dir.is_dir()
        assert stale_path.name in {p.name for p in archive_dir.glob("*.md")}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_non_session_end_trigger_skips_dream_cycle(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, consolidation_enabled=True)
    try:
        deps.config.knowledge.consolidation_trigger = "manual"

        await _maybe_run_dream_cycle(deps)

        assert not dream_state_path(deps.knowledge_dir).exists()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_dream_cycle_emits_summary_log_when_changes_occur(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    deps, store = _make_deps(tmp_path, consolidation_enabled=True)
    try:
        _seed_stale_artifact(deps.knowledge_dir)
        store.sync_dir("knowledge", deps.knowledge_dir, glob="*.md")

        with caplog.at_level(logging.INFO, logger="co_cli.main"):
            await _maybe_run_dream_cycle(deps)

        messages = [record.getMessage() for record in caplog.records]
        assert any("Dream cycle:" in msg and "archived" in msg for msg in messages)
    finally:
        store.close()
