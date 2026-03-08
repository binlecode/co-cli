#!/usr/bin/env python3
"""Eval: grace-turn — model summarizes progress when budget is exhausted.

Sets a very low request limit (2) with a multi-step prompt that cannot
complete in budget, then verifies run_turn() fires the grace turn
(status message + /continue hint) instead of crashing.

Target flow:   _orchestrate.py:run_turn() → UsageLimitExceeded → grace turn
Critical impact: without a grace turn the user sees a raw exception or
                 loses all progress from the turn.

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_safety_grace_turn.py
"""

import asyncio
import os
import sys
import time

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
    "OLLAMA_NUM_CTX": "262144",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from co_cli._history import SafetyState  # noqa: E402
from co_cli._orchestrate import run_turn  # noqa: E402
from co_cli.agent import get_agent  # noqa: E402

from evals._common import SilentFrontend, make_eval_deps  # noqa: E402


class _CapturingFrontend(SilentFrontend):
    """Extends SilentFrontend with status printing for diagnostics."""

    def on_status(self, message: str) -> None:
        super().on_status(message)
        print(f"    STATUS: {message}")


async def main() -> int:
    print("=" * 60)
    print("  E2E: Grace Turn on Usage Limit")
    print("=" * 60)

    agent, model_settings, _, _ = get_agent()
    deps = make_eval_deps(session_id="e2e-grace-turn")
    deps.runtime.safety_state = SafetyState()
    frontend = _CapturingFrontend()

    # Limit of 2: model gets one request to start + one tool call.
    # The multi-step prompt guarantees the model cannot finish in 2 requests,
    # so the grace turn MUST fire.
    low_limit = 2
    prompt = (
        "Search the web for 'Python 3.13 new features', then fetch the top result, "
        "then summarize what you found. Do all three steps."
    )

    print(f"\n[1] Running with request_limit={low_limit}...")
    print(f"    Prompt: {prompt[:80]}...")

    t0 = time.monotonic()
    result = await run_turn(
        agent=agent,
        user_input=prompt,
        deps=deps,
        message_history=[],
        model_settings=model_settings,
        max_request_limit=low_limit,
        verbose=False,
        frontend=frontend,
    )
    elapsed = time.monotonic() - t0

    print(f"\n[2] Result ({elapsed:.1f}s):")
    print(f"    outcome: {result.outcome}")
    print(f"    messages: {len(result.messages)}")
    print(f"    output type: {type(result.output).__name__}")
    if isinstance(result.output, str):
        print(f"    output preview: {result.output[:200]}...")

    # Check for grace turn behavior
    print("\n[3] Checking grace turn behavior...")

    has_limit_status = any("Turn limit reached" in s for s in frontend.statuses)
    has_resume_hint = any(
        "/continue" in s for s in frontend.statuses
    ) or (isinstance(result.output, str) and "/continue" in result.output)
    did_not_crash = result.outcome in ("continue", "error")
    has_output = result.output is not None

    checks = {
        "Status: 'Turn limit reached' message": has_limit_status,
        "Status mentions /continue": has_resume_hint,
        "Did not crash (outcome is continue or error)": did_not_crash,
        "Produced some output": has_output,
    }

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
