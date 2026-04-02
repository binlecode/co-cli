#!/usr/bin/env python3
"""Eval: memory-signal-detection — verify proactive memory save triggers.

Runs run_turn() with real tool execution and checks whether save_memory
appears in the extracted tool calls. The point is to validate that the model
chooses to persist durable user signals without the user explicitly saying
"remember this".

Target flow:   System prompt guidance → LLM judgment → save_memory() call
Critical impact: if the agent only saves when told "remember this", the
                 memory system captures only a small fraction of durable user
                 preferences and decisions.

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_memory_signal_detection.py
"""

import asyncio
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from evals._timeouts import EVAL_TURN_TIMEOUT_SECS

from co_cli.agent import build_agent  # noqa: E402
from co_cli.config import settings  # noqa: E402
from co_cli.context._orchestrate import run_turn  # noqa: E402
from co_cli.deps import CoConfig  # noqa: E402

from evals._common import make_eval_deps, make_eval_settings  # noqa: E402
from evals._frontend import SilentFrontend  # noqa: E402
from evals._tools import extract_tool_calls  # noqa: E402


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class SignalCase:
    id: str
    prompt: str
    expect_save: bool
    description: str


CASES: list[SignalCase] = [
    SignalCase(
        id="signal-preference",
        prompt=(
            "I always use 4-space indentation and prefer dark themes in everything"
        ),
        expect_save=True,
        description="Proactive save on preference signal (no 'remember' keyword)",
    ),
    SignalCase(
        id="signal-correction",
        prompt=(
            "Actually no, we switched from Flask to FastAPI for all our services "
            "last month"
        ),
        expect_save=True,
        description="Proactive save on correction signal",
    ),
    SignalCase(
        id="signal-decision",
        prompt=(
            "We've decided to use Kubernetes for production and Docker Compose for dev"
        ),
        expect_save=True,
        description="Proactive save on decision signal",
    ),
    SignalCase(
        id="signal-none",
        prompt="What time is it in Tokyo?",
        expect_save=False,
        description="No signal — save_memory should NOT be called",
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_case(case: SignalCase) -> dict[str, object]:
    """Run a single signal detection case and return scoring dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge"
            memory_dir.mkdir(parents=True)

            agent = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd())).agent
            deps = make_eval_deps(session_id=f"eval-signal-{case.id}")
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

            # Extract all tool calls from the message history
            tool_calls = extract_tool_calls(result.messages)
            save_calls = [
                (name, args) for name, args in tool_calls
                if name == "save_memory"
            ]

            return {
                "signal_detected": len(save_calls) > 0,
                "save_calls": len(save_calls),
                "all_tool_calls": [(n, a) for n, a in tool_calls],
            }
        finally:
            os.chdir(orig_cwd)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Memory Signal Detection (W2)")
    print("=" * 60)
    print()

    t0 = time.monotonic()
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

        # Evaluate
        passed = True

        if case.expect_save:
            if not scores["signal_detected"]:
                print("FAIL (save_memory not called)")
                passed = False
            else:
                print(f"PASS (save_memory called {scores['save_calls']}x)")
        else:
            if scores["signal_detected"]:
                print("FAIL (save_memory called on no-signal prompt)")
                passed = False
            else:
                print("PASS (no save_memory — correct)")

        if not passed:
            all_pass = False
            tools = [n for n, _ in scores["all_tool_calls"]]
            print(f"    Tool calls: {tools}")

    elapsed = time.monotonic() - t0
    total = len(CASES)
    print(f"\n{'=' * 60}")
    verdict = "PASS" if all_pass else "FAIL"
    print(f"  Verdict: {verdict} ({elapsed:.1f}s)")
    print(f"{'=' * 60}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
