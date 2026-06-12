"""Performance overlay for behavioral evals — plan T-8a (dims 2 + 4).

Derives a :class:`PerfRecord` per behavioral case from co's structured-log
model-request spans: per-call wall durations (p50 / p95 / over-budget count),
peak input tokens, a context-overflow flag, and a goal-fulfillment fraction.

Span plumbing
-------------
``co_cli.observability.tracing`` writes one JSON-line record per span to the
file configured by ``tracing.setup_log(...)``. ``create_deps`` /
``make_eval_deps`` do **not** call ``setup_log`` — that wiring lives in the CLI
entry (``co_cli.observability.setup``) — so an eval that wants perf must enable
span file logging itself. :func:`setup_perf_spans` does exactly that against an
isolated per-run path, mirroring ``eval_context_stability.py``. Keeping the log
per-run means :func:`collect_perf` reads a small file holding only this eval's
traces.

Model-request spans carry ``kind == "model"`` (both the agent-path ``chat
<model>`` span and the direct ``llm_call <model>`` span), with ``duration_ms``
top-level and ``attributes["co.model.tokens.input"]`` for prompt tokens
(``co_cli/llm/surrogate_recovery_model.py``, ``co_cli/llm/call.py``).

Bands (calibrated)
------------------
``PERF_BANDS_GATING`` is True: T-8b (2026-06-11) calibrated the duration and
peak-ctx bands against the phase-2 behavioral suite (W7-W12, multiple runs, distinct
Gemini judge). :func:`perf_verdict` FAILs on a hard context overflow and SOFT_FAILs
a case whose ``call_p95_s`` exceeds ``WARM_CALL_BUDGET_S`` (15s), whose
``peak_input_tokens`` exceeds ``PEAK_INPUT_TOKENS_BUDGET`` (24k), or whose
``goal_fulfillment`` is below 1.0. SOFT_FAIL is a review signal — it never overrides
a behavioral FAIL upstream.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from evals._observability import Verdict
from evals._timeouts import WARM_CALL_BUDGET_S

from co_cli.observability import tracing

PERF_BANDS_GATING: bool = True
"""Calibrated by T-8b (2026-06-11) — duration + peak-ctx bands gate as SOFT_FAIL."""

PEAK_INPUT_TOKENS_BUDGET: int = 24_000
"""Peak per-case model-request input-token band.

Calibrated by T-8b against the phase-2 behavioral suite: observed
``peak_input_tokens`` p50 12.0k, p90 13.4k, p95 15.8k, max 16.1k, zero context
overflows. 24k sits ~1.5x over the observed max — flags a context regression
(e.g. compaction not firing, history ballooning) without firing on the steady
~16k working set. ``perf_verdict`` SOFT_FAILs a case exceeding this band.
"""

_MODEL_SPAN_KIND = "model"

_OVERFLOW_PHRASES = (
    "prompt is too long",
    "context length",
    "context size",
    "context window",
    "token limit",
    "too many tokens",
    "exceeds the limit",
    "input token count",
    "maximum number of tokens",
    "prompt length",
    "input is too long",
    "maximum model length",
    "max input token",
    "exceeds the max_model_len",
    "reduce the length",
    "context_length_exceeded",
    "max_tokens_exceeded",
)
"""Substrings flagging a context-overflow error on a model span's status_msg.

