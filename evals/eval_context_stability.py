"""UAT eval — Context stability under sustained text/reasoning pressure (ISSUE-2).

Drives a long multi-turn conversation through a **text/reasoning-heavy phase**
that accumulates near-incompressible content with no spillable
``ToolReturnPart`` candidates. This is the only phase that engages the
anti-thrash gate's previously-unreproduced no-op→growth path: a text middle has
nothing for the layer-2 ``evict_old_tool_results`` spill to bite on, so the
proactive compactor (``proactive_window_processor``) is the sole defense and the
anti-thrash branch is reachable.

Sequencing / file ownership
---------------------------
This plan (``antithrash-static-marker-fallback``, ISSUE-2) **ships first and
owns creation** of this file, carrying the **text-heavy phase only**. The parent
plan's combined loop-stability eval (``context-stability-sizing-control``, which
lists prerequisites ISSUE-2, ISSUE-3, ISSUE-5 and is structurally inert until
ISSUE-2 lands) is a downstream extension that adds the tool-output-heavy phase.

What this validates (load-bearing, within this plan's authority)
----------------------------------------------------------------
- **No context-overflow error** across the whole run.
- **Bounded number of compaction passes** — the loop never runs away.
- **Every triggered pass reduces token count** — the anti-thrash branch produces
  a static-marker pass that trims, never a no-op (the bug this plan fixes).
- **Post-pass total stays below the trigger** — each fired pass leaves headroom.

Gate-conditional anti-thrash assertion
---------------------------------------
The anti-thrash gate trips only after ≥2 consecutive proactive passes each
saving <``min_proactive_savings`` (0.10). A competent real summarizer compresses
text well, so the gate may not engage from organic load. Therefore:
- If a pass with ``skip_reason="anti_thrash_gate"`` fires, it MUST be a
  static-marker compaction that reduced tokens (a gate-trips-and-no-ops outcome
  is a HARD FAILURE).
- If the gate does not engage, the eval **logs the non-engagement explicitly**
  (so the result is not silently mistaken for a full validation) and the
  bounded-loop assertions still hold. Per the plan's Open Questions resolution
  (option b), TASK-3's unit test owns the deterministic tripped-state guarantee;
  this eval validates the bounded loop and *conditionally* the runtime trip.

**No coherence probe** — deliberately out of scope (the parent plan owns
coherence-after-trim, which holds the ``tail_fraction`` lever this plan cannot
act on). No recall-probe machinery is built here.

Specs: docs/specs/compaction.md
Plan: docs/exec-plans/completed/2026-06-03-220905-antithrash-static-marker-fallback.md

Helpers (from sibling modules): ``make_eval_deps``, ``ensure_ollama_warm``,
``MULTI_TURN_COMPACT_BUDGET_S``, ``CaseResult``/``Verdict``/``open_eval_run``,
``record_turn``, ``prepend_report``, ``_force_blocking_stdio``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report
from evals._settings import eval_max_ctx
from evals._timeouts import MULTI_TURN_COMPACT_BUDGET_S
from evals._trace import record_turn

from co_cli.context.orchestrate import run_turn
from co_cli.observability import tracing

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-context-stability.md"

_PROACTIVE_SPAN_NAME = "compaction.proactive_check"

# Number of text-heavy turns to drive. Each turn injects a large block of
# near-incompressible content so the running history crosses the proactive
# trigger (compaction_ratio x model_max_ctx) within a bounded turn count, then
# keeps pressure on it so multiple proactive passes fire. Bounded so the run is
# a tractable real-LLM UAT smoke (not an endurance test).
_NUM_TURNS = 10

# An overall ceiling on how many proactive passes are tolerable across the run.
# A correct bounded loop fires a small number of passes (one per turn that
# crosses the trigger, at most). A runaway loop — the failure this eval guards —
# would fire far more. Generous relative to _NUM_TURNS so normal LLM variance
# never trips it; a true runaway blows past it.
_MAX_PROACTIVE_PASSES = _NUM_TURNS * 3


def _high_entropy_block(approx_tokens: int) -> str:
    """A dense, near-incompressible block of unique identifiers + varied facts.

    The summarizer cannot shrink this below ~90% of its region — every line is a
    distinct random identifier paired with a unique factual statement, so there
    is no redundancy to compress away. This is what makes a proactive pass
    *low-yield*; ≥2 consecutive low-yield passes are what trip the anti-thrash
    gate (the runtime path this eval probes). ``approx_tokens`` targets roughly
    4 chars/token (co's estimator basis).
    """
    target_chars = approx_tokens * 4
    lines: list[str] = []
    chars = 0
    while chars < target_chars:
        token_id = secrets.token_hex(16)
        serial = secrets.randbelow(10_000_000)
        coord = f"{secrets.randbelow(180)}.{secrets.randbelow(999999):06d}"
        line = (
            f"Record {token_id}: serial {serial}, sensor at lat {coord} reported "
            f"calibration offset {secrets.randbelow(9999)} with checksum "
            f"{secrets.token_hex(8)} — verified independent, no duplicate."
        )
        lines.append(line)
        chars += len(line) + 1
    return "\n".join(lines)


def _read_proactive_spans(spans_log: Path) -> list[dict[str, Any]]:
    """All ``compaction.proactive_check`` span records from the isolated log.

    Each record's ``attributes`` carries the trigger/outcome fields the eval
    asserts on: ``compaction.fired``, ``compaction.skip_reason``,
    ``compaction.token_count``, ``compaction.tokens_after``,
    ``compaction.threshold``, ``compaction.savings_pct``.
    """
    if not spans_log.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in spans_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("name") == _PROACTIVE_SPAN_NAME:
            out.append(rec)
    return out


def _setup_isolated_spans_log(run_dir: Path) -> Path:
    """Point the spans logger at a run-local file and return its path.

    ``create_deps`` does not configure the spans handler (only the CLI's
    ``main.py`` does), so an eval must wire it up itself to capture the
    ``compaction.proactive_check`` records emitted inside ``run_turn``. Mirrors
    the ``isolated_spans_log`` fixture pattern in
    ``tests/test_flow_compaction_proactive.py``.
    """
    spans_log = run_dir / "spans.jsonl"
    tracing.setup_log(spans_log)
    return spans_log


# ---------------------------------------------------------------------------
# CS.A — text_pressure_keeps_window_bounded
# ---------------------------------------------------------------------------


async def case_cs_a_text_pressure_bounded(
    deps: Any, agent: Any, frontend: Any, run: Any, spans_log: Path
) -> CaseResult:
    """Drive text-heavy turns; assert the proactive loop stays bounded.

    Hard assertions:
      - no context-overflow error on any turn;
      - bounded number of fired proactive passes (≤ _MAX_PROACTIVE_PASSES);
      - every fired pass reduced token count (tokens_after < token_count);
      - every fired pass left the post-pass total below the trigger threshold.

    Gate-conditional: any ``anti_thrash_gate`` pass must be a static-marker
    compaction that reduced tokens (covered by the per-pass reduction check);
    non-engagement is logged, not silently passed.
    """
    case_id = "CS.A"
    t0 = time.monotonic()
    reason = ""
    passed = True
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
    trace_id = ""

    case_dir = run.case_trace_path(case_id)
    history: list[Any] = []

    # Each turn appends a near-incompressible block (see _high_entropy_block) so
    # the running history climbs gradually toward the 0.50 x model_max_ctx ~= 32k
    # trigger (model_max_ctx sourced from the real system config, default 64k) and
    # sustains pressure past it. Sized small enough that a single turn's prefill
    # stays a tractable warm-latency call even once the context is near-trigger
    # (large blocks balloon prefill past the per-turn budget — a model-latency
    # hazard, not the behavior under test, per feedback_llm_call_timing).
    per_turn_block_tokens = 2500

    # Capture status strings so the real failure under test (a context-overflow
    # event, which run_turn's recovery path announces via on_status) is told
    # apart from an unrelated transient LLM stall. This is frontend-instance
    # configuration, not a patch — the eval owns its frontend's status sink, the
    # same spirit as EvalFrontend overriding the interactive prompts. run_turn
    # reads frontend.on_status to set deps.runtime.status_callback each turn.
    statuses: list[str] = []
    base_on_status = frontend.on_status

    def _capture_status(message: str) -> None:
        statuses.append(message)
        base_on_status(message)

    frontend.on_status = _capture_status

    overflow_seen = False
    turns_run = 0

    try:
        for i in range(_NUM_TURNS):
            block = _high_entropy_block(per_turn_block_tokens)
            user_input = (
                "Acknowledge receipt of this data batch with a single short line. "
                "Do not summarize or repeat it back.\n\n" + block
            )
            # Per-turn budget: covers a near-trigger large-context prefill plus,
            # on a triggering turn, an in-turn compaction summary. A single turn
            # exceeding this is a genuine stall worth failing fast on, not a budget
            # to relax (feedback_call_timeout_no_cold_start: warm latency only).
            try:
                async with asyncio.timeout(MULTI_TURN_COMPACT_BUDGET_S):
                    turn_result, trace = await record_turn(
                        case_id=case_id,
                        turn_index=i,
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
            except TimeoutError:
                passed = False
                reason = f"turn {i} stalled past {MULTI_TURN_COMPACT_BUDGET_S}s per-turn budget"
                break

            turns_run += 1
            model_call_seconds += trace.model_call_seconds
            for k, v in trace.token_usage.items():
                token_usage[k] = token_usage.get(k, 0) + v
            if trace.trace_ids:
                trace_id = trace.trace_ids[-1]

            if any("Context overflow" in s for s in statuses):
                # run_turn's overflow-recovery path announced overflow — the exact
                # failure this eval guards against. A bounded proactive loop must
                # keep the window below the hard limit so this never fires.
                overflow_seen = True
                passed = False
                reason = f"context overflow at turn {i} — the bounded-loop invariant failed"
                break
            if turn_result.outcome == "error":
                # Errored turn with no overflow status: a transient LLM/network
                # stall, not the behavior under test. Record it distinctly so the
                # result is never mistaken for the bounded-loop guarantee.
                passed = False
                reason = f"turn {i} errored (no overflow status — transient LLM stall)"
                break
            history = list(turn_result.messages)
    except Exception as exc:
        passed = False
        reason = f"turn loop failed after {turns_run} turns: {type(exc).__name__}: {exc}"
    finally:
        frontend.on_status = base_on_status

    # Inspect the proactive-compaction spans captured during the run.
    spans = _read_proactive_spans(spans_log)
    fired = [s for s in spans if s["attributes"].get("compaction.fired") is True]
    anti_thrash = [
        s for s in fired if s["attributes"].get("compaction.skip_reason") == "anti_thrash_gate"
    ]

    # Hard span assertions run UNCONDITIONALLY — a no-op / runaway regression
    # must be caught even if a later turn stalled transiently, since the bug
    # this eval guards (an anti-thrash no-op pass) lives in the span record
    # regardless of downstream turn outcomes. These checks can only turn a
    # passing run into a FAIL, never the reverse.
    span_violation = ""

    # Hard assertion: bounded number of passes.
    if len(fired) > _MAX_PROACTIVE_PASSES:
        span_violation = (
            f"runaway compaction: {len(fired)} fired passes > cap {_MAX_PROACTIVE_PASSES}"
        )

    # Hard assertion: every fired pass reduced tokens and stayed below trigger.
    if not span_violation:
        for s in fired:
            attrs = s["attributes"]
            before = attrs.get("compaction.token_count")
            after = attrs.get("compaction.tokens_after")
            threshold = attrs.get("compaction.threshold")
            if before is None or after is None:
                span_violation = "a fired pass is missing token_count/tokens_after attributes"
                break
            if after >= before:
                span_violation = (
                    f"a fired pass grew/held tokens: {before} → {after} "
                    f"(skip_reason={attrs.get('compaction.skip_reason')!r}) — no-op regression"
                )
                break
            if threshold is not None and after >= threshold:
                span_violation = (
                    f"post-pass total {after} not below trigger {threshold} "
                    f"(skip_reason={attrs.get('compaction.skip_reason')!r})"
                )
                break

    if span_violation:
        passed = False
        reason = span_violation if not reason else f"{span_violation} | {reason}"

    # Gate-conditional: the anti-thrash branch must never no-op. The per-pass
    # reduction check above already covers every fired pass; re-state it
    # explicitly for the anti-thrash subset so a no-op there is unambiguous, and
    # log engagement / non-engagement.
    gate_note = ""
    if not span_violation:
        if anti_thrash:
            bad = [
                s
                for s in anti_thrash
                if s["attributes"].get("compaction.tokens_after", 0)
                >= s["attributes"].get("compaction.token_count", 0)
            ]
            if bad:
                passed = False
                reason = (
                    f"anti-thrash gate tripped but no-op'd on {len(bad)} pass(es) "
                    "— HARD FAILURE (gate must static-marker, never return unchanged)"
                )
            else:
                gate_note = (
                    f"anti-thrash gate ENGAGED on {len(anti_thrash)} pass(es), "
                    "each a static-marker compaction that reduced tokens"
                )
        else:
            gate_note = (
                "anti-thrash gate did NOT engage this run "
                "(summarizer kept savings high or middle stayed thin) — "
                "bounded-loop invariants verified; TASK-3 owns the deterministic trip"
            )

    # Always surface the bounded-loop diagnostics (turns + fired-pass accounting
    # + gate engagement) — even on a transient-stall FAIL — so the run is never
    # read as a silent pass and the proactive-loop behavior stays visible.
    diag = (
        f"turns={turns_run} fired_passes={len(fired)} "
        f"anti_thrash_passes={len(anti_thrash)} overflow={overflow_seen}"
    )
    if gate_note:
        diag += f" | {gate_note}"
    reason = diag if not reason else f"{reason} || {diag}"

    duration = time.monotonic() - t0
    result = CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_id=trace_id,
        trace_files=[f"{case_dir.parent.name}/{case_dir.name}"],
        reason=reason,
    )
    run.append(result)
    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _force_blocking_stdio() -> None:
    """Force stdout/stderr to blocking mode.

    The case drives many sequential turns; rich's streaming renderer floods the
    pipe buffer when piped through ``tee``. macOS sets piped fds non-blocking by
    default, so a fast burst can raise ``BlockingIOError(EAGAIN)`` deep inside
    rich's writer. Forcing blocking mode makes the pipeline backpressure
    naturally. Mirrors ``eval_session_continuity.py``.
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
    """Run the context-stability case against the real ``~/.co-cli/`` workspace.

    Warms Ollama first (outside any ``asyncio.timeout`` — cold model load is
    infrastructure prep, not behavior under test), bootstraps real ``CoDeps`` +
    agent + frontend via :func:`make_eval_deps`, wires an isolated spans log so
    the proactive-compaction records are capturable, then runs the case.
    """
    _force_blocking_stdio()
    await ensure_ollama_warm()

    deps, agent, frontend, stack = await make_eval_deps()
    # Operational window from the centralized eval settings — sourced from the
    # real system config (default 64k), so this is idempotent with what
    # create_deps already resolved. It pins the window to the shared eval knob
    # and is the single place to override if a future run needs a smaller window
    # (eval_max_ctx(<n>)) — never a literal coined inline here.
    deps.model_max_ctx = eval_max_ctx()
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("context-stability") as run:
            spans_log = _setup_isolated_spans_log(run.dir)
            logging.getLogger(__name__).info("spans log: %s", spans_log)

            case_a = await case_cs_a_text_pressure_bounded(deps, agent, frontend, run, spans_log)
            cases.append(case_a)
            print(
                f"[context-stability] {case_a.name}: "
                f"{'PASS' if case_a.passed else 'FAIL'} — {case_a.reason or 'ok'}"
            )

            iso = run.iso
            run_dir = run.dir

        prepend_report(
            _REPORT_PATH,
            "context-stability",
            iso,
            cases,
            run_dir=run_dir,
        )
    finally:
        await stack.aclose()

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
