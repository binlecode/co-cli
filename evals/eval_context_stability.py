"""UAT eval — Context stability under sustained text/reasoning pressure (ISSUE-2).

Drives a long multi-turn conversation through a **text/reasoning-heavy phase**
that accumulates near-incompressible content with no spillable ``ToolReturnPart``
candidates, under the shared 32k eval window (``EVAL_MAX_CTX``, halved from the
system default to magnify pressure). A text middle has nothing for the layer-2
spill to bite on, so the proactive compactor (``proactive_window_processor``) is
the sole defense and the anti-thrash gate's no-op→growth path is reachable.

CS.C (tool-output-heavy phase, drop-reported trigger) is present but **disabled**
(``_CS_C_ENABLED = False``) — emitted as a SKIPPED case, not run. It is a legit
test, but the *current* environment cannot reach its precondition: the 32k eval
window + ~10.8k static prefill floor + 4k per-result auto-spill cap together route
any oversized request into the L3 ``fallback_to_summarize`` case before a fitting
L2 aggregate spill can occur. Those three sizings are themselves open issues —
re-enable CS.C once they are resolved (see the constant's note). Until then the
chain is guarded by the deterministic unit test
``test_l3_fastpaths_after_l2_spill_fits_payload``.

What this validates (load-bearing)
----------------------------------
- **No context-overflow error** across the whole run.
- **Bounded number of compaction passes** — the loop never runs away.
- **Every triggered pass reduces token count** — the anti-thrash branch produces
  a static-marker pass that trims, never a no-op.
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
Plans: docs/exec-plans/completed/2026-06-03-220905-antithrash-static-marker-fallback.md (CS.A/CS.B);
       docs/exec-plans/completed/2026-06-04-130800-drop-reported-realtime-trigger.md (CS.C, disabled)

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
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report
from evals._settings import apply_eval_window
from evals._timeouts import MULTI_TURN_COMPACT_BUDGET_S
from evals._trace import record_turn

from co_cli.context.orchestrate import run_turn
from co_cli.context.summarization import SUMMARY_BUDGET_FLOOR
from co_cli.memory.frontmatter import render_frontmatter
from co_cli.memory.item import MemoryKindEnum
from co_cli.observability import tracing
from co_cli.tools.tool_io import SPILL_THRESHOLD_CHARS

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

# Mandatory trailing template section (summarization.py _SUMMARIZE_PROMPT). It is
# emitted even when the task just completed ("say so"), so it is the truncation
# canary: if the hard output cap cut the summary mid-structure (the Mode-B failure
# the summary-output-length-control plan prevents), this trailing header is the
# first casualty. ## Critical Context is "Skip if none" — legitimately absent for
# the high-entropy fixture — so it is logged, not gated.
_MANDATORY_TRAILING_SECTION = "## Next Step"

# CS.C — tool-output spill pressure. DISABLED: legit test, but the current
# environment can't reach its precondition. A fitting L2 aggregate spill requires
# a pile of raw, force-spillable tool returns (1500 < chars <= 4000) to survive in
# history when a request crosses the trigger. Under the 32k window the ~10.8k
# static prefill floor leaves only ~5.6k headroom, L3 (same 16384 trigger) trims
# the pile between turns, and the 4k auto-spill cap pre-spills larger returns
# upstream — so the spill always falls back to summarize. Re-enable once the window/
# floor/cap sizings are addressed (then flip _CS_C_ENABLED). The chain itself is
# guarded meanwhile by test_l3_fastpaths_after_l2_spill_fits_payload (unit).
_CS_C_ENABLED = False
# Each seeded artifact is sized just under the per-result tool_io spill threshold
# so memory_view returns it raw; raw returns accumulate until the L2 aggregate
# trigger fires. ~950 tokens/artifact.
_CS_C_ARTIFACT_CHARS = SPILL_THRESHOLD_CHARS - 200
_CS_C_MAX_TURNS = 16
_CS_C_FACT_TOKEN = "ANCHOR_FACT"
_CS_C_STEM_PREFIX = "eval_csc_doc_"
_SPILL_EVENT_NAME = "tool_budget.spill_largest_tool_results"


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


def _summary_text_from_output(model_output: str | None) -> str:
    """Extract the summary text from a child ``llm_call`` span's ``co.model.output``.

    ``co.model.output`` is the compact-JSON serialization of the response parts
    (``serialize_response`` → ``[{"type": "text", "content": "<summary>"}]``). Join
    every text part's content so the trailing ``## Section`` headers are searchable.
    """
    if not model_output:
        return ""
    try:
        parts = json.loads(model_output)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(parts, list):
        return ""
    return "\n".join(p.get("content", "") for p in parts if p.get("type") == "text")


def _read_summarizer_passes(spans_log: Path) -> list[dict[str, Any]]:
    """Per-summarizer-pass records correlated across the proactive_check / llm_call spans.

    The summary output budget lands on the parent ``compaction.proactive_check`` span
    (``co.compaction.summary.budget`` / ``.cap`` / ``.focus``); the produced summary and
    its output token count land on the child ``llm_call <model>`` span
    (``co.model.tokens.output`` / ``co.model.output``) — the two cannot share a span
    (the llm_call span is pushed/popped inside ``llm_call``). Correlate the child to its
    parent via ``parent_span_id`` and merge into one record per real summarizer pass.

    Only passes carrying ``co.compaction.summary.budget`` are real summarizer calls —
    static-marker / circuit-breaker passes never invoke the summarizer, so they have no
    budget attribute and are excluded.
    """
    if not spans_log.exists():
        return []
    records = [
        json.loads(line)
        for line in spans_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if rec.get("name", "").startswith("llm_call ") and rec.get("parent_span_id"):
            children_by_parent.setdefault(rec["parent_span_id"], []).append(rec)

    passes: list[dict[str, Any]] = []
    for rec in records:
        if rec.get("name") != _PROACTIVE_SPAN_NAME:
            continue
        attrs = rec.get("attributes", {})
        if "co.compaction.summary.budget" not in attrs:
            continue
        child = next(
            (
                c
                for c in children_by_parent.get(rec["span_id"], [])
                if c.get("attributes", {}).get("co.model.tokens.output") is not None
            ),
            None,
        )
        if child is None:
            continue
        child_attrs = child["attributes"]
        passes.append(
            {
                "budget": attrs["co.compaction.summary.budget"],
                "cap": attrs["co.compaction.summary.cap"],
                "focus": bool(attrs.get("co.compaction.summary.focus")),
                "output_tokens": child_attrs["co.model.tokens.output"],
                "summary": _summary_text_from_output(child_attrs.get("co.model.output")),
                "savings_pct": attrs.get("compaction.savings_pct"),
            }
        )
    return passes


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
    # the running history climbs gradually toward the 0.50 x model_max_ctx ~= 16k
    # trigger (model_max_ctx is the shared EVAL_MAX_CTX baseline, capped at 32k to
    # magnify pressure) and sustains pressure past it. Sized small enough that a single turn's prefill
    # stays a tractable warm-latency call even once the context is near-trigger
    # (large blocks balloon prefill past the per-turn budget — a model-latency
    # hazard, not the behavior under test, per feedback_llm_call_timing), and so
    # that after a compaction pass the preserved tail + static floor leaves
    # headroom below the tighter 16k trigger (2500-tok blocks left the post-pass
    # total ~12 tokens over at 32k).
    per_turn_block_tokens = 1500

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
# CS.B — summary_output_bounded_proportionally
# ---------------------------------------------------------------------------


async def case_cs_b_summary_output_bounded(run: Any, spans_log: Path) -> CaseResult:
    """Analyze CS.A's summarizer passes — output bounded by the proportional cap.

    No extra LLM cost: re-reads the same spans CS.A produced. Each real summarizer
    pass carries ``budget`` / ``cap`` / ``focus`` on its ``compaction.proactive_check``
    span and ``output_tokens`` / the produced summary on its child ``llm_call`` span.

    Hard assertions (within authority — the cap override is honored end-to-end through
    Ollama, and the cap never truncates the structure):
      - every summarizer ``output_tokens`` ≤ its derived ``cap`` (the proportional
        ``max_tokens`` override flowed through the Ollama root-vs-max_completion_tokens
        lockstep — a broken lockstep would let output run to the flat 8192 ceiling);
      - every summary still contains its mandatory trailing ``## Next Step`` section
        (the cap did not cut the structure short — the Mode-B no-truncation guarantee);
      - at least one cap-applied pass ran with ``focus`` set (focus pushes length up
        while the cap pushes down — the worst case for the no-truncation guarantee;
        on the proactive path focus is the norm, so this exercises it).

    Conditional / logged (not gated — depends on organic load, not authority):
      - a small dropped region exercising the FLOOR budget (cap 2600): confirmed
        no mid-template truncation if it occurs, logged as not-exercised otherwise;
      - per-pass ``budget`` / ``cap`` / ``output_tokens`` and the overshoot ratio
        (``output_tokens / budget``) and cap pressure (``output_tokens / cap``) — the
        tuning signal for SUMMARY_CAP_OVERSHOOT_RATIO;
      - ## Critical Context presence (skip-if-none, legitimately absent).
    """
    case_id = "CS.B"
    t0 = time.monotonic()
    passes = _read_summarizer_passes(spans_log)

    floor_budget = min((p["budget"] for p in passes), default=None)

    # Per-pass tuning telemetry (logged, never gated).
    pass_lines: list[str] = []
    for i, p in enumerate(passes):
        overshoot = p["output_tokens"] / p["budget"] if p["budget"] else 0.0
        cap_pressure = p["output_tokens"] / p["cap"] if p["cap"] else 0.0
        has_critical = "## Critical Context" in p["summary"]
        pass_lines.append(
            f"  pass {i}: budget={p['budget']} cap={p['cap']} "
            f"output_tokens={p['output_tokens']} focus={p['focus']} "
            f"overshoot={overshoot:.2f} cap_pressure={cap_pressure:.2f} "
            f"savings_pct={p['savings_pct']} critical_ctx={has_critical}"
        )
    for line in pass_lines:
        logging.getLogger(__name__).info(line)

    passed = True
    reason = ""

    if not passes:
        # No real summarizer pass exercised the budget path (load did not open the
        # gate / cross the trigger). The feature is unvalidated this run — a review
        # signal, not a hard failure, since it is load-dependent not authority-bound.
        verdict = Verdict.SOFT_FAIL
        reason = (
            "no real summarizer pass observed — budget bounding not exercised this run "
            "(load did not trigger a gated proactive summary); TASK-3 unit tests own the "
            "deterministic budget/cap guarantee"
        )
    else:
        over_cap = [p for p in passes if p["output_tokens"] > p["cap"]]
        truncated = [p for p in passes if _MANDATORY_TRAILING_SECTION not in p["summary"]]
        focus_passes = [p for p in passes if p["focus"]]

        if over_cap:
            passed = False
            worst = max(over_cap, key=lambda p: p["output_tokens"] - p["cap"])
            reason = (
                f"{len(over_cap)} summarizer pass(es) exceeded the proportional cap "
                f"(worst: output_tokens={worst['output_tokens']} > cap={worst['cap']}) "
                "— the max_tokens override was NOT honored (Ollama lockstep broken)"
            )
        elif truncated:
            passed = False
            reason = (
                f"{len(truncated)} summarizer pass(es) missing the mandatory trailing "
                f"'{_MANDATORY_TRAILING_SECTION}' section — the cap truncated the summary "
                "mid-structure (Mode-B failure)"
            )
        elif not focus_passes:
            # Focus is the norm on the proactive path; its total absence means the
            # worst-case (focus-up vs cap-down) was not exercised — review signal.
            verdict = Verdict.SOFT_FAIL
            reason = (
                "no cap-applied pass ran with focus set — the focus-vs-cap worst case "
                "was not exercised this run (focus is normally the proactive-path norm)"
            )
            passed = True
        if passed and reason == "":
            verdict = Verdict.PASS
        elif passed:
            verdict = Verdict.SOFT_FAIL
        else:
            verdict = Verdict.FAIL

    # Diagnostics — always surfaced so the run is never read as a silent pass.
    floor_note = (
        f"FLOOR-budget pass exercised (budget={SUMMARY_BUDGET_FLOOR}, no mid-template truncation)"
        if floor_budget == SUMMARY_BUDGET_FLOOR
        else f"no FLOOR-budget pass this run (smallest budget={floor_budget})"
    )
    focus_count = sum(1 for p in passes if p["focus"])
    diag = f"summarizer_passes={len(passes)} focus_passes={focus_count} | {floor_note}" + (
        "\n" + "\n".join(pass_lines) if pass_lines else ""
    )
    reason = diag if not reason else f"{reason} || {diag}"

    duration = time.monotonic() - t0
    result = CaseResult(
        name=case_id,
        verdict=verdict,
        duration_s=duration,
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


def _read_spill_events(spans_log: Path) -> list[dict[str, Any]]:
    """Every ``tool_budget.spill_largest_tool_results`` event (L2 spill decision), in log order.

    ``spill_largest_tool_results`` adds its event to ``current_span()`` inside the history
    processor, so it can land on any active span — scan every record's ``events``,
    not a single named span. Each returned dict is the event's ``attributes``
    (``request.spill_fired``, ``request.tokens_after``, ``request.threshold_tokens``,
    ``request.skip_reason``).
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
        for event in rec.get("events", []):
            if event.get("name") == _SPILL_EVENT_NAME:
                out.append(event.get("attributes", {}))
    return out


