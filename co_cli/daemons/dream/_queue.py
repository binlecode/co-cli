"""Queue file helpers for the dream daemon."""

from __future__ import annotations

import json
import os
from pathlib import Path

from co_cli.fileio.atomic import atomic_write_text


def read_queue_item(path: Path) -> dict:
    """Read and return the JSON payload from a queue file."""
    return json.loads(path.read_text())


def write_queue_item(path: Path, payload: dict) -> None:
    """Atomically write JSON payload to a queue file.

    Routes through the shared atomic_write_text primitive so a crash mid-write
    never leaves a truncated queue file. Its tempfile carries a .tmp suffix and
    list_queue_files globs only *.json, so in-flight writes are invisible to the
    daemon scanner.
    """
    atomic_write_text(path, json.dumps(payload))


def list_queue_files(queue_dir: Path) -> list[Path]:
    """Return a sorted list of *.json files in queue_dir, skipping *.tmp files."""
    if not queue_dir.exists():
        return []
    return sorted(queue_dir.glob("*.json"))


def move_to_done(path: Path, done_dir: Path) -> None:
    """Move a queue file to the done directory."""
    done_dir.mkdir(parents=True, exist_ok=True)
    os.replace(path, done_dir / path.name)


def move_to_failed(path: Path, failed_dir: Path, last_error: str) -> None:
    """Add last_error to the queue file payload and move it to the failed directory."""
    failed_dir.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        payload = {}
    payload["last_error"] = last_error
    write_queue_item(path, payload)
    os.replace(path, failed_dir / path.name)
