"""Behavioral tests for CircuitBreaker state machine."""

from __future__ import annotations

import time

from co_cli.index._circuit import CircuitBreaker

_TINY = 0.01


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
    # Use tiny cooldowns (0.01s initial, 0.1s max) so test completes quickly.
    cb = CircuitBreaker(failure_threshold=2, initial_cooldown_s=_TINY, max_cooldown_s=0.1)
    err = RuntimeError("x")

    cb.on_failure(err)
    cb.on_failure(err)
    assert cb.is_open()

    # Third failure doubles the cooldown — still open immediately after
    cb.on_failure(err)
    assert cb.is_open()

    # Wait past the doubled cooldown (2 * 0.01 = 0.02s); use a safe margin
    time.sleep(_TINY * 4)
    assert not cb.is_open()


def test_cooldown_capped_at_max():
    # max_s=0.03 — many failures cannot push cooldown above this cap
    cb = CircuitBreaker(failure_threshold=2, initial_cooldown_s=_TINY, max_cooldown_s=0.03)
    err = RuntimeError("x")

    for _ in range(10):
        cb.on_failure(err)

    assert cb.is_open()
    # Wait past the cap
    time.sleep(0.05)
    assert not cb.is_open()


def test_open_until_expires_half_open():
    cb = CircuitBreaker(failure_threshold=2, initial_cooldown_s=_TINY, max_cooldown_s=0.05)
    err = RuntimeError("x")

    cb.on_failure(err)
    cb.on_failure(err)
    assert cb.is_open()

    time.sleep(_TINY * 2)
    assert not cb.is_open()


def test_success_resets_to_closed():
    cb = CircuitBreaker(failure_threshold=2, initial_cooldown_s=_TINY, max_cooldown_s=0.05)
    err = RuntimeError("x")

    cb.on_failure(err)
    cb.on_failure(err)
    assert cb.is_open()

    time.sleep(_TINY * 2)
    cb.on_success()
    assert not cb.is_open()

    # A fresh failure cycle must trip the threshold again
    cb.on_failure(err)
    assert not cb.is_open()
    cb.on_failure(err)
    assert cb.is_open()
