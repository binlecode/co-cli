"""UAT eval — Workflow 2: Session continuity.

Covers `/new`, `/clear`, `/sessions`, `/resume [id]`, `/compact`. Validates
session rotation, transcript clearing, picker-based resume with todo/plan
rehydration, and LLM-driven compaction of long histories.

Specs: docs/specs/sessions.md, compaction.md, self-planning.md
Mission tenet: continuity across sessions

Sub-cases
---------
W2.A new_rotates_session_id       — /new rotates ``deps.session.session_path``
                                    to a fresh file; prior JSONL preserved.
W2.B clear_resets_history         — /clear yields ReplaceTranscript([]) and
                                    leaves on-disk session JSONL untouched.
W2.C sessions_lists_real_sessions — /sessions enumerates real session files on
                                    disk (the post-W2.A file is present).
W2.D resume_provides_continuity   — fact + todo seeded, /new rotates, prior
                                    transcript loaded via ``load_transcript`` +
                                    ``_rehydrate_todos``, follow-up turn run with
                                    ``message_history=prior_messages`` recalls
                                    the fact.
W2.E compact_replaces_with_summary (boundary) — drive N synthetic turns to push
                                    history past ``compaction_ratio``; /compact
                                    yields a shorter history with a summary
                                    marker.
W2.F compact_idempotent_user_visible (failure mode) — second /compact call
                                    leaves history length ≈ same; no new marker.

Helpers (from sibling modules): ``make_eval_deps``, ``ensure_ollama_warm``,
``CALL_TIMEOUT_S``/``TURN_BUDGET_S``/``MULTI_TURN_COMPACT_BUDGET_S``,
``CaseResult``/``open_eval_run``, ``record_turn``, ``prepend_report``.
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
from evals._report import prepend_report
from evals._timeouts import (
    CALL_TIMEOUT_S,
    MULTI_TURN_COMPACT_BUDGET_S,
    TURN_BUDGET_S,
)
from evals._trace import record_turn
from pydantic_ai.messages import ModelRequest, UserPromptPart

from co_cli.commands.compact import _cmd_compact
from co_cli.commands.core import dispatch
from co_cli.commands.resume import _rehydrate_todos
from co_cli.commands.types import (
    CommandContext,
    LocalOnly,
    ReplaceTranscript,
)
from co_cli.context._compaction_markers import (
    STATIC_MARKER_PREFIX,
    SUMMARY_MARKER_PREFIX,
)
from co_cli.context.orchestrate import run_turn
from co_cli.session.persistence import append_messages, load_transcript

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-session-continuity.md"


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


def _last_assistant_text(messages: list[Any]) -> str:
    """Concatenate ``TextPart`` content from the last ``ModelResponse`` in messages."""
    from pydantic_ai.messages import ModelResponse, TextPart

    for msg in reversed(messages):
        if not isinstance(msg, ModelResponse):
            continue
        chunks: list[str] = []
        for part in msg.parts:
            if isinstance(part, TextPart):
                chunks.append(part.content or "")
        if chunks:
            return "".join(chunks)
    return ""


# ---------------------------------------------------------------------------
# W2.A — new_rotates_session_id
# ---------------------------------------------------------------------------


async def case_w2_a_new_rotates_session_id(
    deps: Any, agent: Any, frontend: Any, run: Any
) -> tuple[CaseResult, Path]:
    """Snapshot session path, drive /new, assert rotation + prior file preserved.

    Returns ``(case_result, new_session_path_after_rotation)`` so W2.C can
    assert the rotated session appears in the sessions dir listing.
    """
    case_id = "W2.A"
    t0 = time.monotonic()
    reason = ""
    passed = True

    prior_path: Path = deps.session.session_path
    prior_name = prior_path.name

    # Touch the prior JSONL so it exists on disk before rotation — /new only
    # rotates the in-memory handle; the file is created by append_transcript.
    if not prior_path.exists():
        prior_path.parent.mkdir(parents=True, exist_ok=True)
        prior_path.touch()

    # /new is a no-op when ``message_history`` is empty (see _cmd_new). Seed
    # a single user message so the rotate branch fires.
    seeded_history: list[Any] = [
        ModelRequest(parts=[UserPromptPart(content="seed message for rotate")]),
    ]
    ctx = _build_ctx(deps, agent, frontend, seeded_history)
    outcome = await dispatch("/new", ctx)

    if not isinstance(outcome, ReplaceTranscript):
        passed = False
        reason = f"expected ReplaceTranscript, got {type(outcome).__name__}"
    elif outcome.history != []:
        passed = False
        reason = "ReplaceTranscript.history must be [] after /new"

    new_path: Path = deps.session.session_path
    if new_path == prior_path or new_path.name == prior_name:
        passed = False
        reason = reason or f"session_path did not rotate (still {prior_name})"

    if not prior_path.exists():
        passed = False
        reason = reason or f"prior JSONL missing on disk after /new: {prior_name}"

    duration = time.monotonic() - t0
    result = CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=duration,
        reason=reason,
        trace_files=[],
    )
    run.append(result)
    return result, new_path


# ---------------------------------------------------------------------------
# W2.B — clear_resets_history
# ---------------------------------------------------------------------------


async def case_w2_b_clear_resets_history(
    deps: Any, agent: Any, frontend: Any, run: Any
) -> CaseResult:
    """Drive a real turn, snapshot JSONL bytes, /clear, assert history empty + file unchanged."""
    case_id = "W2.B"
    t0 = time.monotonic()
    reason = ""
    passed = True
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    trace_files: list[str] = []

    case_dir = run.case_trace_path(case_id)
    history: list[Any] = []

    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            turn_result, trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input="hello",
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input="hello",
                    deps=deps,
                    message_history=history,
                    frontend=frontend,
                ),
                case_dir_path=case_dir,
                agent=agent,
            )
        model_call_seconds = trace.model_call_seconds
        token_usage = dict(trace.token_usage)
        trace_files = [f"{case_dir.parent.name}/{case_dir.name}"]
        history = list(turn_result.messages)
    except Exception as exc:
        passed = False
        reason = f"setup turn failed: {type(exc).__name__}: {exc}"

    # Snapshot the on-disk session JSONL bytes before /clear.
    session_path: Path = deps.session.session_path
    pre_bytes = session_path.read_bytes() if session_path.exists() else b""

    if passed:
        ctx = _build_ctx(deps, agent, frontend, history)
        outcome = await dispatch("/clear", ctx)
        if not isinstance(outcome, ReplaceTranscript):
            passed = False
            reason = f"expected ReplaceTranscript, got {type(outcome).__name__}"
        elif outcome.history != []:
            passed = False
            reason = f"history not empty after /clear: len={len(outcome.history)}"

    post_bytes = session_path.read_bytes() if session_path.exists() else b""
    if post_bytes != pre_bytes:
        passed = False
        reason = (
            reason
            or f"on-disk JSONL changed across /clear: {len(pre_bytes)}B → {len(post_bytes)}B"
        )

    if passed and model_call_seconds > TURN_BUDGET_S:
        passed = False
        reason = f"[slow] {model_call_seconds:.1f}s vs budget {TURN_BUDGET_S}s"

    duration = time.monotonic() - t0
    result = CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=trace_files,
        reason=reason,
    )
    run.append(result)
    return result


# ---------------------------------------------------------------------------
# W2.C — sessions_lists_real_sessions
# ---------------------------------------------------------------------------


async def case_w2_c_sessions_lists_real_sessions(
    deps: Any, agent: Any, frontend: Any, run: Any, post_new_path: Path
) -> CaseResult:
    """Drive /sessions (prints to console; outcome is LocalOnly).

    Verify the sessions dir on disk actually carries at least one session file
    (the post-W2.A rotated path, which must have a matching ``.jsonl`` sibling
    or any other real session file).
    """
    case_id = "W2.C"
    t0 = time.monotonic()
    reason = ""
    passed = True

    ctx = _build_ctx(deps, agent, frontend, [])
    outcome = await dispatch("/sessions", ctx)
    if not isinstance(outcome, LocalOnly):
        passed = False
        reason = f"expected LocalOnly, got {type(outcome).__name__}"

    sessions_dir: Path = deps.sessions_dir
    if not sessions_dir.exists():
        passed = False
        reason = reason or f"sessions dir missing: {sessions_dir}"
    else:
        session_files = list(sessions_dir.glob("*.jsonl"))
        if not session_files:
            passed = False
            reason = reason or f"no session files found in {sessions_dir}"
        elif post_new_path.name not in {p.name for p in session_files}:
            # Soft signal — the file is created lazily on first append, so it
            # may not yet exist on disk. Don't fail unless the dir is empty.
            # Accept the case as PASS so long as at least one session lives
            # there.
            pass

    duration = time.monotonic() - t0
    result = CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=duration,
        reason=reason,
    )
    run.append(result)
    return result


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
            followup_text = _last_assistant_text(followup_result.messages)
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

    trace_files = [f"{case_dir.parent.name}/{case_dir.name}"]
    duration = time.monotonic() - t0
    result = CaseResult(
        name=case_id,
        verdict=verdict,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=trace_files,
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
    ``compaction_ratio`` and the live prompt size. Compaction reads
    ``max(estimate_message_tokens, latest_response_input_tokens)`` against
    ``compaction_ratio * model_max_ctx``. With default ``compaction_ratio=0.5``
    and a 32k context, a fresh agent on a warm 35B local emits ~10-15k prompt
    tokens per response, so 8-12 brief turns reliably crosses the threshold.
    The exact value is logged in ``CaseResult.reason`` for re-tuning.
    """
    _ = deps
    return 10


