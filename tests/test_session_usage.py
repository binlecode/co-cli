"""Tests for the durable token-usage ledger primitives (append_turn + aggregate)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from co_cli.session.usage import aggregate, append_turn


def _seed_ledger(path: Path, now: datetime) -> None:
    """Write a real ledger: in-window, 3-day-old, 40-day-old session lines + a now daemon line."""
    append_turn(
        path,
        origin="session",
        session_id="sessA",
        input_tokens=100,
        output_tokens=10,
        turn_ended_at=now,
    )
    append_turn(
        path,
        origin="session",
        session_id="sessB",
        input_tokens=200,
        output_tokens=20,
        turn_ended_at=now - timedelta(days=3),
    )
    append_turn(
        path,
        origin="session",
        session_id="sessC",
        input_tokens=400,
        output_tokens=40,
        turn_ended_at=now - timedelta(days=40),
    )
    append_turn(
        path,
        origin="daemon",
        session_id=None,
        input_tokens=1000,
        output_tokens=100,
        turn_ended_at=now,
    )


def test_week_window_sums_only_in_window_lines(tmp_path: Path) -> None:
    """aggregate(since=now-7d) keeps only the now + 3-day session lines and the daemon line."""
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    ledger = tmp_path / "usage.jsonl"
    _seed_ledger(ledger, now)

    window = aggregate(ledger, since=now - timedelta(days=7))

    assert window.session.input_tokens == 300
    assert window.session.output_tokens == 30
    assert window.daemon.input_tokens == 1000
    assert window.daemon.output_tokens == 100
    assert window.total.input_tokens == 1300
    assert window.total.output_tokens == 130


def test_total_window_sums_all_lines(tmp_path: Path) -> None:
    """aggregate() with no cutoff sums every line, including the 40-day-old one."""
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    ledger = tmp_path / "usage.jsonl"
    _seed_ledger(ledger, now)

    window = aggregate(ledger)

    assert window.session.input_tokens == 700
    assert window.session.output_tokens == 70
    assert window.daemon.input_tokens == 1000
    assert window.total.input_tokens == 1700
    assert window.total.output_tokens == 170


def test_daemon_never_folded_into_session_subtotal(tmp_path: Path) -> None:
    """In every window the daemon line lands in total but never in the session subtotal."""
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    ledger = tmp_path / "usage.jsonl"
    _seed_ledger(ledger, now)

    for since in (now - timedelta(days=7), now - timedelta(days=30), None):
        window = aggregate(ledger, since=since)
        assert window.daemon.input_tokens == 1000
        assert window.session.input_tokens not in (0,)  # session has its own tokens
        assert window.total.input_tokens == window.session.input_tokens + 1000


def test_session_filter_with_origin_sums_only_that_session(tmp_path: Path) -> None:
    """aggregate(session_id=X, origin='session') sums only that session's lines."""
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    ledger = tmp_path / "usage.jsonl"
    _seed_ledger(ledger, now)

    window = aggregate(ledger, session_id="sessB", origin="session")

    assert window.session.input_tokens == 200
    assert window.session.output_tokens == 20
    assert window.daemon.input_tokens == 0
    assert window.total.input_tokens == 200


def test_session_count_reflects_only_session_origin_lines(tmp_path: Path) -> None:
    """Distinct-session count over all lines counts only the three session origins."""
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    ledger = tmp_path / "usage.jsonl"
    _seed_ledger(ledger, now)

    window = aggregate(ledger)

    assert window.session_count == 3


def test_append_turn_zero_tokens_writes_nothing(tmp_path: Path) -> None:
    """append_turn with both counts 0 writes no line."""
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    ledger = tmp_path / "usage.jsonl"
    append_turn(
        ledger,
        origin="session",
        session_id="sessA",
        input_tokens=0,
        output_tokens=0,
        turn_ended_at=now,
    )

    assert not ledger.exists()


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    """A garbage line between valid records is skipped without error."""
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    ledger = tmp_path / "usage.jsonl"
    append_turn(
        ledger,
        origin="session",
        session_id="sessA",
        input_tokens=100,
        output_tokens=10,
        turn_ended_at=now,
    )
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write('{"turn_ended_at": "bad-timestamp", "input_tokens": 5}\n')

    window = aggregate(ledger)

    assert window.session.input_tokens == 100
