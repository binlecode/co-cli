"""Per-skill usage tracking sidecar (~/.co-cli/skills/.usage.json).

Tracks counters and timestamps only for agent-created skills (any user skill
file under user_skills_dir). Bundled skills (under co_cli/skills/) are
upstream-managed and excluded.

Sidecar I/O is best-effort: exceptions are logged and swallowed so usage
tracking never blocks the underlying skill operation. Atomic writes via
co_cli.persistence.atomic.atomic_write_text.

Schema:
    {
      "version": 1,
      "skills": {
        "<name>": {
          "use_count": int,
          "view_count": int,
          "patch_count": int,
          "created_at": ISO8601,
          "last_used_at": ISO8601 | null,
          "last_viewed_at": ISO8601 | null,
          "last_patched_at": ISO8601 | null,
          "state": "active",
          "pinned": bool,
          "recall_days": [ISO8601-date, ...]
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from co_cli.fileio.atomic import atomic_write_text

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

SIDECAR_FILENAME = ".usage.json"
SIDECAR_VERSION = 1
STATE_ACTIVE = "active"


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sidecar_path(deps: CoDeps) -> Path:
    return deps.user_skills_dir / SIDECAR_FILENAME


def _enabled(deps: CoDeps) -> bool:
    return deps.config.skills.usage_tracking_enabled


def is_agent_created(name: str, deps: CoDeps) -> bool:
    """Return True iff <name>.md exists under user_skills_dir.

    Co-cli has no URL-install path; every user skill is treated as
    agent-created (eligible for usage tracking and curation).
    """
    skill_path = deps.user_skills_dir / f"{name}.md"
    return skill_path.exists()


def _empty_records() -> dict[str, Any]:
    return {"version": SIDECAR_VERSION, "skills": {}}


def read_records(deps: CoDeps) -> dict[str, Any]:
    """Load sidecar. Returns empty structure on missing file or parse error."""
    path = _sidecar_path(deps)
    if not path.exists():
        return _empty_records()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("skill usage sidecar unreadable at %s: %s — starting fresh", path, exc)
        return _empty_records()
    if not isinstance(data, dict) or "skills" not in data:
        return _empty_records()
    for record in data["skills"].values():
        record.setdefault("recall_days", [])
    return data


def write_records(deps: CoDeps, data: dict[str, Any]) -> None:
    """Atomically write the full sidecar."""
    path = _sidecar_path(deps)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2))


def _new_record(now: str) -> dict[str, Any]:
    return {
        "use_count": 0,
        "view_count": 0,
        "patch_count": 0,
        "created_at": now,
        "last_used_at": None,
        "last_viewed_at": None,
        "last_patched_at": None,
        "state": STATE_ACTIVE,
        "pinned": False,
        "recall_days": [],
    }


def _get_or_init_record(records: dict[str, Any], name: str, now: str) -> dict[str, Any]:
    skills = records.setdefault("skills", {})
    record = skills.get(name)
    if record is None:
        record = _new_record(now)
        skills[name] = record
    return record


def _bump(deps: CoDeps, name: str, counter_field: str, timestamp_field: str) -> None:
    if not _enabled(deps):
        return
    if not is_agent_created(name, deps):
        return
    try:
        now = _utcnow_iso()
        records = read_records(deps)
        record = _get_or_init_record(records, name, now)
        record[counter_field] = int(record.get(counter_field, 0)) + 1
        record[timestamp_field] = now
        write_records(deps, records)
    except Exception as exc:
        logger.debug("skill usage bump (%s, %s) failed: %s", name, counter_field, exc)


def bump_view(deps: CoDeps, name: str) -> None:
    """Increment view_count and update last_viewed_at. Best-effort."""
    _bump(deps, name, "view_count", "last_viewed_at")


def bump_use(deps: CoDeps, name: str) -> None:
    """Increment use_count and update last_used_at. Best-effort."""
    _bump(deps, name, "use_count", "last_used_at")


def bump_patch(deps: CoDeps, name: str) -> None:
    """Increment patch_count and update last_patched_at. Best-effort."""
    _bump(deps, name, "patch_count", "last_patched_at")


def bump_recall(deps: CoDeps, name: str) -> None:
    """Append today's ISO date to recall_days (deduped). Best-effort."""
    if not _enabled(deps):
        return
    if not is_agent_created(name, deps):
        return
    try:
        today = date.today().isoformat()
        records = read_records(deps)
        record = _get_or_init_record(records, name, _utcnow_iso())
        recall_days: list[str] = record.setdefault("recall_days", [])
        if today not in recall_days:
            recall_days.append(today)
            write_records(deps, records)
    except Exception as exc:
        logger.debug("skill usage bump_recall (%s) failed: %s", name, exc)


def record_create(deps: CoDeps, name: str) -> None:
    """Initialize a fresh record for a newly-created agent skill. Best-effort."""
    if not _enabled(deps):
        return
    if not is_agent_created(name, deps):
        return
    try:
        records = read_records(deps)
        skills = records.setdefault("skills", {})
        skills[name] = _new_record(_utcnow_iso())
        write_records(deps, records)
    except Exception as exc:
        logger.debug("skill usage record_create (%s) failed: %s", name, exc)


def forget(deps: CoDeps, name: str) -> None:
    """Remove a skill's entry from the sidecar. Best-effort."""
    if not _enabled(deps):
        return
    try:
        records = read_records(deps)
        skills = records.get("skills", {})
        if name in skills:
            del skills[name]
            write_records(deps, records)
    except Exception as exc:
        logger.debug("skill usage forget (%s) failed: %s", name, exc)


def set_pinned(deps: CoDeps, name: str, pinned: bool) -> None:
    """Flip pinned flag for a skill. Creates a stub record if none exists.

    Not best-effort: callers (CLI) expect a definitive success/failure signal,
    so I/O errors propagate.
    """
    records = read_records(deps)
    record = _get_or_init_record(records, name, _utcnow_iso())
    record["pinned"] = bool(pinned)
    write_records(deps, records)
