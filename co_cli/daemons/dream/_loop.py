"""Dream daemon main event loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from co_cli.daemons.dream._queue import (
    list_queue_files,
    move_to_done,
    move_to_failed,
    read_queue_item,
    write_queue_item,
)
from co_cli.daemons.dream._state import DaemonState

logger = logging.getLogger(__name__)


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
