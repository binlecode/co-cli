"""Defect-B consumer side: the daemon reads the override snapshot and cleans it up.

A KICK carrying ``transcript_override`` makes the reviewer read that snapshot file
(uncapped) instead of the live session, so the pre-compaction turns are reviewed
at full fidelity. The snapshot is unlinked on the terminal (done) transition.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._settings import SETTINGS_NO_MCP

from co_cli.config.dream import DreamSettings
from co_cli.daemons.dream import _loop
from co_cli.daemons.dream._queue import write_queue_item
from co_cli.daemons.dream.state import DaemonState
from co_cli.deps import CoDeps
from co_cli.session.persistence import append_messages
from co_cli.tools.shell_backend import ShellBackend

_UNIQUE_FACT = "the staging cluster lives in us-west-2"


def _make_deps(tmp_path: Path) -> CoDeps:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return CoDeps(shell=ShellBackend(), config=SETTINGS_NO_MCP, sessions_dir=sessions_dir)


@pytest.mark.asyncio
async def test_override_snapshot_is_read_then_cleaned_up(monkeypatch, tmp_path: Path) -> None:
    """The reviewer's transcript contains the snapshot's fact; the snapshot is unlinked."""
    snapshot = tmp_path / "snap.jsonl"
    append_messages(
        snapshot,
        [
            ModelRequest(parts=[UserPromptPart(content=f"durable: {_UNIQUE_FACT}")]),
            ModelResponse(parts=[TextPart(content="acknowledged")]),
        ],
    )

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    done_dir = tmp_path / "done"
    failed_dir = tmp_path / "failed"
    kick_path = queue_dir / "2024-01-01T00-00-00.json"
    write_queue_item(
        kick_path,
        {
            "domain": "memory",
            "session_id": "sess-x",
            "persisted_message_count": None,
            "transcript_override": str(snapshot),
            "attempts": 0,
        },
    )

    captured: dict = {}

    async def _fake_run_standalone(spec, deps, prompt):
        captured["prompt"] = prompt

    monkeypatch.setattr("co_cli.agent.run.run_standalone", _fake_run_standalone)

    deps = _make_deps(tmp_path)
    state = DaemonState(start_time=time.time(), spawn_origin="test", spawn_session_id="s")
    cfg = DreamSettings(tick_interval_seconds=1, review_timeout_seconds=30)
    shutdown = asyncio.Event()

    async def stopper() -> None:
        moved = done_dir / kick_path.name
        for _ in range(200):
            if moved.exists():
                break
            await asyncio.sleep(0.01)
        shutdown.set()

    await asyncio.gather(
        _loop.main_loop(deps, queue_dir, done_dir, failed_dir, state, cfg, shutdown),
        stopper(),
    )

    assert "prompt" in captured, "the reviewer must have been invoked"
    assert _UNIQUE_FACT in captured["prompt"], (
        "reviewer must read the snapshot content, not a no-op"
    )
    assert not snapshot.exists(), "snapshot must be unlinked after the terminal move"
    assert (done_dir / kick_path.name).exists(), "KICK must land in done/"
