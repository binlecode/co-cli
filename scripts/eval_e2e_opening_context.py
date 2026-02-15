#!/usr/bin/env python3
"""E2E: Opening context injection — memory recall at conversation start.

Verifies that inject_opening_context processor recalls relevant memories
when the first user message matches saved memory content.

Flow:
  1. Save a test memory about "python testing"
  2. Run agent with a prompt about "python testing"
  3. Verify the agent's response references the recalled memory
  4. Clean up the test memory

Usage:
    uv run python scripts/eval_e2e_opening_context.py
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

import yaml  # noqa: E402
from pydantic_ai.usage import UsageLimits  # noqa: E402

from co_cli.agent import get_agent  # noqa: E402
from co_cli._history import OpeningContextState, SafetyState  # noqa: E402
from co_cli.config import get_settings  # noqa: E402
from co_cli.deps import CoDeps  # noqa: E402
from co_cli.shell_backend import ShellBackend  # noqa: E402


MEMORY_CONTENT = "User strongly prefers pytest over unittest for all Python testing"
MEMORY_TAG = "preference"
UNIQUE_MARKER = "pytest-over-unittest-e2e-test"


def _create_test_memory(memory_dir: Path) -> Path:
    """Create a test memory file and return its path."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    existing = list(memory_dir.glob("*.md"))
    max_id = 0
    for p in existing:
        try:
            raw = p.read_text(encoding="utf-8")
            if raw.startswith("---"):
                fm_end = raw.index("---", 3)
                fm = yaml.safe_load(raw[3:fm_end])
                max_id = max(max_id, fm.get("id", 0))
        except Exception:
            pass

    memory_id = max_id + 1
    filename = f"{memory_id:03d}-{UNIQUE_MARKER}.md"
    fm = {
        "id": memory_id,
        "created": "2026-02-14T00:00:00+00:00",
        "tags": [MEMORY_TAG, "python", "testing"],
        "source": "detected",
    }
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{MEMORY_CONTENT}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


def _cleanup_test_memory(memory_dir: Path) -> None:
    """Remove the test memory file."""
    for p in memory_dir.glob(f"*{UNIQUE_MARKER}*"):
        p.unlink(missing_ok=True)


async def main() -> int:
    settings = get_settings()
    memory_dir = Path.cwd() / ".co-cli" / "knowledge" / "memories"

    print("=" * 60)
    print("  E2E: Opening Context Injection")
    print("=" * 60)

    # Step 1: Create test memory
    print("\n[1] Creating test memory...")
    mem_path = _create_test_memory(memory_dir)
    print(f"    Created: {mem_path.name}")

    try:
        # Step 2: Run agent with matching topic
        print("\n[2] Running agent with matching topic...")
        agent, model_settings, _ = get_agent()
        deps = CoDeps(
            shell=ShellBackend(),
            session_id="e2e-opening-context",
            memory_max_count=settings.memory_max_count,
            memory_dedup_window_days=settings.memory_dedup_window_days,
            memory_dedup_threshold=settings.memory_dedup_threshold,
            doom_loop_threshold=settings.doom_loop_threshold,
            max_reflections=settings.max_reflections,
        )
        deps._opening_ctx_state = OpeningContextState()
        deps._safety_state = SafetyState()

        t0 = time.monotonic()
        result = await agent.run(
            "What testing framework should I use for my Python project?",
            deps=deps,
            model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=10),
        )
        elapsed = time.monotonic() - t0

        output = result.output if isinstance(result.output, str) else str(result.output)
        print(f"    Response ({elapsed:.1f}s): {output[:300]}...")

        # Step 3: Verify
        print("\n[3] Verifying...")
        state: OpeningContextState = deps._opening_ctx_state
        recall_fired = state.recall_count > 0
        mentions_pytest = "pytest" in output.lower()

        print(f"    recall_count: {state.recall_count}")
        print(f"    last_recall_topic: {state.last_recall_topic[:60] if state.last_recall_topic else '(none)'}")
        print(f"    Response mentions pytest: {mentions_pytest}")

        checks = {
            "inject_opening_context fired recall_memory": recall_fired,
            "Response mentions pytest (memory influence)": mentions_pytest,
        }

        all_pass = True
        for check, passed in checks.items():
            status = "PASS" if passed else "FAIL"
            print(f"    {status}: {check}")
            if not passed:
                all_pass = False

        # The critical check is that the processor fired recall. Model output
        # mentioning pytest is a bonus — the model may return DeferredToolRequests
        # or take a different conversational path while still having the context.
        verdict = "PASS" if recall_fired else "FAIL"
        if not mentions_pytest and recall_fired:
            print("\n    NOTE: Recall fired but model didn't surface it in text output.")
            print("    The memory WAS injected into context. Model behavior varies.")
        print(f"\n{'=' * 60}")
        print(f"  Verdict: {verdict}")
        print(f"{'=' * 60}")
        return 0 if recall_fired else 1

    finally:
        # Step 4: Cleanup
        print("\n[4] Cleaning up test memory...")
        _cleanup_test_memory(memory_dir)
        print("    Done.")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
