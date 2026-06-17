"""Daemon runtime state and PID-file helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from co_cli.fileio.atomic import atomic_write_text

logger = logging.getLogger(__name__)

_HOUSEKEEPING_STATE_FILENAME = "_dream_state.json"


@dataclass
class DaemonState:
    """Mutable runtime state for the dream daemon process."""

    start_time: float
    spawn_origin: str
    spawn_session_id: str
    current_item: str | None = field(default=None)


def load_pid_state(pid_file: Path) -> dict:
    """Read and return the JSON payload stored in the PID file.

    Returns an empty dict if the file is absent or contains invalid JSON.
    """
    if not pid_file.exists():
        return {}
    try:
        return json.loads(pid_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


class HousekeepingStats(BaseModel):
    """Cumulative counters across every housekeeping pass."""

    memory_merged: int = 0
    memory_decayed: int = 0
    skill_merged: int = 0
    skill_decayed: int = 0
    done_pruned: int = 0
    session_pruned: int = 0


class HousekeepingState(BaseModel):
    """Persisted housekeeping state at DREAM_DAEMON_DIR/_dream_state.json.

    Distinct from the in-memory DaemonState — survives daemon restarts and
    drives the scheduled-tick cadence (last_housekeeping_at + run_interval_hours).
    """

    last_housekeeping_at: str | None = None
    stats: HousekeepingStats = Field(default_factory=HousekeepingStats)


def housekeeping_state_path(daemon_dir: Path) -> Path:
    """Canonical path for the persisted housekeeping state JSON file."""
    return daemon_dir / _HOUSEKEEPING_STATE_FILENAME


def load_housekeeping_state(daemon_dir: Path) -> HousekeepingState:
    """Load housekeeping state from disk; return a fresh instance on miss/corruption."""
    path = housekeeping_state_path(daemon_dir)
    if not path.exists():
        return HousekeepingState()
    try:
        return HousekeepingState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("load_housekeeping_state: ignoring corrupt state at %s: %s", path, exc)
        return HousekeepingState()


def save_housekeeping_state(daemon_dir: Path, state: HousekeepingState) -> None:
    """Atomically persist housekeeping state as JSON."""
    path = housekeeping_state_path(daemon_dir)
    atomic_write_text(path, json.dumps(state.model_dump(), indent=2))
