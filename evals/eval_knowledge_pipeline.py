#!/usr/bin/env python3
"""Eval: knowledge pipeline — web search → save article → knowledge retrieval.

Two-turn chat session driven through run_turn() with a real agent instance:

  Turn 1: Agent searches the web, fetches a page, saves it as an article.
  Turn 2: Turn 1 history is carried forward; agent retrieves from its knowledge
          base to answer a follow-up question about the same topic.

Validates the full end-to-end pipeline:
  web discovery → knowledge persistence → semantic recall → grounded answer.

Dimensions:
  save_chain   — Turn 1 executes web_search → web_fetch → save_article in order
  recall_chain — Turn 2 calls search_knowledge or recall_article
  answer_ok    — Turn 2 response mentions the topic and is non-trivial

Prerequisites:
  BRAVE_SEARCH_API_KEY set (eval skips gracefully when absent).

Usage:
    uv run python evals/eval_knowledge_pipeline.py
"""

import asyncio
import asyncio
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS

import sys
import time
from pathlib import Path
from typing import Any

from co_cli.context.types import SafetyState  # noqa: E402
from co_cli.knowledge._store import KnowledgeStore  # noqa: E402
from co_cli.context.orchestrate import run_turn  # noqa: E402
from co_cli.agent import build_agent  # noqa: E402
from co_cli.config._core import settings, Settings  # noqa: E402
from co_cli.config._knowledge import KnowledgeSettings  # noqa: E402

from evals._common import make_eval_deps, make_eval_settings  # noqa: E402
from evals._frontend import SilentFrontend  # noqa: E402
from evals._tools import is_ordered_subsequence, tool_names  # noqa: E402


# ---------------------------------------------------------------------------
# Pipeline spec
# ---------------------------------------------------------------------------

TOPIC = "Python asyncio event loop best practices"
TOPIC_KEYWORD = "asyncio"

TURN1_PROMPT = (
    f"Search the web for '{TOPIC}', fetch the most relevant result, "
    "and save it as an article in my knowledge base with tag 'python'."
)

TURN2_PROMPT = (
    f"Search my knowledge base for {TOPIC_KEYWORD} and summarise what you find there."
)

TURN1_EXPECTED_CHAIN = ["web_search", "web_fetch", "save_article"]
TURN2_EXPECTED_TOOLS = {"search_knowledge", "recall_article"}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_pipeline(
    agent: Any,
    deps: Any,
    model_settings: Any,
) -> dict[str, Any]:
    """Run the two-turn knowledge pipeline through the real agent loop."""
    frontend = SilentFrontend(approval_response="y")

    # --- Turn 1: search → fetch → save ---
    deps.runtime.safety_state = SafetyState()
    t0 = time.monotonic()
    async with asyncio.timeout(120):
        result1 = await run_turn(
            agent=agent,
            user_input=TURN1_PROMPT,
            deps=deps,
            message_history=[],
            model_settings=model_settings,
            frontend=frontend,
        )
    elapsed1 = time.monotonic() - t0

    names1 = tool_names(result1.messages)
    save_chain_ok = is_ordered_subsequence(TURN1_EXPECTED_CHAIN, names1)

    # --- Turn 2: retrieve from knowledge base ---
    deps.runtime.safety_state = SafetyState()
    t1 = time.monotonic()
    async with asyncio.timeout(120):
        result2 = await run_turn(
            agent=agent,
            user_input=TURN2_PROMPT,
            deps=deps,
            message_history=result1.messages,
            model_settings=model_settings,
            frontend=frontend,
        )
    elapsed2 = time.monotonic() - t1

    # Extract only the new tool calls introduced in turn 2
    all_names2 = tool_names(result2.messages)
    names2 = all_names2[len(names1) :]
    recall_chain_ok = bool(TURN2_EXPECTED_TOOLS & set(names2))

    # Answer quality: non-trivial response that references the topic
    answer = result2.output or ""
    answer_ok = TOPIC_KEYWORD in answer.lower() and len(answer.strip()) > 50

    passed = save_chain_ok and recall_chain_ok and answer_ok

    return {
        "passed": passed,
        "turn1": {
            "tool_names": names1,
            "expected_chain": TURN1_EXPECTED_CHAIN,
            "save_chain_ok": save_chain_ok,
            "elapsed": elapsed1,
            "outcome": result1.outcome,
        },
        "turn2": {
            "tool_names": names2,
            "expected_any": sorted(TURN2_EXPECTED_TOOLS),
            "recall_chain_ok": recall_chain_ok,
            "answer_ok": answer_ok,
            "answer_preview": answer[:300],
            "elapsed": elapsed2,
            "outcome": result2.outcome,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Knowledge Pipeline (search → save → retrieve)")
    print("=" * 60)

    # Build agent with real settings (mcp_servers stripped to avoid network noise)
    agent_config = settings.model_copy(update={"mcp_servers": {}})
    agent = build_agent(config=agent_config)

    knowledge_store = KnowledgeStore(
        config=settings,
        knowledge_db_path=Path(".co-cli/search.db"),
    )
    deps = make_eval_deps(
        session_id="eval-knowledge-pipeline",
        knowledge_store=knowledge_store,
    )

    if not deps.config.brave_search_api_key:
        print("\n  SKIP: brave_search_api_key not configured")
        print(f"{'=' * 60}")
        return 0

    print(f"\n  Topic:   {TOPIC}")
    print(f"  Library: {deps.library_dir}")
    print()

    result: dict[str, Any] = {}
    try:
        print("knowledge-pipeline ...", end=" ", flush=True)
        t_total = time.monotonic()
        result = await run_pipeline(agent, deps, make_eval_settings())
        elapsed_total = time.monotonic() - t_total

        status = "PASS" if result["passed"] else "FAIL"
        print(f"{status} ({elapsed_total:.1f}s)")

        t1 = result["turn1"]
        t2 = result["turn2"]

        print(f"\n  Turn 1 — search + save ({t1['elapsed']:.1f}s)")
        print(f"    expected:   {t1['expected_chain']}")
        print(f"    actual:     {t1['tool_names']}")
        print(f"    save_chain: {'ok' if t1['save_chain_ok'] else 'FAIL'}")

        print(f"\n  Turn 2 — retrieve + answer ({t2['elapsed']:.1f}s)")
        print(f"    expected any: {t2['expected_any']}")
        print(f"    actual:       {t2['tool_names']}")
        print(f"    recall_chain: {'ok' if t2['recall_chain_ok'] else 'FAIL'}")
        print(f"    answer_ok:    {'ok' if t2['answer_ok'] else 'FAIL'}")
        print(f"    answer:       {t2['answer_preview']!r}")

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
