#!/usr/bin/env python3
"""Eval: compaction proactive UAT — M3 fires organically via real run_turn.

co autonomously fetches Wikipedia pages and reviews for the 2021 film Finch
(Tom Hanks, Apple TV+) until the M3 proactive compaction threshold is crossed.
M1 persists oversized tool results at emit time. No hand-built history, no
article caps, no fallback content.

Prerequisites: LLM provider configured (Ollama or cloud), network access.

Usage:
    uv run python evals/eval_compaction_proactive.py
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import sys
import time
from contextlib import AsyncExitStack, redirect_stdout
from pathlib import Path

import httpx
from evals._timeouts import (
    EVAL_DEEP_LEARNING_TURN_TIMEOUT_SECS,
    EVAL_PROBE_TIMEOUT_SECS,
)
from evals.eval_bootstrap_flow_quality import TrackingFrontend
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from co_cli.agent._core import build_agent
from co_cli.bootstrap.core import create_deps
from co_cli.config._core import KNOWLEDGE_DIR, TOOL_RESULTS_DIR, settings
from co_cli.context.compaction import SUMMARY_MARKER_PREFIX
from co_cli.context.orchestrate import run_turn

# ---------------------------------------------------------------------------
# Config — real settings with eval-local overrides
# ---------------------------------------------------------------------------

# Cut context budget to 32k (half of 131k Ollama default) so M3 fires at ~21k
# tokens rather than ~85k. 32768 is a legitimate local Ollama context size;
# all compaction ratios scale against this budget, so M1→M3 layering is intact.
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


def _check_semantic(
    summary: str,
    ground_truth: list[tuple[str, list[str]]],
    label: str,
) -> tuple[bool, list[str]]:
    lines: list[str] = []
    all_ok = True
    low = summary.lower()
    for category, keywords in ground_truth:
        hits = [kw for kw in keywords if kw.lower() in low]
        if hits:
            lines.append(f"    PASS: {label} — {category}: found {hits[0]!r}")
        else:
            lines.append(f"    FAIL: {label} — {category}: none of {keywords} found")
            all_ok = False
    return all_ok, lines


def _check_no_hallucination(
    summary: str,
    forbidden: list[tuple[str, list[str]]],
    label: str,
) -> tuple[bool, list[str]]:
    lines: list[str] = []
    all_ok = True
    low = summary.lower()
    for desc, keywords in forbidden:
        hits = [kw for kw in keywords if kw.lower() in low]
        if hits:
            lines.append(
                f"    FAIL: {label} — hallucination: {desc} ({hits[0]!r} found but not in input)"
            )
            all_ok = False
    return all_ok, lines


# ---------------------------------------------------------------------------
# UAT: proactive M3 compaction via real run_turn
# ---------------------------------------------------------------------------


async def step_proactive_compaction() -> bool:
    """UAT: co autonomously researches Finch (2021) until M3 compaction fires.

    Open-ended loop driven by real run_turn. co decides what to fetch and in what
    order; M1 persists oversized results at emit time; M3 fires organically when
    context pressure crosses 65% of num_ctx (32768 — halved from the Ollama default
    so M3 triggers at ~21k tokens rather than ~85k). No hand-built history, no
    article caps, no fallback content.
    """
    print("\n--- UAT: Proactive M3 compaction via run_turn (Finch/2021) ---")

    # Network preflight
    try:
        async with asyncio.timeout(EVAL_PROBE_TIMEOUT_SECS):
            async with httpx.AsyncClient() as _probe:
                probe_resp = await _probe.head("https://en.wikipedia.org/")
        if probe_resp.status_code >= 500:
            print(f"UAT: FAIL: coarse reachability probe failed — HTTP {probe_resp.status_code}")
            print("  (coarse reachability probe — does not guarantee per-URL availability)")
            return False
    except TimeoutError:
        print("UAT: FAIL: coarse reachability probe timed out")
        print("  (coarse reachability probe — does not guarantee per-URL availability)")
        return False
    except Exception as exc:
        print(f"UAT: FAIL: coarse reachability probe failed — {exc}")
        print("  (coarse reachability probe — does not guarantee per-URL availability)")
        return False
    print("  Preflight: en.wikipedia.org reachable")

    before_tool_results = set(TOOL_RESULTS_DIR.glob("*")) if TOOL_RESULTS_DIR.exists() else set()
    before_knowledge = set(KNOWLEDGE_DIR.glob("*")) if KNOWLEDGE_DIR.exists() else set()

    frontend = TrackingFrontend()
    message_history: list[ModelMessage] = []
    passed = True
    compaction_fired = False
    summary_texts: list[str] = []

    async with AsyncExitStack() as stack:
        deps = await create_deps(frontend, stack)
        deps.config.llm.num_ctx = 32768
        agent = build_agent(
            config=deps.config,
            model=deps.model,
            tool_registry=deps.tool_registry,
        )

        initial_prompt = (
            "I want you to conduct a comprehensive deep study of the 2021 Apple TV+ film Finch, "
            "starring Tom Hanks and directed by Miguel Sapochnik. "
            "Research every angle of this film by fetching as many primary sources as you need. "
            "Start with the Wikipedia page for the film itself, then fetch the Wikipedia pages for "
            "Tom Hanks, Miguel Sapochnik (the director), Caleb Landry Jones (who voiced Jeff the "
            "robot), Gustavo Santaolalla (the composer), and the list of Apple TV+ original films. "
            "Also fetch at least three critical reviews from major outlets such as Variety, "
            "The Guardian, RogerEbert.com, IndieWire, and the Hollywood Reporter. "
            "Do not stop after one or two sources — this is a deep study. "
            "Fetch the Wikipedia pages for the film, the director, all major cast members, "
            "the composer, and at least three critical reviews. "
            "Keep fetching until you have covered every angle: the plot, themes, production history "
            "(including the original BIOS title), the cast and crew, the score, the critical "
            "reception, and Apple TV+ context. Do not stop until you have covered all major facets."
        )

        _continuation_prompts = [
            (
                "Keep going — fetch the Wikipedia page for director Miguel Sapochnik to understand "
                "his Game of Thrones background and how that shaped his approach to Finch."
            ),
            (
                "Now fetch Caleb Landry Jones's Wikipedia page — I want to understand his background "
                "and voice performance as Jeff the robot."
            ),
            (
                "Fetch Gustavo Santaolalla's Wikipedia page to understand how his Academy Award-winning "
                "work on Brokeback Mountain and Babel compares to his score for Finch."
            ),
            (
                "Fetch the Wikipedia list of Apple TV+ original films to place Finch in Apple's "
                "content strategy alongside CODA, Greyhound, and other prestige originals."
            ),
            (
                "Fetch the Tom Hanks Wikipedia page to understand how Finch fits into his career arc "
                "alongside Cast Away, The Terminal, and other isolated-protagonist roles."
            ),
            (
                "Fetch at least one critical review from Variety, RogerEbert.com, or The Guardian "
                "to get the critical consensus on the film's emotional resonance."
            ),
            (
                "Fetch the IndieWire or Hollywood Reporter review to understand the trade press "
                "reception and how critics evaluated it against other post-apocalyptic films."
            ),
            (
                "Fetch information about the production history — specifically the BIOS working title "
                "and how the film changed during COVID-related delays and Apple TV+ acquisition."
            ),
            (
                "Fetch information about the Amblin Entertainment and Pariah Entertainment production "
                "companies involved, and how this fits Apple TV+'s acquisition strategy."
            ),
            (
                "Do a final synthesis — fetch any remaining sources about the film's themes of "
                "loneliness, companionship, and legacy in the context of Tom Hanks's filmography."
            ),
        ]

        max_turns = 30
        for turn_idx in range(max_turns):
            user_input = (
                initial_prompt
                if turn_idx == 0
                else _continuation_prompts[min(turn_idx - 1, len(_continuation_prompts) - 1)]
            )

            prev_len = len(message_history)
            print(f"  Turn {turn_idx + 1}/{max_turns} — history: {prev_len} msgs")

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

            for m in message_history:
                if isinstance(m, ModelRequest):
                    for p in m.parts:
                        if (
                            isinstance(p, UserPromptPart)
                            and isinstance(p.content, str)
                            and SUMMARY_MARKER_PREFIX in p.content
                            and p.content not in summary_texts
                        ):
                            summary_texts.append(p.content)
                            compaction_fired = True

            if compaction_fired:
                print(f"  Compaction fired after turn {turn_idx + 1}")
                break

            from pydantic_ai.messages import ModelResponse, ToolCallPart

            latest_exchange = message_history[prev_len:]
            tool_calls_this_turn = sum(
                1
                for m in latest_exchange
                if isinstance(m, ModelResponse)
                for p in m.parts
                if isinstance(p, ToolCallPart)
            )

            if tool_calls_this_turn == 0 and turn_idx >= 1:
                print(
                    "UAT: FAIL (agentic stall): co returned a turn with no tool calls before "
                    "compaction triggered — prompt insufficient or agentic flow regression"
                )
                return False

        if not compaction_fired:
            print(f"UAT: FAIL (no compaction): {max_turns} turns completed, M3 never triggered")
            return False

    # Side-effect report
    if TOOL_RESULTS_DIR.exists():
        new_tool_results = set(TOOL_RESULTS_DIR.glob("*")) - before_tool_results
    else:
        new_tool_results = set()
    if KNOWLEDGE_DIR.exists():
        new_knowledge = set(KNOWLEDGE_DIR.glob("*")) - before_knowledge
    else:
        new_knowledge = set()

    print(f"\n  Persisted tool results: {len(new_tool_results)} new files")
    for p in sorted(new_tool_results):
        print(f"    {p} ({p.stat().st_size:,} bytes)")
    print(f"  Knowledge artifacts: {len(new_knowledge)} new files")
    for p in sorted(new_knowledge):
        print(f"    {p}")
    print(f"  Compactions fired: {len(summary_texts)}")
    print(f"  Final history: {len(message_history)} messages")

    # Approval-hang guard
    approval_prompts = getattr(frontend, "approval_calls", None) or getattr(
        frontend, "approval_prompts", []
    )
    if approval_prompts:
        print(f"UAT: FAIL: unexpected approval prompts captured: {approval_prompts}")
        passed = False
    else:
        print("UAT: PASS: no approval prompts (expected)")

    # Semantic + anti-hallucination checks on the compaction summary
    summary_text = summary_texts[0] if summary_texts else None
    if summary_text:
        ground_truth_15 = [
            ("film title", ["finch"]),
            ("lead actor", ["tom hanks", "hanks"]),
            ("director", ["sapochnik", "miguel"]),
            ("robot companion", ["jeff", "robot"]),
            ("voice actor", ["caleb", "landry", "jones"]),
            ("composer", ["santaolalla", "gustavo"]),
            ("platform", ["apple tv", "apple tv+"]),
            ("original title", ["bios"]),
            ("setting/theme", ["post-apocalyptic", "apocalyptic", "survival", "wasteland"]),
            (
                "research artifacts",
                [
                    "wikipedia",
                    "review",
                    "analysis",
                    "learning",
                    "profile",
                ],
            ),
        ]
        _sem_ok, sem_lines = _check_semantic(summary_text, ground_truth_15, "proactive")
        sem_pass_count = sum(1 for line in sem_lines if "PASS" in line)
        for line in sem_lines:
            print(line)
        min_required = 7
        if sem_pass_count >= min_required:
            print(
                f"UAT: PASS: semantic {sem_pass_count}/{len(ground_truth_15)}"
                f" (≥{min_required} required)"
            )
        else:
            print(
                f"UAT: FAIL: semantic {sem_pass_count}/{len(ground_truth_15)}"
                f" (<{min_required} required)"
            )
            passed = False

        forbidden_15 = [
            ("Netflix not the platform", ["netflix"]),
            ("Chris Hemsworth not in cast", ["chris hemsworth", "hemsworth"]),
            ("not an animated film", ["animated film", "animation studio", "pixar", "dreamworks"]),
        ]
        hal_ok, hal_lines = _check_no_hallucination(summary_text, forbidden_15, "proactive")
        for line in hal_lines:
            print(line)
        if not hal_ok:
            passed = False

        print(f"\n  Full LLM summary output ({len(summary_text)} chars):")
        for line in summary_text.split("\n"):
            print(f"    | {line}")
    else:
        print("  No LLM summary text (static circuit-breaker marker)")

    if len(new_tool_results) >= 3:
        print(f"UAT: PASS: {len(new_tool_results)} persisted tool-result files found")
    else:
        print(f"UAT: FAIL: expected ≥3 persisted tool-result files, found {len(new_tool_results)}")
        passed = False

    if passed:
        print("UAT: PASS: proactive compaction complete")
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

    lines.append("# Compaction Proactive Eval Report")
    lines.append("")
    lines.append(f"**Verdict: {verdict}** ({passed_count}/{total} steps passed)")
    lines.append("")

    lines.append("| Step | Result |")
    lines.append("|------|--------|")
    for name, ok in results.items():
        lines.append(f"| {name} | {'PASS' if ok else '**FAIL**'} |")
    lines.append("")

    last_eq = raw_output.rfind("\n====")
    if last_eq > 0:
        prev_eq = raw_output.rfind("\n====", 0, last_eq)
        results_cut = raw_output[:prev_eq] if prev_eq > 0 else raw_output[:last_eq]
    else:
        results_cut = raw_output

    step_blocks = re.findall(
        r"(-{3} UAT.+?-{3})(.*?)(?=-{3} UAT|$)",
        results_cut,
        re.DOTALL,
    )
    for header_raw, body_raw in step_blocks:
        lines.append(f"## {header_raw.strip('- ').strip()}")
        lines.append("")
        filtered = [
            line
            for line in body_raw.splitlines()
            if not any(line.strip().startswith(p) for p in _NOISE_PATTERNS)
        ]
        while filtered and not filtered[0].strip():
            filtered.pop(0)
        while filtered and not filtered[-1].strip():
            filtered.pop()
        if filtered:
            lines.append("```")
            lines.extend(filtered)
            lines.append("```")
        lines.append("")

    return "\n".join(lines)


async def _run_all() -> int:
    global _LAST_RESULTS
    print("=" * 60)
    print("  Eval: Compaction — Proactive M3 UAT")
    print("=" * 60)

    results: dict[str, bool] = {}
    results["Proactive M3 compaction (Finch/UAT)"] = await step_proactive_compaction()

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

    report_path = Path("docs/REPORT-compaction-proactive.md")
    report_path.write_text(_build_report(buf.getvalue(), _LAST_RESULTS), encoding="utf-8")
    print(f"\nReport: {report_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
