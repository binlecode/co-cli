#!/usr/bin/env python3
"""Eval: memory-proactive-recall — verify inject_opening_context fires.

Pre-seeds memory files on disk, runs run_turn(), checks for SystemPromptPart
injection from inject_opening_context. This is a history processor (not a
tool), so the recall is NOT visible as a ToolCallPart — the eval scans for
SystemPromptPart containing "Relevant memories:".

Target flow:   _history.py:inject_opening_context() → _recall_for_context() →
               SystemPromptPart injection
Critical impact: this is the difference between "assistant that remembers"
                 and "assistant with a memory tool".

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_memory_proactive_recall.py
"""

import asyncio
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals._common import make_eval_deps, make_eval_settings
from evals._fixtures import seed_memory
from evals._frontend import SilentFrontend
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import (
    ModelRequest,
    SystemPromptPart,
)

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.context.orchestrate import run_turn

# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class RecallCase:
    id: str
    memories: list[dict[str, Any]]  # [{content, tags, days_ago}]
    prompt: str
    expect_injection: bool
    expect_keyword: str | None  # keyword expected in injected memories


CASES: list[RecallCase] = [
    RecallCase(
        id="recall-topic-match",
        memories=[
            {"content": "User prefers pytest for testing", "tags": ["preference"], "days_ago": 3},
            {
                "content": "Project uses PostgreSQL for the database",
                "tags": ["decision"],
                "days_ago": 5,
            },
        ],
        prompt="Set up testing for my Python project",
        expect_injection=True,
        expect_keyword="pytest",
    ),
    RecallCase(
        id="recall-partial-kw",
        memories=[
            {
                "content": "User prefers vim keybindings in all editors",
                "tags": ["preference"],
                "days_ago": 2,
            },
        ],
        prompt="Configure my editor settings",
        expect_injection=True,
        expect_keyword="vim",
    ),
    RecallCase(
        id="recall-no-match",
        memories=[
            {
                "content": "User prefers dark mode in all applications",
                "tags": ["preference"],
                "days_ago": 1,
            },
        ],
        prompt="What is 2 + 2?",
        expect_injection=False,
        expect_keyword=None,
    ),
    RecallCase(
        id="recall-empty-store",
        memories=[],
        prompt="Set up my project",
        expect_injection=False,
        expect_keyword=None,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_memory_injection(messages: list[Any]) -> str | None:
    """Find the SystemPromptPart with 'Relevant memories:' in messages."""
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart) and "Relevant memories:" in part.content:
                    return part.content
    return None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_case(case: RecallCase) -> dict[str, Any]:
    """Run a single recall case and return scoring dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge"
            memory_dir.mkdir(parents=True)

            # Seed memories
            for i, mem in enumerate(case.memories, 1):
                seed_memory(
                    memory_dir,
                    i,
                    mem["content"],
                    days_ago=mem.get("days_ago", 0),
                    tags=mem.get("tags"),
                )

            # Build agent and deps
            agent = build_agent(config=settings)
            deps = make_eval_deps(session_id=f"eval-recall-{case.id}")

            frontend = SilentFrontend()

            async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
                result = await run_turn(
                    agent=agent,
                    user_input=case.prompt,
                    deps=deps,
                    message_history=[],
                    model_settings=make_eval_settings(),
                    max_request_limit=5,
                    verbose=False,
                    frontend=frontend,
                )

            # Score: check for SystemPromptPart injection
            injection = _find_memory_injection(result.messages)
            injection_present = injection is not None

            # Score: content influence
            content_influenced = False
            if case.expect_keyword and injection:
                content_influenced = case.expect_keyword.lower() in injection.lower()

            return {
                "injection_present": injection_present,
                "content_influenced": content_influenced,
                "injection_text": injection[:200] if injection else None,
            }
        finally:
            os.chdir(orig_cwd)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Memory Proactive Recall (W1)")
    print("=" * 60)
    print()

    t0 = time.monotonic()
    all_pass = True

    for case in CASES:
        print(f"  [{case.id}] {case.prompt[:50]}...", end=" ", flush=True)

        try:
            scores = await run_case(case)
        except Exception as exc:
            print(f"ERROR ({exc})")
            all_pass = False
            continue

        # Evaluate
        passed = True

        if case.expect_injection:
            if not scores["injection_present"]:
                print("FAIL (no injection)")
                passed = False
            elif case.expect_keyword and not scores["content_influenced"]:
                print(f"FAIL (injection present but missing '{case.expect_keyword}')")
                passed = False
            else:
                print("PASS")
        else:
            # Expect NO injection
            if scores["injection_present"]:
                print("FAIL (unexpected injection)")
                passed = False
            else:
                print("PASS")

        if not passed:
            all_pass = False
            if scores.get("injection_text"):
                print(f"    Injection: {scores['injection_text']}")

    elapsed = time.monotonic() - t0
    print(f"\n{'=' * 60}")
    verdict = "PASS" if all_pass else "FAIL"
    print(f"  Verdict: {verdict} ({elapsed:.1f}s)")
    print(f"{'=' * 60}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
