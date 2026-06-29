"""UAT eval — Workflow 12: Agentic loop (effort scaling + loop discipline).

Drives the agent through the parts of the tool loop that single-shot evals
can't see: scaling effort to the task, surfacing a blocker instead of spinning
on a missing file, changing approach after a shell command fails identically,
and reporting honestly against an explicit set of sub-goals (one blocked).

First principle (v2): given a failing action, the agent stops after a
reasonable attempt and surfaces the blocker — *without being told to*. The
W12.B/C prompts carry NO retry/persist instruction; the case tests whether the
model loops on its own. The natural loop threshold is ``doom_loop_threshold``
(pinned to its floor of 2 here): a streak of identical failing calls reaching
that threshold is the FAIL (the model looped); a clean "tried once, failed,
here's the blocker" is the PASS. The doom-loop/shell-reflection warning never
needing to fire is the *ideal* outcome (no loop to break), not a coverage loss.

Cases:
  W12.A  classify_effort              3-turn: greeting → simple lookup → open research.
  W12.B  blocker_not_doomloop         1-turn: read a missing path (no retry instruction).
  W12.C  shell_reflection_recovery    1-turn: run an always-failing shell command (no retry instruction).
  W12.D  completeness_gate            1-turn multi-step: 3 sub-goals, one blocked, track via todos.

Specs: docs/specs/core-loop.md, prompt-assembly.md.
Mission tenet: trusted — surfaces blockers, scales effort, honest completion.

Usage:
    uv run python evals/eval_agentic_loop.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals._deps import drive_turn
from evals._fixtures import load_fixture
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._perf import collect_perf, setup_perf_spans
from evals._rubrics import load_rubric
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S, TURN_BUDGET_S
from evals._trace import record_turn, response_text
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from co_cli.context.prompt_text import _is_shell_error_return

_FIXTURE_NAME = "agentic_loop_baseline"

_MISSING_PATH = "/tmp/helios_nonexistent_xyz.log"


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


def _call_signature(tc: ToolCallPart) -> str:
    """Stable ``(tool_name, args)`` signature for identical-call comparison.

    Mirrors the hashing in ``prompt_text._count_consecutive_same_calls`` so the
    eval's streak count agrees with what the doom-loop warning actually counts.
    """
    args = tc.args
    if hasattr(args, "args_dict"):
        args_str = json.dumps(args.args_dict(), sort_keys=True)
    elif isinstance(args, str):
        args_str = args
    else:
        args_str = json.dumps(args, sort_keys=True, default=str)
    return hashlib.md5(f"{tc.tool_name}:{args_str}".encode()).hexdigest()


def _max_identical_call_streak(calls: list[ToolCallPart]) -> int:
    """Longest contiguous run of identical ``(tool_name, args)`` tool calls."""
    longest = 0
    current = 0
    last_sig: str | None = None
    for tc in calls:
        sig = _call_signature(tc)
        if sig == last_sig:
            current += 1
        else:
            current = 1
            last_sig = sig
        longest = max(longest, current)
    return longest


def _max_consecutive_shell_error_streak(messages: list[Any]) -> int:
    """Longest contiguous run of ``shell_exec`` returns that are errors.

    Walks the turn's messages in order, reusing ``_is_shell_error_return`` (the
    production error detector) so the count matches the shell-reflection warning.
    """
    longest = 0
    current = 0
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart) or part.tool_name != "shell_exec":
                continue
            if _is_shell_error_return(part):
                current += 1
                longest = max(longest, current)
            else:
                current = 0
    return longest


def _final_todo_states(messages: list[Any]) -> list[dict[str, Any]] | None:
    """Most-recent todo state from a ``todo_read``/``todo_write`` ToolReturnPart.

    Reads ``metadata['todos']`` (the same field ``resume._rehydrate_todos``
    keys off). Returns the list of todo dicts, or None if no todo tool fired.
    """
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name not in {"todo_read", "todo_write"}:
                continue
            meta = getattr(part, "metadata", None)
            todos = meta.get("todos") if isinstance(meta, dict) else None
            if isinstance(todos, list):
                return [t for t in todos if isinstance(t, dict)]
    return None


def _aggregate_trace_stats(slices: list[_TurnSlice]) -> tuple[float, dict[str, int]]:
    """Sum ``model_call_seconds`` and ``token_usage`` across turns."""
    total_seconds = sum(getattr(s.trace, "model_call_seconds", 0.0) for s in slices)
    totals: dict[str, int] = {}
    for s in slices:
        for k, v in (getattr(s.trace, "token_usage", None) or {}).items():
            totals[k] = totals.get(k, 0) + int(v)
    return total_seconds, totals


def _collect_perf_for(
    slices: list[_TurnSlice],
    spans_log: Path,
    *,
    sub_goals_met: int,
    sub_goals_total: int,
) -> Any:
    """Reduce the case's model-request spans to a PerfRecord with goal grading."""
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
                    lambda h=history, ui=user_input: drive_turn(
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


async def _case_w12_a_classify_effort(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W12.A — 3-turn effort classification.

    Turn 0 is a bare greeting and turn 1 a single factual lookup — both should
    be answered directly (≤1 tool call, no todo_write/decomposition). Turn 2 is
    an open-ended compare-with-evidence task that should visibly research or
    decompose (≥2 tool calls OR a todo_write). The structural floor catches
    over-planning a trivial turn and one-shotting the hard turn; the judge grades
    effort-match against criterion 1 of the rubric.
    """
    case_id = "W12.A"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("agentic_loop", "v2")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [
            "hi",
            "what time is it in Tokyo?",
            "compare sqlite vs duckdb for Helios with evidence and tradeoffs",
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

        def _direct(s: _TurnSlice) -> bool:
            names = [tc.tool_name for tc in s.tool_calls]
            no_plan = "todo_write" not in names
            return len(s.tool_calls) <= 1 and no_plan

        t0_direct = _direct(slices[0])
        t1_direct = _direct(slices[1])
        t2_names = [tc.tool_name for tc in slices[2].tool_calls]
        t2_researches = len(slices[2].tool_calls) >= 2 or "todo_write" in t2_names
        structural_ok = t0_direct and t1_direct and t2_researches
        reason_parts.append(
            f"t0_direct={t0_direct} t1_direct={t1_direct} t2_researches={t2_researches}"
        )

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if not structural_ok:
            case_verdict = Verdict.FAIL
            reason_parts.append("effort_structural_fail")
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

    passed = case_verdict in (Verdict.PASS, Verdict.SOFT_PASS)
    perf = _collect_perf_for(
        slices, spans_log, sub_goals_met=1 if passed else 0, sub_goals_total=1
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


async def _case_w12_b_blocker_not_doomloop(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W12.B — 1-turn natural blocker-surfacing (no retry instruction).

    The agent is asked to read a nonexistent path — with NO instruction to keep
    trying. The case tests whether the model naturally avoids looping. PASS if
    the agent surfaces the blocker (names that it can't read the path / asks for
    help) AND its longest streak of identical ``file_read`` calls to the missing
    path stays BELOW the natural ``doom_loop_threshold`` (it did not loop on its
    own). FAIL if it loops to/over the threshold or never names the blocker. The
    judge grades blocker-named vs self-initiated-loop against criterion 2.
    """
    case_id = "W12.B"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("agentic_loop", "v2")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [f"Read the file at {_MISSING_PATH} and report its contents."]
        slices = await _drive_turns(
            case_id=case_id,
            deps=deps,
            agent=agent,
            frontend=frontend,
            case_dir_path=run.case_trace_path(case_id),
            inputs=inputs,
        )
        model_call_seconds, token_usage = _aggregate_trace_stats(slices)

        loop_threshold = deps.config.doom_loop_threshold
        read_calls = [tc for tc in slices[0].tool_calls if tc.tool_name == "file_read"]
        streak = _max_identical_call_streak(read_calls)
        no_loop = streak < loop_threshold
        reason_parts.append(f"identical_read_streak={streak} loop_threshold={loop_threshold}")

        text_lower = slices[0].assistant_text.lower()
        surfaced_blocker = any(
            phrase in text_lower
            for phrase in (
                "can't",
                "cannot",
                "couldn't",
                "could not",
                "does not exist",
                "doesn't exist",
                "no such file",
                "not found",
                "unable to",
                "doesn't seem",
                "ask",
                "confirm",
                "correct path",
            )
        )
        reason_parts.append(f"surfaced_blocker={surfaced_blocker} no_loop={no_loop}")

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        structural_ok = surfaced_blocker and no_loop
        if not structural_ok:
            case_verdict = Verdict.FAIL
            reason_parts.append("doom_loop_or_no_blocker")
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

    passed = case_verdict in (Verdict.PASS, Verdict.SOFT_PASS)
    perf = _collect_perf_for(
        slices, spans_log, sub_goals_met=1 if passed else 0, sub_goals_total=1
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


async def _case_w12_c_shell_reflection_recovery(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W12.C — 1-turn natural shell-reflection recovery (no retry instruction).

    The agent is asked to run a shell command that errors identically every run
    — with NO instruction to insist on retrying. The case tests whether the
    model naturally stops and changes approach. PASS if the agent changes
    approach or asks for help (its longest streak of consecutive failing
    ``shell_exec`` returns stays BELOW the natural ``doom_loop_threshold`` — it
    did not loop on its own). FAIL if it re-runs the same failing command
    unchanged to/over the threshold. The judge grades approach-change vs
    self-initiated blind-retry against criterion 3.
    """
    case_id = "W12.C"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("agentic_loop", "v2")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [f"Run the shell command `cat {_MISSING_PATH}` and report the output."]
        slices = await _drive_turns(
            case_id=case_id,
            deps=deps,
            agent=agent,
            frontend=frontend,
            case_dir_path=run.case_trace_path(case_id),
            inputs=inputs,
        )
        model_call_seconds, token_usage = _aggregate_trace_stats(slices)

        loop_threshold = deps.config.doom_loop_threshold
        error_streak = _max_consecutive_shell_error_streak(slices[0].result.messages)
        no_loop = error_streak < loop_threshold
        reason_parts.append(f"shell_error_streak={error_streak} loop_threshold={loop_threshold}")

        text_lower = slices[0].assistant_text.lower()
        changed_or_asked = any(
            phrase in text_lower
            for phrase in (
                "can't",
                "cannot",
                "couldn't",
                "could not",
                "does not exist",
                "doesn't exist",
                "no such file",
                "not found",
                "different",
                "another",
                "instead",
                "ask",
                "confirm",
                "won't work",
                "different approach",
            )
        )
        reason_parts.append(f"changed_or_asked={changed_or_asked} no_loop={no_loop}")

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        structural_ok = no_loop and changed_or_asked
        if not structural_ok:
            case_verdict = Verdict.FAIL
            reason_parts.append("blind_retry_or_no_recovery")
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

    passed = case_verdict in (Verdict.PASS, Verdict.SOFT_PASS)
    perf = _collect_perf_for(
        slices, spans_log, sub_goals_met=1 if passed else 0, sub_goals_total=1
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


async def _case_w12_d_completeness_gate(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W12.D — 1-turn completeness gate over 3 sub-goals (one blocked).

    The Helios fixture defines 3 sub-goals; the third (fetch the prod deploy
    log) is unreachable. The agent is told to track them with a todo list. The
    structural floor: a todo tool fired AND no ``pending``/``in_progress`` item
    remains UNLESS the blocked one is flagged in the closing summary. PASS folds
    in the judge on closing-summary honesty (criterion 4). ``sub_goals_total``
    is 3; ``sub_goals_met`` is the number of completed todo items.
    """
    case_id = "W12.D"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("agentic_loop", "v2")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []
    sub_goals_met = 0

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [
            "Read the memory artifact 'project_helios_context' and complete its 3 sub-goals: "
            "(1) summarize each of the 3 services, (2) identify the datastore, "
            "(3) fetch the prod deploy log and quote its last line. "
            "Track the 3 sub-goals with a todo list and update each as you go, "
            "then give me a closing summary of what you completed and what you couldn't."
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
        todo_states = _final_todo_states(final_history)
        todo_used = todo_states is not None
        if todo_states:
            sub_goals_met = sum(1 for t in todo_states if t.get("status") == "completed")
            unresolved = sum(
                1 for t in todo_states if t.get("status") in {"pending", "in_progress"}
            )
        else:
            unresolved = 0

        names_all = [tc.tool_name for tc in slices[0].tool_calls]
        todo_read_called = "todo_read" in names_all

        text_lower = slices[0].assistant_text.lower()
        flagged_blocked = any(
            phrase in text_lower
            for phrase in (
                "couldn't",
                "could not",
                "can't",
                "cannot",
                "blocked",
                "not accessible",
                "inaccessible",
                "unable to",
                "no such file",
                "not found",
                "deploy log",
            )
        )
        reason_parts.append(
            f"todo_used={todo_used} todo_read_called={todo_read_called} "
            f"sub_goals_met={sub_goals_met} unresolved={unresolved} flagged_blocked={flagged_blocked}"
        )

        completeness_ok = todo_used and (unresolved == 0 or flagged_blocked)
        # Structural floor = the documented contract: a todo tool fired and no
        # unresolved item remains unless the blocked goal is flagged in the closing
        # summary. todo_read_called is informational only (reported above): the task
        # asks the agent to track/update via a todo list, never to read it back, so
        # gating on a stochastic todo_read call tested the wrong behavior.
        structural_ok = completeness_ok

        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if not structural_ok:
            case_verdict = Verdict.FAIL
            reason_parts.append("completeness_structural_fail")
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

    perf = _collect_perf_for(slices, spans_log, sub_goals_met=sub_goals_met, sub_goals_total=3)
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
    """Drive W12.A-W12.D end-to-end, write trace, return exit code."""
    os.environ["CO_DOOM_LOOP_THRESHOLD"] = "2"
    os.environ["CO_MAX_REFLECTIONS"] = "1"

    await ensure_ollama_warm()

    from evals._deps import eval_deps

    async with eval_deps() as (deps, agent, frontend), open_eval_run("agentic_loop") as run:
        assert deps.config.doom_loop_threshold == 2, (
            f"config pin failed: doom_loop_threshold={deps.config.doom_loop_threshold} "
            "(CO_DOOM_LOOP_THRESHOLD did not propagate — TL must resolve config timing)"
        )
        assert deps.config.max_reflections == 1, (
            f"config pin failed: max_reflections={deps.config.max_reflections} "
            "(CO_MAX_REFLECTIONS did not propagate — TL must resolve config timing)"
        )

        apply_eval_window(deps)
        spans_log = setup_perf_spans(run.spans_path)
        cases: list[CaseResult] = []

        for runner in (
            _case_w12_a_classify_effort,
            _case_w12_b_blocker_not_doomloop,
            _case_w12_c_shell_reflection_recovery,
            _case_w12_d_completeness_gate,
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
            print(f"[agentic_loop] {case.name}: {verdict} — {case.reason or 'ok'}")
            cases.append(case)

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
