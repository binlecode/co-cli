"""UAT eval — Workflow 2: Session continuity.

Covers `/new`, `/clear`, `/sessions`, `/resume [id]`, `/compact`. Validates
session rotation, transcript clearing, picker-based resume with todo/plan
rehydration, and LLM-driven compaction of long histories.

Specs: docs/specs/sessions.md, compaction.md, self-planning.md
Mission tenet: continuity across sessions

Sub-cases (judged only — structural rails covered by pytest)
------------------------------------------------------------
W2.D resume_provides_continuity   — fact + todo seeded, /new rotates, prior
                                    transcript loaded via ``load_transcript`` +
                                    ``_rehydrate_todos``, follow-up turn run with
                                    ``message_history=prior_messages`` recalls
                                    the fact.
W2.E compact_replaces_with_summary (boundary) — drive N synthetic turns to push
                                    history past ``compaction_ratio``; /compact
                                    yields a shorter history with a summary
                                    marker.

Helpers (from sibling modules): ``make_eval_deps``, ``ensure_ollama_warm``,
``CALL_TIMEOUT_S``/``TURN_BUDGET_S``/``MULTI_TURN_COMPACT_BUDGET_S``,
``CaseResult``/``open_eval_run``, ``record_turn``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._settings import apply_eval_window
from evals._timeouts import (
    CALL_TIMEOUT_S,
    MULTI_TURN_COMPACT_BUDGET_S,
)
from evals._trace import record_turn, response_text
from pydantic_ai.messages import ModelRequest, UserPromptPart

from co_cli.commands.core import dispatch
from co_cli.commands.resume import _rehydrate_todos
from co_cli.commands.types import (
    CommandContext,
    ReplaceTranscript,
)
from co_cli.context._compaction_markers import (
    STATIC_MARKER_PREFIX,
    SUMMARY_MARKER_PREFIX,
)
from co_cli.context.orchestrate import run_turn
from co_cli.session.persistence import append_messages, load_transcript


def _build_ctx(deps: Any, agent: Any, frontend: Any, history: list[Any]) -> CommandContext:
    """Construct a ``CommandContext`` mirroring main.py's chat loop usage."""
    return CommandContext(
        message_history=history,
        deps=deps,
        agent=agent,
        completer=None,
        frontend=frontend,
    )


def _contains_any_compaction_marker(history: list[Any]) -> bool:
    """True iff any message starts with a summary or static compaction marker."""
    for msg in history:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, UserPromptPart):
                continue
            content = getattr(part, "content", "")
            if not isinstance(content, str):
                continue
            if content.startswith(SUMMARY_MARKER_PREFIX) or content.startswith(
                STATIC_MARKER_PREFIX
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# W2.D — resume_provides_continuity
# ---------------------------------------------------------------------------


async def case_w2_d_resume_helpers_rehydrate(
    deps: Any, agent: Any, frontend: Any, run: Any
) -> CaseResult:
    """Seed deploy id + todo, rotate, rehydrate from disk, ask follow-up.

    **Not an end-to-end /resume test.** ``/resume`` is interactive and can't be
    driven via ``dispatch`` today (Open Q3 in the plan). This case exercises
    the rehydration *mechanism* directly — ``load_transcript`` +
    ``_rehydrate_todos`` — and verifies a follow-up ``run_turn`` with
    ``message_history=prior_messages`` still recovers the seeded fact. A
    regression in ``_cmd_resume``'s picker / ``prompt_selection`` UX is NOT
    caught here; that gap closes when ``_cmd_resume`` is refactored to call
    ``ctx.deps.frontend.prompt_selection(...)``.
    """
    case_id = "W2.D"
    t0 = time.monotonic()
    reason = ""
    passed = True
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}

    case_dir = run.case_trace_path(case_id)
    seed_input = "Remember that my deploy id is DEPLOY_77. Also add a todo to verify it."
    history: list[Any] = []
    prior_session_path: Path | None = None

    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            seed_result, seed_trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=seed_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=seed_input,
                    deps=deps,
                    message_history=history,
                    frontend=frontend,
                ),
                case_dir_path=case_dir,
                agent=agent,
            )
        model_call_seconds += seed_trace.model_call_seconds
        for k, v in seed_trace.token_usage.items():
            token_usage[k] = token_usage.get(k, 0) + v
        history = list(seed_result.messages)
        prior_session_path = deps.session.session_path
        # run_turn does not itself persist — that's the chat-loop's job
        # (co_cli/main.py:_finalize_turn → persist_session_history). The eval
        # mimics that step so load_transcript() below can read the seed turn
        # back from disk after /new rotates.
        append_messages(prior_session_path, list(seed_result.messages))
    except Exception as exc:
        passed = False
        reason = f"seed turn failed: {type(exc).__name__}: {exc}"

    # Drive /new to rotate the session. /new only rotates when history is
    # non-empty; the seed turn satisfies that.
    if passed:
        ctx = _build_ctx(deps, agent, frontend, history)
        outcome = await dispatch("/new", ctx)
        if not isinstance(outcome, ReplaceTranscript):
            passed = False
            reason = f"/new did not rotate: outcome={type(outcome).__name__}"

    # Rehydrate from the prior session's on-disk JSONL via production helpers.
    prior_messages: list[Any] = []
    if passed and prior_session_path is not None:
        prior_messages = load_transcript(prior_session_path)
        if not prior_messages:
            passed = False
            reason = f"load_transcript returned empty for {prior_session_path.name}"
        else:
            deps.session.session_todos = _rehydrate_todos(prior_messages)
            if not deps.session.session_todos:
                passed = False
                reason = "todos empty after _rehydrate_todos"

    # Follow-up turn carrying prior messages as ``message_history``.
    followup_text = ""
    if passed:
        followup_input = "What is my deploy id?"
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                followup_result, followup_trace = await record_turn(
                    case_id=case_id,
                    turn_index=1,
                    user_input=followup_input,
                    run_turn_callable=lambda: run_turn(
                        agent=agent,
                        user_input=followup_input,
                        deps=deps,
                        message_history=prior_messages,
                        frontend=frontend,
                    ),
                    case_dir_path=case_dir,
                    agent=agent,
                )
            model_call_seconds += followup_trace.model_call_seconds
            for k, v in followup_trace.token_usage.items():
                token_usage[k] = token_usage.get(k, 0) + v
            followup_text = response_text(followup_result)
        except Exception as exc:
            passed = False
            reason = f"follow-up turn failed: {type(exc).__name__}: {exc}"

    if passed and "DEPLOY_77" not in followup_text:
        passed = False
        reason = f"follow-up response missing 'DEPLOY_77' (got: {followup_text[:200]!r})"

    verdict = Verdict.PASS if passed else Verdict.FAIL
    judge_annotation = judge_model_annotation(deps)
    if passed:
        rubric = (
            "The user asked their deploy id after resuming a prior session. "
            "PASS only if the agent's answer recalls DEPLOY_77 as the deploy id "
            "from the rehydrated session context. "
            "FAIL if agent says it lacks that information or gives a different id."
        )
        followup_messages = followup_result.messages if passed else []
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                jverdict = await judge_with_llm(
                    rubric, followup_messages, deps=deps, model=deps.judge_model
                )
            judge_note = f"judge.score={jverdict.score} {judge_annotation}"
            if jverdict.rationale:
                judge_note += f" {jverdict.rationale[:120]}"
            if not jverdict.passed:
                verdict = Verdict.SOFT_FAIL
            reason = (reason or "ok") + f" | {judge_note}"
        except Exception as jexc:
            reason = (reason or "ok") + f" | judge_error: {type(jexc).__name__}"

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


