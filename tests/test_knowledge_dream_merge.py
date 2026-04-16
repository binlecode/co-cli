"""Tests for knowledge merge (TASK-5.5)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from tests._settings import make_settings
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.deps import CoDeps
from co_cli.knowledge._artifact import (
    ArtifactKindEnum,
    KnowledgeArtifact,
    PinModeEnum,
    SourceTypeEnum,
    load_knowledge_artifact,
    load_knowledge_artifacts,
)
from co_cli.knowledge._dream import (
    _cluster_by_similarity,
    _is_merge_immune,
    _merge_similar_artifacts,
)
from co_cli.knowledge._frontmatter import render_knowledge_file
from co_cli.knowledge._store import KnowledgeStore
from co_cli.llm._factory import build_model
from co_cli.tools.shell_backend import ShellBackend


def _write_artifact(
    knowledge_dir: Path,
    *,
    content: str,
    artifact_kind: str = ArtifactKindEnum.PREFERENCE.value,
    title: str | None = None,
    pin_mode: str = PinModeEnum.NONE.value,
    decay_protected: bool = False,
    tags: list[str] | None = None,
) -> KnowledgeArtifact:
    """Write a synthetic canonical knowledge artifact and return it."""
    artifact_id = str(uuid4())
    slug = f"test-{artifact_id[:8]}"
    path = knowledge_dir / f"{slug}.md"
    artifact = KnowledgeArtifact(
        id=artifact_id,
        path=path,
        artifact_kind=artifact_kind,
        title=title,
        content=content,
        created=datetime.now(UTC).isoformat(),
        pin_mode=pin_mode,
        decay_protected=decay_protected,
        tags=list(tags or []),
        source_type=SourceTypeEnum.DETECTED.value,
    )
    path.write_text(render_knowledge_file(artifact), encoding="utf-8")
    return artifact


def _make_deps(tmp_path: Path, *, with_model: bool) -> tuple[CoDeps, KnowledgeStore]:
    knowledge_dir = tmp_path / "knowledge"
    sessions_dir = tmp_path / "sessions"
    knowledge_dir.mkdir()
    sessions_dir.mkdir()
    config = make_settings()
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


# ---------------------------------------------------------------------------
# _is_merge_immune — pure logic
# ---------------------------------------------------------------------------


def test_merge_immunity_for_standing_and_decay_protected(tmp_path: Path) -> None:
    artifact_standing = _write_artifact(
        tmp_path,
        content="pinned",
        pin_mode=PinModeEnum.STANDING.value,
    )
    artifact_protected = _write_artifact(
        tmp_path,
        content="protected",
        decay_protected=True,
    )
    artifact_regular = _write_artifact(tmp_path, content="regular")

    assert _is_merge_immune(artifact_standing) is True
    assert _is_merge_immune(artifact_protected) is True
    assert _is_merge_immune(artifact_regular) is False


# ---------------------------------------------------------------------------
# _cluster_by_similarity — pure logic
# ---------------------------------------------------------------------------


def _art(content: str, kind: str = ArtifactKindEnum.PREFERENCE.value) -> KnowledgeArtifact:
    return KnowledgeArtifact(
        id=str(uuid4()),
        path=Path("/dev/null"),
        artifact_kind=kind,
        title=None,
        content=content,
        created="2026-04-16T00:00:00+00:00",
    )


def test_cluster_groups_similar_entries() -> None:
    members = [
        _art("user prefers pytest for testing always default to pytest"),
        _art("user prefers pytest for testing always default to pytest"),
        _art("completely unrelated content about docker containers and orchestration"),
    ]

    clusters = _cluster_by_similarity(members, threshold=0.75)

    assert len(clusters) == 1
    assert len(clusters[0]) == 2


def test_cluster_returns_empty_when_nothing_is_similar() -> None:
    members = [
        _art("user prefers pytest for testing default"),
        _art("completely unrelated content about docker containers"),
        _art("another distinct topic concerning frontend routing"),
    ]

    clusters = _cluster_by_similarity(members, threshold=0.75)

    assert clusters == []


def test_cluster_transitively_links_related_entries() -> None:
    common = "user prefers pytest for testing always default to pytest"
    members = [
        _art(common + " additional tag one"),
        _art(common + " additional tag two"),
        _art(common + " additional tag three"),
    ]

    clusters = _cluster_by_similarity(members, threshold=0.75)

    assert len(clusters) == 1
    assert len(clusters[0]) == 3


def test_cluster_returns_empty_for_single_member() -> None:
    clusters = _cluster_by_similarity([_art("alone")], threshold=0.75)

    assert clusters == []


# ---------------------------------------------------------------------------
# _merge_similar_artifacts — no-op and immune paths (no LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_returns_zero_when_no_similar_artifacts_exist(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        _write_artifact(deps.knowledge_dir, content="prefer pytest always over unittest")
        _write_artifact(deps.knowledge_dir, content="docker compose up -d for local dev")

        merged = await _merge_similar_artifacts(deps)

        assert merged == 0
        assert len(list(deps.knowledge_dir.glob("*.md"))) == 2
        assert not (deps.knowledge_dir / "_archive").exists()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_merge_skips_when_every_similar_artifact_is_immune(tmp_path: Path) -> None:
    deps, store = _make_deps(tmp_path, with_model=False)
    try:
        common_content = "user prefers pytest over unittest always default to pytest framework"
        _write_artifact(
            deps.knowledge_dir,
            content=common_content,
            pin_mode=PinModeEnum.STANDING.value,
        )
        _write_artifact(
            deps.knowledge_dir,
            content=common_content,
            decay_protected=True,
        )

        merged = await _merge_similar_artifacts(deps)

        assert merged == 0
        assert len(list(deps.knowledge_dir.glob("*.md"))) == 2
    finally:
        store.close()


# ---------------------------------------------------------------------------
# _merge_similar_artifacts — live LLM (satisfies plan done_when)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.local
async def test_merge_four_similar_artifacts_produces_one_merged_and_archives_originals(
    tmp_path: Path,
) -> None:
    deps, store = _make_deps(tmp_path, with_model=True)
    try:
        common = (
            "pytest preferred testing framework default pytest tests never unittest "
            "prefer real dependencies"
        )
        entries = [
            _write_artifact(
                deps.knowledge_dir,
                content=f"{common} always",
                title="Testing framework",
                tags=["testing", "pytest"],
            ),
            _write_artifact(
                deps.knowledge_dir,
                content=f"{common} everywhere",
                title="Testing framework",
                tags=["testing", "unittest"],
            ),
            _write_artifact(
                deps.knowledge_dir,
                content=f"{common} strictly",
                title="Testing framework",
                tags=["pytest", "defaults"],
            ),
            _write_artifact(
                deps.knowledge_dir,
                content=f"{common} consistently",
                title="Testing framework",
                tags=["pytest"],
            ),
        ]
        original_ids = {entry.id for entry in entries}
        original_paths = [entry.path for entry in entries]

        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 3):
            merged_count = await _merge_similar_artifacts(deps)

        assert merged_count == 1

        active_files = [path for path in deps.knowledge_dir.glob("*.md") if path.is_file()]
        assert len(active_files) == 1

        archive_dir = deps.knowledge_dir / "_archive"
        assert archive_dir.is_dir()
        archived_files = [path for path in archive_dir.glob("*.md") if path.is_file()]
        assert len(archived_files) == 4

        for path in original_paths:
            assert not path.exists()

        merged = load_knowledge_artifact(active_files[0])
        assert merged.id not in original_ids
        assert merged.source_type == SourceTypeEnum.CONSOLIDATED.value
        assert merged.artifact_kind == ArtifactKindEnum.PREFERENCE.value
        assert set(merged.tags) >= {"pytest", "testing", "unittest", "defaults"}
        assert merged.content.strip()

        fts_hits = store.search("pytest", source="knowledge", limit=10)
        assert any(Path(hit.path) == active_files[0] for hit in fts_hits)
        archive_paths_str = {str(path) for path in archived_files}
        assert not any(hit.path in archive_paths_str for hit in fts_hits)

        remaining_after_merge = load_knowledge_artifacts(deps.knowledge_dir)
        assert len(remaining_after_merge) == 1
    finally:
        store.close()
