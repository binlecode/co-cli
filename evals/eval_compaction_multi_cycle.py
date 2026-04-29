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
import io
import logging
import sys
import time
from contextlib import AsyncExitStack, redirect_stdout
from pathlib import Path

import httpx
from evals._timeouts import (
    EVAL_DEEP_LEARNING_TURN_TIMEOUT_SECS,
    EVAL_PROBE_TIMEOUT_SECS,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)

from co_cli.agent.core import build_agent
from co_cli.bootstrap.core import create_deps
from co_cli.config.core import settings
from co_cli.context.compaction import SUMMARY_MARKER_PREFIX
from co_cli.context.orchestrate import run_turn
from co_cli.display.headless import HeadlessFrontend

# ---------------------------------------------------------------------------
# Config — real settings with eval-local overrides
# ---------------------------------------------------------------------------

_EVAL_CONFIG = settings.model_copy(
    update={
        "mcp_servers": {},
        "llm": settings.llm.model_copy(update={"num_ctx": 32768}),
    }
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

    # Phase prompts — phase 1 builds Finch context, phase 2 pivots to a new
    # research topic rich enough to accumulate another compaction window.
    _phase1_prompts = [
        (
            "I want you to conduct a comprehensive deep study of the 2021 Apple TV+ film Finch, "
            "starring Tom Hanks and directed by Miguel Sapochnik. Fetch the Wikipedia pages for "
            "the film, Tom Hanks, Miguel Sapochnik, Caleb Landry Jones, and Gustavo Santaolalla. "
            "Also fetch at least two critical reviews. Do not stop — keep fetching until you have "
            "covered the plot, themes, production history (including the BIOS working title), "
            "cast and crew, score, critical reception, and Apple TV+ context."
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
            "Now I want to pivot to a deep study of the 2000 film Cast Away, also starring "
            "Tom Hanks. Fetch the Wikipedia page for Cast Away, then fetch pages for director "
            "Robert Zemeckis, composer Alan Silvestri, and the FedEx product placement context. "
            "Keep fetching new sources — this is a comparative study of Hanks's isolated-"
            "protagonist roles across two decades."
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

    async with AsyncExitStack() as stack:
        deps = await create_deps(frontend, stack)
        deps.config.llm.num_ctx = 32768
        agent = build_agent(
            config=deps.config,
            model=deps.model,
            tool_registry=deps.tool_registry,
        )

        phase = 1
        phase1_turn = 0
        phase2_turn = 0

        for turn_idx in range(max_turns):
            markers_before = len(summary_texts)

            if phase == 1:
                user_input = _phase1_prompts[min(phase1_turn, len(_phase1_prompts) - 1)]
                phase1_turn += 1
            else:
                user_input = _phase2_prompts[min(phase2_turn, len(_phase2_prompts) - 1)]
                phase2_turn += 1

            prev_len = len(message_history)
            print(f"  Turn {turn_idx + 1}/{max_turns} [phase {phase}] — history: {prev_len} msgs")

            _turn_start = time.monotonic()
            try:
                async with asyncio.timeout(EVAL_DEEP_LEARNING_TURN_TIMEOUT_SECS):
                    turn_result = await run_turn(
                        agent=agent,
                        user_input=user_input,
                        deps=deps,
                        message_history=message_history,
                        frontend=frontend,
                    )
            except TimeoutError:
                print(
                    f"UAT: FAIL (turn timeout): turn {turn_idx + 1} exceeded"
                    f" {EVAL_DEEP_LEARNING_TURN_TIMEOUT_SECS} seconds"
                )
                return False
            _elapsed = time.monotonic() - _turn_start
            print(f"    turn elapsed: {_elapsed:.1f}s")

            message_history = turn_result.messages
            summary_texts = _count_summary_markers(message_history)
            new_markers = len(summary_texts) - markers_before

            if new_markers > 0:
                print(f"  Compaction fired (total: {len(summary_texts)}) — phase {phase}")

            # Stall detection: no tool calls on turn ≥2 and no compaction yet
            latest_exchange = message_history[prev_len:]
            tool_calls_this_turn = sum(
                1
                for m in latest_exchange
                if isinstance(m, ModelResponse)
                for p in m.parts
                if isinstance(p, ToolCallPart)
            )
            if tool_calls_this_turn == 0 and turn_idx >= 1 and len(summary_texts) < phase:
                print(
                    f"UAT: FAIL (agentic stall): turn {turn_idx + 1} had no tool calls "
                    f"before phase-{phase} compaction triggered"
                )
                return False

            # Phase transition: first compaction fired → switch to phase 2
            if phase == 1 and len(summary_texts) >= 1:
                print("  Phase 1 complete — switching to phase 2 (Cast Away research)")
                phase = 2

            # Done: both compactions fired
            if len(summary_texts) >= 2:
                print(f"  Both compactions fired after turn {turn_idx + 1}")
                break
        else:
            fired = len(summary_texts)
            print(f"UAT: FAIL (incomplete): {max_turns} turns, only {fired}/2 compactions fired")
            return False

    # Validate iterative summary chain: key facts from phase 1 survive into phase 2 marker
    summary_1 = summary_texts[0] if len(summary_texts) >= 1 else ""
    summary_2 = summary_texts[1] if len(summary_texts) >= 2 else ""

    print(f"\n  Summary 1 ({len(summary_1)} chars, phase-1 Finch compaction)")
    print(f"  Summary 2 ({len(summary_2)} chars, phase-2 Cast Away compaction)")

    # Phase-1 facts that must survive into the phase-2 marker
    phase1_survival_checks = [
        ("Finch film title", ["finch"]),
        ("Tom Hanks", ["tom hanks", "hanks"]),
        ("Apple TV+", ["apple tv", "apple"]),
    ]
    low2 = summary_2.lower()
    survival_ok = True
    for label, keywords in phase1_survival_checks:
        hits = [kw for kw in keywords if kw in low2]
        if hits:
            print(f"  PASS: phase-1 fact '{label}' survived into phase-2 marker ({hits[0]!r})")
        else:
            print(
                f"  FAIL: phase-1 fact '{label}' lost from phase-2 marker "
                f"(iterative summary chain broken)"
            )
            survival_ok = False
            passed = False

    if survival_ok:
        print("UAT: PASS: iterative summary chain preserved phase-1 facts into phase-2 marker")

    # Phase-2 facts present in second marker
    phase2_checks = [
        ("Cast Away title", ["cast away"]),
        ("Robert Zemeckis", ["zemeckis"]),
    ]
    for label, keywords in phase2_checks:
        hits = [kw for kw in keywords if kw in low2]
        if hits:
            print(f"  PASS: phase-2 fact '{label}' in second marker ({hits[0]!r})")
        else:
            print(f"  WARN: phase-2 fact '{label}' absent from second marker (LLM may paraphrase)")

    # Approval-hang guard
    approval_prompts = getattr(frontend, "approval_calls", None) or getattr(
        frontend, "approval_prompts", []
    )
    if approval_prompts:
        print(f"UAT: FAIL: unexpected approval prompts: {approval_prompts}")
        passed = False
    else:
        print("UAT: PASS: no approval prompts")

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


def _build_report(raw_output: str, results: dict[str, bool]) -> str:
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
    global _LAST_RESULTS
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
    buf = io.StringIO()

    class Tee:
        def __init__(self, *targets):
            self.targets = targets

        def write(self, s):
            for t in self.targets:
                t.write(s)
            return len(s)

        def flush(self):
            for t in self.targets:
                t.flush()

    tee = Tee(sys.stdout, buf)
    with redirect_stdout(tee):
        exit_code = asyncio.run(_run_all())

    report_path = Path("docs/REPORT-compaction-multi-cycle.md")
    report_path.write_text(_build_report(buf.getvalue(), _LAST_RESULTS), encoding="utf-8")
    print(f"\nReport: {report_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
