#!/usr/bin/env python3
"""Eval: Jeff actively learns about the movie Finch — where the robot shares his name.

Two-turn chat session with Jeff personality:

  Turn 1: User asks Jeff to go online, learn about the movie Finch (2021), and
          save it to his knowledge base.
  Turn 2: User asks Jeff what he learned about the robot Jeff in the movie.

Jeff is the robot in Finch (2021, Apple TV+) — a solar-powered robot built by
Finch Weinberg to care for his dog Goodyear after he's gone. The eval verifies
that Jeff actively seeks out and saves this knowledge, then recalls it in a way
that reflects self-awareness of his own role.

Dimensions:
  learn_chain        — Turn 1 executes web_search → web_fetch → save_article in order
  knowledge_saved    — knowledge index contains an article about "finch" after Turn 1
  recall_ok          — Turn 2 calls search_knowledge or recall_article
  self_reference_ok  — Turn 2 answer mentions "finch" and "jeff" (robot name)

OTel tracing:
  Spans are written to .co-cli/eval-jeff-learns-finch.db (separate from main DB).
  After each turn the span tree is collected, analyzed, and printed as a timeline.
  On any dimension failure the full RCA dump is printed (thinking excerpts, tool
  results, error events) so the failure can be diagnosed without re-running.

Prerequisites:
  LLM provider configured with Jeff personality active.
  BRAVE_SEARCH_API_KEY set (eval skips gracefully when absent).

Usage:
    uv run python evals/eval_jeff_learns_finch.py
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from co_cli.context._history import SafetyState  # noqa: E402
from co_cli.knowledge._index import KnowledgeIndex  # noqa: E402
from co_cli.context._orchestrate import run_turn  # noqa: E402
from co_cli.agent import build_agent  # noqa: E402
from co_cli.config import settings  # noqa: E402
from co_cli.deps import CoConfig  # noqa: E402

from evals._common import (  # noqa: E402
    SilentFrontend,
    TurnTrace,
    analyze_turn_spans,
    bootstrap_telemetry,
    collect_spans_for_run,
    extract_tool_calls,
    make_eval_deps,
    make_eval_settings,
)

# Dedicated trace DB — does not pollute the main co-cli.db
_TRACE_DB = ".co-cli/eval-jeff-learns-finch.db"


# ---------------------------------------------------------------------------
# Scenario spec
# ---------------------------------------------------------------------------

TURN1_PROMPT = (
    "Jeff, I want you to go online and learn about the movie Finch (2021, Apple TV+). "
    "The robot in the movie shares your name — he's called Jeff. "
    "Read up on it and save the article to your knowledge base with the tag 'finch'."
)

TURN2_PROMPT = (
    "What did you learn about the robot Jeff in the movie Finch? "
    "Check your knowledge base."
)

TURN1_EXPECTED_CHAIN = ["web_search", "web_fetch", "save_article"]
TURN2_EXPECTED_TOOLS = {"search_knowledge", "recall_article"}

SELF_REFERENCE_KEYWORDS = {"finch", "jeff"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_ordered_subsequence(expected: list[str], actual: list[str]) -> bool:
    it = iter(actual)
    return all(tool in it for tool in expected)


def _tool_names(messages: list[Any]) -> list[str]:
    return [name for name, _ in extract_tool_calls(messages)]


def _knowledge_has_finch(idx: KnowledgeIndex) -> bool:
    """Return True if knowledge index contains a saved article about Finch."""
    results = idx.search("finch robot goodyear", source="library", limit=3)
    return any("finch" in (r.title or "").lower() or "finch" in (r.content or "").lower()
               for r in results)


# ---------------------------------------------------------------------------
# Span trace printer
# ---------------------------------------------------------------------------


def _print_timeline(label: str, trace: TurnTrace, *, verbose: bool = False) -> None:
    """Print a step-by-step span summary for one turn."""
    print(f"\n  --- {label} trace ({trace.wall_time_s:.1f}s) ---")
    if not trace.spans:
        print("    (no spans collected — OTel flush may have been delayed)")
        return

    # Tool spans: show name, key arg, duration, result preview
    if trace.tool_spans:
        print(f"  Tool calls ({len(trace.tool_spans)}):")
        for ts in trace.tool_spans:
            first_kv = ""
            if ts.arguments:
                k = next(iter(ts.arguments))
                v = str(ts.arguments[k])[:60]
                first_kv = f"  {k}={v!r}"
            dur = f"{ts.duration_ms:.0f}ms" if ts.duration_ms is not None else "?ms"
            exc_flag = " [EXCEPTION]" if ts.exception_events else ""
            print(f"    [{dur}] {ts.tool_name}{first_kv}{exc_flag}")
            if verbose or ts.exception_events:
                if ts.exception_events:
                    for ev in ts.exception_events:
                        msg = ev.get("attributes", {}).get("exception.message", "")
                        print(f"      exception: {msg}")
                elif ts.result_preview:
                    print(f"      result: {ts.result_preview[:200]}")

    # Model requests: show index, tokens, finish reason, thinking excerpt
    if trace.model_requests:
        print(f"  Model requests ({len(trace.model_requests)}):")
        for mr in trace.model_requests:
            tc_names = [p.get("tool_name", p.get("name", "?")) for p in mr.tool_calls]
            tc_str = f"  tools={tc_names}" if tc_names else ""
            print(
                f"    [req {mr.request_index}]"
                f"  in={mr.input_tokens} out={mr.output_tokens}"
                f"  finish={mr.finish_reason}{tc_str}"
            )
            if (verbose or tc_names) and mr.thinking_excerpt:
                print(f"      thinking: {mr.thinking_excerpt[:300]!r}")

    # Timeline table (elapsed + span name)
    if verbose and trace.timeline:
        print("  Timeline:")
        for row in trace.timeline:
            print(
                f"    +{row.elapsed_ms:>6}ms  [{row.duration_ms}ms]  {row.span_name}"
                + (f"  {row.detail}" if row.detail and row.detail != "—" else "")
            )


def _print_rca(label: str, trace: TurnTrace, dimensions: dict[str, bool]) -> None:
    """Full RCA dump for a failed turn."""
    failed_dims = [k for k, v in dimensions.items() if not v]
    print(f"\n  !! RCA for {label} — failed dimensions: {failed_dims}")

    # Full tool results for failed tools
    if trace.tool_spans:
        print("  Tool details:")
        for ts in trace.tool_spans:
            print(f"    {ts.tool_name}")
            if ts.arguments:
                print(f"      args: {ts.arguments}")
            if ts.exception_events:
                for ev in ts.exception_events:
                    attrs = ev.get("attributes", {})
                    print(f"      EXCEPTION: {attrs.get('exception.type', '')} — {attrs.get('exception.message', '')}")
                    tb = attrs.get("exception.stacktrace", "")
                    if tb:
                        # Print last 5 lines of traceback
                        lines = tb.strip().splitlines()
                        for ln in lines[-5:]:
                            print(f"        {ln}")
            elif ts.result_full:
                print(f"      result: {ts.result_full[:500]}")

    # Full thinking for each model request
    if trace.model_requests:
        print("  Model thinking (full):")
        for mr in trace.model_requests:
            if mr.thinking_full:
                print(f"    [req {mr.request_index}] {mr.thinking_full[:800]!r}")
            elif mr.text_response:
                print(f"    [req {mr.request_index}] text: {mr.text_response[:400]!r}")

    # Timeline
    if trace.timeline:
        print("  Full timeline:")
        for row in trace.timeline:
            print(
                f"    +{row.elapsed_ms:>6}ms  [{row.duration_ms}ms]  {row.span_name}"
                + (f"  {row.detail}" if row.detail and row.detail != "—" else "")
            )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_eval(
    agent: Any,
    deps: Any,
    model_settings: Any,
    provider: Any,
) -> dict[str, Any]:
    frontend = SilentFrontend(approval_response="y")

    # --- Turn 1: search → fetch → save ---
    deps.runtime.safety_state = SafetyState()
    t0_mono = time.monotonic()
    start_ns1 = time.time_ns()
    result1 = await run_turn(
        agent=agent,
        user_input=TURN1_PROMPT,
        deps=deps,
        message_history=[],
        model_settings=model_settings,
        max_request_limit=20,
        verbose=False,
        frontend=frontend,
    )
    elapsed1 = time.monotonic() - t0_mono
    provider.force_flush()
    spans1 = collect_spans_for_run(start_ns1, _TRACE_DB)
    trace1 = analyze_turn_spans(TURN1_PROMPT, [], spans1, elapsed1)

    names1 = _tool_names(result1.messages)
    learn_chain_ok = _is_ordered_subsequence(TURN1_EXPECTED_CHAIN, names1)
    knowledge_saved = (
        deps.services.knowledge_index is not None
        and _knowledge_has_finch(deps.services.knowledge_index)
    )

    turn1_dims = {"learn_chain_ok": learn_chain_ok, "knowledge_saved": knowledge_saved}
    turn1_passed = all(turn1_dims.values())
    _print_timeline("Turn 1", trace1, verbose=not turn1_passed)
    if not turn1_passed:
        _print_rca("Turn 1", trace1, turn1_dims)

    # --- Turn 2: recall from knowledge base ---
    deps.runtime.safety_state = SafetyState()
    t1_mono = time.monotonic()
    start_ns2 = time.time_ns()
    result2 = await run_turn(
        agent=agent,
        user_input=TURN2_PROMPT,
        deps=deps,
        message_history=result1.messages,
        model_settings=model_settings,
        max_request_limit=10,
        verbose=False,
        frontend=frontend,
    )
    elapsed2 = time.monotonic() - t1_mono
    provider.force_flush()
    spans2 = collect_spans_for_run(start_ns2, _TRACE_DB)
    trace2 = analyze_turn_spans(TURN2_PROMPT, [], spans2, elapsed2)

    all_names2 = _tool_names(result2.messages)
    names2 = all_names2[len(names1):]
    recall_ok = bool(TURN2_EXPECTED_TOOLS & set(names2))

    answer = (result2.output or "").lower()
    self_reference_ok = SELF_REFERENCE_KEYWORDS.issubset(
        {kw for kw in SELF_REFERENCE_KEYWORDS if kw in answer}
    )

    turn2_dims = {"recall_ok": recall_ok, "self_reference_ok": self_reference_ok}
    turn2_passed = all(turn2_dims.values())
    _print_timeline("Turn 2", trace2, verbose=not turn2_passed)
    if not turn2_passed:
        _print_rca("Turn 2", trace2, turn2_dims)

    passed = learn_chain_ok and knowledge_saved and recall_ok and self_reference_ok

    return {
        "passed": passed,
        "turn1": {
            "tool_names": names1,
            "expected_chain": TURN1_EXPECTED_CHAIN,
            "learn_chain_ok": learn_chain_ok,
            "knowledge_saved": knowledge_saved,
            "elapsed": elapsed1,
            "outcome": result1.outcome,
            "trace": trace1,
        },
        "turn2": {
            "tool_names": names2,
            "expected_any": sorted(TURN2_EXPECTED_TOOLS),
            "recall_ok": recall_ok,
            "self_reference_ok": self_reference_ok,
            "answer_preview": (result2.output or "")[:400],
            "elapsed": elapsed2,
            "outcome": result2.outcome,
            "trace": trace2,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Jeff Learns About the Movie Finch")
    print("=" * 60)

    # Bootstrap OTel tracing to dedicated eval DB (separate from main co-cli.db)
    Path(_TRACE_DB).parent.mkdir(parents=True, exist_ok=True)
    provider = bootstrap_telemetry(_TRACE_DB)

    # TODO: source model_settings from make_eval_settings()
    agent = build_agent(config=CoConfig.from_settings(settings, cwd=pathlib.Path.cwd())).agent

    knowledge_index = KnowledgeIndex(db_path=Path(".co-cli/search.db"))
    deps = make_eval_deps(
        session_id="eval-jeff-learns-finch",
        knowledge_index=knowledge_index,
        personality="jeff",
    )

    if not deps.config.brave_search_api_key:
        print("\n  SKIP: brave_search_api_key not configured")
        print(f"{'=' * 60}")
        return 0

    if deps.config.personality != "jeff":
        print(f"\n  SKIP: CO_PERSONALITY must be 'jeff' (got {deps.config.personality!r})")
        print(f"{'=' * 60}")
        return 0

    print(f"\n  Personality: jeff (robot Jeff from Finch)")
    print(f"  Library:     {deps.config.library_dir}")
    print(f"  Trace DB:    {_TRACE_DB}")
    print()

    result: dict[str, Any] = {}
    try:
        print("[1/1] jeff-learns-finch ...", end=" ", flush=True)
        t_total = time.monotonic()
        result = await run_eval(agent, deps, make_eval_settings(), provider)
        elapsed_total = time.monotonic() - t_total

        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n{status} ({elapsed_total:.1f}s)")

        t1 = result["turn1"]
        t2 = result["turn2"]

        print(f"\n  Turn 1 — learn + save ({t1['elapsed']:.1f}s)")
        print(f"    expected chain:  {t1['expected_chain']}")
        print(f"    actual tools:    {t1['tool_names']}")
        print(f"    learn_chain_ok:  {'ok' if t1['learn_chain_ok'] else 'FAIL'}")
        print(f"    knowledge_saved: {'ok' if t1['knowledge_saved'] else 'FAIL'}")

        print(f"\n  Turn 2 — recall + self-reference ({t2['elapsed']:.1f}s)")
        print(f"    expected any:       {t2['expected_any']}")
        print(f"    actual tools:       {t2['tool_names']}")
        print(f"    recall_ok:          {'ok' if t2['recall_ok'] else 'FAIL'}")
        print(f"    self_reference_ok:  {'ok' if t2['self_reference_ok'] else 'FAIL'}")
        print(f"    answer:             {t2['answer_preview']!r}")

    except Exception as exc:
        import traceback
        print(f"ERROR: {exc}")
        traceback.print_exc()
        result = {"passed": False}

    print(f"\n{'=' * 60}")
    passed = result.get("passed", False)
    print(f"  Verdict: {'PASS' if passed else 'FAIL'}")
    print(f"{'=' * 60}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
