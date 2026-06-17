"""Behavioral tests for dream daemon housekeeping — scheduled tick + merge + decay.

LLM-dependent paths (the actual merge sub-agent prompt) are covered by
eval_memory.py W3.F and eval_daily_chat.py W1.D — these unit tests cover the
pure logic: clustering, canonical pick, decay candidacy, scheduled-tick math.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from co_cli.config.dream import DreamSettings
from co_cli.config.memory import MemorySettings
from co_cli.daemons.dream._housekeeping import (
    _identify_mergeable_clusters,
    _select_canonical,
)
from co_cli.daemons.dream._loop import scheduled_tick_due
from co_cli.daemons.dream.state import HousekeepingState
from co_cli.memory.item import MemoryItem


def _item(
    *,
    memory_kind: str = "note",
    content: str = "x",
    title: str = "t",
    recall_count: int = 0,
    last_recalled_at: str | None = None,
    created_at: str | None = None,
    decay_protected: bool = False,
) -> MemoryItem:
    """Build an in-memory MemoryItem fixture — path is synthetic."""
    return MemoryItem(
        id=str(uuid4()),
        path=Path(f"/tmp/{uuid4().hex}.md"),
        memory_kind=memory_kind,
        title=title,
        content=content,
        created_at=created_at or datetime.now(UTC).isoformat(),
        last_recalled_at=last_recalled_at,
        recall_count=recall_count,
        decay_protected=decay_protected,
    )


def _write_md(memory_dir: Path, item: MemoryItem) -> Path:
    """Write a minimal memory-item .md so load_memory_items can read it back."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / f"{item.title.replace(' ', '-')}-{item.id[:8]}.md"
    lines = [
        "---",
        f"id: {item.id}",
        f"memory_kind: {item.memory_kind}",
        f"title: {item.title}",
        f"created_at: '{item.created_at}'",
        f"recall_count: {item.recall_count}",
        f"decay_protected: {str(item.decay_protected).lower()}",
    ]
    if item.last_recalled_at:
        lines.append(f"last_recalled_at: '{item.last_recalled_at}'")
    lines += ["---", "", item.content, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# scheduled_tick_due
# ---------------------------------------------------------------------------


def _cfg(**overrides) -> DreamSettings:
    return DreamSettings(**overrides)


def test_interval_must_align_to_daily_grid():
    """run_interval_hours must be a factor of 24 below 24, or a multiple of 24 above it."""
    import pytest
    from pydantic import ValidationError

    for ok in (1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 720):
        assert DreamSettings(run_interval_hours=ok).run_interval_hours == ok
    for bad in (5, 7, 9, 23, 25, 36, 100):
        with pytest.raises(ValidationError):
            DreamSettings(run_interval_hours=bad)


def test_scheduled_tick_due_never_run_returns_true():
    """Fresh daemon with no prior pass fires immediately on the first idle tick."""
    state = HousekeepingState()
    assert scheduled_tick_due(state, _cfg()) is True


def test_scheduled_tick_due_within_interval_returns_false():
    """Within run_interval_hours since last pass: not due."""
    now = datetime.now(UTC)
    state = HousekeepingState(last_housekeeping_at=(now - timedelta(hours=5)).isoformat())
    assert scheduled_tick_due(state, _cfg(run_interval_hours=24)) is False


def test_scheduled_tick_due_past_interval_and_past_run_start_at_returns_true():
    """Past run_interval boundary AND past the next run_start_at clamp: due."""
    now = datetime.now().astimezone()
    last = now - timedelta(hours=25)
    state = HousekeepingState(last_housekeeping_at=last.astimezone(UTC).isoformat())
    early_time = (now - timedelta(minutes=5)).strftime("%H:%M")
    assert scheduled_tick_due(state, _cfg(run_interval_hours=24, run_start_at=early_time)) is True


def test_scheduled_tick_due_past_interval_before_run_start_at_returns_false():
    """Just past run_interval but the next run_start_at clamp is still ahead: not due."""
    now = datetime.now().astimezone()
    last = now - timedelta(hours=25)
    state = HousekeepingState(last_housekeeping_at=last.astimezone(UTC).isoformat())
    future_time = (now + timedelta(minutes=5)).strftime("%H:%M")
    assert (
        scheduled_tick_due(state, _cfg(run_interval_hours=24, run_start_at=future_time)) is False
    )


# ---------------------------------------------------------------------------
# _select_canonical — recall-aware anchor
# ---------------------------------------------------------------------------


def test_select_canonical_picks_highest_recall_count():
    """Cluster with mixed recall_count → highest wins."""
    cluster = [
        _item(content="A", recall_count=1),
        _item(content="B", recall_count=5),
        _item(content="C", recall_count=2),
    ]
    anchor = _select_canonical(cluster)
    assert anchor.content == "B"


def test_select_canonical_ties_break_by_recency():
    """All-zero-recall cluster: tiebreaker uses most recent created_at."""
    old = datetime(2024, 1, 1, tzinfo=UTC).isoformat()
    mid = datetime(2024, 6, 1, tzinfo=UTC).isoformat()
    new = datetime(2024, 12, 1, tzinfo=UTC).isoformat()
    cluster = [
        _item(content="old", recall_count=0, created_at=old),
        _item(content="new", recall_count=0, created_at=new),
        _item(content="mid", recall_count=0, created_at=mid),
    ]
    anchor = _select_canonical(cluster)
    assert anchor.content == "new"


# ---------------------------------------------------------------------------
# _identify_mergeable_clusters — article exclusion
# ---------------------------------------------------------------------------


def test_merge_excludes_articles(tmp_path: Path):
    """kind=article items never enter merge clusters even when content matches."""
    from types import SimpleNamespace

    memory_dir = tmp_path / "memory"
    base_body = "alpha bravo charlie delta echo " * 4
    _write_md(memory_dir, _item(memory_kind="article", content=base_body, title="art-a"))
    _write_md(
        memory_dir, _item(memory_kind="article", content=base_body + " extra", title="art-b")
    )
    _write_md(memory_dir, _item(memory_kind="note", content=base_body, title="note-a"))
    _write_md(memory_dir, _item(memory_kind="note", content=base_body + " extra", title="note-b"))

    deps = SimpleNamespace(
        memory_dir=memory_dir,
        config=SimpleNamespace(memory=MemorySettings(consolidation_similarity_threshold=0.5)),
    )

    clusters = _identify_mergeable_clusters(deps)
    flat_kinds = {item.memory_kind for cluster in clusters for item in cluster}
    assert "article" not in flat_kinds, f"articles must be excluded; got kinds={flat_kinds}"
    assert flat_kinds == {"note"}, f"only notes should cluster; got {flat_kinds}"


# ---------------------------------------------------------------------------
# _write_consolidated_item — save-time dedup honors configured threshold
# ---------------------------------------------------------------------------


def test_consolidated_write_honors_configured_threshold(tmp_path: Path):
    """Dream consolidated write dedups at the configured threshold, not the 0.75 default.

    Seeds one existing note, then writes a consolidated body ~0.43 Jaccard-similar
    (3 shared / 7 union tokens). With consolidation_similarity_threshold=0.3 the
    save-time dedup must fold it into the existing item (no second file). Under the
    old hardcoded 0.75 the body fell below threshold and a distinct file was created.
    """
    from types import SimpleNamespace

    from co_cli.daemons.dream._housekeeping import _write_consolidated_item

    memory_dir = tmp_path / "memory"
    existing = _item(
        memory_kind="note", content="alpha bravo charlie delta echo", title="existing"
    )
    _write_md(memory_dir, existing)

    deps = SimpleNamespace(
        memory_dir=memory_dir,
        memory_store=None,
        index_store=None,
        config=SimpleNamespace(memory=MemorySettings(consolidation_similarity_threshold=0.3)),
    )
    anchor = _item(memory_kind="note", content="unused", title="merged")
    merged_body = "alpha bravo charlie foxtrot golf"

    _write_consolidated_item(deps, [anchor], anchor, merged_body)

    md_files = list(memory_dir.glob("*.md"))
    assert len(md_files) == 1, (
        "consolidated write must dedup into the existing note at threshold 0.3; "
        f"got {len(md_files)} files: {[p.name for p in md_files]}"
    )


# ---------------------------------------------------------------------------
# memory count tripwire — warn-only, never evicts
# ---------------------------------------------------------------------------


def test_memory_count_over_cap_warns_but_never_archives(tmp_path: Path, monkeypatch) -> None:
    """An over-cap store emits the warn span event and archives zero items.

    The tripwire is a safety net, not an evictor: crossing the threshold must
    surface a warning without deleting/archiving anything on count grounds.
    Failure mode: re-introducing value-blind eviction under a different name.
    """
    import asyncio
    import importlib
    from types import SimpleNamespace

    monkeypatch.setenv("CO_HOME", str(tmp_path))

    import co_cli.config.core as core_mod
    import co_cli.daemons.dream._housekeeping as housekeeping_mod

    importlib.reload(core_mod)
    importlib.reload(housekeeping_mod)

    memory_dir = tmp_path / "memory"
    for n in range(3):
        _write_md(memory_dir, _item(content=f"item-{n}", title=f"t{n}"))

    monkeypatch.setattr(housekeeping_mod, "MEMORY_ITEM_COUNT_WARN", 2)

    async def _noop_merge(_deps, _state):
        return 0

    monkeypatch.setattr(housekeeping_mod, "merge_memory", _noop_merge)

    from co_cli.config.skills import SkillsSettings

    deps = SimpleNamespace(
        memory_dir=memory_dir,
        memory_store=None,
        user_skills_dir=tmp_path / "user-skills",
        sessions_dir=tmp_path / "sessions",
        config=SimpleNamespace(memory=MemorySettings(), skills=SkillsSettings()),
    )
    state = HousekeepingState()
    cfg = DreamSettings(max_pass_seconds=60)

    core_mod.DREAM_DAEMON_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(housekeeping_mod.run_housekeeping(deps, cfg, state))

    md_files = list(memory_dir.glob("*.md"))
    assert len(md_files) == 3, "over-cap tripwire must not archive any item"
    archive_dir = memory_dir / "_archive"
    archived = list(archive_dir.glob("*.md")) if archive_dir.exists() else []
    assert archived == [], "nothing may be archived on count grounds"


def test_memory_count_under_cap_does_not_warn(tmp_path: Path) -> None:
    """Below the threshold, the helper reports not-over-cap (no false fire)."""
    items = [_item(content=f"c{n}") for n in range(3)]
    from co_cli.daemons.dream._housekeeping import memory_count_over_cap

    assert memory_count_over_cap(items, warn_at=4) is False
    assert memory_count_over_cap(items, warn_at=2) is True


# ---------------------------------------------------------------------------
# co dream tidy — sentinel + daemon-not-running path
# ---------------------------------------------------------------------------


def test_dream_tidy_errors_when_daemon_not_running(tmp_path: Path, monkeypatch) -> None:
    """`co dream tidy` with no PID file: exits 1, no sentinel written."""
    from typer.testing import CliRunner

    monkeypatch.setenv("CO_HOME", str(tmp_path))
    import importlib

    import co_cli.commands.dream as dream_mod
    import co_cli.config.core as core_mod

    importlib.reload(core_mod)
    importlib.reload(dream_mod)

    runner = CliRunner()
    result = runner.invoke(dream_mod.dream_app, ["tidy"])
    assert result.exit_code == 1
    assert "not running" in result.stderr or "not running" in result.output
    assert not core_mod.DREAM_TIDY_TAG.exists(), "sentinel must not be written when daemon down"


# ---------------------------------------------------------------------------
# prune_done_and_snapshots
# ---------------------------------------------------------------------------


def test_prune_removes_aged_done_and_snapshots_keeps_fresh(tmp_path: Path) -> None:
    """Aged done files and stale snapshots are deleted; fresh done files survive."""
    import os

    from co_cli.daemons.dream._housekeeping import prune_done_and_snapshots

    done_dir = tmp_path / "done"
    snapshots_dir = tmp_path / "snapshots"
    done_dir.mkdir()
    snapshots_dir.mkdir()

    old_done = done_dir / "aged.json"
    fresh_done = done_dir / "recent.json"
    stale_snapshot = snapshots_dir / "orphan.snap"
    old_done.write_text("{}", encoding="utf-8")
    fresh_done.write_text("{}", encoding="utf-8")
    stale_snapshot.write_text("data", encoding="utf-8")

    old = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(old_done, (old, old))
    os.utime(stale_snapshot, (old, old))

    prune_done_and_snapshots(
        DreamSettings(done_retention_days=7),
        done_dir=done_dir,
        snapshots_dir=snapshots_dir,
    )

    assert not old_done.exists()
    assert not stale_snapshot.exists()
    assert fresh_done.exists()


# ---------------------------------------------------------------------------
# prune_sessions
# ---------------------------------------------------------------------------


def _write_session(sessions_dir: Path, *, age_days: int) -> Path:
    """Create a canonical-named session file aged ``age_days`` via mtime."""
    import os

    from co_cli.session.filename import session_filename

    sessions_dir.mkdir(parents=True, exist_ok=True)
    created = datetime.now(UTC) - timedelta(days=age_days)
    name = session_filename(created, uuid4().hex)
    path = sessions_dir / name
    path.write_text("{}\n", encoding="utf-8")
    stamp = created.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_prune_sessions_deletes_aged_keeps_recent(tmp_path: Path) -> None:
    """Sessions older than the cutoff are deleted; recent ones survive."""
    from co_cli.daemons.dream._housekeeping import prune_sessions

    sessions_dir = tmp_path / "sessions"
    aged = _write_session(sessions_dir, age_days=45)
    recent = _write_session(sessions_dir, age_days=1)

    count = prune_sessions(DreamSettings(session_retention_days=30), sessions_dir=sessions_dir)

    assert count == 1
    assert not aged.exists()
    assert recent.exists()


def test_prune_sessions_disabled_is_noop(tmp_path: Path) -> None:
    """session_retention_days == 0 deletes nothing, even for very old sessions."""
    from co_cli.daemons.dream._housekeeping import prune_sessions

    sessions_dir = tmp_path / "sessions"
    aged = _write_session(sessions_dir, age_days=400)

    count = prune_sessions(DreamSettings(session_retention_days=0), sessions_dir=sessions_dir)

    assert count == 0
    assert aged.exists()


def test_prune_sessions_leaves_non_canonical_files(tmp_path: Path) -> None:
    """A foreign .jsonl that isn't a canonical session name is never deleted."""
    import os

    from co_cli.daemons.dream._housekeeping import prune_sessions

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    foreign = sessions_dir / "notes.jsonl"
    foreign.write_text("{}\n", encoding="utf-8")
    old = (datetime.now(UTC) - timedelta(days=400)).timestamp()
    os.utime(foreign, (old, old))

    count = prune_sessions(DreamSettings(session_retention_days=30), sessions_dir=sessions_dir)

    assert count == 0
    assert foreign.exists()


def test_run_housekeeping_prunes_expired_sessions(tmp_path: Path, monkeypatch) -> None:
    """Full pass deletes expired sessions and keeps recent ones."""
    import asyncio
    import importlib
    from types import SimpleNamespace

    from co_cli.config.skills import SkillsSettings

    monkeypatch.setenv("CO_HOME", str(tmp_path))

    import co_cli.config.core as core_mod
    import co_cli.daemons.dream._housekeeping as housekeeping_mod

    importlib.reload(core_mod)
    importlib.reload(housekeeping_mod)

    sessions_dir = tmp_path / "sessions"
    aged = _write_session(sessions_dir, age_days=45)
    recent = _write_session(sessions_dir, age_days=1)

    deps = SimpleNamespace(
        memory_dir=tmp_path / "memory",
        memory_store=None,
        user_skills_dir=tmp_path / "user-skills",
        sessions_dir=sessions_dir,
        config=SimpleNamespace(
            memory=MemorySettings(),
            skills=SkillsSettings(),
        ),
    )
    state = HousekeepingState()
    cfg = DreamSettings(session_retention_days=30)

    core_mod.DREAM_DAEMON_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(housekeeping_mod.run_housekeeping(deps, cfg, state))

    assert not aged.exists()
    assert recent.exists()
