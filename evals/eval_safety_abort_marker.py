import pathlib
#!/usr/bin/env python3
"""Eval: abort-marker — Ctrl-C injects history marker for next turn awareness.

Starts a run_turn(), cancels it mid-flight, then verifies the returned
message history contains the abort marker message so the next turn knows
the previous one was interrupted.

Target flow:   _orchestrate.py:run_turn() → asyncio.CancelledError → marker injection
Critical impact: without the abort marker the agent has no awareness of
                 interrupted work and may repeat or contradict itself.

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_safety_abort_marker.py
"""

import asyncio
import sys

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    UserPromptPart,
)

from co_cli.context._history import SafetyState  # noqa: E402
from co_cli.context._orchestrate import run_turn  # noqa: E402
from co_cli.agent import build_agent  # noqa: E402
from co_cli.config import settings  # noqa: E402
from co_cli.deps import CoConfig  # noqa: E402

from evals._common import SilentFrontend, make_eval_deps, make_eval_settings  # noqa: E402


async def main() -> int:
    print("=" * 60)
    print("  E2E: Abort Marker Injection")
    print("=" * 60)

    # TODO: source model_settings from make_eval_settings()
    agent, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=pathlib.Path.cwd()))
    deps = make_eval_deps(session_id="e2e-abort-marker")
    deps.runtime.safety_state = SafetyState()
    frontend = SilentFrontend()

    # Start a run_turn that we'll cancel after a short delay
    print("\n[1] Starting agent turn (will cancel after 2s)...")

    async def _run_and_cancel():
        task = asyncio.create_task(run_turn(
            agent=agent,
            user_input="Write a detailed essay about the history of computing from the 1940s to present day",
            deps=deps,
            message_history=[],
            model_settings=make_eval_settings(),
            max_request_limit=50,
            verbose=False,
            frontend=frontend,
        ))
        # Let it stream for a bit, then cancel
        await asyncio.sleep(2.0)
        task.cancel()
        try:
            return await task
        except asyncio.CancelledError:
            # run_turn should catch this and return with interrupted=True
            # If it propagates, that's a failure
            return None

    result = await _run_and_cancel()

    # Step 2: Check the result
    print("\n[2] Checking result...")

    if result is None:
        print("    CancelledError propagated (run_turn did not catch it)")
        print(f"\n{'=' * 60}")
        print("  Verdict: FAIL -- CancelledError not handled by run_turn")
        print(f"{'=' * 60}")
        return 1

    print(f"    interrupted: {result.interrupted}")
    print(f"    outcome: {result.outcome}")
    print(f"    messages: {len(result.messages)}")
    print(f"    statuses: {frontend.statuses}")

    # Step 3: Find abort marker in messages
    print("\n[3] Searching for abort marker in message history...")
    found_abort = False
    for msg in result.messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    if "interrupted the previous turn" in part.content:
                        found_abort = True
                        print(f"    Found: '{part.content[:80]}...'")

    checks = {
        "interrupted flag": result.interrupted,
        "outcome is 'continue'": result.outcome == "continue",
        "abort marker in history": found_abort,
        "status message 'Interrupted'": any("Interrupted" in s for s in frontend.statuses),
    }

    print("\n[4] Results:")
    all_pass = True
    for check, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"    {status}: {check}")
        if not passed:
            all_pass = False

    verdict = "PASS" if all_pass else "FAIL"
    print(f"\n{'=' * 60}")
    print(f"  Verdict: {verdict}")
    print(f"{'=' * 60}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
