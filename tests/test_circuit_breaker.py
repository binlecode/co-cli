"""Unit tests for CircuitBreaker state machine."""

from __future__ import annotations

from unittest.mock import patch

from co_cli.index._circuit import CircuitBreaker


def _breaker(*, threshold: int = 3, initial: float = 5.0, max_s: float = 10.0) -> CircuitBreaker:
    return CircuitBreaker(
        failure_threshold=threshold,
        initial_cooldown_s=initial,
        max_cooldown_s=max_s,
    )


def test_closed_initially():
    assert not _breaker().is_open()


def test_stays_closed_below_threshold():
    cb = _breaker(threshold=3)
    cb.on_failure(RuntimeError("x"))
    cb.on_failure(RuntimeError("x"))
    assert not cb.is_open()


def test_opens_at_threshold():
    cb = _breaker(threshold=3)
    for _ in range(3):
        cb.on_failure(RuntimeError("x"))
    assert cb.is_open()


def test_cooldown_doubles_on_extra_failures():
    cb = _breaker(threshold=2, initial=5.0, max_s=100.0)
    err = RuntimeError("x")

    with patch("co_cli.index._circuit.time") as mock_time:
        mock_time.monotonic.return_value = 0.0
        cb.on_failure(err)
        cb.on_failure(err)
        # open_until = 0 + 5s (2^0 * 5)
        mock_time.monotonic.return_value = 4.9
        assert cb.is_open()

        cb.on_failure(err)
        # open_until = 4.9 + 10s (2^1 * 5)
        mock_time.monotonic.return_value = 14.8
        assert cb.is_open()

        mock_time.monotonic.return_value = 15.0
        assert not cb.is_open()


def test_cooldown_capped_at_max():
    cb = _breaker(threshold=2, initial=5.0, max_s=10.0)
    err = RuntimeError("x")

    with patch("co_cli.index._circuit.time") as mock_time:
        mock_time.monotonic.return_value = 0.0
        for _ in range(10):
            cb.on_failure(err)
        # Despite many failures cooldown is capped at 10s
        mock_time.monotonic.return_value = 9.9
        assert cb.is_open()
        mock_time.monotonic.return_value = 10.1
        assert not cb.is_open()


def test_open_until_expires_half_open():
    cb = _breaker(threshold=2, initial=5.0, max_s=10.0)
    err = RuntimeError("x")

    with patch("co_cli.index._circuit.time") as mock_time:
        mock_time.monotonic.return_value = 0.0
        cb.on_failure(err)
        cb.on_failure(err)
        assert cb.is_open()

        mock_time.monotonic.return_value = 5.1
        assert not cb.is_open()


def test_success_resets_to_closed():
    cb = _breaker(threshold=2)
    err = RuntimeError("x")

    with patch("co_cli.index._circuit.time") as mock_time:
        mock_time.monotonic.return_value = 0.0
        cb.on_failure(err)
        cb.on_failure(err)
        assert cb.is_open()

        mock_time.monotonic.return_value = 5.1
        cb.on_success()
        assert not cb.is_open()

        # A fresh failure cycle must trip the threshold again
        mock_time.monotonic.return_value = 5.1
        cb.on_failure(err)
        assert not cb.is_open()
        cb.on_failure(err)
        assert cb.is_open()
