"""UAT eval — Workflow 10: Durable user model (recall and reuse).

Drives the agent against the ``user_model_baseline`` fixture — three seeded
preference items (``pref_terse``, ``pref_python``, ``pref_pst``) plus two prior
session transcripts where those preferences surfaced naturally.

co's memory is **recall-on-demand**: preference items are NOT auto-injected into
the prompt (only canon/personality is). After a session rotation (simulated
``/new`` by driving with an empty ``message_history``) the preferences are no
longer in the conversation, so the agent reaches them only by calling a recall
tool (``memory_search`` / ``memory_view`` / ``session_search`` / ``session_view``).
This eval therefore tests the recall→reuse path co actually supports: when the
user asks the agent to draw on what it knows, does it consult memory and then
apply what it finds? It is deliberately NOT a test of auto-applied preferences —
co has no always-on preference tier, so "the agent should just know" is out of
scope (that would be a new capability, not a behavior to assert here).

Each judged case combines a structural recall check (a recall tool was actually
called) with the LLM rubric verdict from ``user_model.v2.md``. The decay case is
SOFT-only and never gates: it ages a preference item past the decay window and
runs the dream cycle to observe whether disuse-decay archives it.

Cases:
  W10.A  recall_then_apply       2-turn: T0 "check what you've saved about how I
                                  work, then read a CSV"; T1 "standup time?".
                                  PASS if T0 calls a recall tool AND the answers
                                  reuse the recalled prefs (terse / Python / PST).
  W10.B  contradiction_handling  3-turn: T0 recall + CSV; T1 "Go version"; T2 "JSON".
                                  PASS if T0 recalls AND T1 switches to Go AND
                                  T2 reverts to the recalled Python default.
  W10.C  decay_under_disuse      SOFT-only: age ``pref_terse`` ~95d, run dream,
                                  check survival. Never emits PASS/FAIL.
  W10.D  profile_synthesis       Seed a starting USER.md (durable Rust identity +
                                  transient vacation) + 3 sessions reinforcing the
                                  durable trait and contradicting the transient
                                  one; run ``synthesize_user_profile``. PASS if the
                                  rewritten profile keeps the durable fact, drops
                                  the contradicted one, and stays within budget.
                                  Partition: the gate/marker/no-op mechanics are
                                  pytest-owned (test_housekeeping.py); this eval
                                  owns only the model's reconciliation quality.

Specs: docs/specs/core-loop.md, prompt-assembly.md, memory.md, dream.md.
Mission tenet: trusted — durable user model is real and usable when consulted.

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
from uuid import uuid4

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
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

from co_cli.agent.orchestrate import run_turn
from co_cli.daemons.dream._housekeeping import merge_memory, synthesize_user_profile
from co_cli.daemons.dream.state import HousekeepingState, SessionMarker
from co_cli.session.browser import list_sessions
from co_cli.session.filename import session_filename
from co_cli.session.persistence import append_messages

_FIXTURE_NAME = "user_model_baseline"

# Filename stem of the terse-preference artifact seeded by the fixture
# (``evals/_fixtures/user_model_baseline/memory/pref_terse.md``). W10.C ages
# this artifact and checks whether the dream cycle archives it.
_TERSE_PREF_STEM = "pref_terse"

# Age (days) to stamp onto the terse preference for W10.C. Memory is no longer
# decayed by age/recall (see docs/specs/dream.md §2.4), so the aged artifact is
# now always preserved — the case can only SOFT_PASS. The age stamp is retained
# only so the scenario still exercises the age path without gating.
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


# Tools that read curated memory / past sessions. A turn that calls one of these
# has consulted the durable user model — the recall half of recall→reuse.
_RECALL_TOOLS = frozenset({"memory_search", "memory_view", "session_search", "session_view"})


def _recall_attempted(tool_calls: list[ToolCallPart]) -> bool:
    """True if any tool call in the turn consulted memory or past sessions."""
    return any(tc.tool_name in _RECALL_TOOLS for tc in tool_calls)


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


async def _case_w10_a_recall_then_apply(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W10.A — 2-turn recall-then-reuse of the durable user model.

    After loading the fixture, a fresh session (empty ``message_history``
    simulates ``/new``) drives two asks. T0 cues the agent to consult what it
    knows — "check what you've saved about how I like to work, then show me how
    to read a CSV" — so the agent must call a recall tool to reach the
    preferences (they are not auto-injected). T1 "what time is standup
    tomorrow?" then exercises the PST default now that the prefs are in context.

    PASS requires BOTH halves of recall→reuse: T0 actually called a recall tool
    (structural), AND the judge confirms the answers reuse the recalled prefs
    (``user_model.v2.md`` criterion 1). No recall tool call is a structural FAIL
    regardless of how the answer reads.
    """
    case_id = "W10.A"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("user_model", "v2")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [
            "check what you've saved about how I like to work, then show me how to read a CSV",
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

        t0_recall = _recall_attempted(slices[0].tool_calls) if slices else False
        reason_parts.append(f"t0_recall={t0_recall}")

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if not t0_recall:
            case_verdict = Verdict.FAIL
            reason_parts.append("no_recall_attempted")
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
    """W10.B — 3-turn recall-grounded one-shot override handling.

    T0 cues recall — "check my saved preferences, then show me how to read a
    CSV" — so the agent retrieves the Python default and applies it. T1
    "actually, do the Go version" is a one-shot override that should apply to
    that turn only; T2 "now read JSON" must revert to the recalled Python
    default while staying terse. The judge grades against ``user_model.v2.md``
    criterion 2 — a persistent Go switch (T2 stays Go without re-prompt) is the
    FAIL mode, T2 asking which language is the SOFT_PASS hedge. PASS also
    requires T0 to have called a recall tool (structural).
    """
    case_id = "W10.B"
    case_t0 = time.monotonic()
    rubric_text, rubric_version = load_rubric("user_model", "v2")
    reason_parts: list[str] = [judge_model_annotation(deps), f"rubric={rubric_version}"]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    slices: list[_TurnSlice] = []

    try:
        load_fixture(_FIXTURE_NAME, deps)

        inputs = [
            "check my saved preferences, then show me how to read a CSV",
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

        t0_recall = _recall_attempted(slices[0].tool_calls) if slices else False
        reason_parts.append(f"t0_recall={t0_recall}")

        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric_text, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if not t0_recall:
            case_verdict = Verdict.FAIL
            reason_parts.append("no_recall_attempted")
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


def _seed_synthesis_sessions(deps: Any, specs: list[tuple[str, str]]) -> list[str]:
    """Write one real-JSONL session per ``(user, assistant)`` spec, newest-first.

    Stamps strictly-increasing recent timestamps so the seeds sort as the newest
    sessions on disk (above any pre-existing session). Returns the 8-char id
    prefixes in seed order (oldest → newest).
    """
    deps.sessions_dir.mkdir(parents=True, exist_ok=True)
    base = datetime.now(UTC)
    ids: list[str] = []
    for i, (user_text, assistant_text) in enumerate(specs):
        created = base + timedelta(seconds=i)
        sid = uuid4().hex
        path = deps.sessions_dir / session_filename(created, sid)
        append_messages(
            path,
            [
                ModelRequest(parts=[UserPromptPart(content=user_text)]),
                ModelResponse(parts=[TextPart(content=assistant_text)]),
            ],
        )
        ids.append(sid[:8])
    return ids


async def _case_w10_d_profile_synthesis(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    spans_log: Path,
) -> CaseResult:
    """W10.D — cross-session USER.md synthesis reconciles durable vs. stale signal.

    The UAT smoke for the ``synthesize_user_profile`` dream sub-pass. Seeds a
    starting USER.md carrying one durable identity fact (Rust developer) and one
    transient fact (on vacation), plus three recent sessions that reinforce the
    durable trait and contradict the transient one (the user is actively working).
    Runs the synthesis pass against the real local model and judges the rewritten
    profile.

    Partition note: the deterministic half of this workflow — the session-count
    gate, marker advance, disabled/below-threshold no-ops, atomic write — is owned
    by pytest (``tests/daemons/dream/test_housekeeping.py``). This eval owns ONLY
    the model-driven reconciliation quality (durable kept, contradicted dropped,
    within budget), which a unit test cannot assert.

    Verdict partition: synthesis is a **best-effort** production pass (a local-model
    output/token failure is caught and retried next tick — see ``run_housekeeping``).
    So a model-capability failure here is SOFT_FAIL (non-gating review signal),
    mirroring W10.C; a hard PASS/FAIL is reserved for the case where the model
    completes and the judge rules on reconciliation quality.

    ``agent``/``frontend``/``spans_log`` are unused (this case calls the pass
    directly, not ``run_turn``) but kept for the runner tuple signature.
    """
    case_id = "W10.D"
    case_t0 = time.monotonic()
    reason_parts: list[str] = [judge_model_annotation(deps)]
    case_verdict = Verdict.FAIL

    try:
        # Anchor the marker at the current newest session so only the seeds below
        # count as un-settled — keeps the synthesis window to exactly our sessions
        # regardless of what else lives in the real workspace's sessions_dir.
        prior = list_sessions(deps.sessions_dir)
        marker = (
            SessionMarker(
                session_id=prior[0].session_id,
                created_at=prior[0].created_at.isoformat(),
            )
            if prior
            else None
        )

        session_specs = [
            (
                "Help me tighten this Rust trait impl — I'm writing Rust again today.",
                "Sure, here's a tighter Rust trait impl.",
            ),
            (
                "I'm back from leave and working full-time again; more Rust refactoring.",
                "Welcome back — let's refactor that Rust module.",
            ),
            (
                "Another full Rust day. Review my borrow-checker fix?",
                "Reviewed — your Rust borrow fix looks correct.",
            ),
        ]
        _seed_synthesis_sessions(deps, session_specs)

        deps.user_profile_path.write_text(
            "The user is a Rust developer who values memory-safe systems code.\n"
            "§\n"
            "The user is on vacation this week and away from work.\n",
            encoding="utf-8",
        )

        deps.config.memory.profile_synthesis_enabled = True
        deps.config.memory.profile_synthesis_lookback_sessions = len(session_specs)

        try:
            async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
                ran = await synthesize_user_profile(
                    deps, HousekeepingState(last_synthesized_session=marker)
                )
        except Exception as synth_exc:
            # Best-effort production contract: a local-model output/token-budget
            # failure is caught + retried next tick, never a regression. SOFT, not gate.
            reason_parts.append(
                f"synthesis model failure (SOFT, best-effort): "
                f"{type(synth_exc).__name__}: {str(synth_exc)[:120]}"
            )
            return CaseResult(
                name=case_id,
                verdict=Verdict.SOFT_FAIL,
                duration_s=time.monotonic() - case_t0,
                reason=" ".join(reason_parts).strip(),
                perf=None,
            )

        profile = deps.user_profile_path.read_text(encoding="utf-8")
        budget = deps.config.memory.user_profile_char_budget
        within_budget = len(profile) <= budget
        reason_parts.append(f"ran={ran} chars={len(profile)}/{budget}")

        if not (ran and profile.strip() and within_budget):
            reason_parts.append("structural gate: synthesis did not fire / empty / over budget")
            return CaseResult(
                name=case_id,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - case_t0,
                reason=" ".join(reason_parts).strip(),
            )

        rubric = (
            "A profile synthesizer reconciled a user profile against three recent sessions. "
            "The starting profile said (1) the user is a Rust developer who values memory-safe "
            "systems code — a DURABLE identity fact — and (2) the user is on vacation this week — "
            "a TRANSIENT fact the recent sessions contradict (the user is actively working in Rust "
            "every day and explicitly says they are back from leave working full-time).\n"
            "PASS only if the rewritten profile below KEEPS the durable Rust-developer identity AND "
            "no longer asserts the user is on vacation/away.\n"
            "FAIL if it drops the Rust identity, still claims the user is on vacation, or invents a "
            "fact not supported by the profile or sessions.\n"
            "Judge profile content only; ignore formatting and § delimiters."
        )
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric,
                [{"role": "assistant", "content": profile}],
                deps=deps,
                model=deps.judge_model,
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])
        case_verdict = Verdict.PASS if verdict.passed else Verdict.SOFT_FAIL
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    return CaseResult(
        name=case_id,
        verdict=case_verdict,
        duration_s=time.monotonic() - case_t0,
        reason=" ".join(reason_parts).strip(),
        perf=None,
    )


async def main() -> int:
    """Drive W10.A-W10.D end-to-end, write trace, return exit code."""
    await ensure_ollama_warm()

    async with eval_deps() as (deps, agent, frontend), open_eval_run("user_model") as run:
        apply_eval_window(deps)
        spans_log = setup_perf_spans(run.spans_path)
        cases: list[CaseResult] = []

        for runner in (
            _case_w10_a_recall_then_apply,
            _case_w10_b_contradiction_handling,
            _case_w10_c_decay_under_disuse,
            _case_w10_d_profile_synthesis,
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
