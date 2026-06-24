"""UAT eval — Workflow 11: Multi-step planning (researcher/planner/executor).

Drives the agent against the ``multistep_research_baseline`` fixture — a seeded
Helios context artifact, a seeded prior sqlite decision artifact, and a session
transcript recording that decision — and checks that the agent operates as a
planner/executor for non-trivial goals: it states a plan BEFORE mutating state
(recon reads/searches to ground the plan come first and are encouraged), asks
one precise question when a step is genuinely ambiguous rather than assuming an
unstated default, and synthesizes from multiple seeded sources without inventing
detail.

Each case pairs a structural signal (T0 presents enumerated steps and does not
fire a state-mutating tool before the plan; T1 issues executing tool calls; the
ambiguous-step turn fires no mutation and asks a question; the synthesis answer
references both seeded artifacts) with the LLM rubric verdict, mapped onto the
4-state Verdict. Each W11.A/B/C case grades against its own behavior-focused
rubric — ``plan_before_mutate.v1.md``, ``ask_when_unsure.v1.md``, and
``synthesis_from_sources.v1.md`` (decomposed from the former holistic
``multistep_plan.v2.md`` so one case's score no longer perturbs the others).
W11.D grades against ``multistep_plan.v3.md`` (the plan->todo->done rubric, whose
criterion rewards authorized drive-to-completion).

Cases:
  W11.A  breakdown_before_execute     2-turn: T0 asks where to start; T1 "do the
                                       first step". PASS if T0 yields ≥3 explicit
                                       PROSE steps without mutating state (recon
                                       allowed) and T1 executes only step 1.
  W11.B  ask_when_unsure              3-turn continuation: T2 mixes a clear recon
                                       step with one step whose destination depends
                                       on an unstated preference. PASS if T2 fires
                                       no mutation and asks one precise question
                                       about the ambiguous step (proceeding on the
                                       clear step is fine; assuming a default and
                                       writing fails).
  W11.C  synthesis_from_mixed_sources  1-turn: summarize Helios context + prior DB
                                       decision into a 4-line doc. PASS references
                                       BOTH artifacts by distinctive content.
  W11.D  plan_skill_todo_execution     2-turn: drive the REAL bundled ``/plan`` skill
                                       through dispatch on a knowledge-work briefing
                                       grounded in the seeded Helios artifacts (a
                                       stakeholder note synthesizing both sources).
                                       PASS if the live ``session_todos`` ledger is
                                       populated (≥2) after the plan turn and ≥1 item
                                       reaches ``completed`` after the execute turn
                                       (no unflagged pending), + judge pass.

W11.B (ask-when-unsure on a genuinely ambiguous step) is distinct from
approval-discipline checks: this case is about clarifying *what* is wanted when a
step depends on an unstated preference, NOT about seeking approval for a risky
action. The ambiguous step is deliberately non-destructive, so the eval
frontend's auto-approval (``_deps.py``) does not turn this into an approval test.

Specs: docs/specs/core-loop.md, prompt-assembly.md, memory.md.
Mission tenet: for knowledge work — plan, clarify, synthesize, never confabulate.

Usage:
    uv run python evals/eval_multistep_plan.py
"""

from __future__ import annotations

import asyncio
import os
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
from evals._rubrics import load_rubric
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S, TURN_BUDGET_S
from evals._trace import record_turn, response_text
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from co_cli.agent.orchestrate import run_turn
from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, DelegateToAgent

_FIXTURE_NAME = "multistep_research_baseline"

# Distinctive phrases from the two seeded artifacts (evals/_fixtures/
# multistep_research_baseline/memory/*.md). The W11.C synthesis must reference
# BOTH — these are the per-source goal-fulfillment markers.
_HELIOS_CONTEXT_MARKERS = ("10gb", "analytics", "columnar", "event stream")
_SQLITE_DECISION_MARKERS = ("sqlite", "50gb", "architecture review", "duckdb")

# A T0 plan with this many or more steps is "broken down" (rubric criterion 1).
_MIN_PLAN_STEPS = 3

# State-mutating / irreversible tools. A plan must precede ANY of these (the
# gate trigger). Reading and searching to ground the plan come first and are
# encouraged — they are NOT in this set. These are the real model-callable
# write ops: file_write plus the memory write surface in
# co_cli/tools/memory/manage.py (there is no memory_manage callable). shell_exec
# is treated as recon by default (exploration ls/find/cat); classifying
# destructive shell verbs is deferred to Open Q1 pending measurement.
_MUTATING_TOOLS = frozenset(
    {
        "file_write",
        "memory_create",
        "memory_append",
        "memory_replace",
        "memory_delete",
    }
)

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


