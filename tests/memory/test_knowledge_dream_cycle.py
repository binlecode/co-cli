"""Tests for the dream-cycle orchestrator (TASK-5.7)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._settings import make_settings
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.config.core import Settings
from co_cli.deps import CoDeps
from co_cli.llm.factory import build_model
from co_cli.main import _maybe_run_dream_cycle
from co_cli.memory.artifact import (
    ArtifactKindEnum,
    KnowledgeArtifact,
    SourceTypeEnum,
)
from co_cli.memory.dream import (
    DreamResult,
    dream_state_path,
    load_dream_state,
    run_dream_cycle,
)
from co_cli.memory.frontmatter import render_knowledge_file
from co_cli.memory.knowledge_store import KnowledgeStore
from co_cli.memory.transcript import append_messages
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(
    tmp_path: Path, *, with_model: bool, config: Settings | None = None
) -> tuple[CoDeps, KnowledgeStore]:
    knowledge_dir = tmp_path / "knowledge"
    sessions_dir = tmp_path / "sessions"
    knowledge_dir.mkdir()
    sessions_dir.mkdir()
    config = config or make_settings()
    store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")
    model = build_model(config.llm) if with_model else None
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        knowledge_dir=knowledge_dir,
        sessions_dir=sessions_dir,
        knowledge_store=store,
        model=model,
    )
    return deps, store


def _seed_similar_cluster(knowledge_dir: Path) -> list[Path]:
    common = (
        "pytest preferred testing framework default pytest tests never unittest "
        "prefer real dependencies"
    )
    suffixes = ["always", "everywhere", "strictly"]
    paths: list[Path] = []
    for suffix in suffixes:
        artifact_id = str(uuid4())
        path = knowledge_dir / f"pref-{artifact_id[:8]}.md"
        artifact = KnowledgeArtifact(
            id=artifact_id,
            path=path,
            artifact_kind=ArtifactKindEnum.PREFERENCE.value,
            title="Testing framework",
            content=f"{common} {suffix}",
            created=datetime.now(UTC).isoformat(),
            source_type=SourceTypeEnum.DETECTED.value,
            tags=["testing"],
        )
        path.write_text(render_knowledge_file(artifact), encoding="utf-8")
        paths.append(path)
    return paths


def _seed_decay_candidate(knowledge_dir: Path) -> Path:
    artifact_id = str(uuid4())
    path = knowledge_dir / f"decay-{artifact_id[:8]}.md"
    artifact = KnowledgeArtifact(
        id=artifact_id,
        path=path,
        artifact_kind=ArtifactKindEnum.NOTE.value,
        title="stale",
        content="an ancient note that nobody has recalled in a year",
        created=(datetime.now(UTC) - timedelta(days=365)).isoformat(),
        source_type=SourceTypeEnum.DETECTED.value,
    )
    path.write_text(render_knowledge_file(artifact), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_run_dream_cycle_returns_zero_result_on_empty_system(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        result = await run_dream_cycle(deps)

        assert isinstance(result, DreamResult)
        assert result.extracted == 0
        assert result.merged == 0
        assert result.decayed == 0
        assert result.any_changes is False

        state = load_dream_state(deps.knowledge_dir)
        assert state.last_dream_at is not None
        assert state.stats.total_cycles == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_dry_run_counts_without_writing(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        cluster_paths = _seed_similar_cluster(deps.knowledge_dir)
        decay_path = _seed_decay_candidate(deps.knowledge_dir)

        result = await run_dream_cycle(deps, dry_run=True)

        assert result.merged == 1
        assert result.decayed == 1
        assert result.extracted == 0

        for path in cluster_paths:
            assert path.exists()
        assert decay_path.exists()
        assert not (deps.knowledge_dir / "_archive").exists()
        assert not dream_state_path(deps.knowledge_dir).exists()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_state_persists_across_no_op_cycles(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        await run_dream_cycle(deps)

        reloaded = load_dream_state(deps.knowledge_dir)
        assert reloaded.last_dream_at is not None
        assert reloaded.stats.total_cycles == 1

        await run_dream_cycle(deps)
        reloaded_again = load_dream_state(deps.knowledge_dir)
        assert reloaded_again.stats.total_cycles == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_full_cycle_executes_all_phases_with_live_llm(tmp_path: Path) -> None:
    """End-to-end: mining extracts, merge consolidates, decay archives — all in one cycle."""
    deps, store = _make_deps(tmp_path, with_model=True)
    try:
        session_path = deps.sessions_dir / "2026-04-16-T120000Z-aaaaaaaa.jsonl"
        append_messages(
            session_path,
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=(
                                "For future sessions: I always prefer ruff for linting, "
                                "never flake8, never pylint."
                            )
                        )
                    ]
                ),
                ModelResponse(
                    parts=[TextPart(content="Understood — ruff for linting.")],
                    model_name="test-model",
                ),
            ],
        )
        cluster_paths = _seed_similar_cluster(deps.knowledge_dir)
        decay_path = _seed_decay_candidate(deps.knowledge_dir)
        store.sync_dir("knowledge", deps.knowledge_dir, glob="*.md")

        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 4):
            result = await run_dream_cycle(deps)

        assert result.any_changes is True
        assert result.errors == []
        assert result.extracted >= 1
        assert result.merged >= 1
        assert result.decayed == 1

        for path in cluster_paths:
            assert not path.exists()
        assert not decay_path.exists()

        state = load_dream_state(deps.knowledge_dir)
        assert state.last_dream_at is not None
        assert state.stats.total_cycles == 1
        assert state.stats.total_extracted == result.extracted
        assert state.stats.total_merged == result.merged
        assert state.stats.total_decayed == result.decayed
        assert session_path.name in state.processed_sessions
    finally:
        store.close()


@pytest.mark.asyncio
async def test_run_dream_cycle_enforces_timeout_bound(tmp_path: Path) -> None:
    """A timeout smaller than any real LLM round-trip must return a partial
    result with ``timed_out=True`` rather than hanging.

    Seeds a real session so the mine phase actually enters an LLM await under
    the timeout context. Requires a live model — without at least one async
    await inside the cycle there is nothing for ``asyncio.timeout`` to interrupt.
    """
    deps, store = _make_deps(tmp_path, with_model=True)
    try:
        session_path = deps.sessions_dir / "2026-04-18-T100000Z-timeoutt.jsonl"
        append_messages(
            session_path,
            [
                ModelRequest(
                    parts=[UserPromptPart(content="Remember: always prefer ruff for linting.")]
                ),
                ModelResponse(
                    parts=[TextPart(content="Understood.")],
                    model_name="test-model",
                ),
            ],
        )

        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
            result = await run_dream_cycle(deps, timeout_secs=0.001)

        assert result.timed_out is True
        assert any("timeout" in err for err in result.errors)
        assert result.any_changes is False
    finally:
        store.close()


@pytest.mark.asyncio
async def test_run_dream_cycle_accumulates_stats_across_cycles(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        await run_dream_cycle(deps)
        state_first = load_dream_state(deps.knowledge_dir)
        state_first.stats.total_extracted = 5
        state_first.stats.total_merged = 2
        state_first.stats.total_decayed = 7
        from co_cli.memory.dream import save_dream_state

        save_dream_state(deps.knowledge_dir, state_first)

        _seed_decay_candidate(deps.knowledge_dir)
        result = await run_dream_cycle(deps)

        assert result.decayed == 1
        final_state = load_dream_state(deps.knowledge_dir)
        assert final_state.stats.total_cycles == 2
        assert final_state.stats.total_extracted == 5 + result.extracted
        assert final_state.stats.total_merged == 2 + result.merged
        assert final_state.stats.total_decayed == 7 + result.decayed
    finally:
        store.close()


# ---------------------------------------------------------------------------
# _maybe_run_dream_cycle — session-end wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_run_dream_cycle_skips_when_consolidation_disabled(
    tmp_path: Path,
) -> None:
    base = make_settings()
    config = base.model_copy(
        update={"knowledge": base.knowledge.model_copy(update={"consolidation_enabled": False})}
    )
    deps, store = _make_deps(tmp_path, with_model=False, config=config)
    try:
        await _maybe_run_dream_cycle(deps)
        assert not dream_state_path(deps.knowledge_dir).exists()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_maybe_run_dream_cycle_skips_when_trigger_not_session_end(
    tmp_path: Path,
) -> None:
    base = make_settings()
    config = base.model_copy(
        update={
            "knowledge": base.knowledge.model_copy(
                update={"consolidation_enabled": True, "consolidation_trigger": "manual"}
            )
        }
    )
    deps, store = _make_deps(tmp_path, with_model=False, config=config)
    try:
        await _maybe_run_dream_cycle(deps)
        assert not dream_state_path(deps.knowledge_dir).exists()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_mine_skips_empty_session_and_marks_processed(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        session_path = deps.sessions_dir / "2026-04-28-T000000Z-emptyses.jsonl"
        session_path.write_text("", encoding="utf-8")

        await run_dream_cycle(deps)

        state = load_dream_state(deps.knowledge_dir)
        assert session_path.name in state.processed_sessions
    finally:
        store.close()
