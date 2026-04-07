"""Session browser — listing, title extraction, and summary for UI.

Lightweight session metadata for listing/picker display. No transcript
content loading — delegates to ``_transcript.py`` for I/O primitives.

Public API:
    SessionSummary    — frozen dataclass for listing UI
    list_sessions     — list past sessions by mtime descending
    format_file_size  — human-readable byte size
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionSummary:
    """Lightweight summary of a past session for listing/picker UI."""

    session_id: str
    title: str
    last_modified: datetime
    file_size: int


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
                if isinstance(data, list):
                    for msg in data:
                        for part in msg.get("parts", []):
                            if part.get("part_kind") == "user-prompt":
                                content = part.get("content", "")
                                if content:
                                    return content[:80] + ("..." if len(content) > 80 else "")
            except (json.JSONDecodeError, KeyError, TypeError):
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
    """List past sessions with title extraction, sorted by mtime descending.

    Title is extracted from the first 4KB of the .jsonl file (head read only).
    File size comes from stat — no full file scan. Matches fork-claude-code's
    lite listing pattern (stat-based, no content parsing for listing).
    """
    if not sessions_dir.exists():
        return []
    jsonl_files = list(sessions_dir.glob("*.jsonl"))
    if not jsonl_files:
        return []

    # Sort by mtime descending (most recent first)
    jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    summaries: list[SessionSummary] = []
    for path in jsonl_files:
        session_id = path.stem
        title = _extract_title(path)
        try:
            st = path.stat()
            last_modified = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        except OSError:
            continue
        summaries.append(SessionSummary(
            session_id=session_id,
            title=title,
            last_modified=last_modified,
            file_size=st.st_size,
        ))
    return summaries