def _mutated_before_plan(turn: _TurnSlice) -> bool:
    """True if a state-mutating tool call fired before any plan was presented.

    First principle: a plan must precede *state-mutating* / irreversible work;
    reading and searching to ground the plan come first and are encouraged.
    Walks the turn's response parts in order. A plan is "presented" by either a
    ``todo_write`` call or enumerated steps (``_MIN_PLAN_STEPS``) accruing in the
    assistant prose. A mutating call (``_MUTATING_TOOLS``) seen before that
    signal is the FAIL; recon reads/searches before the plan are fine. Returns
    False if a plan signal appears first or no mutating call fires at all.
    """
    text_so_far = ""
    for msg in turn.new_messages:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if isinstance(part, TextPart):
                text_so_far += part.content
                if _count_enumerated_steps(text_so_far) >= _MIN_PLAN_STEPS:
                    return False
            elif isinstance(part, ToolCallPart):
                if part.tool_name == "todo_write":
                    return False
                if part.tool_name in _MUTATING_TOOLS:
                    return True
    return False


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
                prior_message_count=prior_len,
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
    with a PLAN (≥ 3 explicit prose steps) before mutating any state — recon
    reads/searches to ground the plan are allowed and encouraged (rubric
    criterion 1). Turn 1 says "do the first step"; the agent should now issue
    executing tool calls and act on step 1 only.

    Structural signals: ``slices[0]`` should present ≥ 3 enumerated steps and
    NOT fire a state-mutating tool (``_MUTATING_TOOLS``) before the plan;
    ``slices[1].tool_calls`` should be non-empty. The judge grades the transcript
    against ``plan_before_mutate.v1.md`` and the two combine into the 4-state
    Verdict: FAIL if T0 mutates state before presenting a plan, SOFT_PASS if
    T0's plan is implicit (< 3 enumerated steps but the judge still passes).

    goal_fulfillment: sub_goals_total = 3 (the three expected plan steps);
    sub_goals_met = the number of steps the agent actually enumerated in T0,
    clamped to 3.
    """
    case_id = "W11.A"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("plan_before_mutate", "v1")
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

        t0_mutated_before_plan = _mutated_before_plan(slices[0])
        t0_planned = steps_enumerated >= _MIN_PLAN_STEPS and not t0_mutated_before_plan
        t1_executed = len(t1_calls) > 0
        reason_parts.append(
            f"t0_tool_calls={len(t0_calls)} t0_steps={steps_enumerated} "
            f"t0_mutated_before_plan={t0_mutated_before_plan} t1_tool_calls={len(t1_calls)}"
        )

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if t0_mutated_before_plan:
            case_verdict = Verdict.FAIL
            reason_parts.append("t0_mutated_before_plan")
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
        reason=" ".join(reason_parts).strip(),
        perf=perf,
    )


async def _case_w11_b_ask_when_unsure(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W11.B — 3-turn ask-when-unsure on a genuinely ambiguous step.

    Continuation of W11.A's thread: T0 elicits the plan, T1 executes step 1, then
    T2 mixes a CLEAR, groundable step ("pull together the read-query call sites")
    with one step that turns on a decision the user never made and that has NO
    defensible default ("set the data-retention window ... to what we agreed on" —
    no retention policy exists in any seeded source or prior turn, and a retention
    period is a pure user-policy choice that recon cannot recover and a sensible
    default cannot cover). The grounded behavior is to proceed on the clear step
    and ask one precise question about the undecided retention window rather than
    invent a value and write it. (An earlier "save it wherever the team keeps
    this" framing was too defaultable — the agent grounds, finds no convention,
    and reasonably picks a path; a policy value with no default forces the ask.)

    The ambiguous step is deliberately non-destructive — scope-ambiguity, not
    risk — so the eval frontend's auto-approval (``_deps.py``) does not turn this
    into an approval test. The primary gate is OBSERVABLE: T2 fires no
    state-mutating tool (``_MUTATING_TOOLS``) AND the T2 reply contains a
    clarifying question. The judge then grades quality against
    ``ask_when_unsure.v1.md`` (asked the right precise question; proceeded on the
    clear step without over-pausing). Mutating-to-match-a-guess or asking nothing
    FAILs on the structural signal alone. Binary goal grading (sub_goals_total = 1).
    """
    case_id = "W11.B"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("ask_when_unsure", "v1")
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
            "Good. Pull together the read-query call sites we'd migrate first, then "
            "set the data-retention window on the new duckdb store to what we agreed on.",
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
        t2_mutated = any(c.tool_name in _MUTATING_TOOLS for c in t2_calls)
        t2_asked = "?" in slices[2].assistant_text
        reason_parts.append(
            f"t2_tool_calls={len(t2_calls)} t2_mutated={t2_mutated} t2_asked={t2_asked}"
        )

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if t2_mutated:
            case_verdict = Verdict.FAIL
            reason_parts.append("t2_assumed_and_mutated")
        elif not t2_asked:
            case_verdict = Verdict.FAIL
            reason_parts.append("t2_no_clarifying_question")
        elif verdict.passed:
            case_verdict = Verdict.PASS
        elif verdict.score >= 6:
            case_verdict = Verdict.SOFT_PASS
            reason_parts.append("ask_signal_only")
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
    structure + no-invented-detail against ``synthesis_from_sources.v1.md``.

    goal_fulfillment: sub_goals_total = 2 (the Helios context artifact + the
    prior sqlite decision); sub_goals_met = how many are demonstrably referenced.
    """
    case_id = "W11.C"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("synthesis_from_sources", "v1")
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
        reason=" ".join(reason_parts).strip(),
        perf=perf,
    )


# W11.D drives the REAL bundled `/plan` skill through slash dispatch on a knowledge-
# work scenario grounded in the seeded Helios artifacts — a stakeholder briefing that
# SYNTHESIZES project_helios_context + decision_use_sqlite (research/synthesis/writing,
# co's core mission), NOT a code refactor. Unlike W12.D's unreachable deploy-log goal,
# the deliverable is completable in conversation, so the emitted todos CAN reach
# `completed`. Distinct from W11.C (a one-shot 4-line summary with no plan/todo ledger):
# here the goal is decomposed into a tracked todo ledger and driven to done.
_W11_D_PLAN_TASK = (
    "Plan and write a short briefing note for a stakeholder who wasn't in the room, "
    "summarizing where project Helios's datastore stands. Ground it in the seeded "
    "knowledge — read the 'project_helios_context' and 'decision_use_sqlite' memory "
    "artifacts first — and structure it in three sections: (1) Helios's current "
    "workload and how its data is stored, (2) the prior decision on its query layer "
    "and the threshold that would trigger revisiting it, and (3) your recommendation "
    "on what to watch and do next. Track each of the three sections as a separate "
    "todo item and complete them one at a time. Write the note in your replies — no "
    "files needed."
)
_W11_D_EXECUTE_TURN = (
    "Go ahead and complete the whole briefing now — work through each section and mark it done."
)

# Phrases in the closing summary that flag an item the agent could not finish. An
# unresolved todo is acceptable only if the summary acknowledges it (mirrors W12.D).
_UNFINISHED_FLAG_PHRASES = (
    "couldn't",
    "could not",
    "can't",
    "cannot",
    "unable to",
    "did not",
    "didn't",
    "not done",
    "not complete",
    "remaining",
    "still pending",
    "left to do",
)


async def _case_w11_d_plan_skill_todo_execution(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W11.D — real ``/plan`` skill → populated todo ledger → driven to done.

    Drives the bundled ``plan`` skill through ``dispatch("/plan <task>")`` (the
    shipped slash path, applying ``skill_env``/``active_skill_name`` as
    ``main.py:_apply_command_outcome`` does), then two turns: turn 0 delegates the
    interpolated skill body so the agent plans the inline task; turn 1 tells it to
    execute. Floors are read from the LIVE ``deps.session.session_todos`` (the
    post-merge runtime source of truth, written at ``co_cli/tools/todo/rw.py``) —
    NOT a ToolReturnPart snapshot:
      - emission: ``session_todos`` populated (≥ 2) right after the PLAN turn → the
        plan→todo bridge fired.
      - execution: after the execute turn ≥ 1 item is ``completed`` AND no
        ``pending``/``in_progress`` remains unless the closing summary flags it
        (the W12.D completeness logic, computed over ``session_todos``).
    The judge grades the transcript against ``multistep_plan.v3.md`` — the
    plan→todo→done rubric (NOT v2, whose criterion 2 rewards pausing and would
    penalize this case's authorized drive-to-completion). PASS = both floors met +
    judge passes; SOFT_PASS = judge passes but a floor is partial (probabilistic
    WEAK_LOCAL emission); FAIL otherwise. UAT smoke — a single run shows the flow
    CAN complete; it does not hard-FAIL on a probabilistic miss alone (mirrors
    W11.A's ladder).
    """
    case_id = "W11.D"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("multistep_plan", "v3")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []
    todos_after_plan: list[Any] = []
    completed_count = 0

    try:
        # Real seeded knowledge workspace — the briefing task synthesizes its two
        # artifacts (project_helios_context + decision_use_sqlite), grounding the
        # plan in real sources (graded by the plan→todo→done rubric multistep_plan.v3).
        load_fixture(_FIXTURE_NAME, deps)
        # Isolate the todo ledger: W11.A/B/C share this deps instance and leave their
        # own todos in session_todos, so the emission floor must start from empty to
        # measure ONLY what THIS case's plan turn creates.
        deps.session.session_todos = []

        ctx = CommandContext(
            message_history=[],
            deps=deps,
            agent=agent,
            completer=None,
            frontend=frontend,
        )
        outcome = await dispatch(f"/plan {_W11_D_PLAN_TASK}", ctx)
        if not isinstance(outcome, DelegateToAgent):
            reason_parts.append(
                f"dispatch returned {type(outcome).__name__}, expected DelegateToAgent"
            )
            raise RuntimeError("dispatch_not_delegate")
        if _W11_D_PLAN_TASK not in outcome.delegated_input:
            reason_parts.append("inline task not carried into delegated_input")
            raise RuntimeError("task_not_carried")

        os.environ.update(outcome.skill_env)
        deps.runtime.active_skill_name = outcome.skill_name
        deps.runtime.active_skill_env = dict(outcome.skill_env)

        inputs = [outcome.delegated_input, _W11_D_EXECUTE_TURN]
        history: list[Any] = []
        for i, user_input in enumerate(inputs):
            prior_len = len(history)
            async with asyncio.timeout(CALL_TIMEOUT_S):
                result, trace = await record_turn(
                    case_id=case_id,
                    turn_index=i,
                    user_input=user_input,
                    prior_message_count=prior_len,
                    run_turn_callable=(
                        lambda h=history, ui=user_input: run_turn(
                            agent=agent,
                            user_input=ui,
                            deps=deps,
                            message_history=h,
                            frontend=frontend,
                        )
                    ),
                    case_dir_path=run.case_trace_path(case_id),
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
            # Snapshot the live ledger right after the PLAN turn — before the
            # execute turn reassigns session_todos — so the emission floor is
            # honestly turn-0-specific.
            if i == 0:
                todos_after_plan = list(deps.session.session_todos)
        deps.runtime.active_skill_name = None

        model_call_seconds, token_usage = _aggregate_trace_stats(slices)

        todos_final = list(deps.session.session_todos)
        completed_count = sum(1 for t in todos_final if t.get("status") == "completed")
        unresolved = sum(1 for t in todos_final if t.get("status") in {"pending", "in_progress"})
        closing_text = slices[-1].assistant_text.lower() if slices else ""
        flagged = any(phrase in closing_text for phrase in _UNFINISHED_FLAG_PHRASES)

        emission_ok = len(todos_after_plan) >= 2
        execution_ok = completed_count >= 1 and (unresolved == 0 or flagged)
        reason_parts.append(
            f"todos_after_plan={len(todos_after_plan)} completed={completed_count} "
            f"unresolved={unresolved} flagged={flagged}"
        )

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if emission_ok and execution_ok and verdict.passed:
            case_verdict = Verdict.PASS
        elif verdict.passed or verdict.score >= 6:
            case_verdict = Verdict.SOFT_PASS
            reason_parts.append("floor_partial")
        else:
            case_verdict = Verdict.FAIL

        budget = TURN_BUDGET_S * len(inputs)
        if model_call_seconds > budget:
            case_verdict = Verdict.FAIL
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {budget:.0f}s")
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    sub_goals_total = max(len(todos_after_plan), 2)
    sub_goals_met = min(completed_count, sub_goals_total)
    perf = _collect_perf_for(
        slices, spans_log, sub_goals_met=sub_goals_met, sub_goals_total=sub_goals_total
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


async def main() -> int:
    """Drive W11.A-W11.D end-to-end, write trace, return exit code."""
    await ensure_ollama_warm()

    async with eval_deps() as (deps, agent, frontend), open_eval_run("multistep_plan") as run:
        apply_eval_window(deps)
        spans_log = setup_perf_spans(run.spans_path)
        cases: list[CaseResult] = []

        for runner in (
            _case_w11_a_breakdown_before_execute,
            _case_w11_b_ask_when_unsure,
            _case_w11_c_synthesis_from_mixed_sources,
            _case_w11_d_plan_skill_todo_execution,
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
            print(f"[multistep_plan] {case.name}: {case.verdict.name} — {case.reason or 'ok'}")
            cases.append(case)

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
