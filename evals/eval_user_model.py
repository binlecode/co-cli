"""UAT eval — Workflow 10: Durable user model (preferences across rotation).

Drives the agent against the ``user_model_baseline`` fixture — three seeded
preference artifacts (``pref_terse``, ``pref_python``, ``pref_pst``) plus two
prior session transcripts where those preferences surfaced naturally. After a
session rotation (simulated ``/new`` by driving with an empty
``message_history``), the agent should adapt to the durable user model without
being re-told: terse responses, Python by default, PST time references. A
one-shot override ("give me the Go version") must apply for that turn only and
not leak forward.

Each judged case combines the LLM rubric verdict from ``user_model.v1.md``
(mapped onto the 4-state Verdict per the rubric's SOFT tone notes) with the
case-specific structural expectations from the scenario table. The decay case
is SOFT-only and never gates: it ages a preference artifact past the decay
window and runs the dream cycle to observe whether disuse-decay archives it.

Cases:
  W10.A  post_rotation_adaptation  2-turn: T0 "read a CSV"; T1 "standup time?".
                                    PASS if both honor terse / Python / PST.
  W10.B  contradiction_handling    3-turn: T0 CSV; T1 "Go version"; T2 "JSON".
                                    PASS if T1 switches to Go AND T2 reverts.
  W10.C  decay_under_disuse        SOFT-only: age ``pref_terse`` ~90d, run dream,
                                    check survival. Never emits PASS/FAIL.

Specs: docs/specs/core-loop.md, prompt-assembly.md, memory.md, dream.md.
Mission tenet: for knowledge work — adapts to the user; trusted — durable model.

Usage:
    uv run python evals/eval_user_model.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from evals._deps import eval_deps
from evals._fixtures import load_fixture
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._perf import collect_perf, setup_perf_spans
from evals._rubrics import load_rubric
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S, DREAM_CYCLE_BUDGET_S, TURN_BUDGET_S
from evals._trace import record_turn, response_text
from pydantic_ai.messages import ModelResponse, ToolCallPart

from co_cli.context.orchestrate import run_turn
from co_cli.daemons.dream._housekeeping import merge_memory
from co_cli.daemons.dream._state import HousekeepingState

_FIXTURE_NAME = "user_model_baseline"

# Filename stem of the terse-preference artifact seeded by the fixture
# (``evals/_fixtures/user_model_baseline/memory/pref_terse.md``). W10.C ages
# this artifact and checks whether the dream cycle archives it.
_TERSE_PREF_STEM = "pref_terse"

# Age (days) to stamp onto the terse preference for W10.C — past the default
# 90-day decay window (``co_cli/config/memory.py`` decay_after_days=90).
_DECAY_AGE_DAYS = 95


@dataclass(frozen=True)
class _TurnSlice:
    """Per-turn view of a multi-turn drive.

    ``assistant_text`` and ``tool_calls`` cover ONLY the messages added during
    this turn — not the cumulative history. The cumulative history lives on
    ``result.messages`` and is what the next turn carries forward.
    """

    result: Any
    trace: Any
    new_messages: list[Any]
    assistant_text: str
    tool_calls: list[ToolCallPart]


def _tool_calls_from(messages: list[Any]) -> list[ToolCallPart]:
    """Extract ToolCallParts in call order across the given assistant messages."""
    calls: list[ToolCallPart] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    calls.append(part)
    return calls


def _aggregate_trace_stats(slices: list[_TurnSlice]) -> tuple[float, dict[str, int]]:
    """Sum ``model_call_seconds`` and ``token_usage`` across turns."""
    total_seconds = sum(getattr(s.trace, "model_call_seconds", 0.0) for s in slices)
    totals: dict[str, int] = {}
    for s in slices:
        for k, v in (getattr(s.trace, "token_usage", None) or {}).items():
            totals[k] = totals.get(k, 0) + int(v)
    return total_seconds, totals


def _collect_perf_for(slices: list[_TurnSlice], spans_log: Path, *, passed: bool) -> Any:
    """Reduce the case's model-request spans to a PerfRecord with binary goal grading."""
    trace_ids = [tid for s in slices for tid in s.trace.trace_ids]
    return collect_perf(
        spans_log,
        trace_ids,
        sub_goals_met=(1 if passed else 0),
        sub_goals_total=1,
    )


