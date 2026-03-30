import pathlib
#!/usr/bin/env python3
"""Eval: tool-chains — verify the agent completes multi-step tool sequences.

Sends prompts that require sequential tool calls (search then fetch,
recall then save, shell then shell) through the real run_turn() loop
with a SilentFrontend that auto-approves.  Inspects the full message
history for correct tool ordering and final text output.

Target flow:   _orchestrate.py:run_turn() with real tool execution
Critical impact: multi-step chains are co's #1 value proposition — if the
                 agent can't chain tools, it's a fancy autocomplete.

Dimensions:    chain_match (ordered subsequence), chain_complete (final text)

Prerequisites: LLM provider configured.  Cases with ``requires`` field are
               skipped when credentials are absent.

Usage:
    uv run python evals/eval_tool_chains.py
"""

import asyncio
import sys
import time
from dataclasses import dataclass
from typing import Any

from co_cli.context._types import SafetyState  # noqa: E402
from co_cli.context._orchestrate import run_turn  # noqa: E402
from co_cli.agent import build_agent  # noqa: E402
from co_cli.config import settings  # noqa: E402
from co_cli.deps import CoConfig  # noqa: E402

from evals._common import make_eval_deps, make_eval_settings  # noqa: E402
from evals._frontend import SilentFrontend  # noqa: E402
from evals._tools import extract_tool_calls, is_ordered_subsequence  # noqa: E402


# ---------------------------------------------------------------------------
# Cases (inline — small set, tightly coupled to scoring)
# ---------------------------------------------------------------------------


@dataclass
class ChainCase:
    id: str
    prompt: str
    expected_chain: list[str]
    requires: str | None = None  # credential key, e.g. "brave_search_api_key"


CASES: list[ChainCase] = [
    ChainCase(
        id="chain-shell-seq",
        prompt=(
            "List files in the current directory, then show the first 5 lines "
            "of pyproject.toml"
        ),
        expected_chain=["run_shell_command", "run_shell_command"],
    ),
    ChainCase(
        id="chain-recall-save",
        prompt=(
            "Check if I have memories about testing frameworks. "
            "If not, save that I prefer pytest."
        ),
        expected_chain=["recall_memory", "save_memory"],
    ),
    ChainCase(
        id="chain-web-search-fetch",
        prompt=(
            "Search the web for 'Python 3.13 new features' and fetch the "
            "top result"
        ),
        expected_chain=["web_search", "web_fetch"],
        requires="brave_search_api_key",
    ),
    ChainCase(
        id="chain-memory-list-recall",
        prompt=(
            "List all my memories, then recall any about database preferences"
        ),
        expected_chain=["list_memories", "recall_memory"],
    ),
    ChainCase(
        id="research-and-save",
        prompt=(
            "Search the web for 'Python asyncio best practices 2024', fetch the "
            "most relevant result, and save it as an article in my knowledge base."
        ),
        expected_chain=["web_search", "web_fetch", "save_article"],
        requires="brave_search_api_key",
    ),
]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_case(
    case: ChainCase,
    tool_names: list[str],
    has_text_output: bool,
) -> dict[str, bool]:
    return {
        "chain_match": is_ordered_subsequence(case.expected_chain, tool_names),
        "chain_complete": has_text_output,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_chain_case(
    case: ChainCase,
    agent: Any,
    deps: Any,
    model_settings: Any,
) -> dict[str, Any]:
    """Run a single chain case through run_turn()."""
    frontend = SilentFrontend(approval_response="y")
    deps.runtime.safety_state = SafetyState()

    t0 = time.monotonic()
    result = await run_turn(
        agent=agent,
        user_input=case.prompt,
        deps=deps,
        message_history=[],
        model_settings=model_settings,
        max_request_limit=15,
        verbose=False,
        frontend=frontend,
    )
    elapsed = time.monotonic() - t0

    calls = extract_tool_calls(result.messages)
    tool_names = [name for name, _ in calls]
    has_text = isinstance(result.output, str) and len(result.output) > 0

    scores = score_case(case, tool_names, has_text)

    return {
        "id": case.id,
        "tool_names": tool_names,
        "expected_chain": case.expected_chain,
        "scores": scores,
        "passed": all(scores.values()),
        "elapsed": elapsed,
        "outcome": result.outcome,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Multi-Step Tool Chains")
    print("=" * 60)

    agent = build_agent(config=CoConfig.from_settings(settings, cwd=pathlib.Path.cwd())).agent
    deps = make_eval_deps(session_id="eval-tool-chains")
    deps.runtime.safety_state = SafetyState()

    # Determine which cases to run based on available credentials
    runnable: list[ChainCase] = []
    skipped: list[ChainCase] = []
    for case in CASES:
        if case.requires:
            val = getattr(deps, case.requires, None)
            if not val:
                skipped.append(case)
                continue
        runnable.append(case)

    print(f"\n  Cases: {len(runnable)} runnable, {len(skipped)} skipped")
    for s in skipped:
        print(f"    SKIP: {s.id} (requires {s.requires})")
    print()

    results: list[dict[str, Any]] = []
    for i, case in enumerate(runnable, 1):
        print(f"[{i}/{len(runnable)}] {case.id} ...", end=" ", flush=True)
        try:
            r = await run_chain_case(case, agent, deps, make_eval_settings())
            results.append(r)
            status = "PASS" if r["passed"] else "FAIL"
            print(f"{status} ({r['elapsed']:.1f}s)")
            print(f"    expected: {r['expected_chain']}")
            print(f"    actual:   {r['tool_names']}")
            for dim, ok in r["scores"].items():
                print(f"    {dim}: {'ok' if ok else 'FAIL'}")
        except Exception as exc:
            print(f"ERROR: {exc}")
            results.append({
                "id": case.id,
                "passed": False,
                "error": str(exc),
                "scores": {"chain_match": False, "chain_complete": False},
            })

    # Summary
    print(f"\n{'=' * 60}")
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"  Results: {passed}/{total} passed", end="")
    if skipped:
        print(f", {len(skipped)} skipped", end="")
    print()

    verdict = "PASS" if passed == total and total > 0 else "FAIL"
    print(f"  Verdict: {verdict}")
    print(f"{'=' * 60}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
