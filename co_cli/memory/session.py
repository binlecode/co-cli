"""Session filename helpers for co-cli chat sessions.

Session files follow the naming format YYYY-MM-DD-THHMMSSZ-{uuid8}.jsonl —
lexicographically sortable, human-readable, and self-describing. The display
short ID is the 8-char UUID suffix embedded in the filename stem.
"""

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMESTAMP_FORMAT = "%Y-%m-%d-T%H%M%SZ"
# Length of the timestamp prefix: "YYYY-MM-DD-THHMMSSZ" = 19 chars
_TIMESTAMP_LEN = 19


def session_filename(created_at: datetime, session_id: str) -> str:
    """Build the canonical session filename from creation time and UUID.

    Format: YYYY-MM-DD-THHMMSSZ-{uuid8}.jsonl
    Example: 2026-04-11-T142305Z-550e8400.jsonl
    """
    ts = created_at.strftime(_TIMESTAMP_FORMAT)
    return f"{ts}-{session_id[:8]}.jsonl"


def parse_session_filename(name: str) -> tuple[str, datetime] | None:
    """Parse a session filename into (uuid8_prefix, created_at), or None on mismatch.

    Accepts filenames with or without the .jsonl extension.
    """
    stem = name.removesuffix(".jsonl")
    # Expected stem: 19 (timestamp) + 1 (dash) + 8 (uuid8) = 28 chars
    if len(stem) != _TIMESTAMP_LEN + 1 + 8:
        return None
    if stem[_TIMESTAMP_LEN] != "-":
        return None
    ts_part = stem[:_TIMESTAMP_LEN]
    uuid8 = stem[_TIMESTAMP_LEN + 1 :]
    try:
        created_at = datetime.strptime(ts_part, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    return uuid8, created_at


def find_latest_session(sessions_dir: Path) -> Path | None:
    """Return the most recent session Path by lexicographic sort (= chronological order).

    New-format filenames sort lexicographically = chronologically. No stat() needed.
    Returns None if no session files exist or the directory does not exist.
    """
    if not sessions_dir.exists():
        return None
    files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)
    return files[0] if files else None


def new_session_path(sessions_dir: Path) -> Path:
    """Return a new session Path without creating the file.

    The JSONL file is created on the first append_transcript call.
    """
    now = datetime.now(UTC)
    session_id = str(uuid.uuid4())
    name = session_filename(now, session_id)
    return sessions_dir / name
