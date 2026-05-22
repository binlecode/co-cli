"""Daemon runtime state and PID-file helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


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
