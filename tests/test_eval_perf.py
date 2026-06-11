"""Unit smoke for the eval performance overlay (plan T-8a).

Drives ``evals/_perf.py``'s pure reducers with synthetic model-request span
dicts — no real LLM, no spans file — and asserts the distribution metrics,
context-overflow detection, trace/kind filtering, and verdict folding behave as
specified. This is the deterministic backstop for the perf infra; the real
span-derived numbers are exercised by the behavioral evals themselves.
"""

from __future__ import annotations

from pathlib import Path

from evals._observability import Verdict
from evals._perf import (
    PerfRecord,
    collect_perf,
    model_spans_for_traces,
    perf_from_spans,
    perf_verdict,
)
from evals._timeouts import WARM_CALL_BUDGET_S


def _model_span(trace_id: str, duration_ms: float, input_tokens: int, **extra: object) -> dict:
    span = {
        "kind": "model",
        "trace_id": trace_id,
        "name": "chat test-model",
        "duration_ms": duration_ms,
        "status": "OK",
        "status_msg": None,
        "attributes": {"co.model.tokens.input": input_tokens},
    }
    span.update(extra)
    return span


def test_perf_from_spans_computes_distribution() -> None:
    spans = [
        _model_span("t_a", 2000.0, 1000),
        _model_span("t_a", 10000.0, 4096),
        _model_span("t_a", 30000.0, 2048),
    ]
    rec = perf_from_spans(spans, sub_goals_met=2, sub_goals_total=3)

    assert rec.call_durations_s == [2.0, 10.0, 30.0]
    assert rec.call_p50_s == 10.0
    assert rec.call_p95_s == 28.0
    assert rec.calls_over_budget == 1
    assert rec.peak_input_tokens == 4096
    assert rec.context_overflow is False
    assert rec.goal_fulfillment == 2 / 3


def test_empty_spans_yield_neutral_record() -> None:
    rec = perf_from_spans([], sub_goals_met=0, sub_goals_total=0)

    assert rec.call_p50_s == 0.0
    assert rec.call_p95_s == 0.0
    assert rec.calls_over_budget == 0
    assert rec.peak_input_tokens == 0
    assert rec.context_overflow is False
    assert rec.goal_fulfillment == 1.0


def test_context_overflow_detected_from_error_span() -> None:
    spans = [
        _model_span("t_a", 5000.0, 8000),
        _model_span(
            "t_a",
            1000.0,
            9000,
            status="ERROR",
            status_msg="400: this model's maximum context length is 8192 tokens",
        ),
    ]
    rec = perf_from_spans(spans, sub_goals_met=1, sub_goals_total=1)
    assert rec.context_overflow is True


def test_model_spans_for_traces_filters_by_trace_and_kind(tmp_path: Path) -> None:
    spans_log = tmp_path / "spans.jsonl"
    lines = [
        '{"kind": "model", "trace_id": "t_keep", "duration_ms": 1000.0, '
        '"attributes": {"co.model.tokens.input": 100}}',
        '{"kind": "model", "trace_id": "t_drop", "duration_ms": 2000.0, '
        '"attributes": {"co.model.tokens.input": 200}}',
        '{"kind": "tool", "trace_id": "t_keep", "duration_ms": 50.0, "attributes": {}}',
        "not json at all",
    ]
    spans_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    kept = model_spans_for_traces(spans_log, ["t_keep"])
    assert len(kept) == 1
    assert kept[0]["trace_id"] == "t_keep"
    assert kept[0]["kind"] == "model"


def test_collect_perf_missing_file_is_neutral(tmp_path: Path) -> None:
    rec = collect_perf(tmp_path / "absent.jsonl", ["t_x"], sub_goals_met=0, sub_goals_total=0)
    assert rec.peak_input_tokens == 0
    assert rec.context_overflow is False


def test_perf_verdict_overflow_always_fails() -> None:
    rec = PerfRecord(context_overflow=True)
    assert perf_verdict(rec, gate_bands=False) == Verdict.FAIL
    assert perf_verdict(rec, gate_bands=True) == Verdict.FAIL


def test_perf_verdict_bands_gate_only_when_enabled() -> None:
    over_band = PerfRecord(call_p95_s=WARM_CALL_BUDGET_S + 10.0, goal_fulfillment=1.0)

    assert perf_verdict(over_band, gate_bands=False) == Verdict.PASS
    assert perf_verdict(over_band, gate_bands=True) == Verdict.SOFT_FAIL


def test_perf_verdict_unmet_goal_soft_fails_when_gated() -> None:
    partial = PerfRecord(call_p95_s=1.0, goal_fulfillment=0.5)

    assert perf_verdict(partial, gate_bands=False) == Verdict.PASS
    assert perf_verdict(partial, gate_bands=True) == Verdict.SOFT_FAIL
