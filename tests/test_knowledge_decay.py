"""Tests for decay candidate identification in the knowledge lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from co_cli.config._knowledge import KnowledgeSettings
from co_cli.knowledge._decay import find_decay_candidates


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_artifact(
    dir_path: Path,
    *,
    artifact_id: str,
    created: str,
    last_recalled: str | None = None,
    pin_mode: str | None = None,
    decay_protected: bool = False,
    artifact_kind: str = "note",
    body: str = "content body",
) -> Path:
    """Write a real KnowledgeArtifact .md file to *dir_path* and return its path."""
    lines = [
        f"id: {artifact_id}",
        "kind: knowledge",
        f"artifact_kind: {artifact_kind}",
        f"created: '{created}'",
    ]
    if last_recalled is not None:
        lines.append(f"last_recalled: '{last_recalled}'")
    if pin_mode is not None:
        lines.append(f"pin_mode: {pin_mode}")
    if decay_protected:
        lines.append("decay_protected: true")
    frontmatter = "\n".join(lines) + "\n"
    path = dir_path / f"{artifact_id}.md"
    path.write_text(f"---\n{frontmatter}---\n\n{body}\n", encoding="utf-8")
    return path


def _settings(days: int = 90) -> KnowledgeSettings:
    return KnowledgeSettings(decay_after_days=days)


def test_empty_directory_returns_empty_list(tmp_path: Path) -> None:
    """No .md files → no candidates."""
    assert find_decay_candidates(tmp_path, _settings()) == []


def test_missing_directory_returns_empty_list(tmp_path: Path) -> None:
    """Nonexistent knowledge_dir is not an error — just yields no candidates."""
    missing = tmp_path / "does-not-exist"
    assert find_decay_candidates(missing, _settings()) == []


def test_recent_artifact_is_not_candidate(tmp_path: Path) -> None:
    """Artifact created within the decay window is excluded."""
    now = datetime.now(UTC)
    _write_artifact(
        tmp_path,
        artifact_id="recent",
        created=_iso(now - timedelta(days=5)),
    )
    assert find_decay_candidates(tmp_path, _settings(90)) == []


def test_old_never_recalled_is_candidate(tmp_path: Path) -> None:
    """Old artifact that was never recalled is eligible for decay."""
    now = datetime.now(UTC)
    _write_artifact(
        tmp_path,
        artifact_id="old-unused",
        created=_iso(now - timedelta(days=200)),
        last_recalled=None,
    )
    candidates = find_decay_candidates(tmp_path, _settings(90))
    assert [c.id for c in candidates] == ["old-unused"]


def test_old_recently_recalled_is_not_candidate(tmp_path: Path) -> None:
    """Old artifact recalled inside the window is immune — still in use."""
    now = datetime.now(UTC)
    _write_artifact(
        tmp_path,
        artifact_id="old-but-active",
        created=_iso(now - timedelta(days=200)),
        last_recalled=_iso(now - timedelta(days=3)),
    )
    assert find_decay_candidates(tmp_path, _settings(90)) == []


def test_old_recalled_long_ago_is_candidate(tmp_path: Path) -> None:
    """Old artifact whose last recall is itself older than the window is eligible."""
    now = datetime.now(UTC)
    _write_artifact(
        tmp_path,
        artifact_id="stale-recall",
        created=_iso(now - timedelta(days=400)),
        last_recalled=_iso(now - timedelta(days=200)),
    )
    candidates = find_decay_candidates(tmp_path, _settings(90))
    assert [c.id for c in candidates] == ["stale-recall"]


def test_pin_mode_standing_is_immune(tmp_path: Path) -> None:
    """pin_mode='standing' blocks decay regardless of age / recall staleness."""
    now = datetime.now(UTC)
    _write_artifact(
        tmp_path,
        artifact_id="pinned",
        created=_iso(now - timedelta(days=500)),
        last_recalled=None,
        pin_mode="standing",
    )
    assert find_decay_candidates(tmp_path, _settings(90)) == []


def test_decay_protected_is_immune(tmp_path: Path) -> None:
    """decay_protected=true blocks decay regardless of age / recall staleness."""
    now = datetime.now(UTC)
    _write_artifact(
        tmp_path,
        artifact_id="protected",
        created=_iso(now - timedelta(days=500)),
        last_recalled=None,
        decay_protected=True,
    )
    assert find_decay_candidates(tmp_path, _settings(90)) == []


def test_pin_mode_none_does_not_grant_immunity(tmp_path: Path) -> None:
    """The default pin_mode='none' must NOT be treated as pinned."""
    now = datetime.now(UTC)
    _write_artifact(
        tmp_path,
        artifact_id="plain",
        created=_iso(now - timedelta(days=200)),
        last_recalled=None,
        pin_mode="none",
    )
    candidates = find_decay_candidates(tmp_path, _settings(90))
    assert [c.id for c in candidates] == ["plain"]


def test_candidates_sorted_oldest_created_first(tmp_path: Path) -> None:
    """Multiple candidates are returned sorted by created ascending (oldest first)."""
    now = datetime.now(UTC)
    _write_artifact(
        tmp_path,
        artifact_id="mid",
        created=_iso(now - timedelta(days=200)),
    )
    _write_artifact(
        tmp_path,
        artifact_id="oldest",
        created=_iso(now - timedelta(days=400)),
    )
    _write_artifact(
        tmp_path,
        artifact_id="youngest-old",
        created=_iso(now - timedelta(days=120)),
    )
    candidates = find_decay_candidates(tmp_path, _settings(90))
    assert [c.id for c in candidates] == ["oldest", "mid", "youngest-old"]


def test_mixed_population_returns_correct_subset(tmp_path: Path) -> None:
    """Seed six artifacts covering every branch; verify exactly the eligible set returns."""
    now = datetime.now(UTC)

    _write_artifact(
        tmp_path,
        artifact_id="a-recent",
        created=_iso(now - timedelta(days=10)),
    )
    _write_artifact(
        tmp_path,
        artifact_id="b-old-active",
        created=_iso(now - timedelta(days=300)),
        last_recalled=_iso(now - timedelta(days=5)),
    )
    _write_artifact(
        tmp_path,
        artifact_id="c-pinned",
        created=_iso(now - timedelta(days=300)),
        pin_mode="standing",
    )
    _write_artifact(
        tmp_path,
        artifact_id="d-protected",
        created=_iso(now - timedelta(days=300)),
        decay_protected=True,
    )
    _write_artifact(
        tmp_path,
        artifact_id="e-old-untouched",
        created=_iso(now - timedelta(days=200)),
        last_recalled=None,
    )
    _write_artifact(
        tmp_path,
        artifact_id="f-old-stale-recall",
        created=_iso(now - timedelta(days=500)),
        last_recalled=_iso(now - timedelta(days=180)),
    )

    candidates = find_decay_candidates(tmp_path, _settings(90))
    ids = [c.id for c in candidates]
    assert ids == ["f-old-stale-recall", "e-old-untouched"]


def test_decay_after_days_is_honoured(tmp_path: Path) -> None:
    """Changing decay_after_days changes which artifacts are eligible."""
    now = datetime.now(UTC)
    _write_artifact(
        tmp_path,
        artifact_id="age-60",
        created=_iso(now - timedelta(days=60)),
    )
    _write_artifact(
        tmp_path,
        artifact_id="age-120",
        created=_iso(now - timedelta(days=120)),
    )

    strict = find_decay_candidates(tmp_path, _settings(30))
    assert sorted(c.id for c in strict) == ["age-120", "age-60"]

    relaxed = find_decay_candidates(tmp_path, _settings(90))
    assert [c.id for c in relaxed] == ["age-120"]

    permissive = find_decay_candidates(tmp_path, _settings(365))
    assert permissive == []
