"""Dream daemon main event loop and queue drain logic."""

from __future__ import annotations

import asyncio
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
    """Run the daemon main loop: initial drain then poll-and-drain until shutdown is set.

    The shutdown event is owned by the caller (_run_foreground) so signal handlers
    can be registered before bootstrap. If shutdown is already set on entry,
    the loop exits without running any drains.
    """
    if shutdown.is_set():
        logger.info("Dream daemon: shutdown requested before main_loop start")
        return

    await _initial_drain(deps, queue_dir, cfg, state, shutdown)

    while not shutdown.is_set():
        if list_queue_files(queue_dir):
            await _drain_queue(deps, queue_dir, cfg, state, shutdown=shutdown)
            continue
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=cfg.poll_interval_seconds)
        except TimeoutError:
            pass

    logger.info("Dream daemon shutting down")


async def _initial_drain(
    deps, queue_dir: Path, cfg, state: DaemonState, shutdown: asyncio.Event
) -> None:
    """Drain the queue once at startup."""
    await _drain_queue(deps, queue_dir, cfg, state, shutdown=shutdown)


async def _drain_queue(
    deps, queue_dir: Path, cfg, state: DaemonState, *, shutdown: asyncio.Event
) -> None:
    """Process queue files one by one until the queue is empty or shutdown is requested."""
    while True:
        if shutdown.is_set():
            return
        files = list_queue_files(queue_dir)
        if not files:
            break
        item_path = files[0]
        try:
            payload = read_queue_item(item_path)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read queue item %s: %s", item_path.name, exc)
            move_to_failed(item_path, queue_dir.parent / "failed", str(exc))
            continue

        try:
            async with asyncio.timeout(cfg.review_timeout_seconds):
                await _process_kick_file(deps, item_path, state)
            move_to_done(item_path, queue_dir / "done")
        except Exception as exc:
            attempts = payload.get("attempts", 0) + 1
            payload["attempts"] = attempts
            write_queue_item(item_path, payload)
            if attempts >= cfg.max_retry_attempts:
                move_to_failed(item_path, queue_dir / "failed", str(exc))
            else:
                await asyncio.sleep(cfg.retry_backoff_seconds)


async def _process_kick_file(deps, path: Path, state: DaemonState) -> None:
    """Load a queue file and dispatch the review."""
    payload = read_queue_item(path)
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
