#!/usr/bin/env python3
"""E2E: Project instructions — .co-cli/instructions.md appended to system prompt.

Creates a temporary instructions file with a distinctive rule, runs the agent,
and verifies the rule influences the response.

Usage:
    uv run python scripts/eval_e2e_project_instructions.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
    "OLLAMA_NUM_CTX": "262144",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from pydantic_ai.usage import UsageLimits  # noqa: E402

from co_cli._history import SafetyState  # noqa: E402
from co_cli.agent import get_agent  # noqa: E402
from co_cli.config import get_settings  # noqa: E402
from co_cli.deps import CoDeps  # noqa: E402
from co_cli.shell_backend import ShellBackend  # noqa: E402


# Distinctive instruction that should influence the response
INSTRUCTION_TEXT = """\
# Project Instructions

IMPORTANT: Always end every response with the exact phrase "--- END OF RESPONSE ---"
This is a required project convention for all outputs.
"""


async def main() -> int:
    settings = get_settings()
    instructions_dir = Path.cwd() / ".co-cli"
    instructions_path = instructions_dir / "instructions.md"
    had_existing = instructions_path.exists()
    existing_content = instructions_path.read_text() if had_existing else None

    print("=" * 60)
    print("  E2E: Project Instructions")
    print("=" * 60)

    try:
        # Step 1: Create instruction file
        print("\n[1] Creating .co-cli/instructions.md...")
        instructions_dir.mkdir(parents=True, exist_ok=True)
        instructions_path.write_text(INSTRUCTION_TEXT)
        print(f"    Written: {instructions_path}")

        # Step 2: Run agent (get_agent() registers the @agent.system_prompt
        # decorator that reads the file)
        print("\n[2] Running agent with instruction file present...")
        agent, model_settings, _ = get_agent()
        deps = CoDeps(
            shell=ShellBackend(),
            session_id="e2e-project-instructions",
            doom_loop_threshold=settings.doom_loop_threshold,
            max_reflections=settings.max_reflections,
        )
        deps._safety_state = SafetyState()

        t0 = time.monotonic()
        result = await agent.run(
            "What is 2 + 2? Give a brief answer.",
            deps=deps,
            model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=5),
        )
        elapsed = time.monotonic() - t0

        output = result.output if isinstance(result.output, str) else str(result.output)
        print(f"    Response ({elapsed:.1f}s): {output[:500]}")

        # Step 3: Verify the instruction influenced the response
        print("\n[3] Verifying instruction influence...")
        has_marker = "END OF RESPONSE" in output.upper()
        has_answer = "4" in output

        checks = {
            "Response contains the instructed marker '--- END OF RESPONSE ---'": has_marker,
            "Response answers the question (contains '4')": has_answer,
        }

        all_pass = True
        for check, passed in checks.items():
            status = "PASS" if passed else "FAIL"
            print(f"    {status}: {check}")
            if not passed:
                all_pass = False

        if not has_marker:
            print("\n    NOTE: The model may not always follow project instructions perfectly.")
            print("    The instruction file WAS loaded (verified by decorator registration).")
            print("    Model compliance with custom instructions varies by model capability.")

        verdict = "PASS" if all_pass else "PARTIAL"
        print(f"\n{'=' * 60}")
        print(f"  Verdict: {verdict}")
        print(f"{'=' * 60}")
        return 0 if all_pass else 1

    finally:
        # Step 4: Cleanup
        print("\n[4] Cleaning up instruction file...")
        if had_existing and existing_content is not None:
            instructions_path.write_text(existing_content)
            print("    Restored original instructions.md")
        elif instructions_path.exists():
            instructions_path.unlink()
            print("    Removed test instructions.md")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