Mirrors ``_OVERFLOW_PHRASES`` + ``_OVERFLOW_CODES`` in
``co_cli/context/_http_error_classifier.py`` (the production source of truth,
which classifies live ``ModelHTTPError`` objects). That module is package-
private, and here we only have the post-hoc ``str(exc)`` recorded on the span —
so we mirror the phrase set and substring-match it. Keep in sync if the
classifier's phrases change.
"""


@dataclass(frozen=True)
class PerfRecord:
    """Span-derived performance metrics for one behavioral case.

    Fields the parent ``CaseResult`` does not already hold: the per-call
    distribution (``call_p50_s`` / ``call_p95_s`` / ``calls_over_budget``),
    ``peak_input_tokens``, and ``context_overflow``. ``goal_fulfillment`` is the
    met/total sub-goal fraction the case supplies (1.0 when not graded).
    """

    call_durations_s: list[float] = field(default_factory=list)
    call_p50_s: float = 0.0
    call_p95_s: float = 0.0
    calls_over_budget: int = 0
    peak_input_tokens: int = 0
    context_overflow: bool = False
    goal_fulfillment: float = 1.0


def setup_perf_spans(spans_log: Path) -> Path:
    """Enable span file logging to the run's ``spans_log`` path and return it.

    Call once before driving turns (pass ``run.spans_path``). ``create_deps``
    does not enable span file logging, so without this the spans file does not
    exist and :func:`collect_perf` reads nothing. Isolating per run keeps the
    read scoped to this eval's traces (filtering by trace id is still applied
    on top).
    """
    tracing.setup_log(spans_log)
    return spans_log


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile. ``pct`` in [0, 100]. 0.0 for empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = pct / 100.0 * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def model_spans_for_traces(spans_log: Path, trace_ids: list[str]) -> list[dict]:
    """All ``kind == "model"`` span records in ``spans_log`` whose trace_id is wanted.

    Tolerates a missing file (returns ``[]``) and skips malformed lines, so a run
    that captured no spans degrades to an empty :class:`PerfRecord` rather than
    raising.
    """
    if not spans_log.exists():
        return []
    wanted = set(trace_ids)
    out: list[dict] = []
    for line in spans_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == _MODEL_SPAN_KIND and rec.get("trace_id") in wanted:
            out.append(rec)
    return out


def _span_is_overflow(span: dict) -> bool:
    if span.get("status") != "ERROR":
        return False
    msg = (span.get("status_msg") or "").lower()
    return any(phrase in msg for phrase in _OVERFLOW_PHRASES)


def perf_from_spans(
    model_spans: list[dict],
    sub_goals_met: int,
    sub_goals_total: int,
) -> PerfRecord:
    """Compute a :class:`PerfRecord` from already-loaded model-request spans.

    Pure over its inputs — the unit smoke drives this directly with synthetic
    span dicts. ``goal_fulfillment`` is 1.0 when ``sub_goals_total <= 0`` (the
    case did not declare gradable sub-goals), so an ungraded case carries no perf
    penalty; a case wanting binary grading passes ``total=1`` with ``met`` 0/1.
    """
    durations = [float(s.get("duration_ms", 0.0)) / 1000.0 for s in model_spans]
    input_tokens = [
        int(s.get("attributes", {}).get("co.model.tokens.input", 0)) for s in model_spans
    ]
    goal = 1.0 if sub_goals_total <= 0 else sub_goals_met / sub_goals_total
    return PerfRecord(
        call_durations_s=durations,
        call_p50_s=_percentile(durations, 50.0),
        call_p95_s=_percentile(durations, 95.0),
        calls_over_budget=sum(1 for d in durations if d > WARM_CALL_BUDGET_S),
        peak_input_tokens=max(input_tokens, default=0),
        context_overflow=any(_span_is_overflow(s) for s in model_spans),
        goal_fulfillment=goal,
    )


def collect_perf(
    spans_log: Path,
    trace_ids: list[str],
    sub_goals_met: int,
    sub_goals_total: int,
) -> PerfRecord:
    """Read the case's model-request spans from ``spans_log`` and reduce to a PerfRecord."""
    return perf_from_spans(
        model_spans_for_traces(spans_log, trace_ids),
        sub_goals_met,
        sub_goals_total,
    )


def perf_verdict(rec: PerfRecord, *, gate_bands: bool | None = None) -> Verdict:
    """Fold a PerfRecord into a Verdict — never overrides a behavioral FAIL upstream.

    A hard context overflow always FAILs. The duration / peak-ctx / goal-fulfillment
    bands only produce SOFT_FAIL when band gating is on (``PERF_BANDS_GATING``,
    calibrated by T-8b); ``gate_bands`` overrides the module default for the unit smoke.
    """
    if rec.context_overflow:
        return Verdict.FAIL
    gate = PERF_BANDS_GATING if gate_bands is None else gate_bands
    if not gate:
        return Verdict.PASS
    over_band = (
        rec.call_p95_s > WARM_CALL_BUDGET_S
        or rec.peak_input_tokens > PEAK_INPUT_TOKENS_BUDGET
        or rec.goal_fulfillment < 1.0
    )
    if over_band:
        return Verdict.SOFT_FAIL
    return Verdict.PASS
