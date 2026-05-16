"""Per-call asyncio.timeout budgets for evals.

Single source of truth: warm-LLM-latency constants are imported from
``tests/_timeouts.py`` so a single edit relaxes or tightens a budget across
every test and eval that uses it. Eval-only longer-budget constants for
multi-turn / dream-cycle cases are defined here.

Per ``feedback_call_timeout_no_cold_start.md``: budgets cover warm-model
latency only. Never fold cold model load into the call budget. Diagnose
slow calls rather than bumping the constant.
"""

from __future__ import annotations

from tests._timeouts import (
    BG_TASK_COMPLETION_TIMEOUT_SECS,
    BG_TASK_TEARDOWN_TIMEOUT_SECS,
    FILE_DB_TIMEOUT_SECS,
    HTTP_HEALTH_TIMEOUT_SECS,
    LLM_COMPACTION_SUMMARY_TIMEOUT_SECS,
    LLM_NON_REASONING_TIMEOUT_SECS,
    LLM_REASONING_TIMEOUT_SECS,
    LLM_TOOL_CONTEXT_TIMEOUT_SECS,
)

__all__ = [
    "BG_TASK_COMPLETION_TIMEOUT_SECS",
    "BG_TASK_TEARDOWN_TIMEOUT_SECS",
    "CALL_TIMEOUT_S",
    "DREAM_CYCLE_BUDGET_S",
    "FILE_DB_TIMEOUT_SECS",
    "HTTP_HEALTH_TIMEOUT_SECS",
    "LLM_COMPACTION_SUMMARY_TIMEOUT_SECS",
    "LLM_NON_REASONING_TIMEOUT_SECS",
    "LLM_REASONING_TIMEOUT_SECS",
    "LLM_TOOL_CONTEXT_TIMEOUT_SECS",
    "MULTI_TURN_COMPACT_BUDGET_S",
    "TURN_BUDGET_S",
]

CALL_TIMEOUT_S: int = LLM_TOOL_CONTEXT_TIMEOUT_SECS
"""Default per-call ceiling for an agent run_turn at the eval layer.

Aliases LLM_TOOL_CONTEXT_TIMEOUT_SECS (50s) so eval call sites read as
``asyncio.timeout(CALL_TIMEOUT_S)`` without coupling to the tests constant
name. Use TURN_BUDGET_S for soft per-case baseline assertions
(Behavioral Constraint #13), not for the absolute timeout ceiling.
"""

TURN_BUDGET_S: int = 35
"""Soft per-case baseline budget for ``model_call_seconds`` of a single turn.

Asserted against per-case as a regression signal — a turn that takes longer
than this is logged as ``[slow] N.Ns vs budget M.Ms`` and FAILs the case,
even when the absolute asyncio.timeout(CALL_TIMEOUT_S) did not fire. Catches
model-regression slowdowns where the timeout ceiling (50s) is above the
expected baseline (~20-30s on warm 35B). Recalibrate from first-run
``model_call_seconds`` if the configured model legitimately runs slower.
"""

DREAM_CYCLE_BUDGET_S: int = 240
"""End-to-end ceiling for ``run_dream_cycle()`` on a real session.

Dream cycle drives multiple sub-agent calls (mine, dedup, merge, decay)
plus knowledge-dir mutations. 240s covers a four-stage cycle with warm
sub-agents; longer means a sub-agent stalled.
"""

MULTI_TURN_COMPACT_BUDGET_S: int = 180
"""Multi-turn compaction case (W2.E) — N synthetic turns + compaction summarizer.

Inflation drives ~10 brief turns to push history past compaction_ratio, then
``/compact`` runs the LLM summarizer. 180s covers worst-case 10x non-reasoning
turns + one compaction call.
"""
