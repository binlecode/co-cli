"""Behavioral tests for the skill-lifecycle phases inside dream housekeeping.

LLM-dependent merge calls are covered by evals; these unit tests exercise the
pure logic: candidate loading, recall-aware canonical pick, decay candidacy,
archive moves, and the persisted-state schema extension.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from co_cli.config.skills import SkillsSettings
from co_cli.daemons.dream._housekeeping import (
    _archive_user_skill,
    _identify_skill_clusters,
    _load_user_skill_candidates,
    _select_canonical_skill,
    _skill_recall_key,
    decay_skills,
)
from co_cli.daemons.dream.state import HousekeepingState


def _write_skill(user_skills_dir: Path, name: str, body: str, description: str = "test") -> Path:
    text = f"---\ndescription: {description}\n---\n\n{body}\n"
    path = user_skills_dir / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_sidecar(
    user_skills_dir: Path,
    name: str,
    *,
    use_count: int = 0,
    created_at: str | None = None,
    recall_days: list[str] | None = None,
    pinned: bool = False,
) -> Path:
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "use_count": use_count,
        "view_count": 0,
        "patch_count": 0,
        "created_at": created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_used_at": None,
        "last_viewed_at": None,
        "last_patched_at": None,
        "state": "active",
        "pinned": pinned,
        "recall_days": recall_days or [],
    }
    path = user_skills_dir / name / "SKILL.usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _deps(
    user_skills_dir: Path, *, skills_settings: SkillsSettings | None = None
) -> SimpleNamespace:
    settings = skills_settings or SkillsSettings()
    return SimpleNamespace(
        user_skills_dir=user_skills_dir,
        config=SimpleNamespace(skills=settings),
    )


# ---------------------------------------------------------------------------
# _skill_recall_key / canonical pick (T2)
# ---------------------------------------------------------------------------


def test_skill_recall_key_missing_sidecar_returns_zero_zero(tmp_path: Path) -> None:
    """No sidecar on disk → (0, 0) so canonical loses to anything tracked."""
    deps = _deps(tmp_path / "skills")
    assert _skill_recall_key(deps, "no-such-skill") == (0, 0)


def test_skill_recall_key_reports_distinct_days_and_use_count(tmp_path: Path) -> None:
    """recall_days length + use_count both reported."""
    user_skills_dir = tmp_path / "skills"
    _write_skill(user_skills_dir, "x", "body")
    _write_sidecar(user_skills_dir, "x", use_count=42, recall_days=["2026-01-01", "2026-01-02"])
    deps = _deps(user_skills_dir)
    assert _skill_recall_key(deps, "x") == (2, 42)


def test_select_canonical_skill_prefers_highest_recall_days(tmp_path: Path) -> None:
    """Highest len(recall_days) wins; use_count is the tiebreaker only when tied."""
    user_skills_dir = tmp_path / "skills"
    for n in ("a", "b", "c"):
        _write_skill(user_skills_dir, n, f"body-{n}")
    _write_sidecar(user_skills_dir, "a", use_count=100, recall_days=["2026-01-01"])
    _write_sidecar(
        user_skills_dir, "b", use_count=1, recall_days=["2026-01-01", "2026-01-02", "2026-01-03"]
    )
    _write_sidecar(user_skills_dir, "c", use_count=50, recall_days=["2026-01-01", "2026-01-02"])

    cluster = _load_user_skill_candidates(user_skills_dir)
    anchor = _select_canonical_skill(_deps(user_skills_dir), cluster)
    assert anchor.name == "b"


def test_select_canonical_skill_use_count_breaks_recall_ties(tmp_path: Path) -> None:
    """Same recall_days length → higher use_count wins."""
    user_skills_dir = tmp_path / "skills"
    for n in ("a", "b"):
        _write_skill(user_skills_dir, n, f"body-{n}")
    _write_sidecar(user_skills_dir, "a", use_count=10, recall_days=["2026-01-01"])
    _write_sidecar(user_skills_dir, "b", use_count=50, recall_days=["2026-01-01"])

    cluster = _load_user_skill_candidates(user_skills_dir)
    anchor = _select_canonical_skill(_deps(user_skills_dir), cluster)
    assert anchor.name == "b"


# ---------------------------------------------------------------------------
# _identify_skill_clusters — pinned exclusion and basic clustering (T2)
# ---------------------------------------------------------------------------


def test_identify_clusters_groups_similar_user_skills(tmp_path: Path) -> None:
    """Two skills with high body overlap fall into one cluster."""
    user_skills_dir = tmp_path / "skills"
    shared = "deploy the artifact via the staging pipeline using terraform plan apply " * 6
    _write_skill(user_skills_dir, "deploy-session-123", shared)
    _write_skill(user_skills_dir, "deploy-session-456", shared + " extra-token-foo")
    _write_skill(user_skills_dir, "completely-unrelated", "totally different unrelated content")

    deps = _deps(
        user_skills_dir,
        skills_settings=SkillsSettings(consolidation_similarity_threshold=0.5),
    )
    clusters = _identify_skill_clusters(deps)
    assert len(clusters) == 1
    cluster_names = {s.name for s in clusters[0]}
    assert cluster_names == {"deploy-session-123", "deploy-session-456"}


def test_identify_clusters_excludes_pinned_skills(tmp_path: Path) -> None:
    """Pinned skills never enter merge clusters even when body matches."""
    user_skills_dir = tmp_path / "skills"
    shared = "review pull request style checks lint test ci cd " * 6
    _write_skill(user_skills_dir, "review-a", shared)
    _write_skill(user_skills_dir, "review-b", shared + " variant")
    _write_sidecar(user_skills_dir, "review-a", pinned=True)
    _write_sidecar(user_skills_dir, "review-b", pinned=False)

    deps = _deps(
        user_skills_dir,
        skills_settings=SkillsSettings(consolidation_similarity_threshold=0.5),
    )
    clusters = _identify_skill_clusters(deps)
    assert clusters == [], "pinned skill must drop the cluster below the min-size threshold"


# ---------------------------------------------------------------------------
# decay_skills — candidacy matrix (T3)
# ---------------------------------------------------------------------------


def _ago_iso(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago_date(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def test_decay_skills_protects_pinned(tmp_path: Path) -> None:
    """Pinned skill is immune to decay even when aged + zero recall."""
    user_skills_dir = tmp_path / "skills"
    _write_skill(user_skills_dir, "pinned", "body")
    _write_sidecar(
        user_skills_dir, "pinned", created_at=_ago_iso(200), recall_days=[], pinned=True
    )

    deps = _deps(
        user_skills_dir,
        skills_settings=SkillsSettings(decay_after_days=90, recall_protection_days=30),
    )
    state = HousekeepingState()
    archived = decay_skills(deps, state)
    assert archived == 0
    assert state.stats.skill_decayed == 0


def test_decay_skills_protects_recent_recall(tmp_path: Path) -> None:
    """Aged skill recalled within recall_protection_days → protected."""
    user_skills_dir = tmp_path / "skills"
    _write_skill(user_skills_dir, "active", "body")
    _write_sidecar(
        user_skills_dir,
        "active",
        created_at=_ago_iso(120),
        recall_days=[_ago_date(5)],
    )

    deps = _deps(
        user_skills_dir,
        skills_settings=SkillsSettings(decay_after_days=90, recall_protection_days=30),
    )
    state = HousekeepingState()
    archived = decay_skills(deps, state)
    assert archived == 0
    assert state.stats.skill_decayed == 0


def test_decay_skills_archives_recall_outside_window(tmp_path: Path) -> None:
    """Aged + recall older than recall_protection_days → archives the skill.

    Also pins move-not-copy: the source .md must be gone after archiving.
    """
    user_skills_dir = tmp_path / "skills"
    skill_path = _write_skill(user_skills_dir, "lapsed", "body")
    _write_sidecar(
        user_skills_dir,
        "lapsed",
        created_at=_ago_iso(120),
        recall_days=[_ago_date(60)],
    )

    deps = _deps(
        user_skills_dir,
        skills_settings=SkillsSettings(decay_after_days=90, recall_protection_days=30),
    )
    state = HousekeepingState()
    archived = decay_skills(deps, state)
    assert archived == 1
    assert state.stats.skill_decayed == 1
    assert not skill_path.exists(), "source skill must be moved, not copied"
    assert (user_skills_dir / ".archive" / "lapsed" / "SKILL.md").exists()
    assert (user_skills_dir / ".archive" / "lapsed" / "SKILL.usage.json").exists(), (
        "sidecar must travel with the archived folder (whole-folder move, not per-file)"
    )


def test_decay_skills_skips_skills_without_sidecar(tmp_path: Path) -> None:
    """A skill without a usage.json sidecar is never decayed."""
    user_skills_dir = tmp_path / "skills"
    _write_skill(user_skills_dir, "orphan", "body without sidecar")

    deps = _deps(
        user_skills_dir,
        skills_settings=SkillsSettings(decay_after_days=90, recall_protection_days=30),
    )
    state = HousekeepingState()
    archived = decay_skills(deps, state)
    assert archived == 0
    assert (user_skills_dir / "orphan" / "SKILL.md").exists(), (
        "sidecar-less skill must not be archived"
    )


# ---------------------------------------------------------------------------
# Archive helper — collision handling (T2 + T3)
# ---------------------------------------------------------------------------


def test_archive_user_skill_resolves_collisions(tmp_path: Path) -> None:
    """When .archive/<name>/ exists, the new archive folder gets a -1 suffix."""
    user_skills_dir = tmp_path / "skills"
    archive_dir = user_skills_dir / ".archive"
    (archive_dir / "dup").mkdir(parents=True, exist_ok=True)
    (archive_dir / "dup" / "SKILL.md").write_text("prior archive", encoding="utf-8")

    path = _write_skill(user_skills_dir, "dup", "fresh body")
    deps = _deps(user_skills_dir)
    ok = _archive_user_skill(deps, path)
    assert ok is True
    assert (archive_dir / "dup-1" / "SKILL.md").exists()
    assert (archive_dir / "dup" / "SKILL.md").read_text(encoding="utf-8") == "prior archive"


# ---------------------------------------------------------------------------
# Recall-bump regression — bump_recall appends today's date to sidecar
# ---------------------------------------------------------------------------


def test_bump_recall_appends_today_to_recall_days(tmp_path: Path) -> None:
    """Invoking a skill (via slash dispatch or skill_view tool) calls bump_recall,
    which appends today's ISO date to the sidecar's recall_days exactly once.

    This protects the decay-signal contract: the slash dispatch at
    commands/core.py:113 and the skill_view tool at tools/system/skills.py:69
    both delegate to bump_recall. If bump_recall ever stops persisting today's
    date, every user skill effectively becomes "never recalled" and decay
    eligibility drifts.
    """
    from co_cli.skills.usage import bump_recall, read_record

    user_skills_dir = tmp_path / "skills"
    _write_skill(user_skills_dir, "tracked", "body")
    _write_sidecar(user_skills_dir, "tracked", recall_days=[])

    deps = _deps(user_skills_dir)
    bump_recall(deps, "tracked")
    bump_recall(deps, "tracked")

    record = read_record(deps, "tracked")
    today = date.today().isoformat()
    assert record is not None
    assert record["recall_days"] == [today], "duplicate same-day bumps must dedupe"


# ---------------------------------------------------------------------------
# Recall-bump anti-regression — manifest rendering does NOT mutate sidecars
# ---------------------------------------------------------------------------


def test_manifest_render_does_not_bump_recall_days(tmp_path: Path) -> None:
    """render_skill_manifest emits description-only; sidecar.recall_days untouched."""
    from co_cli.context.manifests.skill_manifest import render_skill_manifest
    from co_cli.skills.skill_types import SkillInfo

    user_skills_dir = tmp_path / "skills"
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    skill_path = _write_skill(user_skills_dir, "watched", "skill body content")
    _write_sidecar(user_skills_dir, "watched", recall_days=["2026-01-01"])

    index = {"watched": SkillInfo(name="watched", description="d", body="body", path=skill_path)}
    out = render_skill_manifest(index, bundled_dir, user_skills_dir)
    assert "<available_skills>" in out
    assert 'name="watched"' in out

    sidecar = json.loads(
        (user_skills_dir / "watched" / "SKILL.usage.json").read_text(encoding="utf-8")
    )
    assert sidecar["recall_days"] == ["2026-01-01"], (
        "manifest assembly must NOT mutate recall_days"
    )
