"""Session persistence for co-cli chat sessions.

Sessions are stored as JSON in .co-cli/session.json (mode 0o600).
A session tracks its ID, creation time, last-used time, and compaction count.
Session IDs are restored across restarts when the session is still fresh
(last_used_at within ttl_minutes).
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


def new_session() -> dict:
    """Create a new session dict with a fresh UUID and current timestamps."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "session_id": uuid.uuid4().hex,
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


def save_session(path: Path, session: dict) -> None:
    """Save session dict to path with mode 0o600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2), encoding="utf-8")
    path.chmod(0o600)


def is_fresh(session: dict | None, ttl_minutes: int) -> bool:
    """Return True when the session is non-None and last_used_at is within ttl_minutes.

    Future-dated timestamps return True (clock-skew guard).
    """
    if session is None:
        return False
    last_used = session.get("last_used_at", "")
    if not last_used:
        return False
    try:
        last_dt = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        # Future-dated → treat as fresh (clock skew)
        if last_dt > now:
            return True
        elapsed_minutes = (now - last_dt).total_seconds() / 60
        return elapsed_minutes <= ttl_minutes
    except Exception:
        return False


def touch_session(session: dict) -> dict:
    """Return a new session dict with last_used_at updated to now.

    Does not mutate the input dict.
    """
    return {**session, "last_used_at": datetime.now(timezone.utc).isoformat()}


def increment_compaction(session: dict) -> dict:
    """Return a new session dict with compaction_count incremented by 1.

    Does not mutate the input dict.
    """
    return {**session, "compaction_count": session.get("compaction_count", 0) + 1}
