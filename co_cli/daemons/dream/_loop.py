"""Dream daemon main event loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.config.core import DREAM_DAEMON_DIR, DREAM_RUN_TAG
from co_cli.config.dream import DreamSettings
from co_cli.daemons.dream._queue import (
    list_queue_files,
    move_to_done,
    move_to_failed,
    read_queue_item,
    write_queue_item,
)
from co_cli.daemons.dream._state import (
    DaemonState,
    HousekeepingState,
    load_housekeeping_state,
)

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)


def scheduled_tick_due(state: HousekeepingState, cfg: DreamSettings) -> bool:
    """Return True when the next housekeeping pass is due.

    Cadence: at least ``cfg.run_interval_hours`` since the last pass, then
    clamped to the next ``cfg.run_at`` time-of-day boundary in local time.
    Never-run state (``last_housekeeping_at is None``) returns True so a
    fresh daemon gets a baseline pass on the first idle tick.
    """
    if state.last_housekeeping_at is None:
        return True
    last_utc = datetime.fromisoformat(state.last_housekeeping_at)
    if last_utc.tzinfo is None:
        last_utc = last_utc.replace(tzinfo=UTC)
    now_local = datetime.now().astimezone()
    earliest = (last_utc + timedelta(hours=cfg.run_interval_hours)).astimezone()
    if now_local < earliest:
        return False
    hh, mm = (int(x) for x in cfg.run_at.split(":"))
    target = earliest.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target < earliest:
        target += timedelta(days=1)
    return now_local >= target


async def _maybe_housekeep(deps: CoDeps, cfg: DreamSettings) -> None:
    """Manual-trigger first, then scheduled-tick. Runs on empty-queue branch only."""
    from co_cli.daemons.dream._housekeeping import run_housekeeping

    state = load_housekeeping_state(DREAM_DAEMON_DIR)
    if DREAM_RUN_TAG.exists():
        DREAM_RUN_TAG.unlink(missing_ok=True)
        await run_housekeeping(deps, cfg, state)
        return
    if scheduled_tick_due(state, cfg):
        await run_housekeeping(deps, cfg, state)


async def main_loop(
    deps,
    queue_dir: Path,
    state: DaemonState,
    cfg,
    shutdown: asyncio.Event,
) -> None:
    """Process queued KICK files in FIFO order; idle-poll when empty; exit on shutdown.

    The shutdown event is owned by the caller (_run_foreground) so signal handlers
    can be registered before bootstrap. Every sleep point — idle poll and retry
    backoff — uses asyncio.wait_for(shutdown.wait(), ...) so SIGTERM wakes the
    loop immediately rather than after the timeout. Cold-start drain happens
    implicitly: the first iterations see pending files and process them before
    reaching any sleep.
    """
    while not shutdown.is_set():
        files = list_queue_files(queue_dir)
        if not files:
            await _maybe_housekeep(deps, cfg)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown.wait(), timeout=cfg.poll_interval_seconds)
            continue

        item_path = files[0]
        try:
            payload = read_queue_item(item_path)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read queue item %s: %s", item_path.name, exc)
            move_to_failed(item_path, queue_dir.parent / "failed", str(exc))
            continue

        try:
            async with asyncio.timeout(cfg.review_timeout_seconds):
                await _process_kick_file(deps, item_path, payload, state)
            move_to_done(item_path, queue_dir / "done")
        except Exception as exc:
            attempts = payload.get("attempts", 0) + 1
            payload["attempts"] = attempts
            write_queue_item(item_path, payload)
            if attempts >= cfg.max_retry_attempts:
                move_to_failed(item_path, queue_dir / "failed", str(exc))
            else:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(shutdown.wait(), timeout=cfg.retry_backoff_seconds)

    logger.info("Dream daemon shutting down")


async def _process_kick_file(deps, path: Path, payload: dict, state: DaemonState) -> None:
    """Dispatch a review for an already-loaded queue payload."""
    state.current_item = path.name
    try:
        domain = payload["domain"]
        session_id = payload["session_id"]
        persisted_message_count = payload["persisted_message_count"]
        await _process_review(deps, domain, session_id, persisted_message_count)
    finally:
        state.current_item = None


async def _process_review(
    deps,
    domain: str,
    session_id: str,
    persisted_message_count: int,
) -> None:
    """Route a review job to the appropriate domain reviewer."""
    from co_cli.daemons.dream._reviewer import process_review

    await process_review(deps, domain, session_id, persisted_message_count)
