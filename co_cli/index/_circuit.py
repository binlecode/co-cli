"""Circuit breaker for external HTTP services (embed, rerank)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _CircuitState:
    failures: int = 0
    open_until: float = field(default=0.0)


class CircuitBreaker:
    """Open-after-threshold, exponential-backoff circuit breaker.

    After ``failure_threshold`` consecutive failures the breaker opens and
    short-circuits calls until the cooldown expires.  Cooldown doubles on each
    additional failure, capped at ``max_cooldown_s``.  A successful probe resets
    the breaker fully.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        initial_cooldown_s: float = 5.0,
        max_cooldown_s: float = 10.0,
    ) -> None:
        self._threshold = failure_threshold
        self._initial_cooldown = initial_cooldown_s
        self._max_cooldown = max_cooldown_s
        self._state = _CircuitState()

    def is_open(self) -> bool:
        """Return True if the breaker is open and calls should be skipped."""
        if self._state.open_until == 0.0:
            return False
        # Cooldown expired — half-open: allow the next probe through.
        return time.monotonic() < self._state.open_until

    def on_success(self) -> None:
        """Reset breaker to closed after a successful call."""
        if self._state.failures > 0:
            logger.debug("circuit breaker reset after success")
        self._state = _CircuitState()

    def on_failure(self, err: Exception) -> None:
        """Record a failure and open the breaker once the threshold is reached."""
        self._state.failures += 1
        if self._state.failures < self._threshold:
            return
        exponent = self._state.failures - self._threshold
        cooldown = min(self._initial_cooldown * (2**exponent), self._max_cooldown)
        self._state.open_until = time.monotonic() + cooldown
        logger.warning(
            "circuit breaker open after %d failures (cooldown %.0fs): %s",
            self._state.failures,
            cooldown,
            err,
        )
