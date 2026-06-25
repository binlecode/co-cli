"""Behavioral tests for the owned loop's ToolCapState cap arithmetic.

Assert observable cap decisions only (shed boundary, hard-stop latch, streak
reset) — never field existence. The cap is the small-model flood defense; its
correctness is the shed/latch behavior, not the struct shape.
"""

from __future__ import annotations

from co_cli.agent.turn_state import ToolCapState
from co_cli.config.tuning import (
    MAX_TOOL_CALLS_PER_MODEL_REQUEST,
    TOOL_CAP_HARD_STOP_CONSECUTIVE,
)

CAP = MAX_TOOL_CALLS_PER_MODEL_REQUEST
HARD_STOP = TOOL_CAP_HARD_STOP_CONSECUTIVE


def test_over_cap_step_sheds_calls_at_index_at_or_above_cap() -> None:
    state = ToolCapState()
    issued = CAP + 2
    executes = state.shed_boundary(issued)
    assert executes == CAP
    # Calls at index < CAP execute; index >= CAP are shed.
    shed = issued - executes
    assert shed == 2


def test_within_cap_step_sheds_nothing() -> None:
    state = ToolCapState()
    assert state.shed_boundary(CAP) == CAP
    assert state.shed_boundary(1) == 1


def test_hard_stop_latches_only_after_consecutive_over_cap_steps() -> None:
    state = ToolCapState()
    # One short of the threshold: not latched yet.
    for _ in range(HARD_STOP - 1):
        state.note_calls(CAP + 1)
    assert state.hard_stop is False
    # The threshold-th consecutive over-cap step latches it.
    state.note_calls(CAP + 1)
    assert state.hard_stop is True


def test_within_cap_step_resets_the_streak() -> None:
    state = ToolCapState()
    for _ in range(HARD_STOP - 1):
        state.note_calls(CAP + 1)
    assert state.hard_stop is False
    # A within-cap step breaks the streak before it reaches the threshold.
    state.note_calls(CAP)
    # Now a fresh run of over-cap steps must restart from zero.
    for _ in range(HARD_STOP - 1):
        state.note_calls(CAP + 1)
    assert state.hard_stop is False
    state.note_calls(CAP + 1)
    assert state.hard_stop is True


def test_hard_stop_stays_latched_after_a_later_within_cap_step() -> None:
    state = ToolCapState()
    for _ in range(HARD_STOP):
        state.note_calls(CAP + 1)
    assert state.hard_stop is True
    # A within-cap step resets the streak but cannot un-earn the hard stop.
    state.note_calls(1)
    assert state.hard_stop is True
