"""UAT eval — cross-session recall via concept expansion (FM-1).

The behavioral delta for ``session-recall-concept-expansion``: a past session
recorded a *shaped* entity (a parcel tracking number, ``1Z...`` shape) and never
the word the user later asks with ("parcel"). A literal
``session_search(query="parcel")`` is a guaranteed miss — the session never
contains that word. The feature's value is that the agent, guided by the
``session_search`` docstring and the ``07_memory_protocol.md`` recall cascade,
expands intent into a structural ``session_search(pattern=...)`` regex and
recovers the entity.

What is asserted here is the *outcome* — after the plain-word search misses, the
agent recovers the shaped entity from a past conversation — NOT a specific tool
call. The recall cascade legitimately reaches the entity by several routes
(``pattern=`` regex, a shape-prefix ``query=``, or a recency browse); pinning the
assertion to the ``pattern=`` arg tests a structural mechanism the model is free
to substitute, so we gate on recovery and record which route was used as a
signal. The engine mechanics are pytest's job in
``tests/test_flow_session_search.py``; this eval proves the model actually
performs cross-session recall, which only the docstring + rule elicit.

Specs: docs/specs/sessions.md, memory.md
Plan:  docs/exec-plans/active/2026-06-16-143837-session-recall-concept-expansion.md

Real-data discipline (Constraint: all data real, no test stores, no cleanup):
the fixture is a real JSONL transcript written into the real ``deps.sessions_dir``
under CO_HOME, indistinguishable from a genuine past session. The agent runs a
real turn against the real local model; the eval-only Gemini judge grades it.

Pollution-resilience (why this eval does not test "flight"): the probe word must
genuinely MISS the literal search over the *real* accumulated corpus, or the
concept-expansion trigger never fires. The original "flight"/``AA890`` domain is
now permanently poisoned — the live incident transcript that motivated the
feature literally contains "flight", so the literal probe hits it and the agent
never expands. Two defenses keep this eval honest against accumulation:
  * the probe concept ("parcel") is absent from the real session corpus;
  * the shaped entity is regenerated UNIQUE per run, so a stale hit from a prior
    run's transcript can never satisfy recovery — only the current fixture,
    reached via a ``pattern=`` search, carries the current code.
The eval still slowly re-seeds its own probe word into future transcripts; the
per-run-unique entity is what stops that self-pollution from producing a false
pass. The deterministic fixture is overwritten in place rather than accumulated.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

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


def _make_tracking_code() -> str:
    """A per-run-unique parcel tracking number in the recognizable UPS ``1Z`` shape.

    Unique per run so a stale code surfaced from a prior run's transcript can never
    satisfy the recovery assertion — only the current fixture, reached via a
    ``pattern=`` search for the ``1Z`` shape, carries this run's code.
    """
    return f"1Z{uuid4().hex[:16].upper()}"


def _seed_parcel_session(sessions_dir: Path) -> tuple[Path, str]:
    """Write a real JSONL transcript whose only recallable handle is the shaped code.

    Overwrites the deterministic fixture in place (prior fixtures sharing this eval's
    fixed uuid8 are removed first) so reruns do not accumulate seed sessions. Returns
    the path and the per-run-unique tracking code. Timestamped *now* so recency
    ranking favors it over older real sessions on a match-count tie (score desc,
    created_at desc). The content carries no literal 'parcel' handle.
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    for stale in sessions_dir.glob(f"*-{_FIXTURE_UUID8}.jsonl"):
        stale.unlink()
    code = _make_tracking_code()
    content = (
        f"Confirmed: {code} out the door, ETA 18:40. "
        "I pulled it up earlier and noted the slot for you."
    )
    created_at = datetime.now(UTC)
    path = sessions_dir / session_filename(created_at, _FIXTURE_UUID8)
    line = json.dumps([{"parts": [{"part_kind": "user-prompt", "content": content}]}])
    path.write_text(line + "\n", encoding="utf-8")
    return path, code


def _session_search_made(tool_calls: list[Any]) -> bool:
    """True iff the agent engaged session recall at all (any session_search call)."""
    return any(call.tool_name == "session_search" for call in tool_calls)


def _pattern_call_made(tool_calls: list[Any]) -> bool:
    """True iff the agent invoked session_search with a non-empty regex ``pattern``.

    Recorded as a signal in the verdict reason — the designed expansion path — but
    not the gate; recovery via a shape-prefix query or browse is equally valid.
    """
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


def _entity_recovered(tool_calls: list[Any], answer: str, code: str) -> bool:
    """True iff the run's tracking code surfaced — in the answer or a session_search result."""
    if code in answer:
        return True
    for call in tool_calls:
        if call.tool_name == "session_search" and code in (call.result or ""):
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

    _, code = _seed_parcel_session(deps.sessions_dir)

    user_input = (
        "What parcel tracking number did you pull up for me last time? I lost the "
        "carrier email and can't remember the code — see if it's in a past conversation."
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

    searched = _session_search_made(tool_calls) if passed else False
    recovered = _entity_recovered(tool_calls, answer, code) if passed else False
    pattern_made = _pattern_call_made(tool_calls) if passed else False
    verdict = Verdict.PASS if passed else Verdict.FAIL
    if passed and not searched:
        passed = False
        verdict = Verdict.FAIL
        reason = "agent never engaged session recall (no session_search call)"
    elif passed and not recovered:
        passed = False
        verdict = Verdict.SOFT_FAIL
        reason = (
            f"engaged session recall but did not surface {code} this run "
            "(concept-expansion is model-elicited — review signal, within LLM variance)"
        )

    judge_annotation = judge_model_annotation(deps)
    if passed:
        route = "pattern=" if pattern_made else "query/browse"
        reason = f"recovered {code} cross-session via {route} after literal miss"
        rubric = (
            "The user asked which parcel tracking number was pulled up last time, without "
            "the code, and asked to look in a past conversation. The only past session "
            f"recorded it as '{code}' and never used the word 'parcel'. "
            f"PASS only if the agent's answer reports {code} as the tracking number, "
            "recalled from the past session. FAIL if it says it cannot find it or gives a "
            "different code."
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
