#!/usr/bin/env python3
"""Eval: memory-contradiction-resolution — verify corrected memory is persisted.

Pre-seeds a conflicting memory on disk, runs run_turn() with a correction
prompt, and verifies the agent saves the updated fact. This isolates the
"new information supersedes old memory" contract from the broader proactive
signal-detection eval.

Target flow:   existing memory → model notices contradiction → save_memory()
Critical impact: if corrected information is not persisted, recall drifts
                 toward stale user context and future turns keep using the
                 wrong standard.

Known limitation: duplicate detection is fuzzy-token based, so this eval
checks only the minimum durable contract: the new corrected content is saved.

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_memory_contradiction_resolution.py
"""

import asyncio
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals._timeouts import EVAL_TURN_TIMEOUT_SECS

from co_cli.agent import build_agent  # noqa: E402
from co_cli.config import settings  # noqa: E402
from co_cli.context._orchestrate import run_turn  # noqa: E402
from co_cli.deps import CoConfig  # noqa: E402

from evals._common import make_eval_deps, make_eval_settings  # noqa: E402
from evals._fixtures import seed_memory  # noqa: E402
from evals._frontend import SilentFrontend  # noqa: E402
from evals._tools import extract_tool_calls  # noqa: E402


@dataclass
class ContradictionCase:
    id: str
    seeded_memories: list[dict[str, Any]]
    prompt: str
    expected_keywords: tuple[str, ...]
    description: str


CASES: list[ContradictionCase] = [
    ContradictionCase(
        id="mysql-to-postgresql",
        seeded_memories=[
            {
                "content": "User prefers MySQL for all database work",
                "tags": ["preference"],
                "days_ago": 5,
            },
        ],
        prompt="We've moved everything to PostgreSQL now, that's our standard",
        expected_keywords=("postgresql", "postgres"),
        description="Conflicting prior DB preference should be updated by saving the new standard",
    ),
]


async def run_case(case: ContradictionCase) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge"
            memory_dir.mkdir(parents=True)

            for index, memory in enumerate(case.seeded_memories, start=1):
                seed_memory(
                    memory_dir,
                    index,
                    memory["content"],
                    days_ago=memory.get("days_ago", 0),
                    tags=memory.get("tags"),
                )

            agent = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
            deps = make_eval_deps(session_id=f"eval-contradiction-{case.id}")
            frontend = SilentFrontend()

            async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
                result = await run_turn(
                    agent=agent,
                    user_input=case.prompt,
                    deps=deps,
                    message_history=[],
                    model_settings=make_eval_settings(),
                    max_request_limit=10,
                    verbose=False,
                    frontend=frontend,
                )

            tool_calls = extract_tool_calls(result.messages)
            save_calls = [
                (name, args) for name, args in tool_calls
                if name == "save_memory"
            ]

            saved_new_content = False
            if save_calls:
                for path in memory_dir.glob("*.md"):
                    text = path.read_text(encoding="utf-8").lower()
                    if any(keyword in text for keyword in case.expected_keywords):
                        saved_new_content = True
                        break

            return {
                "save_called": len(save_calls) > 0,
                "saved_new_content": saved_new_content,
                "save_calls": len(save_calls),
                "all_tool_calls": [(name, args) for name, args in tool_calls],
            }
        finally:
            os.chdir(orig_cwd)


async def main() -> int:
    print("=" * 60)
    print("  Eval: Memory Contradiction Resolution (W6)")
    print("=" * 60)
    print()

    started = time.monotonic()
    all_pass = True

    for case in CASES:
        print(f"  [{case.id}] {case.description}")
        print(f"    Prompt: {case.prompt[:60]}...", end=" ", flush=True)

        try:
            scores = await run_case(case)
        except Exception as exc:
            print(f"ERROR ({exc})")
            all_pass = False
            continue

        failures: list[str] = []
        if not scores["save_called"]:
            failures.append("save_memory not called")
        if not scores["saved_new_content"]:
            failures.append("corrected content not found in saved memories")

        if failures:
            print(f"FAIL ({', '.join(failures)})")
            all_pass = False
            tools = [name for name, _ in scores["all_tool_calls"]]
            print(f"    Tool calls: {tools}")
        else:
            print(f"PASS (save_memory called {scores['save_calls']}x)")

    elapsed = time.monotonic() - started
    verdict = "PASS" if all_pass else "FAIL"
    print(f"\n{'=' * 60}")
    print(f"  Verdict: {verdict} ({elapsed:.1f}s)")
    print(f"{'=' * 60}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
