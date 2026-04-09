"""Session persistence for co-cli chat sessions.

Sessions are stored as JSON in .co-cli/sessions/{session-id}.json (mode 0o600).
A session tracks its ID, creation time, last-used time, and compaction count.
Sessions persist indefinitely — a new session is created only via /new.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_valid_uuid(value: str) -> bool:
    """Return True if value is a well-formed UUID string (path-traversal guard)."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def new_session() -> dict:
    """Create a new session dict with a fresh UUID and current timestamps."""
    now = datetime.now(UTC).isoformat()
    return {
        "session_id": str(uuid.uuid4()),
        "created_at": now,
        "last_used_at": now,
        "compaction_count": 0,
    }


def load_session(path: Path) -> dict | None:
    """Load a session from path. Returns None if file is missing or unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_session(sessions_dir: Path, session: dict) -> None:
    """Save session dict to sessions_dir/{session_id}.json with mode 0o600."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{session['session_id']}.json"
    path.write_text(json.dumps(session, indent=2), encoding="utf-8")
    path.chmod(0o600)


def find_latest_session(sessions_dir: Path) -> dict | None:
    """Find and load the most recent session by file mtime in sessions_dir.

    Returns the session dict, or None if no valid session files exist.
    """
    if not sessions_dir.exists():
        return None
    json_files = list(sessions_dir.glob("*.json"))
    if not json_files:
        return None
    # Sort by mtime descending — most recent first
    json_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in json_files:
        session = load_session(path)
        if session and _is_valid_uuid(session.get("session_id", "")):
            return session
    return None


def touch_session(session: dict) -> dict:
    """Return a new session dict with last_used_at updated to now.

    Does not mutate the input dict.
    """
    return {**session, "last_used_at": datetime.now(UTC).isoformat()}


def increment_compaction(session: dict) -> dict:
    """Return a new session dict with compaction_count incremented by 1.

    Does not mutate the input dict.
    """
    return {**session, "compaction_count": session.get("compaction_count", 0) + 1}
