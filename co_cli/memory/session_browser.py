"""Session browser — listing, title extraction, and summary for UI.

Lightweight session metadata for listing/picker display. No transcript
content loading — delegates to ``transcript.py`` for I/O primitives.

Public API:
    SessionSummary    — frozen dataclass for listing UI
    list_sessions     — list past sessions by filename descending (= chronological)
    format_file_size  — human-readable byte size
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from co_cli.memory.session import parse_session_filename

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionSummary:
    """Lightweight summary of a past session for listing/picker UI."""

    session_id: str
    path: Path
    title: str
    last_modified: datetime
    file_size: int
    created_at: datetime


def _extract_title(path: Path, max_bytes: int = 4096) -> str:
    """Extract the first user-prompt content from a JSONL transcript head.

    Reads at most max_bytes to avoid loading full transcripts.
    """
    try:
        raw = path.read_bytes()[:max_bytes].decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if not isinstance(data, list) or not data:
                    continue
                msg = data[0]
                if not isinstance(msg, dict):
                    continue
                for part in msg.get("parts", []):
                    if part.get("part_kind") == "user-prompt":
                        content = part.get("content", "")
                        if content:
                            return content[:80] + ("..." if len(content) > 80 else "")
            except (ValueError, KeyError, TypeError):
                continue
    except OSError:
        pass
    return "(untitled)"


def format_file_size(size: int) -> str:
    """Format byte size as human-readable string."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def list_sessions(sessions_dir: Path) -> list[SessionSummary]:
    """List past sessions sorted by filename descending (= most recent first).

    Session files use the format YYYY-MM-DD-THHMMSSZ-{uuid8}.jsonl.
    session_id is the 8-char UUID suffix; created_at is parsed from the filename prefix.
    Files that do not match this format are skipped.

    Title is extracted from the first 4KB of the JSONL file (head read only).
    File size and last_modified come from stat — no full file scan.
    """
    if not sessions_dir.exists():
        return []
    jsonl_files = list(sessions_dir.glob("*.jsonl"))
    if not jsonl_files:
        return []

    # Lexicographic sort descending (filename format = chronological order)
    jsonl_files.sort(key=lambda p: p.name, reverse=True)

    summaries: list[SessionSummary] = []
    for path in jsonl_files:
        parsed = parse_session_filename(path.name)
        if parsed is None:
            continue
        session_id, created_at = parsed[0], parsed[1]
        title = _extract_title(path)
        try:
            st = path.stat()
            last_modified = datetime.fromtimestamp(st.st_mtime, tz=UTC)
        except OSError:
            continue
        summaries.append(
            SessionSummary(
                session_id=session_id,
                path=path,
                title=title,
                last_modified=last_modified,
                file_size=st.st_size,
                created_at=created_at,
            )
        )
    return summaries
