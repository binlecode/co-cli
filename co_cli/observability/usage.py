"""Realtime turn-scoped token accumulator — observational, never a control input.

Provider-reported token usage (``response.usage`` / ``RunUsage`` — ground truth,
never ``chars/4``) is captured at every model-call boundary into a turn-scoped
``UsageAccumulator`` shared by reference across delegation/task forks (so subagent
and summarizer tokens roll into the active turn's total). The main loop owns the
reset at the turn boundary; forks only ``add``.

This is **observational telemetry only**: it MUST NOT feed compaction triggers or
the status-line context-% (those stay on the realtime
``current_request_tokens_estimate``). The durable per-turn ledger that consumes
these totals at the turn boundary lives in ``co_cli/session/usage.py``.

Accumulator I/O is **best-effort**: ``record_usage`` exceptions are logged and
swallowed so usage tracking never blocks or fails a turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)


@dataclass
class UsageAccumulator:
    """Turn-scoped token tally, shared by reference across forks.

    Forks only ``add``; the main loop owns ``reset`` at the turn boundary.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def reset(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0


def record_usage(deps: CoDeps, usage: object) -> None:
    """Best-effort bump of the fork-shared accumulator from a provider usage object.

    Reads ``input_tokens`` / ``output_tokens`` off the provider-reported usage and
    adds them to ``deps.usage_accumulator``. Swallows and logs any error.
    """
    try:
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        deps.usage_accumulator.add(int(input_tokens), int(output_tokens))
    except Exception as exc:
        logger.debug("record_usage failed: %s", exc)
