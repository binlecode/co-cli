#!/usr/bin/env python3
"""Eval: compaction multi-cycle UAT — M3 fires twice via real run_turn.

co autonomously fetches content until M3 fires, then continues fetching new
content until M3 fires a second time. Validates that the iterative summary
chain (previous_compaction_summary) preserves key facts from the first
compaction into the second marker.

Prerequisites: LLM provider configured (Ollama or cloud), network access.

Usage:
    uv run python evals/eval_compaction_multi_cycle.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from evals._timeouts import EVAL_PROBE_TIMEOUT_SECS
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.agent.core import build_agent
from co_cli.bootstrap.core import create_deps
from co_cli.context.compaction import SUMMARY_MARKER_PREFIX
from co_cli.context.orchestrate import run_turn
from co_cli.display.headless import HeadlessFrontend

# ---------------------------------------------------------------------------
# Context breakdown helpers
# ---------------------------------------------------------------------------


def _context_breakdown(history: list[ModelMessage]) -> dict[str, int]:
    """Return counts of each message/part type in history."""
    n_req = sum(1 for m in history if isinstance(m, ModelRequest))
    n_resp = sum(1 for m in history if isinstance(m, ModelResponse))
    n_tool_calls = sum(
        1
        for m in history
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    )
    n_tool_returns = sum(
        1
        for m in history
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    )
    total_chars = sum(
        len(p.content)
        for m in history
        for p in m.parts
        if hasattr(p, "content") and isinstance(p.content, str)
    )
    return {
        "msgs": len(history),
        "req": n_req,
        "resp": n_resp,
        "tool_calls": n_tool_calls,
        "tool_returns": n_tool_returns,
        "chars": total_chars,
    }


def _print_breakdown(bd: dict[str, int], label: str) -> None:
    print(
        f"    {label}: {bd['msgs']} msgs  "
        f"req={bd['req']} resp={bd['resp']} "
        f"calls={bd['tool_calls']} returns={bd['tool_returns']}  "
        f"~{bd['chars']:,} chars"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snippet(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return repr(text)
    head = max_len // 3
    tail = max_len // 3
    return repr(text[:head]) + f" ...<{len(text) - head - tail} chars>... " + repr(text[-tail:])


def _count_summary_markers(history: list[ModelMessage]) -> list[str]:
    texts = []
    for m in history:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if (
                    isinstance(p, UserPromptPart)
                    and isinstance(p.content, str)
                    and SUMMARY_MARKER_PREFIX in p.content
                    and p.content not in texts
                ):
                    texts.append(p.content)
    return texts


_PHASE1_KEYWORDS = ["finch", "tom hanks", "apple tv"]
_PHASE2_KEYWORDS = ["cast away", "zemeckis"]


def _keyword_chain_check(
    summary_2: str,
    full_history: list[ModelMessage],
) -> tuple[bool, list[str]]:
    """Deterministic chain fidelity check.

    Chain preservation (primary): does the phase-2 *summary* mention phase-1
    facts? The phase-2 summary covers the dropped range (Finch content), updated
    via the previous_compaction_summary iterative template. Phase-1 keywords
    must appear in the summary marker itself.

    Tail preservation (secondary): Cast Away content lands in the protected tail,
    not in the dropped range, so it is preserved verbatim in message_history but
    NOT necessarily in the summary text. We check the full history for it.

    Returns (passed, log_lines). Only chain preservation gates pass/fail.
    """
    s2 = summary_2.lower()
    phase1_ok = any(kw in s2 for kw in _PHASE1_KEYWORDS)

    # Cast Away survives in the preserved tail — check full history, not just summary.
    full_text = " ".join(
        p.content.lower()
        for msg in full_history
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if hasattr(p, "content") and isinstance(p.content, str)
    )
    phase2_ok = any(kw in full_text for kw in _PHASE2_KEYWORDS)

    lines = [
        f"    Keyword check (chain): phase1_in_summary={'PASS' if phase1_ok else 'FAIL'} "
        f"({_PHASE1_KEYWORDS})",
        f"    Keyword check (tail):  phase2_in_history={'PASS' if phase2_ok else 'FAIL'} "
        f"({_PHASE2_KEYWORDS})",
    ]
    return phase1_ok, lines


# ---------------------------------------------------------------------------
# UAT: multi-cycle compaction — M3 fires twice
# ---------------------------------------------------------------------------


async def step_multi_cycle_compaction() -> bool:
    """UAT: run_turn loop until M3 fires twice.

    Phase 1 — Finch research: co fetches Wikipedia pages and reviews until the
    first M3 compaction fires (same as eval_compaction_proactive). Phase 2 —
    continued research on a new angle: after the first compaction, co continues
    fetching content until a second M3 fires. The second compaction's marker is
    checked to confirm the first summary's key facts survived (iterative
    previous_compaction_summary chain).
    """
    print("\n--- UAT: Multi-cycle compaction — M3 fires twice ---")

    try:
        async with asyncio.timeout(EVAL_PROBE_TIMEOUT_SECS):
            async with httpx.AsyncClient() as _probe:
                probe_resp = await _probe.head("https://en.wikipedia.org/")
        if probe_resp.status_code >= 500:
            print(f"UAT: FAIL: coarse reachability probe failed — HTTP {probe_resp.status_code}")
            return False
    except (TimeoutError, Exception) as exc:
        print(f"UAT: FAIL: coarse reachability probe failed — {exc}")
        return False
    print("  Preflight: en.wikipedia.org reachable")

    frontend = HeadlessFrontend(verbose=True)
    message_history: list[ModelMessage] = []
    passed = True
    summary_texts: list[str] = []
    # all_summary_texts accumulates one entry per compaction in order.
    # summary_texts only holds the CURRENT history's markers — after phase-2
    # compaction rewrites history, the phase-1 marker is gone and summary_texts
    # drops back to length 1. all_summary_texts preserves both for chain validation.
    all_summary_texts: list[str] = []
    total_compactions: int = 0

    # Phase prompts — phase 1 builds Finch context, phase 2 pivots to a new
    # research topic rich enough to accumulate another compaction window.
    _phase1_prompts = [
        (
            "I want to study the 2021 Apple TV+ film Finch starring Tom Hanks. "
            "Start by fetching the Wikipedia page for the film."
        ),
        "Fetch the Wikipedia page for Miguel Sapochnik's filmography and his Game of Thrones work.",
        "Fetch Caleb Landry Jones's Wikipedia page for background on his voice performance.",
        "Fetch Gustavo Santaolalla's Wikipedia page and his discography.",
        "Fetch the list of Apple TV+ original films to place Finch in context.",
        "Fetch one critical review from Variety or RogerEbert.com.",
        "Fetch one more review from The Guardian or IndieWire.",
    ]
    _phase2_prompts = [
        (
            "Now pivot to the 2000 film Cast Away, also starring Tom Hanks. "
            "Fetch the Wikipedia page for Cast Away."
        ),
        "Fetch the Wikipedia page for Robert Zemeckis and his major works.",
        "Fetch Alan Silvestri's Wikipedia page and his collaboration history with Zemeckis.",
        "Fetch information about the filming of Cast Away on the Monuriki island location.",
        "Fetch critical reviews of Cast Away from Ebert or Rotten Tomatoes summary.",
        "Fetch the Wikipedia page for Wilson the volleyball from Cast Away.",
        "Fetch Tom Hanks's filmography section focusing on isolated-protagonist films.",
        "Fetch information about the survival film genre and how Finch and Cast Away fit within it.",
    ]

    max_turns = 40
    # step-timing table: one row per turn
    _step_rows: list[dict] = []

    async with AsyncExitStack() as stack:
        deps = await create_deps(frontend, stack)
        deps.config.llm.max_ctx = 32768
        agent = build_agent(
            config=deps.config,
            model=deps.model,
            tool_registry=deps.tool_registry,
        )

        phase = 1
        phase1_turn = 0
        phase2_turn = 0

        for turn_idx in range(max_turns):
            markers_snapshot = list(summary_texts)  # content snapshot for change detection

            if phase == 1:
                user_input = _phase1_prompts[min(phase1_turn, len(_phase1_prompts) - 1)]
                phase1_turn += 1
            else:
                user_input = _phase2_prompts[min(phase2_turn, len(_phase2_prompts) - 1)]
                phase2_turn += 1

            bd_before = _context_breakdown(message_history)
            prev_len = bd_before["msgs"]
            print(
                f"  Turn {turn_idx + 1}/{max_turns} [phase {phase}] — prompt: {user_input[:60]!r}"
            )
            _print_breakdown(bd_before, "before")

            _turn_start = time.monotonic()
            turn_result = await run_turn(
                agent=agent,
                user_input=user_input,
                deps=deps,
                message_history=message_history,
                frontend=frontend,
            )
            _elapsed = time.monotonic() - _turn_start
            print(f"    turn elapsed: {_elapsed:.1f}s")

            if turn_result.outcome == "error":
                print(
                    f"UAT: FAIL (turn error): turn {turn_idx + 1} — LLM call error or timeout "
                    f"({_elapsed:.1f}s); context may be too large for the local model"
                )
                return False

            message_history = turn_result.messages
            summary_texts = _count_summary_markers(message_history)

            bd_after = _context_breakdown(message_history)
            _print_breakdown(bd_after, "after ")
            print(
                f"    delta: msgs={bd_after['msgs'] - bd_before['msgs']:+d}  "
                f"calls={bd_after['tool_calls'] - bd_before['tool_calls']:+d}  "
                f"chars={bd_after['chars'] - bd_before['chars']:+,}"
            )
            _step_rows.append(
                {
                    "turn": turn_idx + 1,
                    "phase": phase,
                    "elapsed": _elapsed,
                    "msgs_before": bd_before["msgs"],
                    "msgs_after": bd_after["msgs"],
                    "calls": bd_after["tool_calls"] - bd_before["tool_calls"],
                    "chars_before": bd_before["chars"],
                    "chars_after": bd_after["chars"],
                    "compacted": summary_texts != markers_snapshot,
                }
            )

            # Compaction detection: any change in marker content (add OR replace).
            # Phase-2 compaction rewrites history and replaces the phase-1 marker,
            # so len(summary_texts) stays at 1 — a content diff is the only reliable signal.
            if summary_texts != markers_snapshot:
                total_compactions += 1
                for text in summary_texts:
                    if text not in all_summary_texts:
                        all_summary_texts.append(text)
                print(f"  Compaction fired (total: {total_compactions}) — phase {phase}")

            # Stall detection: no tool calls on turn ≥2 and phase-N compaction not yet fired.
            # latest_exchange may be empty when history shrank due to compaction; in that case
            # total_compactions just incremented and the guard below handles it.
            latest_exchange = message_history[prev_len:]
            tool_calls_this_turn = sum(
                1
                for m in latest_exchange
                if isinstance(m, ModelResponse)
                for p in m.parts
                if isinstance(p, ToolCallPart)
            )
            if tool_calls_this_turn == 0 and turn_idx >= 1 and total_compactions < phase:
                print(
                    f"UAT: FAIL (agentic stall): turn {turn_idx + 1} had no tool calls "
                    f"before phase-{phase} compaction triggered"
                )
                return False

            # Phase transition: first compaction fired → switch to phase 2
            if phase == 1 and total_compactions >= 1:
                print("  Phase 1 complete — switching to phase 2 (Cast Away research)")
                phase = 2

            # Done: both compactions fired
            if total_compactions >= 2:
                print(f"  Both compactions fired after turn {turn_idx + 1}")
                break
        else:
            print(
                f"UAT: FAIL (incomplete): {max_turns} turns,"
                f" only {total_compactions}/2 compactions fired"
            )
            return False

    # Step-timing table
    print("\n  Step timing summary:")
    print(
        f"  {'Turn':>4}  {'Ph':>2}  {'Elapsed':>8}  {'MsgsBefore':>10}  {'MsgsAfter':>9}  {'Calls':>5}  {'CharsBefore':>11}  {'CharsAfter':>10}  {'Compact':>7}"
    )
    print(
        f"  {'-' * 4}  {'-' * 2}  {'-' * 8}  {'-' * 10}  {'-' * 9}  {'-' * 5}  {'-' * 11}  {'-' * 10}  {'-' * 7}"
    )
    for r in _step_rows:
        print(
            f"  {r['turn']:>4}  {r['phase']:>2}  {r['elapsed']:>7.1f}s"
            f"  {r['msgs_before']:>10}  {r['msgs_after']:>9}"
            f"  {r['calls']:>5}  {r['chars_before']:>11,}  {r['chars_after']:>10,}"
            f"  {'YES' if r['compacted'] else '':>7}"
        )
    total_elapsed = sum(r["elapsed"] for r in _step_rows)
    print(f"  {'total':>4}  {'':>2}  {total_elapsed:>7.1f}s")

    # Validate iterative summary chain via keyword check
    summary_1 = all_summary_texts[0] if len(all_summary_texts) >= 1 else ""
    summary_2 = all_summary_texts[1] if len(all_summary_texts) >= 2 else ""

    print(f"\n  Summary 1 ({len(summary_1)} chars, phase-1 Finch compaction)")
    if summary_1:
        print(f"  S1 preview: {_snippet(summary_1, 300)}")
    print(f"  Summary 2 ({len(summary_2)} chars, phase-2 Cast Away compaction)")
    if summary_2:
        print(f"  S2 preview: {_snippet(summary_2, 300)}")

    if summary_1 and summary_2:
        print("\n  Chain fidelity check (keyword)...")
        chain_ok, kw_lines = _keyword_chain_check(summary_2, message_history)
        for line in kw_lines:
            print(line)
        if chain_ok:
            print("UAT: PASS: iterative summary chain check passed")
        else:
            print(
                "UAT: FAIL: chain check — phase-1 keywords missing from phase-2 summary "
                "(iterative previous_compaction_summary chain broken)"
            )
            passed = False

    # Log approval calls informatively — HeadlessFrontend auto-approves (approval_response="y"),
    # so these never block. Tool choice (web_fetch vs curl) is outside compaction eval scope.
    approval_calls = getattr(frontend, "approval_calls", [])
    if approval_calls:
        print(f"  INFO: {len(approval_calls)} approval call(s) auto-approved (no hang)")
    else:
        print("  INFO: no approval calls")

    if passed:
        print("UAT: PASS: multi-cycle compaction complete")
    else:
        print("UAT: FAIL — see above")
    return passed


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_LAST_RESULTS: dict[str, bool] = {}
_NOISE_PATTERNS = ("WARNING:", "Compacting conversation")


def _build_report(results: dict[str, bool]) -> str:
    lines: list[str] = []
    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    verdict = "PASS" if passed_count == total else "FAIL"

    lines.append("# Compaction Multi-Cycle Eval Report")
    lines.append("")
    lines.append(f"**Verdict: {verdict}** ({passed_count}/{total} passed)")
    lines.append("")

    lines.append("| Step | Result |")
    lines.append("|------|--------|")
    for name, ok in results.items():
        lines.append(f"| {name} | {'PASS' if ok else '**FAIL**'} |")
    lines.append("")

    return "\n".join(lines)


async def _run_all() -> int:
    print("=" * 60)
    print("  Eval: Compaction — Multi-Cycle UAT (M3 × 2)")
    print("=" * 60)

    results: dict[str, bool] = {}
    results["Multi-cycle compaction (UAT)"] = await step_multi_cycle_compaction()

    print("\n" + "=" * 60)
    print("  Results")
    print("=" * 60)
    all_pass = True
    for name, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            all_pass = False

    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    print(f"\n  {passed_count}/{total} passed")
    print(f"\nVERDICT: {'PASS' if all_pass else 'FAIL'}")
    _LAST_RESULTS.update(results)
    return 0 if all_pass else 1


def main() -> int:
    logging.basicConfig(level=logging.WARNING)

    # Targeted DEBUG handlers — root stays at WARNING; only these modules bubble to stdout.
    for _mod, _tag in [
        ("co_cli.agent.core", "agent"),
        ("co_cli.context.orchestrate", "llm"),
        ("co_cli.context.compaction", "compaction"),
    ]:
        _h = logging.StreamHandler(sys.stdout)
        _h.setLevel(logging.DEBUG)
        _h.setFormatter(logging.Formatter(f"  [{_tag}] %(message)s"))
        _l = logging.getLogger(_mod)
        _l.setLevel(logging.DEBUG)
        _l.addHandler(_h)
        _l.propagate = False

    exit_code = asyncio.run(_run_all())

    report_path = Path("docs/REPORT-compaction-multi-cycle.md")
    report_path.write_text(_build_report(_LAST_RESULTS), encoding="utf-8")
    print(f"\nReport: {report_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
