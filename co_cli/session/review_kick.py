"""Shared producer for dream-daemon review KICK files.

A KICK is a small JSON file dropped into ``DREAM_QUEUE_DIR`` that asks the daemon
to run a domain review (memory or skill). The filesystem queue is the sole
cross-process bridge; the producer never touches the daemon's address space.

Both the REPL (``main.py``) and compaction (``context/compaction.py``) produce
KICKs through ``write_review_kick`` so the payload shape stays in one place and
neither caller reaches into the other's module. This module imports only config
constants and the atomic writer — nothing from ``context/`` — so it cannot form
an import cycle with the compaction path.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from co_cli.config.core import DREAM_QUEUE_DIR
from co_cli.fileio.atomic import atomic_write_text


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
