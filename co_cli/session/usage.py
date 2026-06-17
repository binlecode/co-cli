"""Durable per-turn token-usage accounting — append-only ledger.

At each turn boundary the turn's totals (drained from the realtime
``UsageAccumulator`` in ``co_cli/observability/usage.py``) are appended as one
JSON line to ``~/.co-cli/usage.jsonl``.

This module is **write-only durable accounting**: it MUST NOT feed compaction
triggers or the status-line context-% (those stay on the realtime
``current_request_tokens_estimate``). Usage capture is observational, never a
control input.

All ledger I/O is **best-effort** (mirrors ``skills/usage.py``): exceptions are
logged and swallowed so usage tracking never blocks or fails a turn.

Cross-process append safety: the session process and the dream daemon process both
append to the same ``usage.jsonl``. Each line is well under ``PIPE_BUF`` (4096 B)
and writes use ``O_APPEND`` (``open(path, "a")``), so POSIX guarantees atomic,
non-interleaved appends across processes — consistent with the append-only,
no-read-modify-write design.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ORIGIN_SESSION = "session"
ORIGIN_DAEMON = "daemon"


@dataclass(frozen=True)
class UsageTotals:
    """Input/output token totals for one origin bucket."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class UsageWindow:
    """Aggregated ledger totals split by origin.

    Daemon usage is counted in ``total`` but never folded into ``session``.
    ``session_count`` is the distinct session-origin session_id count, populated
    for windowed reads so a window can be read as one-heavy vs many-light sessions.
    """

    session: UsageTotals
    daemon: UsageTotals
    total: UsageTotals
    session_count: int = 0


def append_turn(
    ledger_path: Path,
    *,
    origin: str,
    session_id: str | None,
    input_tokens: int,
    output_tokens: int,
    turn_ended_at: datetime,
) -> None:
    """Best-effort append of one ledger line. No-op when both token counts are 0.

    ``origin`` is ``"session"`` or ``"daemon"`` — it determines how the line is
    bucketed in windowed reporting. ``session_id`` is the active short id for
    session lines and ``None`` for daemon lines.
    """
    if input_tokens == 0 and output_tokens == 0:
        return
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "turn_ended_at": turn_ended_at.isoformat(),
            "origin": origin,
            "session_id": session_id,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
        }
        with open(ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.debug("append_turn failed: %s", exc)


def aggregate(
    ledger_path: Path,
    *,
    since: datetime | None = None,
    session_id: str | None = None,
    origin: str | None = None,
) -> UsageWindow:
    """Stream the ledger and sum usage, split by origin.

    Filters: ``since`` (rolling-window cutoff — keep lines at or after it),
    ``session_id`` (keep only that session's lines), ``origin`` (keep only that
    origin's lines). Malformed or incomplete lines are skipped. Daemon-origin lines
    are summed into the daemon subtotal and the combined total, never the session
    subtotal. ``session_count`` reflects distinct session-origin session_ids only.
    """
    session_in = session_out = 0
    daemon_in = daemon_out = 0
    session_ids: set[str] = set()

    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            turn_ended_at = datetime.fromisoformat(record["turn_ended_at"])
            record_origin = record.get("origin", ORIGIN_SESSION)
            record_session = record.get("session_id")
            input_tokens = int(record["input_tokens"])
            output_tokens = int(record["output_tokens"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            continue

        if since is not None and turn_ended_at < since:
            continue
        if origin is not None and record_origin != origin:
            continue
        if session_id is not None and record_session != session_id:
            continue

        if record_origin == ORIGIN_DAEMON:
            daemon_in += input_tokens
            daemon_out += output_tokens
        else:
            session_in += input_tokens
            session_out += output_tokens
            if record_session:
                session_ids.add(record_session)

    session = UsageTotals(session_in, session_out)
    daemon = UsageTotals(daemon_in, daemon_out)
    total = UsageTotals(session_in + daemon_in, session_out + daemon_out)
    return UsageWindow(session=session, daemon=daemon, total=total, session_count=len(session_ids))