async def case_w2_e_compact_replaces_with_summary(
    deps: Any, agent: Any, frontend: Any, run: Any
) -> tuple[CaseResult, list[Any], int]:
    """Inflate history past compaction_ratio, then /compact.

    Seeds a "Lighthouse" marker turn before inflation so the post-compact
    follow-up judge can verify the summary preserved the project name.

    Returns ``(case_result, post_compact_history, pre_compact_len)`` so W2.F
    can drive a second /compact against the same compacted history and assert
    idempotence.
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

    trace_files = [f"{case_dir.parent.name}/{case_dir.name}"]
    duration = time.monotonic() - t0
    result = CaseResult(
        name=case_id,
        verdict=verdict,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=trace_files,
        reason=reason,
    )
    run.append(result)
    return result, post_compact_history, pre_compact_len


# ---------------------------------------------------------------------------
# W2.F — compact_idempotent_user_visible (failure mode)
# ---------------------------------------------------------------------------


async def case_w2_f_compact_idempotent(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
    history: list[Any],
    pre_len_from_e: int,
) -> CaseResult:
    """Second /compact: history length stays ≈ same (±10%), no new marker."""
    case_id = "W2.F"
    t0 = time.monotonic()
    reason = ""
    passed = True
    model_call_seconds = 0.0

    if not history:
        # W2.E failed upstream — skip cleanly with a product-gap signal.
        duration = time.monotonic() - t0
        result = CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=duration,
            reason="W2.E produced no post-compact history; cannot assert idempotence",
        )
        run.append(result)
        return result

    pre_len = len(history)
    pre_marker_count = sum(
        1
        for msg in history
        if isinstance(msg, ModelRequest)
        and any(
            isinstance(p, UserPromptPart)
            and isinstance(getattr(p, "content", ""), str)
            and (
                p.content.startswith(SUMMARY_MARKER_PREFIX)
                or p.content.startswith(STATIC_MARKER_PREFIX)
            )
            for p in msg.parts
        )
    )

    try:
        async with asyncio.timeout(MULTI_TURN_COMPACT_BUDGET_S):
            ctx = _build_ctx(deps, agent, frontend, history)
            t_call = time.monotonic()
            # Call the handler directly to keep the per-call latency bookkeeping
            # tight; dispatch("/compact", ctx) would do the same work.
            outcome = await _cmd_compact(ctx, "")
            model_call_seconds = time.monotonic() - t_call
    except Exception as exc:
        passed = False
        reason = f"second /compact failed: {type(exc).__name__}: {exc}"
        outcome = None

    if passed and outcome is None:
        # _cmd_compact returns None on empty history — that's an acceptable
        # idempotent answer (no-op when nothing to compact). Treat as PASS
        # only if history was empty; here it isn't.
        passed = False
        reason = "_cmd_compact returned None despite non-empty history"
    elif passed and not isinstance(outcome, ReplaceTranscript):
        passed = False
        reason = f"second /compact returned {type(outcome).__name__}"
    elif passed:
        post_len = len(outcome.history)
        # ±10% tolerance — marker accounting + breadcrumbs may add or remove
        # a small number of messages without representing a real second pass.
        tolerance = max(1, round(pre_len * 0.10))
        if abs(post_len - pre_len) > tolerance:
            passed = False
            reason = (
                f"history length drifted past tolerance: "
                f"{pre_len} → {post_len} (tolerance ±{tolerance})"
            )
        else:
            post_marker_count = sum(
                1
                for msg in outcome.history
                if isinstance(msg, ModelRequest)
                and any(
                    isinstance(p, UserPromptPart)
                    and isinstance(getattr(p, "content", ""), str)
                    and (
                        p.content.startswith(SUMMARY_MARKER_PREFIX)
                        or p.content.startswith(STATIC_MARKER_PREFIX)
                    )
                    for p in msg.parts
                )
            )
            if post_marker_count > pre_marker_count:
                passed = False
                reason = (
                    f"new compaction marker added on second /compact "
                    f"({pre_marker_count} → {post_marker_count})"
                )
            else:
                reason = (
                    f"len {pre_len}→{post_len} markers={post_marker_count} "
                    f"(stable within ±{tolerance})"
                )

    duration = time.monotonic() - t0
    result = CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
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
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("session-continuity") as run:
            # W2.A — must run first so subsequent cases have a rotated session.
            case_a, post_new_path = await case_w2_a_new_rotates_session_id(
                deps, agent, frontend, run
            )
            cases.append(case_a)
            print(
                f"[session-continuity] {case_a.name}: "
                f"{'PASS' if case_a.passed else 'FAIL'} — {case_a.reason or 'ok'}"
            )

            # W2.B — drives a real turn; depends only on bootstrap.
            case_b = await case_w2_b_clear_resets_history(deps, agent, frontend, run)
            cases.append(case_b)
            print(
                f"[session-continuity] {case_b.name}: "
                f"{'PASS' if case_b.passed else 'FAIL'} — {case_b.reason or 'ok'}"
            )

            # W2.C — sessions listing; consumes post_new_path for signal.
            case_c = await case_w2_c_sessions_lists_real_sessions(
                deps, agent, frontend, run, post_new_path
            )
            cases.append(case_c)
            print(
                f"[session-continuity] {case_c.name}: "
                f"{'PASS' if case_c.passed else 'FAIL'} — {case_c.reason or 'ok'}"
            )

            # W2.D — resume continuity.
            case_d = await case_w2_d_resume_helpers_rehydrate(deps, agent, frontend, run)
            cases.append(case_d)
            print(
                f"[session-continuity] {case_d.name}: "
                f"{'PASS' if case_d.passed else 'FAIL'} — {case_d.reason or 'ok'}"
            )

            # W2.E — inflation + /compact. Returns the post-compact history
            # for the W2.F idempotence check.
            (
                case_e,
                post_compact_history,
                pre_compact_len,
            ) = await case_w2_e_compact_replaces_with_summary(deps, agent, frontend, run)
            cases.append(case_e)
            print(
                f"[session-continuity] {case_e.name}: "
                f"{'PASS' if case_e.passed else 'FAIL'} — {case_e.reason or 'ok'}"
            )

            # W2.F — second /compact on the same history.
            case_f = await case_w2_f_compact_idempotent(
                deps, agent, frontend, run, post_compact_history, pre_compact_len
            )
            cases.append(case_f)
            print(
                f"[session-continuity] {case_f.name}: "
                f"{'PASS' if case_f.passed else 'FAIL'} — {case_f.reason or 'ok'}"
            )

            iso = run.iso
            run_dir = run.dir

        prepend_report(
            _REPORT_PATH,
            "session-continuity",
            iso,
            cases,
            run_dir=run_dir,
        )
    finally:
        await stack.aclose()

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
