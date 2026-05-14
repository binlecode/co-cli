"""Curator state machine — pure state transitions, archive/restore, idle gate."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from co_cli.config.skills import (
    CURATOR_ARCHIVE_AFTER_DAYS,
    CURATOR_MIN_IDLE_HOURS,
    CURATOR_STALE_AFTER_DAYS,
    SkillsSettings,
)

if TYPE_CHECKING:
    from co_cli.deps import CoDeps, CoSessionState

CURATOR_STATE_FILENAME = ".curator_state.json"
CURATOR_STATE_VERSION = 1


@dataclass(frozen=True)
class StateTransition:
    """A single state transition applied to one skill."""

    name: str
    from_state: str
    to_state: str


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO 8601 UTC timestamp string to an aware datetime."""
    return datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=UTC)


def _days_since(dt: datetime, now: datetime) -> float:
    """Return elapsed days between dt and now."""
    return (now - dt).total_seconds() / 86400.0


def apply_state_transitions(
    records: dict[str, Any],
    settings: SkillsSettings,
    now: datetime,
) -> list[StateTransition]:
    """Pure function: compute state transitions from usage records.

    Returns a list of StateTransition objects. Does not mutate records,
    does not touch disk.

    State machine:
      active  → stale    if last_used_at > CURATOR_STALE_AFTER_DAYS ago
      stale   → archived if last activity > CURATOR_ARCHIVE_AFTER_DAYS ago
      stale   → active   if recently used (within CURATOR_STALE_AFTER_DAYS)

    Pinned skills and already-archived skills are skipped.
    If last_used_at is None, created_at is used as the proxy. If both are
    None, the skill is skipped.
    """
    transitions: list[StateTransition] = []
    skills = records.get("skills", {})

    for name, record in skills.items():
        state = record.get("state", "active")
        pinned = record.get("pinned", False)

        if pinned:
            continue
        if state == "archived":
            continue

        last_used_at_str = record.get("last_used_at")
        created_at_str = record.get("created_at")

        if last_used_at_str is not None:
            reference_dt = _parse_iso(last_used_at_str)
        elif created_at_str is not None:
            reference_dt = _parse_iso(created_at_str)
        else:
            continue

        days_idle = _days_since(reference_dt, now)

        if state == "active":
            if days_idle > CURATOR_STALE_AFTER_DAYS:
                transitions.append(
                    StateTransition(name=name, from_state="active", to_state="stale")
                )
        elif state == "stale":
            if days_idle > CURATOR_ARCHIVE_AFTER_DAYS:
                transitions.append(
                    StateTransition(name=name, from_state="stale", to_state="archived")
                )
            elif days_idle <= CURATOR_STALE_AFTER_DAYS:
                transitions.append(
                    StateTransition(name=name, from_state="stale", to_state="active")
                )

    return transitions


def archive_skill(deps: CoDeps, name: str) -> None:
    """Move <user_skills_dir>/<name>.md to <user_skills_dir>/.archive/<name>.md.

    Creates .archive/ if missing. Calls refresh_skills after move.
    Idempotent: if source is missing but archive exists, no error.
    Raises FileNotFoundError if neither source nor archive exists.
    """
    from co_cli.skills.lifecycle import refresh_skills

    source = deps.user_skills_dir / f"{name}.md"
    archive_dir = deps.user_skills_dir / ".archive"
    dest = archive_dir / f"{name}.md"

    if not source.exists():
        if dest.exists():
            return
        raise FileNotFoundError(f"Skill '{name}' not found in user skills or archive")

    archive_dir.mkdir(parents=True, exist_ok=True)
    source.rename(dest)
    refresh_skills(deps)


def restore_skill(deps: CoDeps, name: str) -> None:
    """Move <user_skills_dir>/.archive/<name>.md back to <user_skills_dir>/<name>.md.

    Calls refresh_skills after move.
    Raises FileNotFoundError if the skill is not in the archive.
    """
    from co_cli.skills.lifecycle import refresh_skills

    archive_dir = deps.user_skills_dir / ".archive"
    source = archive_dir / f"{name}.md"
    dest = deps.user_skills_dir / f"{name}.md"

    if not source.exists():
        raise FileNotFoundError(f"Skill '{name}' is not in the archive")

    dest.parent.mkdir(parents=True, exist_ok=True)
    source.rename(dest)
    refresh_skills(deps)


def _curator_state_path(deps: CoDeps) -> Path:
    return deps.user_skills_dir / CURATOR_STATE_FILENAME


def read_curator_state(deps: CoDeps) -> dict[str, Any]:
    """Read the curator state file. Returns defaults on missing or error."""
    path = _curator_state_path(deps)
    if not path.exists():
        return {"version": CURATOR_STATE_VERSION, "run_count": 0, "paused": False}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return {"version": CURATOR_STATE_VERSION, "run_count": 0, "paused": False}


def write_curator_state(deps: CoDeps, state: dict[str, Any]) -> None:
    """Atomically write the curator state file via tempfile + os.replace."""
    path = _curator_state_path(deps)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".json.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def should_run_now(
    state: dict[str, Any],
    settings: SkillsSettings,
    now: datetime,
    idle_seconds: float,
    *,
    bypass_time_gate: bool = False,
) -> bool:
    """Return True only when all conditions are met.

    1. settings.curator_enabled is True
    2. state.get("paused") is False/absent
    3. idle_seconds >= CURATOR_MIN_IDLE_HOURS * 3600
    4. bypass_time_gate=True OR last_run_at is None OR (now - last_run_at) > interval
    """
    if not settings.curator_enabled:
        return False
    if state.get("paused"):
        return False
    if idle_seconds < CURATOR_MIN_IDLE_HOURS * 3600:
        return False
    if bypass_time_gate:
        return True
    last_run_str = state.get("last_run_at")
    if last_run_str is None:
        return True
    last_run = _parse_iso(last_run_str)
    return (now - last_run) > timedelta(hours=settings.curator_interval_hours)


def _idle_seconds(session: CoSessionState, now: datetime) -> float:
    """Return seconds since session.last_user_input_at.

    Returns float('inf') if last_user_input_at is None (startup = effectively infinite idle).
    """
    if session.last_user_input_at is None:
        return float("inf")
    delta = now - session.last_user_input_at
    return delta.total_seconds()
