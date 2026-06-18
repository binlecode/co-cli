"""Producer side of the dream-daemon filesystem queue.

The dream daemon runs as a separate process and consumes two kinds of files from
``DREAM_DAEMON_DIR``:

- **KICK files** (``DREAM_QUEUE_DIR``) — small JSON requests asking the daemon to
  run a domain review (memory or skill).
- **Snapshots** (``DREAM_SNAPSHOTS_DIR``) — immutable JSONL copies of pre-compaction
  messages a KICK can point the daemon at, so it reviews full-fidelity content the
  live transcript has since rewritten away.

This module is the sole producer of both. The filesystem queue is the only
cross-process bridge; the producer never touches the daemon's address space.
Both the REPL (``main.py``) and compaction (``context/compaction.py``) write through
here so the payload shapes stay in one place. It is foundational — it imports only
``config`` + ``fileio`` (+ pydantic message serialization) — so any package may depend
on it downward without forming a cycle.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from co_cli.config.core import DREAM_QUEUE_DIR, DREAM_SNAPSHOTS_DIR
from co_cli.fileio.atomic import atomic_write_text


def write_dream_snapshot(session_id: str, messages: list[ModelMessage]) -> Path:
    """Persist pre-compaction messages to an immutable dream snapshot; return its path.

    Serialized as JSONL (one ModelMessage per line, via ModelMessagesTypeAdapter) so the
    daemon reads it with the same loader it uses for live transcripts. The caller passes
    the returned path to ``write_review_kick`` as ``transcript_override``.
    """
    DREAM_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S.%f")
    snapshot_path = DREAM_SNAPSHOTS_DIR / f"{session_id}-{ts}-{uuid.uuid4()}.jsonl"
    payload = "".join(
        ModelMessagesTypeAdapter.dump_json([msg]).decode("utf-8") + "\n" for msg in messages
    )
    fd = os.open(snapshot_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(payload)
    return snapshot_path


def write_review_kick(
    *,
    domain: str,
    session_id: str,
    persisted_message_count: int | None,
    transcript_override: str | None = None,
) -> None:
    """Atomically write a review KICK file to the dream queue.

    Fire-and-forget against the filesystem. The daemon picks up the file on its
    next polling iteration.

    ``transcript_override``, when set, names a snapshot file the daemon reads
    instead of the live session transcript (uncapped) — used by compaction to
    preserve the pre-rewrite content at full fidelity. The key is omitted from
    the payload when None, so existing consumers that never read it are unaffected.
    """
    created_at = datetime.now(UTC).isoformat()
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S.%f")
    kick_path = DREAM_QUEUE_DIR / f"{ts}-{uuid.uuid4()}.json"
    payload: dict = {
        "domain": domain,
        "session_id": session_id,
        "persisted_message_count": persisted_message_count,
        "created_at": created_at,
    }
    if transcript_override is not None:
        payload["transcript_override"] = transcript_override
    atomic_write_text(kick_path, json.dumps(payload))
