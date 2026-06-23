"""Flow tests for the /status slash command — consolidated current-state report.

The command is driven through ``dispatch`` with rendered output captured from the
shared console. Assertions are on observable behavior — the printed report and
the (un)touched CO_HOME — never on internal structure. No LLM, no model call.
"""

from __future__ import annotations

import importlib
import os
from collections import deque
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, LocalOnly
from co_cli.deps import ApprovalKindEnum, CoDeps, CoSessionState, SessionApprovalRule
from co_cli.display.core import console
from co_cli.session.filename import session_filename
from co_cli.tools.background import BackgroundTaskState
from co_cli.tools.shell_backend import ShellBackend

_SESSION_ID = "abcd1234"


def _make_deps(tmp_path: Path, *, dream_autostart: bool = False) -> CoDeps:
    session_name = session_filename(datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC), _SESSION_ID)
    session_path = tmp_path / "sessions" / session_name
    config = SETTINGS
    if dream_autostart:
        config = SETTINGS.model_copy(
            update={"dream": SETTINGS.dream.model_copy(update={"autostart": True})}
        )
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        session=CoSessionState(session_path=session_path),
        sessions_dir=tmp_path / "sessions",
        usage_log_path=tmp_path / "usage.jsonl",
    )


def _make_ctx(deps: CoDeps, queue: deque[str] | None = None) -> CommandContext:
    return CommandContext(
        message_history=[],
        deps=deps,
        agent=None,  # type: ignore[arg-type]  # not needed for dispatch tests
        completer=None,
        input_queue=queue,
    )


@pytest.fixture
def co_home(tmp_path: Path) -> Generator[Path, None, None]:
    """Point CO_HOME at an isolated temp dir and reload the modules that cache it."""
    original = os.environ.get("CO_HOME")
    co_home = tmp_path / "co-home"
    co_home.mkdir()
    os.environ["CO_HOME"] = str(co_home)
    import co_cli.config.core as core_mod
    import co_cli.daemons.dream.process as process_mod

    importlib.reload(core_mod)
    importlib.reload(process_mod)
    try:
        yield co_home
    finally:
        if original is None:
            os.environ.pop("CO_HOME", None)
        else:
            os.environ["CO_HOME"] = original
        importlib.reload(core_mod)
        importlib.reload(process_mod)


def _snapshot(root: Path) -> set[Path]:
    return set(root.rglob("*"))


@pytest.mark.asyncio
@pytest.mark.usefixtures("co_home")
async def test_report_shows_all_sections_with_live_values(tmp_path: Path) -> None:
    """/status prints session id, model, context %, dream state, and in-flight counts."""
    deps = _make_deps(tmp_path)
    deps.runtime.current_request_tokens_estimate = 5_000
    deps.session.background_tasks["t1"] = BackgroundTaskState(
        task_id="t1", command="sleep 9", cwd=".", description="bg", status="running"
    )
    deps.session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
    )
    ctx = _make_ctx(deps, deque(["queued one"]))

    with console.capture() as cap:
        outcome = await dispatch("/status", ctx)
    text = cap.get()

    assert isinstance(outcome, LocalOnly)
    # Session id (last 8 of the stem).
    assert _SESSION_ID in text
    # Model line reflects the configured provider/model.
    assert deps.config.llm.provider in text
    # Context % rendered from the live estimate (5,000 / model_max_context_tokens).
    assert "%" in text
    assert "5,000" in text
    # Dream state present; no daemon in this isolated CO_HOME -> not running.
    assert "not running" in text
    # In-flight counts: 1 running background task, 1 approval, 1 queued input.
    assert "1 running" in text
    assert "active (1 background)" in text


@pytest.mark.asyncio
@pytest.mark.usefixtures("co_home")
async def test_reflects_state_between_invocations(tmp_path: Path) -> None:
    """Mutating the context estimate and queue depth changes the rendered report."""
    deps = _make_deps(tmp_path)
    queue: deque[str] = deque()
    ctx = _make_ctx(deps, queue)

    with console.capture() as cap:
        await dispatch("/status", ctx)
    first = cap.get()

    deps.runtime.current_request_tokens_estimate = 12_345
    queue.append("now queued")

    with console.capture() as cap:
        await dispatch("/status", ctx)
    second = cap.get()

    assert first != second
    # The new context estimate surfaces only in the second report.
    assert "12,345" not in first
    assert "12,345" in second


@pytest.mark.asyncio
async def test_read_only_creates_no_files(co_home: Path, tmp_path: Path) -> None:
    """Invoking /status writes nothing under CO_HOME (read-only contract)."""
    deps = _make_deps(tmp_path, dream_autostart=True)
    ctx = _make_ctx(deps, deque())

    before = _snapshot(co_home)
    with console.capture():
        outcome = await dispatch("/status", ctx)
    after = _snapshot(co_home)

    assert isinstance(outcome, LocalOnly)
    assert before == after


@pytest.mark.asyncio
@pytest.mark.usefixtures("co_home")
async def test_degrades_when_sources_absent(tmp_path: Path) -> None:
    """With dream state and usage ledger absent, /status still prints with placeholders."""
    deps = _make_deps(tmp_path, dream_autostart=True)
    ctx = _make_ctx(deps, deque())

    with console.capture() as cap:
        outcome = await dispatch("/status", ctx)
    text = cap.get()

    assert isinstance(outcome, LocalOnly)
    # Status is runtime-only: no daemon -> "not running" regardless of autostart;
    # never-housekept since no state file exists.
    assert "not running" in text
    assert "never" in text
    # Usage ledger absent -> session tokens degrade to zero, not a crash.
    assert "session tokens" in text
    assert "0  (0 in / 0 out)" in text


@pytest.mark.asyncio
async def test_reports_running_daemon_regardless_of_autostart(
    co_home: Path, tmp_path: Path
) -> None:
    """A daemon started by any session (or /dream start) runs while dream.autostart=False;
    /status reflects the live pidfile and reports it running, matching what /dream shows."""
    import co_cli.daemons.dream.process as process_mod
    from co_cli.daemons.dream._process import write_pid

    deps = _make_deps(tmp_path)
    assert deps.config.dream.autostart is False
    write_pid(process_mod.DREAM_PID_FILE, os.getpid(), "slash", "")
    ctx = _make_ctx(deps, deque())

    with console.capture() as cap:
        outcome = await dispatch("/status", ctx)
    text = cap.get()

    assert isinstance(outcome, LocalOnly)
    # The live daemon -> dream state row reads "running", never the "not running" branch.
    assert "running" in text
    assert "not running" not in text
