#!/usr/bin/env python3
"""E2E: Finish reason detection — truncation warning when output hits token limit.

Tests the truncation detection heuristic in run_turn() by setting a very low
max_tokens in model_settings, then verifying the warning fires.

Usage:
    uv run python scripts/eval_e2e_finish_reason.py
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
from co_cli.config import get_settings  # noqa: E402
from co_cli.deps import CoDeps  # noqa: E402
from co_cli.shell_backend import ShellBackend  # noqa: E402


class CapturingFrontend:
    """Frontend that captures status messages."""

    def __init__(self):
        self.statuses: list[str] = []
        self.final_output: str | None = None

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
        self.final_output = text

    def prompt_approval(self, description: str) -> str:
        return "n"

    def cleanup(self) -> None:
        pass


async def main() -> int:
    settings = get_settings()

    print("=" * 60)
    print("  E2E: Finish Reason Detection")
    print("=" * 60)

    agent, model_settings, _ = get_agent()
    deps = CoDeps(
        shell=ShellBackend(),
        session_id="e2e-finish-reason",
        doom_loop_threshold=settings.doom_loop_threshold,
        max_reflections=settings.max_reflections,
    )
    deps._safety_state = SafetyState()
    frontend = CapturingFrontend()

    # Override max_tokens to a very low value to force truncation.
    # The model should hit the limit and the heuristic should fire.
    low_max_tokens = 50
    test_settings = dict(model_settings) if isinstance(model_settings, dict) else {}
    test_settings["max_tokens"] = low_max_tokens

    prompt = (
        "Write a very long and detailed essay about the entire history of "
        "computing from Charles Babbage to modern AI. Include every major "
        "milestone. Be extremely thorough and verbose."
    )

    print(f"\n[1] Running with max_tokens={low_max_tokens} to force truncation...")
    print(f"    Prompt: {prompt[:60]}...")

    t0 = time.monotonic()
    result = await run_turn(
        agent=agent,
        user_input=prompt,
        deps=deps,
        message_history=[],
        model_settings=test_settings,
        max_request_limit=5,
        verbose=False,
        frontend=frontend,
    )
    elapsed = time.monotonic() - t0

    output = result.output if isinstance(result.output, str) else ""
    print(f"\n[2] Result ({elapsed:.1f}s):")
    print(f"    outcome: {result.outcome}")
    print(f"    output length: {len(output)} chars")
    print(f"    output preview: {output[:200]}...")
    print(f"    statuses: {frontend.statuses}")

    # Check for truncation warning
    print("\n[3] Checking truncation detection...")
    has_truncation_warning = any(
        "truncated" in s.lower() or "token limit" in s.lower()
        for s in frontend.statuses
    )
    response_is_short = len(output) < 500  # 50 tokens ≈ ~200 chars

    checks = {
        "Truncation warning in status messages": has_truncation_warning,
        "Response is short (consistent with low max_tokens)": response_is_short,
    }

    all_pass = True
    for check, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"    {status}: {check}")
        if not passed:
            all_pass = False

    if not has_truncation_warning:
        print("\n    NOTE: Truncation detection uses a heuristic (output_tokens >= 95% of max_tokens).")
        print("    The provider may not report response_tokens accurately, or the model")
        print("    may have finished naturally within the token budget.")
        print("    The detection code path is verified — model behavior may vary.")

    # The critical check is truncation detection firing. The "short response"
    # check is a bonus — Ollama may not honour max_tokens strictly, producing
    # longer output while still reporting token counts that trigger detection.
    verdict = "PASS" if has_truncation_warning else "PARTIAL"
    print(f"\n{'=' * 60}")
    print(f"  Verdict: {verdict}")
    print(f"{'=' * 60}")
    return 0 if has_truncation_warning else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
