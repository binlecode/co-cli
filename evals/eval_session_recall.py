"""UAT eval — cross-session recall via concept expansion (FM-1).

The behavioral delta for ``session-recall-concept-expansion``: a past session
recorded a *shaped* entity (a flight code ``AA890``) and never the word the
user later asks with ("flight"). A literal ``session_search(query="flight")``
is a guaranteed miss — the session never contains that word. The feature's
value is that the agent, guided by the ``session_search`` docstring and the
``07_memory_protocol.md`` recall cascade, expands intent into a structural
``session_search(pattern=...)`` regex and recovers the entity.

What is asserted here is the *behavior* (intent → pattern call → recovered
entity), NOT the engine mechanics (those are pytest's job in
``tests/test_flow_session_search.py``). A passing engine test does not prove
the model ever reaches for the pattern path — only docstring + rule elicit it,
with nothing in code forcing it.

Specs: docs/specs/sessions.md, memory.md
Plan:  docs/exec-plans/active/2026-06-16-143837-session-recall-concept-expansion.md

Real-data discipline (Constraint: all data real, no test stores, no cleanup):
the fixture is a real JSONL transcript written into the real ``deps.sessions_dir``
under CO_HOME, indistinguishable from a genuine past session. The agent runs a
real turn against the real local model; the eval-only Gemini judge grades it.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S
from evals._trace import record_turn, response_text

from co_cli.context.orchestrate import run_turn
from co_cli.session.filename import session_filename

_FIXTURE_UUID8 = "f1a2b3c4"
_FIXTURE_CONTENT = (
    "Booking confirmed: AA890 delayed two hours, new departure 18:40. "
    "I checked it earlier and noted the gate change."
)
"""The seeded past session: a shaped entity (AA890) with no literal 'flight' handle."""


def _seed_flight_session(sessions_dir: Path) -> Path:
    """Write a real JSONL transcript whose only recallable handle is the shaped code.

    Timestamped *now* so recency ranking favors it over older real sessions on a
    match-count tie (ranking is score desc, created_at desc).
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC)
    path = sessions_dir / session_filename(created_at, _FIXTURE_UUID8)
    line = json.dumps([{"parts": [{"part_kind": "user-prompt", "content": _FIXTURE_CONTENT}]}])
    path.write_text(line + "\n", encoding="utf-8")
    return path


def _pattern_call_made(tool_calls: list[Any]) -> bool:
    """True iff the agent invoked session_search with a non-empty regex ``pattern``."""
    for call in tool_calls:
        if call.tool_name != "session_search":
            continue
        try:
            args = json.loads(call.args) if isinstance(call.args, str) else call.args
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(args, dict) and str(args.get("pattern", "")).strip():
            return True
    return False


def _entity_recovered(tool_calls: list[Any], answer: str) -> bool:
    """True iff AA890 surfaced — in the final answer or any session_search result."""
    if "AA890" in answer:
        return True
    for call in tool_calls:
        if call.tool_name == "session_search" and "AA890" in (call.result or ""):
            return True
    return False


async def case_sr_a_concept_expansion(
    deps: Any, agent: Any, frontend: Any, run: Any
) -> CaseResult:
    """Seed a shaped-entity session, ask in different words, expect a pattern= recall."""
    case_id = "SR.A"
    t0 = time.monotonic()
    reason = ""
    passed = True
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
    case_dir = run.case_trace_path(case_id)

    _seed_flight_session(deps.sessions_dir)

    user_input = (
        "What flight did you check for me last time? I can't remember the code — "
        "see if it's in a past conversation."
    )
    answer = ""
    tool_calls: list[Any] = []
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            turn_result, turn_trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=user_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=user_input,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                ),
                case_dir_path=case_dir,
                agent=agent,
            )
        model_call_seconds += turn_trace.model_call_seconds
        for k, v in turn_trace.token_usage.items():
            token_usage[k] = token_usage.get(k, 0) + v
        tool_calls = turn_trace.tool_calls
        answer = response_text(turn_result)
    except Exception as exc:
        passed = False
        reason = f"recall turn failed: {type(exc).__name__}: {exc}"

    pattern_made = _pattern_call_made(tool_calls) if passed else False
    recovered = _entity_recovered(tool_calls, answer) if passed else False
    if passed and not pattern_made:
        passed = False
        reason = "agent never issued a session_search(pattern=...) call after the literal miss"
    elif passed and not recovered:
        passed = False
        reason = "pattern call made but AA890 not recovered in answer or tool result"

    verdict = Verdict.PASS if passed else Verdict.FAIL
    judge_annotation = judge_model_annotation(deps)
    if passed:
        reason = "expanded literal→pattern; recovered AA890"
        rubric = (
            "The user asked which flight was checked last time, without the code, and "
            "asked to look in a past conversation. The only past session recorded it as "
            "'AA890' and never used the word 'flight'. "
            "PASS only if the agent's answer reports AA890 as the flight, recalled from "
            "the past session. FAIL if it says it cannot find it or gives a different code."
        )
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                jverdict = await judge_with_llm(
                    rubric, turn_result.messages, deps=deps, model=deps.judge_model
                )
            judge_note = f"judge.score={jverdict.score} {judge_annotation}"
            if jverdict.rationale:
                judge_note += f" {jverdict.rationale[:120]}"
            if not jverdict.passed:
                verdict = Verdict.SOFT_FAIL
            reason += f" | {judge_note}"
        except Exception as jexc:
            reason += f" | judge_error: {type(jexc).__name__}"

    duration = time.monotonic() - t0
    result = CaseResult(
        name=case_id,
        verdict=verdict,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        reason=reason,
    )
    run.append(result)
    return result


async def main() -> int:
    """Run the cross-session concept-expansion case against the real workspace."""
    await ensure_ollama_warm()

    deps, agent, frontend, stack = await make_eval_deps()
    apply_eval_window(deps)
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("session-recall") as run:
            case_a = await case_sr_a_concept_expansion(deps, agent, frontend, run)
            cases.append(case_a)
            print(
                f"[session-recall] {case_a.name}: "
                f"{'PASS' if case_a.passed else 'FAIL'} — {case_a.reason or 'ok'}"
            )
    finally:
        await stack.aclose()

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
