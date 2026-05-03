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
from evals._judge import run_judge
from evals._timeouts import (
    EVAL_DEEP_LEARNING_TURN_TIMEOUT_SECS,
    EVAL_PROBE_TIMEOUT_SECS,
)
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)

from co_cli.agent.core import build_agent
from co_cli.bootstrap.core import create_deps
from co_cli.context.compaction import SUMMARY_MARKER_PREFIX
from co_cli.context.orchestrate import run_turn
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import LlmModel

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
# LLM judge for iterative summary chain quality
# ---------------------------------------------------------------------------


class _SummaryChainJudgeScore(BaseModel):
    phase1_chain_preserved: int
    """1–5: how well phase-1 facts (Finch, Tom Hanks, Apple TV+) are reflected in the phase-2 summary."""
    phase2_facts_present: int
    """1–5: how much phase-2 content (Cast Away, Zemeckis) is present in the phase-2 summary."""
    rationale: str
    """One sentence overall quality judgment."""


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


async def _judge_summary_chain(
    summary_1: str,
    summary_2: str,
    full_history: list[ModelMessage],
    llm_model: LlmModel,
) -> tuple[bool, list[str]]:
    """Evaluate whether summary_2 faithfully chains from summary_1.

    Primary gate: keyword chain check (deterministic).
      - Phase-1 keywords must appear in the phase-2 summary (iterative chain test).
      - Phase-2 keywords must appear somewhere in the full history (tail preservation).
    Secondary: LLM judge for qualitative scoring (informational only).
    """
    lines: list[str] = []

    phase1_ok, kw_lines = _keyword_chain_check(summary_2, full_history)
    lines.extend(kw_lines)

    passed = phase1_ok  # tail preservation is informational; only chain preservation gates
    if not passed:
        lines.append(
            "    FAIL: chain check — phase-1 keywords (Finch/Tom Hanks/Apple TV+) "
            "missing from phase-2 summary (iterative previous_compaction_summary chain broken)"
        )
    else:
        lines.append("    PASS: keyword chain check — phase-1 facts preserved in phase-2 summary")

    # LLM judge: informational quality score only — does not affect pass/fail.
    prompt = (
        "Score the quality of an iterative compaction summary chain.\n\n"
        "Context: An AI agent researched Finch (Apple TV+ film) in phase-1, then pivoted to\n"
        "research Cast Away in phase-2. The phase-2 summary is an iterative update that should\n"
        "PRESERVE key phase-1 facts AND incorporate new phase-2 facts.\n\n"
        "PHASE-1 SUMMARY (Finch research — these facts should survive into phase-2):\n---\n"
        f"{summary_1[:2000]}\n---\n\n"
        "PHASE-2 SUMMARY (Cast Away research PLUS preserved Finch context — evaluate both):\n---\n"
        f"{summary_2[:2000]}\n---\n\n"
        "Score:\n"
        "- phase1_chain_preserved: how well phase-1 facts (Finch, Tom Hanks, Apple TV+) "
        "survived into the phase-2 summary (1=absent, 5=fully preserved)\n"
        "- phase2_facts_present: how well phase-2 content (Cast Away, Zemeckis) is covered "
        "(1=absent, 5=fully present) — Cast Away content IS expected, not fabrication\n"
        "- rationale: one sentence overall quality judgment"
    )
    score, err = await run_judge(
        prompt,
        _SummaryChainJudgeScore,
        llm_model=llm_model,
        system_prompt=(
            "You are a quality evaluator for iterative AI compaction summaries. "
            "Score chain fidelity and content coverage. "
            "Note: the phase-2 summary is expected to discuss Cast Away — that is correct behavior."
        ),
    )

    if score is None:
        lines.append(f"    INFO: judge skipped — {err}")
    else:
        lines.append(
            f"    INFO judge scores: phase1_chain_preserved={score.phase1_chain_preserved}/5"
            f"  phase2_facts_present={score.phase2_facts_present}/5"
        )
        lines.append(f"    INFO rationale: {score.rationale}")

    return passed, lines


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
    # drops back to length 1. all_summary_texts preserves both for the judge.
    all_summary_texts: list[str] = []
    total_compactions: int = 0
    pre_compaction_histories: list[list[ModelMessage]] = []
    judge_llm_model: LlmModel | None = None

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

    async with AsyncExitStack() as stack:
        deps = await create_deps(frontend, stack)
        deps.config.llm.num_ctx = 32768
        # Lower compaction trigger so phase-2 fires in ~4-5 turns instead of 8-9.
        # With 32k context and 50k persist threshold, persisted pages contribute only ~2k
        # tokens each (placeholder). At 0.65 it takes 8+ turns to accumulate 21k tokens,
        # leaving the local LLM struggling at large contexts. 0.5 → fires at 16k tokens.
        deps.config.compaction.compaction_ratio = 0.5
        judge_llm_model = deps.model
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

            prev_len = len(message_history)
            pre_turn_history = list(message_history)
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

            # run_turn absorbs segment hang timeouts and returns outcome="error" instead of
            # raising — the eval's asyncio.timeout never fires. Detect and fail explicitly.
            if turn_result.outcome == "error":
                print(
                    f"UAT: FAIL (turn error): turn {turn_idx + 1} — LLM segment error or timeout "
                    f"({_elapsed:.1f}s); context may be too large for the local model"
                )
                return False

            message_history = turn_result.messages
            summary_texts = _count_summary_markers(message_history)

            # Compaction detection: any change in marker content (add OR replace).
            # Phase-2 compaction rewrites history and replaces the phase-1 marker,
            # so len(summary_texts) stays at 1 — a content diff is the only reliable signal.
            if summary_texts != markers_snapshot:
                total_compactions += 1
                for text in summary_texts:
                    if text not in all_summary_texts:
                        all_summary_texts.append(text)
                pre_compaction_histories.append(pre_turn_history)
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

    # Validate iterative summary chain via LLM judge
    summary_1 = all_summary_texts[0] if len(all_summary_texts) >= 1 else ""
    summary_2 = all_summary_texts[1] if len(all_summary_texts) >= 2 else ""

    print(f"\n  Summary 1 ({len(summary_1)} chars, phase-1 Finch compaction)")
    if summary_1:
        print(f"  S1 preview: {_snippet(summary_1, 300)}")
    print(f"  Summary 2 ({len(summary_2)} chars, phase-2 Cast Away compaction)")
    if summary_2:
        print(f"  S2 preview: {_snippet(summary_2, 300)}")

    if summary_1 and summary_2 and judge_llm_model is not None:
        print("\n  Chain fidelity check (keyword + LLM judge)...")
        judge_ok, judge_lines = await _judge_summary_chain(
            summary_1, summary_2, message_history, judge_llm_model
        )
        for line in judge_lines:
            print(line)
        if judge_ok:
            print("UAT: PASS: iterative summary chain quality check passed")
        else:
            print("UAT: FAIL: iterative summary chain quality check failed — see scores above")
            passed = False

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