def _seed_spill_artifacts(deps: Any, count: int) -> list[str]:
    """Seed ``count`` near-incompressible memory artifacts just under the per-result
    spill threshold, returning their filename stems in view order.

    Real artifacts (frontmatter + body) synced into the live memory store — the same
    surface ``memory_view`` reads in production. Body is high-entropy so the
    summarizer cannot shrink it and the fact token survives at the head.
    """
    deps.memory_dir.mkdir(parents=True, exist_ok=True)
    body_filler = _high_entropy_block(_CS_C_ARTIFACT_CHARS // 4)
    stems: list[str] = []
    for index in range(count):
        stem = f"{_CS_C_STEM_PREFIX}{index:02d}"
        body = f"{_CS_C_FACT_TOKEN}_{index:02d} is the anchor fact.\n\n{body_filler}"
        frontmatter_dict = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, stem)),
            "memory_kind": MemoryKindEnum.NOTE.value,
            "title": stem,
            "created_at": datetime.now(UTC).isoformat(),
        }
        (deps.memory_dir / f"{stem}.md").write_text(
            render_frontmatter(frontmatter_dict, body), encoding="utf-8"
        )
        stems.append(stem)
    deps.memory_store.sync_dir(deps.memory_dir)
    return stems


async def case_cs_c_tool_spill_precedes_summarize(
    deps: Any, agent: Any, frontend: Any, run: Any, spans_log: Path
) -> CaseResult:
    """Drive tool-output pressure; assert a fitting L2 spill suppresses the L3 summarize.

    DISABLED via ``_CS_C_ENABLED`` (see that constant) — kept for re-enablement once
    the window/floor/cap sizings let the precondition be reached. The drop-reported
    chain: real ``memory_view`` returns accumulate until L2 ``spill_largest_tool_results``
    force-spills the largest to disk, dropping the payload below the L3 threshold so
    the proactive check fast-paths (``below_threshold``) with zero summarizer calls.

    Hard assertions (when enabled):
      - no context-overflow error on any turn;
      - L2 spill fires on the accumulated real tool output (``request.spill_fired``);
      - on the turn a fitting spill occurs (``tokens_after <= threshold``), that turn's
        proactive checks are all ``below_threshold`` with zero new summarizer passes.
    """
    case_id = "CS.C"
    t0 = time.monotonic()
    reason = ""
    passed = True
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
    trace_id = ""
    case_dir = run.case_trace_path(case_id)

    stems = _seed_spill_artifacts(deps, _CS_C_MAX_TURNS)

    statuses: list[str] = []
    base_on_status = frontend.on_status

    def _capture_status(message: str) -> None:
        statuses.append(message)
        base_on_status(message)

    frontend.on_status = _capture_status

    history: list[Any] = []
    # Baseline the diff against records CS.A/CS.B already wrote to the shared spans
    # log, so CS.C's per-turn "new records" are its own, not prior cases'.
    prev_spill = len(_read_spill_events(spans_log))
    prev_proactive = len(_read_proactive_spans(spans_log))
    prev_passes = len(_read_summarizer_passes(spans_log))
    spill_fired_seen = False
    proof_seen = False

    try:
        for index, stem in enumerate(stems):
            user_input = (
                f"Use the `memory_view` tool to read the artifact with filename_stem "
                f"`{stem}` and quote the uppercase anchor token on its first line. "
                "Reply with just that token."
            )
            try:
                async with asyncio.timeout(MULTI_TURN_COMPACT_BUDGET_S):
                    turn_result, trace = await record_turn(
                        case_id=case_id,
                        turn_index=index,
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
                reason = (
                    f"turn {index} stalled past {MULTI_TURN_COMPACT_BUDGET_S}s per-turn budget"
                )
                break

            model_call_seconds += trace.model_call_seconds
            for usage_key, usage_val in trace.token_usage.items():
                token_usage[usage_key] = token_usage.get(usage_key, 0) + usage_val
            if trace.trace_ids:
                trace_id = trace.trace_ids[-1]
            history = turn_result.messages

            if any("Context overflow" in s for s in statuses):
                passed = False
                reason = f"context overflow at turn {index} — spill failed to bound the request"
                break

            spill_events = _read_spill_events(spans_log)
            proactive = _read_proactive_spans(spans_log)
            passes = _read_summarizer_passes(spans_log)
            new_spill = spill_events[prev_spill:]
            new_proactive = proactive[prev_proactive:]
            new_passes = passes[prev_passes:]
            prev_spill, prev_proactive, prev_passes = (
                len(spill_events),
                len(proactive),
                len(passes),
            )

            fitting_spill = any(
                event.get("request.spill_fired")
                and event.get("request.tokens_after", 1 << 30)
                <= event.get("request.threshold_tokens", 0)
                for event in new_spill
            )
            if any(event.get("request.spill_fired") for event in new_spill):
                spill_fired_seen = True

            if fitting_spill:
                skip_reasons = [
                    rec.get("attributes", {}).get("compaction.skip_reason")
                    for rec in new_proactive
                ]
                if (
                    new_proactive
                    and all(r == "below_threshold" for r in skip_reasons)
                    and not new_passes
                ):
                    proof_seen = True
                    reason = (
                        f"turn {index}: L2 spill fit the payload and L3 fast-pathed "
                        f"(below_threshold, {len(new_passes)} summarizer passes)"
                    )
                    break
                passed = False
                reason = (
                    f"turn {index}: L2 spill fit the payload but L3 did not fast-path "
                    f"(skip_reasons={skip_reasons}, summarizer_passes={len(new_passes)}) — "
                    "the spill failed to suppress the summarize"
                )
                break
    finally:
        frontend.on_status = base_on_status

    if passed and not spill_fired_seen:
        passed = False
        reason = (
            f"L2 never spilled across {len(stems)} turns — tool-output accumulation never "
            "crossed the spill trigger (raise _CS_C_MAX_TURNS or artifact size)"
        )
    if passed and not proof_seen:
        passed = False
        reason = reason or "no fitting-spill→below_threshold turn observed"

    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=time.monotonic() - t0,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_id=trace_id,
        trace_files=[f"{case_dir.parent.name}/{case_dir.name}"],
        reason=reason or "ok",
    )


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
    # Budget simulation: lower co's accounting window to 32k and re-derive
    # spill_threshold together so compaction/spill fire under magnified pressure
    # (see apply_eval_window — model keeps its physical num_ctx).
    apply_eval_window(deps)
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

            # CS.B re-reads the spans CS.A just produced — no extra LLM cost.
            case_b = await case_cs_b_summary_output_bounded(run, spans_log)
            cases.append(case_b)
            print(
                f"[context-stability] {case_b.name}: "
                f"{case_b.verdict.value.upper()} — {case_b.reason or 'ok'}"
            )

            # CS.C — tool-output spill. Disabled (see _CS_C_ENABLED): the current
            # window/floor/cap combination can't reach its precondition. Emitted as
            # a SKIPPED case so it stays visible and does not block the run.
            if _CS_C_ENABLED:
                case_c = await case_cs_c_tool_spill_precedes_summarize(
                    deps, agent, frontend, run, spans_log
                )
            else:
                case_c = CaseResult(
                    name="CS.C",
                    verdict=Verdict.SOFT_PASS,
                    duration_s=0.0,
                    skipped=True,
                    skip_category="product-gap",
                    reason=(
                        "DISABLED pending window/floor/cap fixes — at 32k ctx the ~10.8k "
                        "static floor + 4k auto-spill cap route every oversized request "
                        "into L3 fallback_to_summarize before a fitting L2 spill can "
                        "occur; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload"
                    ),
                )
            cases.append(case_c)
            print(f"[context-stability] {case_c.name}: SKIPPED — {case_c.reason}")

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
