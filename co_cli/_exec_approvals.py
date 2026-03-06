"""Persistent exec-approval storage for run_shell_command.

Approvals are stored as JSON in .co-cli/exec-approvals.json (mode 0o600).
Each entry has a pattern (fnmatch-based) that is matched against the command.
"""

import fnmatch
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


def load_approvals(path: Path) -> list[dict]:
    """Load exec approvals from JSON file. Returns [] if file is missing or unreadable."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_approvals(path: Path, entries: list[dict]) -> None:
    """Save exec approvals to JSON file with mode 0o600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    path.chmod(0o600)


def derive_pattern(cmd: str) -> str:
    """Derive an approval pattern from a command.

    Collects consecutive non-flag tokens from the start (up to 3),
    then appends " *". Never produces a bare "*".
    """
    tokens = cmd.split()
    non_flag: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            break
        non_flag.append(token)
        if len(non_flag) >= 3:
            break
    if not non_flag:
        # All tokens are flags — use the raw command as base
        non_flag = tokens[:1] if tokens else [cmd]
    return " ".join(non_flag) + " *"


def find_approved(cmd: str, entries: list[dict]) -> dict | None:
    """Find a matching approved entry for cmd.

    Skips entries where pattern == "*" (safety guard against catch-all approvals).
    Returns the first matching entry or None.
    """
    for entry in entries:
        pattern = entry.get("pattern", "")
        # Safety guard: bare wildcard catch-all is never auto-approved
        if pattern == "*":
            continue
        if fnmatch.fnmatch(cmd, pattern):
            return entry
    return None


def add_approval(path: Path, cmd: str, tool_name: str) -> None:
    """Derive a pattern for cmd and append a new approval entry."""
    entries = load_approvals(path)
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "id": uuid.uuid4().hex[:8],
        "pattern": derive_pattern(cmd),
        "tool_name": tool_name,
        "created_at": now,
        "last_used_at": now,
    }
    entries.append(entry)
    save_approvals(path, entries)


def update_last_used(path: Path, entry_id: str) -> None:
    """Update last_used_at for the entry with the given id."""
    entries = load_approvals(path)
    for entry in entries:
        if entry.get("id") == entry_id:
            entry["last_used_at"] = datetime.now(timezone.utc).isoformat()
            break
    save_approvals(path, entries)


def prune_stale(path: Path, max_age_days: int) -> None:
    """Remove entries whose last_used_at is older than max_age_days."""
    entries = load_approvals(path)
    if not entries:
        return
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
    fresh: list[dict] = []
    for entry in entries:
        last_used = entry.get("last_used_at") or entry.get("created_at", "")
        try:
            ts = datetime.fromisoformat(last_used.replace("Z", "+00:00")).timestamp()
            if ts >= cutoff:
                fresh.append(entry)
        except Exception:
            # Unparseable timestamp — keep the entry to avoid silent data loss
            fresh.append(entry)
    if len(fresh) != len(entries):
        save_approvals(path, fresh)
