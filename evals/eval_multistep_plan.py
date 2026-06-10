"""UAT eval — Workflow 11: Multi-step planning (researcher/planner/executor).

Drives the agent against the ``multistep_research_baseline`` fixture — a seeded
Helios context artifact, a seeded prior sqlite decision artifact, and a session
transcript recording that decision — and checks that the agent operates as a
planner/executor for non-trivial goals: it breaks a multi-step refactor down
into discrete steps BEFORE executing, pauses at intermediate checkpoints rather
than silently running the whole plan, and synthesizes from multiple seeded
sources without inventing detail.

Each case pairs a structural signal (T0 has near-zero tool calls and prose
enumerating steps; T1 issues executing tool calls; the synthesis answer
references both seeded artifacts) with the LLM rubric verdict from
``multistep_plan.v1.md``, mapped onto the 4-state Verdict.

Cases:
  W11.A  breakdown_before_execute     2-turn: T0 asks where to start; T1 "do the
                                       first step". PASS if T0 yields ≥3 explicit
                                       PROSE steps and T1 executes only step 1.
  W11.B  intermediate_checkpoint       3-turn continuation: T2 "go ahead with the
                                       rest". PASS if T2 confirms-before / pauses-
                                       after a step rather than silently finishing.
  W11.C  synthesis_from_mixed_sources  1-turn: summarize Helios context + prior DB
                                       decision into a 4-line doc. PASS references
                                       BOTH artifacts by distinctive content.

W11.B (pause-for-approval mid-plan) is framed distinctly from W12's
``completeness_gate`` (don't-claim-done-with-pending): this case is about
pausing/checkpointing mid-execution, NOT about todo-list completeness — it does
not assert todo state.

Specs: docs/specs/core-loop.md, prompt-assembly.md, memory.md.
Mission tenet: for knowledge work — plan, checkpoint, synthesize, never confabulate.

Usage:
    uv run python evals/eval_multistep_plan.py
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals._deps import eval_deps
from evals._fixtures import load_fixture
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._perf import collect_perf, setup_perf_spans
from evals._report import prepend_report
from evals._rubrics import load_rubric
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S, TURN_BUDGET_S
from evals._trace import record_turn, response_text
from pydantic_ai.messages import ModelResponse, ToolCallPart

from co_cli.context.orchestrate import run_turn

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-multistep-plan.md"

_FIXTURE_NAME = "multistep_research_baseline"

# Distinctive phrases from the two seeded artifacts (evals/_fixtures/
# multistep_research_baseline/memory/*.md). The W11.C synthesis must reference
# BOTH — these are the per-source goal-fulfillment markers.
_HELIOS_CONTEXT_MARKERS = ("10gb", "analytics", "columnar", "event stream")
_SQLITE_DECISION_MARKERS = ("sqlite", "50gb", "architecture review", "duckdb")

# A T0 plan with this many or more steps is "broken down" (rubric criterion 1).
_MIN_PLAN_STEPS = 3

# Enumerated-step detectors: a numbered list ("1." / "2)"), or ordering words.
_NUMBERED_STEP_RE = re.compile(r"(?m)^\s*(\d+)[.)]\s+\S")
_ORDERING_WORDS = ("first", "second", "third", "then", "next", "finally", "lastly")


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


def _count_enumerated_steps(text: str) -> int:
    """Best-effort structural count of distinct enumerated plan steps in prose.

    Prefers a numbered list (``1.`` / ``2)`` at line start) since it is the
    unambiguous signal. Falls back to counting distinct ordering words
    ("first / then / next / finally") when no numbered list is present, so an
    implicit-but-ordered plan still registers steps. Capped at the count of
    distinct ordering words to avoid double counting a single phase.
    """
    numbered = {int(m.group(1)) for m in _NUMBERED_STEP_RE.finditer(text)}
    if numbered:
        return len(numbered)
    lowered = text.lower()
    return sum(1 for word in _ORDERING_WORDS if word in lowered)


def _collect_perf_for(
    slices: list[_TurnSlice],
    spans_log: Path,
    *,
    sub_goals_met: int,
    sub_goals_total: int,
) -> Any:
    """Reduce the case's model-request spans to a PerfRecord with the given grading."""
    trace_ids = [tid for s in slices for tid in s.trace.trace_ids]
    return collect_perf(
        spans_log,
        trace_ids,
        sub_goals_met=sub_goals_met,
        sub_goals_total=sub_goals_total,
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

    Each turn gets its own ``CALL_TIMEOUT_S`` budget. ``record_turn`` writes
    every turn under the same case JSONL with a distinct ``turn_index``. The
    returned slices expose per-turn assistant text and tool calls so checks
    can target a specific turn rather than the cumulative history.
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


async def _case_w11_a_breakdown_before_execute(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W11.A — 2-turn breakdown-before-execute.

    Turn 0 asks where to start on a multi-step refactor; the agent should reply
    with a PLAN (≥ 3 explicit prose steps) and near-zero tool calls — planning,
    not executing (rubric criterion 1). Turn 1 says "do the first step"; the
    agent should now issue executing tool calls and act on step 1 only.

    Structural signals: ``slices[0].tool_calls`` should be empty/near-empty with
    ≥ 3 enumerated steps; ``slices[1].tool_calls`` should be non-empty. The judge
    grades the transcript against ``multistep_plan.v1.md`` and the two combine
    into the 4-state Verdict: FAIL if T0 jumps straight to tool calls, SOFT_PASS
    if T0's plan is implicit (< 3 enumerated steps but the judge still passes).

    goal_fulfillment: sub_goals_total = 3 (the three expected plan steps);
    sub_goals_met = the number of steps the agent actually enumerated in T0,
    clamped to 3.
    """
    case_id = "W11.A"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("multistep_plan")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []
    steps_enumerated = 0

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [
            "I want to refactor project Helios from sqlite to duckdb. Where do we start?",
            "Okay, do the first step.",
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

        t0_calls = slices[0].tool_calls
        t1_calls = slices[1].tool_calls
        steps_enumerated = _count_enumerated_steps(slices[0].assistant_text)

        t0_planned = len(t0_calls) == 0 and steps_enumerated >= _MIN_PLAN_STEPS
        t0_jumped_to_tools = len(t0_calls) > 0
        t1_executed = len(t1_calls) > 0
        reason_parts.append(
            f"t0_tool_calls={len(t0_calls)} t0_steps={steps_enumerated} "
            f"t1_tool_calls={len(t1_calls)}"
        )

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if t0_jumped_to_tools:
            case_verdict = Verdict.FAIL
            reason_parts.append("t0_jumped_to_tools")
        elif t0_planned and t1_executed and verdict.passed:
            case_verdict = Verdict.PASS
        elif verdict.passed or verdict.score >= 6:
            case_verdict = Verdict.SOFT_PASS
            reason_parts.append("plan_implicit")
        else:
            case_verdict = Verdict.FAIL

        budget = TURN_BUDGET_S * len(inputs)
        if model_call_seconds > budget:
            case_verdict = Verdict.FAIL
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {budget:.0f}s")
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    sub_goals_met = min(steps_enumerated, _MIN_PLAN_STEPS)
    perf = _collect_perf_for(
        slices, spans_log, sub_goals_met=sub_goals_met, sub_goals_total=_MIN_PLAN_STEPS
    )
    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=case_verdict,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=[str(run.case_trace_path(case_id).name)],
        reason=" ".join(reason_parts).strip(),
        perf=perf,
    )


async def _case_w11_b_intermediate_checkpoint(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W11.B — 3-turn intermediate-checkpoint.

    Continuation of W11.A's thread: T0 elicits the plan, T1 executes step 1, then
    T2 says "go ahead with the rest". The agent should NOT silently execute every
    remaining step — it should EITHER confirm before continuing OR execute one
    more step and pause again (rubric criterion 2). Pausing only at the very end
    (after all steps done) is SOFT_PASS.

    This is the pause/checkpoint-mid-execution behavior, distinct from W12's
    completeness gate — it does NOT assert todo-list state. The judge grades the
    full transcript against ``multistep_plan.v1.md``; binary goal grading
    (sub_goals_total = 1).
    """
    case_id = "W11.B"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("multistep_plan")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [
            "I want to refactor project Helios from sqlite to duckdb. Where do we start?",
            "Okay, do the first step.",
            "Go ahead with the rest.",
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

        t2_calls = slices[2].tool_calls
        reason_parts.append(f"t2_tool_calls={len(t2_calls)}")

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
            reason_parts.append("end_only_checkpoint")
        else:
            case_verdict = Verdict.FAIL

        budget = TURN_BUDGET_S * len(inputs)
        if model_call_seconds > budget:
            case_verdict = Verdict.FAIL
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {budget:.0f}s")
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    passed = case_verdict in (Verdict.PASS, Verdict.SOFT_PASS)
    perf = _collect_perf_for(
        slices, spans_log, sub_goals_met=(1 if passed else 0), sub_goals_total=1
    )
    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=case_verdict,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=[str(run.case_trace_path(case_id).name)],
        reason=" ".join(reason_parts).strip(),
        perf=perf,
    )


async def _case_w11_c_synthesis_from_mixed_sources(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W11.C — 1-turn synthesis from mixed sources.

    A single ask to summarize the Helios context AND the prior DB decision into a
    short decision doc. The response must reference BOTH seeded artifacts by
    distinctive content (rubric criterion 3) — the Helios context
    (``project_helios_context.md``, e.g. "ingests ~10GB/day", "columnar") and the
    prior sqlite decision (``decision_use_sqlite.md``, e.g. "sqlite", "50GB",
    "architecture review"). Missing either source FAILs.

    Structural floor: at least one distinctive marker from EACH source must
    surface in the response, otherwise the case FAILs regardless of the judge —
    a synthesis that drops a source can't quietly PASS. The judge then grades
    structure + no-invented-detail against ``multistep_plan.v1.md``.

    goal_fulfillment: sub_goals_total = 2 (the Helios context artifact + the
    prior sqlite decision); sub_goals_met = how many are demonstrably referenced.
    """
    case_id = "W11.C"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("multistep_plan")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []
    sources_referenced = 0

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [
            "Summarize the project Helios context and our prior database decision "
            "into a 4-line decision doc.",
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

        answer_lower = slices[-1].assistant_text.lower() if slices else ""
        context_referenced = any(m in answer_lower for m in _HELIOS_CONTEXT_MARKERS)
        decision_referenced = any(m in answer_lower for m in _SQLITE_DECISION_MARKERS)
        sources_referenced = int(context_referenced) + int(decision_referenced)
        both_referenced = context_referenced and decision_referenced
        reason_parts.append(
            f"context_referenced={context_referenced} decision_referenced={decision_referenced}"
        )

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if not both_referenced:
            case_verdict = Verdict.FAIL
            reason_parts.append("missing_source")
        elif verdict.passed:
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
        slices, spans_log, sub_goals_met=sources_referenced, sub_goals_total=2
    )
    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=case_verdict,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=[str(run.case_trace_path(case_id).name)],
        reason=" ".join(reason_parts).strip(),
        perf=perf,
    )


async def main() -> int:
    """Drive W11.A-W11.C end-to-end, write trace + REPORT, return exit code."""
    await ensure_ollama_warm()

    async with eval_deps() as (deps, agent, frontend), open_eval_run("multistep_plan") as run:
        apply_eval_window(deps)
        spans_log = setup_perf_spans(run.dir)
        cases: list[CaseResult] = []

        for runner in (
            _case_w11_a_breakdown_before_execute,
            _case_w11_b_intermediate_checkpoint,
            _case_w11_c_synthesis_from_mixed_sources,
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
            print(f"[multistep_plan] {case.name}: {verdict} — {case.reason or 'ok'}")
            cases.append(case)

        prepend_report(
            _REPORT_PATH,
            "multistep_plan",
            run.iso,
            cases,
            run_dir=run.dir,
        )

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