async def _drive_turns(
    *,
    case_id: str,
    deps: Any,
    agent: Any,
    frontend: Any,
    case_dir_path: Path,
    inputs: list[str],
) -> list[_TurnSlice]:
    """Drive N user turns carrying ``message_history`` forward.

    History starts empty, simulating a post-rotation ``/new`` session: the
    agent has no in-context recall of the seeded preferences and must adapt
    from the durable user model alone. Each turn gets its own ``CALL_TIMEOUT_S``
    budget. ``record_turn`` writes every turn under the same case JSONL with a
    distinct ``turn_index``. The returned slices expose per-turn assistant text
    and tool calls so checks can target a specific turn.
    """
    history: list[Any] = []
    slices: list[_TurnSlice] = []
    for i, user_input in enumerate(inputs):
        prior_len = len(history)
        async with asyncio.timeout(CALL_TIMEOUT_S):
            result, trace = await record_turn(
                case_id=case_id,
                turn_index=i,
                user_input=user_input,
                run_turn_callable=(
                    lambda h=history, ui=user_input: run_turn(
                        agent=agent,
                        user_input=ui,
                        deps=deps,
                        message_history=h,
                        frontend=frontend,
                    )
                ),
                case_dir_path=case_dir_path,
                agent=agent,
            )
        history = list(result.messages)
        new_msgs = history[prior_len:]
        slices.append(
            _TurnSlice(
                result=result,
                trace=trace,
                new_messages=new_msgs,
                assistant_text=response_text(result),
                tool_calls=_tool_calls_from(new_msgs),
            )
        )
    return slices


async def _case_w10_a_post_rotation_adaptation(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W10.A — 2-turn post-rotation preference adaptation.

    After loading the fixture, a fresh session (empty ``message_history``
    simulates ``/new``) drives two neutral asks: T0 "show me how to read a CSV"
    (exercises the Python + terse defaults) and T1 "what time is standup
    tomorrow?" (exercises the PST default). The judge grades the transcript
    against ``user_model.v1.md`` criterion 1 — at least 2 of 3 seeded
    preferences honored is PASS, exactly 1 is SOFT_PASS, JS-or-verbose-or-UTC
    is FAIL.
    """
    case_id = "W10.A"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("user_model")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [
            "show me how to read a CSV",
            "what time is standup tomorrow?",
        ]
        slices = await _drive_turns(
            case_id=case_id,
            deps=deps,
            agent=agent,
            frontend=frontend,
            case_dir_path=run.case_trace_path(case_id),
            inputs=inputs,
        )
        model_call_seconds, token_usage = _aggregate_trace_stats(slices)

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if verdict.passed:
            case_verdict = Verdict.PASS
        elif verdict.score >= 6:
            case_verdict = Verdict.SOFT_PASS
        else:
            case_verdict = Verdict.FAIL

        budget = TURN_BUDGET_S * len(inputs)
        if model_call_seconds > budget:
            case_verdict = Verdict.FAIL
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {budget:.0f}s")
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    perf = _collect_perf_for(
        slices, spans_log, passed=case_verdict in (Verdict.PASS, Verdict.SOFT_PASS)
    )
    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=case_verdict,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        reason=" ".join(reason_parts).strip(),
        perf=perf,
    )


async def _case_w10_b_contradiction_handling(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W10.B — 3-turn one-shot override handling.

    T0 "read a CSV" establishes the Python default; T1 "actually, do the Go
    version" is a one-shot override that should apply to that turn only; T2
    "now read JSON" must revert to the Python default while staying terse. The
    judge grades against ``user_model.v1.md`` criterion 2 — a persistent Go
    switch (T2 stays Go without re-prompt) is the FAIL mode, and T2 asking which
    language is the SOFT_PASS hedge.
    """
    case_id = "W10.B"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("user_model")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [
            "show me how to read a CSV",
            "actually, do the Go version",
            "now read JSON",
        ]
        slices = await _drive_turns(
            case_id=case_id,
            deps=deps,
            agent=agent,
            frontend=frontend,
            case_dir_path=run.case_trace_path(case_id),
            inputs=inputs,
        )
        model_call_seconds, token_usage = _aggregate_trace_stats(slices)

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if verdict.passed:
            case_verdict = Verdict.PASS
        elif verdict.score >= 6:
            case_verdict = Verdict.SOFT_PASS
        else:
            case_verdict = Verdict.FAIL

        budget = TURN_BUDGET_S * len(inputs)
        if model_call_seconds > budget:
            case_verdict = Verdict.FAIL
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {budget:.0f}s")
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    perf = _collect_perf_for(
        slices, spans_log, passed=case_verdict in (Verdict.PASS, Verdict.SOFT_PASS)
    )
    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=case_verdict,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        reason=" ".join(reason_parts).strip(),
        perf=perf,
    )


