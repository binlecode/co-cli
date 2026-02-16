#!/usr/bin/env python3
"""E2E: Abort marker — Ctrl-C injects history marker for next turn awareness.

Starts a run_turn(), cancels it mid-flight, then verifies the returned
message history contains the abort marker message.

Usage:
    uv run python scripts/eval_e2e_abort_marker.py
"""

import asyncio
import os
import sys

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
    "OLLAMA_NUM_CTX": "262144",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    UserPromptPart,
)

from co_cli._history import SafetyState  # noqa: E402
from co_cli._orchestrate import run_turn, FrontendProtocol  # noqa: E402
from co_cli.agent import get_agent  # noqa: E402
from co_cli.config import get_settings  # noqa: E402
from co_cli.deps import CoDeps  # noqa: E402
from co_cli.shell_backend import ShellBackend  # noqa: E402


class SilentFrontend:
    """Minimal frontend that captures status messages."""

    def __init__(self):
        self.statuses: list[str] = []

    def on_text_delta(self, accumulated: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass

    def on_thinking_delta(self, accumulated: str) -> None:
        pass

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_tool_call(self, name: str, args_display: str) -> None:
        pass

    def on_tool_result(self, title: str, content) -> None:
        pass

    def on_status(self, message: str) -> None:
        self.statuses.append(message)

    def on_final_output(self, text: str) -> None:
        pass

    def prompt_approval(self, description: str) -> str:
        return "y"

    def cleanup(self) -> None:
        pass


async def main() -> int:
    settings = get_settings()

    print("=" * 60)
    print("  E2E: Abort Marker Injection")
    print("=" * 60)

    agent, model_settings, _ = get_agent()
    deps = CoDeps(
        shell=ShellBackend(),
        session_id="e2e-abort-marker",
        doom_loop_threshold=settings.doom_loop_threshold,
        max_reflections=settings.max_reflections,
    )
    deps._safety_state = SafetyState()
    frontend = SilentFrontend()

    # Start a run_turn that we'll cancel after a short delay
    print("\n[1] Starting agent turn (will cancel after 2s)...")

    async def _run_and_cancel():
        task = asyncio.create_task(run_turn(
            agent=agent,
            user_input="Write a detailed essay about the history of computing from the 1940s to present day",
            deps=deps,
            message_history=[],
            model_settings=model_settings,
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
        print("  Verdict: FAIL — CancelledError not handled by run_turn")
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
