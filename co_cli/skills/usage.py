"""Per-skill usage tracking sidecars (~/.co-cli/skills/<name>.usage.json).

Tracks counters and timestamps only for agent-created skills (any user skill
file under user_skills_dir). Bundled skills (under co_cli/skills/) are
upstream-managed and excluded.

Each agent-created skill has its own sidecar file next to its <name>.md.
This bounds the blast radius of concurrent writes to a single skill and
avoids whole-library rewrites on every bump.

Sidecar I/O is best-effort: exceptions are logged and swallowed so usage
tracking never blocks the underlying skill operation. Atomic writes via
co_cli.fileio.atomic.atomic_write_text.

Per-skill file schema:
    {
      "version": 1,
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
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from co_cli.fileio.atomic import atomic_write_text

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

SIDECAR_SUFFIX = ".usage.json"
SIDECAR_VERSION = 1
STATE_ACTIVE = "active"


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sidecar_path(deps: CoDeps, name: str) -> Path:
    return deps.user_skills_dir / f"{name}{SIDECAR_SUFFIX}"


def _enabled(deps: CoDeps) -> bool:
    return deps.config.skills.usage_tracking_enabled


def is_agent_created(name: str, deps: CoDeps) -> bool:
    """Return True iff <name>.md exists under user_skills_dir.

    Co-cli has no URL-install path; every user skill is treated as
    agent-created (eligible for usage tracking and curation).
    """
    skill_path = deps.user_skills_dir / f"{name}.md"
    return skill_path.exists()


def _new_record(now: str) -> dict[str, Any]:
    return {
        "version": SIDECAR_VERSION,
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


def read_record(deps: CoDeps, name: str) -> dict[str, Any] | None:
    """Load one skill's record. Returns None if absent."""
    path = _sidecar_path(deps, name)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("skill usage sidecar unreadable at %s: %s — treating as absent", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_record(deps: CoDeps, name: str, record: dict[str, Any]) -> None:
    """Atomically write a single skill's sidecar."""
    path = _sidecar_path(deps, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(record, indent=2))


def iter_records(deps: CoDeps) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (name, record) for every per-skill sidecar.

    Skips sidecars that fail to parse — they're treated as absent.
    """
    skills_dir = deps.user_skills_dir
    if not skills_dir.exists():
        return
    for path in sorted(skills_dir.glob(f"*{SIDECAR_SUFFIX}")):
        name = path.name.removesuffix(SIDECAR_SUFFIX)
        if not name:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("skill usage sidecar unreadable at %s: %s — skipping", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        yield name, data


def _bump(deps: CoDeps, name: str, counter_field: str, timestamp_field: str) -> None:
    if not _enabled(deps):
        return
    if not is_agent_created(name, deps):
        return
    try:
        now = _utcnow_iso()
        record = read_record(deps, name) or _new_record(now)
        record[counter_field] = int(record[counter_field]) + 1
        record[timestamp_field] = now
        write_record(deps, name, record)
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
        record = read_record(deps, name) or _new_record(_utcnow_iso())
        recall_days: list[str] = record["recall_days"]
        if today not in recall_days:
            recall_days.append(today)
            write_record(deps, name, record)
    except Exception as exc:
        logger.debug("skill usage bump_recall (%s) failed: %s", name, exc)


def record_create(deps: CoDeps, name: str) -> None:
    """Initialize a fresh record for a newly-created agent skill. Best-effort.

    Overwrites any existing record (matches pre-refactor behavior).
    """
    if not _enabled(deps):
        return
    if not is_agent_created(name, deps):
        return
    try:
        write_record(deps, name, _new_record(_utcnow_iso()))
    except Exception as exc:
        logger.debug("skill usage record_create (%s) failed: %s", name, exc)


def forget(deps: CoDeps, name: str) -> None:
    """Remove a skill's sidecar. Best-effort. No-op if absent."""
    if not _enabled(deps):
        return
    try:
        path = _sidecar_path(deps, name)
        path.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("skill usage forget (%s) failed: %s", name, exc)


def set_pinned(deps: CoDeps, name: str, pinned: bool) -> None:
    """Flip pinned flag for a skill. Creates a stub record if none exists.

    Not best-effort: callers (CLI) expect a definitive success/failure signal,
    so I/O errors propagate.
    """
    record = read_record(deps, name) or _new_record(_utcnow_iso())
    record["pinned"] = bool(pinned)
    write_record(deps, name, record)
