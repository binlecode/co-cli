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
    "WARM_CALL_BUDGET_S",
]

CALL_TIMEOUT_S: int = LLM_TOOL_CONTEXT_TIMEOUT_SECS
"""Default per-call ceiling for an agent run_turn_owned at the eval layer.

Aliases LLM_TOOL_CONTEXT_TIMEOUT_SECS (50s) so eval call sites read as
``asyncio.timeout(CALL_TIMEOUT_S)`` without coupling to the tests constant
name. Use TURN_BUDGET_S for soft per-case baseline assertions
(Behavioral Constraint #13), not for the absolute timeout ceiling.
"""

TOOL_TURN_BUDGET_S: int = 60
"""Per-case baseline budget for a turn that includes ≥ 1 tool call.

Tool-using turns add tool-execution latency (memory_create write,
file_search scan, etc.) on top of the model call. 60s covers a warm 35B
turn with one or two tool roundtrips; longer means either the model
slowed or a tool blocked.
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
"""End-to-end ceiling for one dream housekeeping pass on a real session.

Wraps ``merge_memory()`` calls in eval_memory.py / eval_daily_chat.py;
covers the LLM sub-agent calls and memory-dir mutations of one merge
phase with warm sub-agents. Longer means a sub-agent stalled.
"""

MULTI_TURN_COMPACT_BUDGET_S: int = 180
"""Multi-turn compaction case (W2.E) — N synthetic turns + compaction summarizer.

Inflation drives ~10 brief turns to push history past compaction_ratio, then
``/compact`` runs the LLM summarizer. 180s covers worst-case 10x non-reasoning
turns + one compaction call.
"""

WARM_CALL_BUDGET_S: float = 15.0
"""Per-*model-request* warm-latency band: a single LLM call within a turn, distinct
from the per-turn ``TURN_BUDGET_S`` (35s) and the ``CALL_TIMEOUT_S`` stall ceiling
(50s). ``evals/_perf.perf_verdict`` SOFT_FAILs a case whose ``call_p95_s`` exceeds
this band when ``PERF_BANDS_GATING`` is True.

Calibrated by T-8b (2026-06-11) from the raw model-request span durations across the
phase-2 behavioral suite — 291 OK warm calls (4 ERROR spans excluded). True per-call
distribution: p50 3.3s, p90 7.5s, p95 9.7s, p99 24.5s, max 35.8s. The bulk of warm
calls finish under 10s; the 24-36s tail is **decode-bound, not a latency regression**
— those calls each emit 1700-1840 output tokens at ~50-70 tok/s (verbose
reasoning), so duration tracks output length, not prefill. The 48-50s spans are
``CALL_TIMEOUT_S`` errors, not warm calls. 15s ≈ 1.5× the warm p95 (9.7s): it flags
the genuinely slow tail (a prefill/infra regression, or a pathologically long
generation) as a SOFT review signal while leaving the normal <10s working set clean.
"""
