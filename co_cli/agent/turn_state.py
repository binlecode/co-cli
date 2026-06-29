"""Turn-scoped state for the owned (graph-free) turn loop.

These types are reconstructed per turn (never agent-object counters) and drive
``co_cli/agent/loop.py``. The owned loop is selected by ``config.llm.use_owned_loop``;
with the flag off the graph path (``orchestrate.run_turn``) owns these concerns
instead, so nothing here is read on the default path.

``TurnExit`` enumerates how a turn ends — the typed replacement for the graph
path's mix of exception types and string-matched signatures. In Phase 2 only the
no-approval / no-recovery slice is reachable; ``PROVIDER_ERROR`` / ``TIMEOUT`` /
``INTERRUPTED`` are terminal-only (their recovery routing is Phase 4).

``ToolCapState`` owns the flood cap at the **step boundary** (pre-fan-out), which
is a deliberate behavior change from the graph's per-call shed inside dispatch
(``toolset.py``) — see the plan's CD-m-3. It counts a step's issued calls, sheds
everything at index >= the cap, and latches a hard stop after
``TOOL_CAP_HARD_STOP_CONSECUTIVE`` consecutive over-cap steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Literal

from co_cli.config.tuning import (
    MAX_TOOL_CALLS_PER_MODEL_REQUEST,
    TOOL_CAP_HARD_STOP_CONSECUTIVE,
)

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

# Typed return value from the owned turn driver (run_turn_owned) to the chat loop.
TurnOutcome = Literal["continue", "error"]


class TurnExit(Enum):
    """How an owned turn ended.

    FINAL_TEXT          — the model produced a final answer with no tool calls
                          (orchestrator) or a validated ``final_result`` (subagent).
    TOOL_CAP            — the consecutive over-cap hard stop fired (circuit breaker).
    REQUEST_CAP         — the turn-cumulative model-request cap was reached.
    REASONING_OVERFLOW  — reasoning consumed the whole output budget before any
                          answer token (finish_reason 'length', empty/thinking-only).
    PROVIDER_ERROR      — a provider/transport error ended the turn (no recovery; Phase 4).
    TIMEOUT             — the model-progress stall window elapsed.
    INTERRUPTED         — the user interrupted the turn (KeyboardInterrupt/cancel).
    """

    FINAL_TEXT = auto()
    TOOL_CAP = auto()
    REQUEST_CAP = auto()
    REASONING_OVERFLOW = auto()
    PROVIDER_ERROR = auto()
    TIMEOUT = auto()
    INTERRUPTED = auto()


@dataclass
class ToolCapState:
    """Per-turn flood-cap accounting for the owned loop, counted at the step boundary.

    Unlike the graph's per-call shed inside ``_CallSeamToolset.call_tool``, the owned
    loop decides the shed boundary **before** fanning out a step's calls: it executes
    the calls at index ``< cap`` and sheds the rest with an exceeded payload. A step
    that issues more than ``cap`` calls is an over-cap step; ``hard_stop`` latches once
    ``hard_stop_threshold`` such steps occur consecutively, and a within-cap step
    resets that streak. ``hard_stop``, once latched, is never cleared within the turn.
    """

    cap: int = MAX_TOOL_CALLS_PER_MODEL_REQUEST
    hard_stop_threshold: int = TOOL_CAP_HARD_STOP_CONSECUTIVE
    consecutive_violations: int = 0
    hard_stop: bool = False

    def note_calls(self, issued: int) -> None:
        """Record a step that issued ``issued`` tool calls; update streak + latch.

        Over-cap steps extend the consecutive-violation streak and latch ``hard_stop``
        once the streak reaches ``hard_stop_threshold``; a within-cap step resets the
        streak (but never un-latches an already-set ``hard_stop``).
        """
        if issued > self.cap:
            self.consecutive_violations += 1
            if self.consecutive_violations >= self.hard_stop_threshold:
                self.hard_stop = True
        else:
            self.consecutive_violations = 0

    def shed_boundary(self, issued: int) -> int:
        """Return how many of a step's ``issued`` calls execute (the rest are shed).

        Calls at index ``< cap`` execute and return real results; calls at index
        ``>= cap`` are shed and return an exceeded payload.
        """
        return min(issued, self.cap)


@dataclass
class TurnState:
    """Per-turn mutable state for the owned turn loop.

    Reconstructed at every ``run_turn_owned`` / owned ``run_standalone`` entry. The
    history is the request-building source. ``model_requests`` counts every
    ``ModelResponse`` across the turn's steps (drives the post-turn skill-review gate,
    matching the graph path's accumulator). ``exit_reason`` is set once the loop
    terminates. ``overflow_recovery_attempted`` latches the once-per-turn emergency
    compaction; ``tool_reformat_budget`` bounds the HTTP 400 tool-call reflection retries
    (both Phase-4 recovery state, parity with the graph's ``_TurnState``).
    """

    history: list[ModelMessage]
    model_requests: int = 0
    exit_reason: TurnExit | None = None
    cap_state: ToolCapState = field(default_factory=ToolCapState)
    overflow_recovery_attempted: bool = False
    tool_reformat_budget: int = 2


@dataclass
class TurnResult:
    """Result of one agent turn, returned by the owned driver to the chat loop.

    Callers pattern-match on ``interrupted`` / ``outcome``; ``output`` and ``usage``
    are forwarded opaquely (kept ``Any`` — the chat loop never inspects their fields).
    ``model_requests`` counts the ModelResponses across the turn (post-turn skill-review gate).
    """

    outcome: TurnOutcome
    interrupted: bool
    messages: list[ModelMessage] = field(default_factory=list)
    output: Any = None
    usage: Any = None
    model_requests: int = 0