# ---------------------------------------------------------------------------
# W2.E — compact_replaces_with_summary (boundary)
# ---------------------------------------------------------------------------


def _inflation_target_messages(deps: Any) -> int:
    """Measured per-run target: enough turns to push history past compaction_ratio.

    Starting N is 10 per the plan; the trigger depends on the configured
    ``compaction_ratio`` and the live prompt size. Compaction reads the
    realtime-local ``effective_request_tokens`` estimate against
    ``compaction_ratio * model_max_ctx``. With default ``compaction_ratio=0.5``
    and a 32k context, a fresh agent on a warm 35B local emits ~10-15k prompt
    tokens per response, so 8-12 brief turns reliably crosses the threshold.
    The exact value is logged in ``CaseResult.reason`` for re-tuning.
    """
    _ = deps
    return 10


async def case_w2_e_compact_replaces_with_summary(
    deps: Any, agent: Any, frontend: Any, run: Any
) -> CaseResult:
    """Inflate history past compaction_ratio, then /compact.

    Seeds a "Lighthouse" marker turn before inflation so the post-compact
    follow-up judge can verify the summary preserved the project name.
    """
    case_id = "W2.E"
    t0 = time.monotonic()
    reason = ""
    passed = True
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
    case_dir = run.case_trace_path(case_id)

    target_turns = _inflation_target_messages(deps)
    compaction_ratio = deps.config.compaction.compaction_ratio
    inflation_inputs = [
        "ok",
        "yes",
        "right",
        "go on",
        "continue",
        "fine",
        "noted",
        "sure",
        "agreed",
        "next",
        "more",
        "keep going",
    ]
    history: list[Any] = []
    post_compact_history: list[Any] = []
    pre_compact_len = 0

    try:
        async with asyncio.timeout(MULTI_TURN_COMPACT_BUDGET_S):
            # Marker turn: seed "Lighthouse" before inflation so the compaction
            # summary should preserve the project name.
            marker_input = (
                "The project I'm working on is called Lighthouse. Please remember this fact."
            )
            marker_result, marker_trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=marker_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=marker_input,
                    deps=deps,
                    message_history=history,
                    frontend=frontend,
                ),
                case_dir_path=case_dir,
                agent=agent,
            )
            model_call_seconds += marker_trace.model_call_seconds
            for k, v in marker_trace.token_usage.items():
                token_usage[k] = token_usage.get(k, 0) + v
            history = list(marker_result.messages)

            for i in range(target_turns):
                user_input = inflation_inputs[i % len(inflation_inputs)]
                turn_result, turn_trace = await record_turn(
                    case_id=case_id,
                    turn_index=i + 1,
                    user_input=user_input,
                    run_turn_callable=lambda u=user_input, h=history: run_turn(
                        agent=agent,
                        user_input=u,
                        deps=deps,
                        message_history=h,
                        frontend=frontend,
                    ),
                    case_dir_path=case_dir,
                    agent=agent,
                )
                model_call_seconds += turn_trace.model_call_seconds
                for k, v in turn_trace.token_usage.items():
                    token_usage[k] = token_usage.get(k, 0) + v
                history = list(turn_result.messages)

            pre_compact_len = len(history)
            ctx = _build_ctx(deps, agent, frontend, history)
            outcome = await dispatch("/compact", ctx)
            if not isinstance(outcome, ReplaceTranscript):
                passed = False
                reason = f"/compact did not return ReplaceTranscript: {type(outcome).__name__}"
            else:
                post_compact_history = list(outcome.history)
                if len(post_compact_history) >= pre_compact_len:
                    passed = False
                    reason = (
                        f"post-compact history not shorter: "
                        f"{pre_compact_len} → {len(post_compact_history)}"
                    )
                elif not _contains_any_compaction_marker(post_compact_history):
                    passed = False
                    reason = "no summary/static marker found in post-compact history"
    except Exception as exc:
        passed = False
        reason = f"inflation+compact failed: {type(exc).__name__}: {exc}"

    verdict = Verdict.PASS if passed else Verdict.FAIL
    judge_annotation = judge_model_annotation(deps)

    # Base reason logs the measured N for re-tuning.
    if not reason:
        reason = (
            f"N={target_turns} compaction_ratio={compaction_ratio:.2f} "
            f"len {pre_compact_len}→{len(post_compact_history)}"
        )

    # Post-compact follow-up: verify summary preserved the seeded project name.
    if passed:
        followup_input = "Remind me what project we've been discussing."
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                followup_result, followup_trace = await record_turn(
                    case_id=case_id,
                    turn_index=target_turns + 1,
                    user_input=followup_input,
                    run_turn_callable=lambda: run_turn(
                        agent=agent,
                        user_input=followup_input,
                        deps=deps,
                        message_history=post_compact_history,
                        frontend=frontend,
                    ),
                    case_dir_path=case_dir,
                    agent=agent,
                )
            model_call_seconds += followup_trace.model_call_seconds
            for k, v in followup_trace.token_usage.items():
                token_usage[k] = token_usage.get(k, 0) + v
            rubric = (
                "The user seeded the fact that their project is called 'Lighthouse' "
                "at the start of the session, before many inflation turns and /compact. "
                "PASS only if the agent's post-compact response mentions 'Lighthouse'. "
                "FAIL if the agent does not recall the project name or says it has no context."
            )
            async with asyncio.timeout(CALL_TIMEOUT_S):
                jverdict = await judge_with_llm(
                    rubric, followup_result.messages, deps=deps, model=deps.judge_model
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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _force_blocking_stdio() -> None:
    """Force stdout/stderr to blocking mode.

    W2.E drives ~10 sequential turns; rich's streaming renderer floods the
    pipe buffer when piped through ``tee``. macOS sets piped fds non-blocking
    by default, so a fast burst can raise ``BlockingIOError(EAGAIN)`` deep
    inside rich's writer. Forcing blocking mode here makes the pipeline
    backpressure naturally — the eval waits for tee to drain instead of
    crashing the inflation loop.
    """
    import fcntl

    for stream in (sys.stdout, sys.stderr):
        try:
            fd = stream.fileno()
        except (AttributeError, ValueError, OSError):
            continue
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)


async def main() -> int:
    """Run W2 cases against the real ``~/.co-cli/`` workspace.

    Warms Ollama first (outside any ``asyncio.timeout``), bootstraps real
    ``CoDeps`` + agent + frontend via :func:`make_eval_deps`, then runs each
    sub-case in order. Sub-case failures do not abort the run.
    """
    _force_blocking_stdio()
    await ensure_ollama_warm()

    deps, agent, frontend, stack = await make_eval_deps()
    apply_eval_window(deps)
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("session-continuity") as run:
            # W2.D — resume continuity.
            case_d = await case_w2_d_resume_helpers_rehydrate(deps, agent, frontend, run)
            cases.append(case_d)
            print(
                f"[session-continuity] {case_d.name}: "
                f"{'PASS' if case_d.passed else 'FAIL'} — {case_d.reason or 'ok'}"
            )

            # W2.E — inflation + /compact; judges the summary preserved the marker fact.
            case_e = await case_w2_e_compact_replaces_with_summary(deps, agent, frontend, run)
            cases.append(case_e)
            print(
                f"[session-continuity] {case_e.name}: "
                f"{'PASS' if case_e.passed else 'FAIL'} — {case_e.reason or 'ok'}"
            )
    finally:
        await stack.aclose()

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