async def _case_w10_c_decay_under_disuse(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W10.C — disuse-decay observation (SOFT-only, never gates).

    Loads the fixture WITHOUT re-stamping mtimes, then ages the ``pref_terse``
    artifact's file mtime to ~95 days old and runs one dream housekeeping pass
    (``merge_memory``). Afterwards it checks whether ``pref_terse.md`` still
    exists in ``deps.memory_dir``.

    This case is deliberately SOFT-only — it emits ``SOFT_PASS`` if the artifact
    survived and ``SOFT_FAIL`` if it was archived, and NEVER ``PASS``/``FAIL``.
    Disuse decay is a soft lifecycle behavior with known LLM/clock variance and
    must never gate the suite's exit code (the ``CaseResult.passed`` property
    treats SOFT_PASS as passing and SOFT_FAIL as non-fatal review signal).
    """
    case_id = "W10.C"
    case_t0 = time.monotonic()
    reason_parts: list[str] = []
    case_verdict = Verdict.SOFT_PASS

    try:
        load_fixture(_FIXTURE_NAME, deps, re_stamp_mtimes=False)

        pref_path = deps.memory_dir / f"{_TERSE_PREF_STEM}.md"
        if not pref_path.exists():
            reason_parts.append(f"seed_missing: {pref_path.name} not found after load")
        else:
            aged = datetime.now(UTC) - timedelta(days=_DECAY_AGE_DAYS)
            aged_epoch = aged.timestamp()
            os.utime(pref_path, (aged_epoch, aged_epoch))
            reason_parts.append(f"aged {_TERSE_PREF_STEM} to ~{_DECAY_AGE_DAYS}d (mtime)")

        async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
            await merge_memory(deps, HousekeepingState())

        survived = pref_path.exists()
        if survived:
            case_verdict = Verdict.SOFT_PASS
            reason_parts.append(f"{_TERSE_PREF_STEM} preserved (SOFT_PASS)")
        else:
            case_verdict = Verdict.SOFT_FAIL
            reason_parts.append(f"{_TERSE_PREF_STEM} archived (SOFT_FAIL)")
    except Exception as exc:
        # Even on error this case must not emit a hard gate — surface as SOFT_FAIL
        # so the failure shows in run.jsonl review signals without failing exit code.
        case_verdict = Verdict.SOFT_FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=case_verdict,
        duration_s=duration,
        reason=" ".join(reason_parts).strip(),
        perf=None,
    )


async def main() -> int:
    """Drive W10.A-W10.C end-to-end, write trace, return exit code."""
    await ensure_ollama_warm()

    async with eval_deps() as (deps, agent, frontend), open_eval_run("user_model") as run:
        apply_eval_window(deps)
        spans_log = setup_perf_spans(run.spans_path)
        cases: list[CaseResult] = []

        for runner in (
            _case_w10_a_post_rotation_adaptation,
            _case_w10_b_contradiction_handling,
            _case_w10_c_decay_under_disuse,
        ):
            try:
                case = await runner(deps, agent, frontend, run, spans_log)
            except Exception as exc:
                case = CaseResult(
                    name=runner.__name__,
                    verdict=Verdict.FAIL,
                    duration_s=0.0,
                    reason=f"runner_crash: {type(exc).__name__}: {exc}",
                )
            run.append(case)
            verdict = "PASS" if case.passed else "FAIL"
            print(f"[user_model] {case.name}: {verdict} — {case.reason or 'ok'}")
            cases.append(case)

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
