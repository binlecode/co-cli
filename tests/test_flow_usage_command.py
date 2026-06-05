"""Flow tests for the /usage slash command and window aggregation.

All ledger I/O is real: a temp usage.jsonl is seeded via ``append_turn`` and the
command is driven through ``dispatch`` with rendered output captured from the
shared console. Assertions are on observable behavior — the printed token figures
and the (un)mutated ledger — never on internal structure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, LocalOnly
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.core import console
from co_cli.display.headless import HeadlessFrontend
from co_cli.session.usage import append_turn
from co_cli.tools.shell_backend import ShellBackend

_CURRENT_SESSION_ID = "abcd1234"
_OTHER_SESSION_ID = "efgh5678"


def _make_deps(tmp_path: Path) -> CoDeps:
    session_path = tmp_path / "sessions" / f"2026-06-04T120000.000-{_CURRENT_SESSION_ID}.jsonl"
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(session_path=session_path),
        sessions_dir=tmp_path / "sessions",
        usage_log_path=tmp_path / "usage.jsonl",
    )


def _make_ctx(deps: CoDeps) -> CommandContext:
    return CommandContext(
        message_history=[],
        deps=deps,
        agent=None,  # type: ignore[arg-type]  # not needed for dispatch tests
        frontend=HeadlessFrontend(),
        completer=None,
    )


def _seed_ledger(deps: CoDeps) -> None:
    now = datetime.now(UTC)
    forty_days_ago = now - timedelta(days=40)
    append_turn(
        deps.usage_log_path,
        origin="session",
        session_id=_CURRENT_SESSION_ID,
        input_tokens=100,
        output_tokens=10,
        turn_ended_at=now,
    )
    append_turn(
        deps.usage_log_path,
        origin="session",
        session_id=_OTHER_SESSION_ID,
        input_tokens=500,
        output_tokens=50,
        turn_ended_at=forty_days_ago,
    )
    append_turn(
        deps.usage_log_path,
        origin="daemon",
        session_id=None,
        input_tokens=7,
        output_tokens=3,
        turn_ended_at=now,
    )


@pytest.mark.asyncio
async def test_no_arg_shows_only_current_session(tmp_path: Path) -> None:
    """/usage (no arg) reports only the current session — other-session and daemon lines excluded."""
    deps = _make_deps(tmp_path)
    _seed_ledger(deps)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        outcome = await dispatch("/usage", ctx)
    text = cap.get()

    assert isinstance(outcome, LocalOnly)
    # Current session: input 100, output 10, total 110.
    assert "100" in text
    assert "110" in text
    # The 40-day-old other-session subtotal (500/550) must not appear.
    assert "500" not in text
    assert "550" not in text
    # No daemon figures (7 / 3).
    assert "Daemon" not in text


@pytest.mark.asyncio
async def test_week_excludes_old_line_and_splits_daemon(tmp_path: Path) -> None:
    """/usage week drops the 40-day-old line; daemon tokens land in Total but not Session."""
    deps = _make_deps(tmp_path)
    _seed_ledger(deps)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        outcome = await dispatch("/usage week", ctx)
    text = cap.get()

    assert isinstance(outcome, LocalOnly)
    lines = text.splitlines()
    session_line = next(line for line in lines if line.startswith("Session"))
    total_line = next(line for line in lines if line.startswith("Total"))

    # Session row: only the current-session 100/10 (110). Daemon tokens absent.
    assert "100" in session_line
    assert "110" in session_line
    # Total row: session + daemon = 107 input, 13 output, 120 total.
    assert "107" in total_line
    assert "120" in total_line
    # The 40-day-old line is outside the 7-day window.
    assert "500" not in text


@pytest.mark.asyncio
async def test_total_includes_old_line_and_daemon(tmp_path: Path) -> None:
    """/usage total includes the 40-day-old line and the daemon row."""
    deps = _make_deps(tmp_path)
    _seed_ledger(deps)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        outcome = await dispatch("/usage total", ctx)
    text = cap.get()

    assert isinstance(outcome, LocalOnly)
    lines = text.splitlines()
    session_line = next(line for line in lines if line.startswith("Session"))
    daemon_line = next(line for line in lines if line.startswith("Daemon"))
    total_line = next(line for line in lines if line.startswith("Total"))

    # Session subtotal: 100+500 input, 10+50 output -> 600 / 60 / 660.
    assert "600" in session_line
    assert "660" in session_line
    # Daemon subtotal: 7 / 3 / 10.
    assert "7" in daemon_line
    # Combined total: 607 input, 670 total.
    assert "607" in total_line
    assert "670" in total_line


@pytest.mark.asyncio
async def test_unknown_arg_errors_and_does_not_mutate_ledger(tmp_path: Path) -> None:
    """An unknown /usage argument prints the valid-args error and leaves the ledger untouched."""
    deps = _make_deps(tmp_path)
    _seed_ledger(deps)
    ctx = _make_ctx(deps)

    before = deps.usage_log_path.read_text(encoding="utf-8")

    with console.capture() as cap:
        outcome = await dispatch("/usage bogus", ctx)
    text = cap.get()

    assert isinstance(outcome, LocalOnly)
    assert "week" in text
    assert "month" in text
    assert "total" in text
    after = deps.usage_log_path.read_text(encoding="utf-8")
    assert before == after
