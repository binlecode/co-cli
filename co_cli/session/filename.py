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
_TIMESTAMP_LEN = 19


def session_filename(created_at: datetime, session_id: str) -> str:
    """Build the canonical session filename from creation time and UUID."""
    ts = created_at.strftime(_TIMESTAMP_FORMAT)
    return f"{ts}-{session_id[:8]}.jsonl"


def parse_session_filename(name: str) -> tuple[str, datetime] | None:
    """Parse a session filename into (uuid8_prefix, created_at), or None on mismatch."""
    stem = name.removesuffix(".jsonl")
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
    """Return the most recent canonically-named session by lexicographic sort.

    Only files matching the canonical ``YYYY-MM-DD-THHMMSSZ-{uuid8}.jsonl`` scheme
    are considered — lexicographic order equals chronological order only for those.
    Foreign ``.jsonl`` files (e.g. eval fixtures written into the real sessions dir)
    are skipped so they never get restored into a live chat.
    """
    if not sessions_dir.exists():
        return None
    files = sorted(
        (p for p in sessions_dir.glob("*.jsonl") if parse_session_filename(p.name) is not None),
        reverse=True,
    )
    return files[0] if files else None


def new_session_path(sessions_dir: Path) -> Path:
    """Return a new session Path without creating the file."""
    now = datetime.now(UTC)
    session_id = str(uuid.uuid4())
    name = session_filename(now, session_id)
    return sessions_dir / name
