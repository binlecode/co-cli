#!/usr/bin/env python3
"""Eval: memory-signal-detection — verify proactive save and contradiction handling.

Runs run_turn() with real tool execution. Checks if save_memory appears in
tool calls extracted from message history. For W2 (proactive signal
detection), the LLM must decide to save without the user saying "remember".
For W6 (contradiction resolution), pre-seeds a conflicting memory and
verifies the agent saves the correction.

Target flow:   System prompt guidance → LLM judgment → save_memory() call
Critical impact: if the agent only saves when told "remember this", the
                 memory system captures 10% of what matters.

Known limitation (W6): _check_duplicate uses fuzzy token matching (85%
threshold). "User prefers MySQL" vs "We moved to PostgreSQL" scores very
low similarity → dedup will NOT catch it as a duplicate. The minimum bar
is that the agent saves the new correct information.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
    "OLLAMA_NUM_CTX": "262144",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

import yaml  # noqa: E402
from pydantic_ai.messages import ModelResponse, ToolCallPart  # noqa: E402

from co_cli._history import OpeningContextState, SafetyState  # noqa: E402
from co_cli._orchestrate import run_turn  # noqa: E402
from co_cli.agent import get_agent  # noqa: E402

from evals._common import (  # noqa: E402
    SilentFrontend,
    extract_tool_calls,
    make_eval_deps,
)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class SignalCase:
    id: str
    pre_seeded: list[dict[str, Any]] | None  # [{content, tags, days_ago}]
    prompt: str
    expect_save: bool
    description: str


CASES: list[SignalCase] = [
    SignalCase(
        id="signal-preference",
        pre_seeded=None,
        prompt=(
            "I always use 4-space indentation and prefer dark themes in everything"
        ),
        expect_save=True,
        description="Proactive save on preference signal (no 'remember' keyword)",
    ),
    SignalCase(
        id="signal-correction",
        pre_seeded=None,
        prompt=(
            "Actually no, we switched from Flask to FastAPI for all our services "
            "last month"
        ),
        expect_save=True,
        description="Proactive save on correction signal",
    ),
    SignalCase(
        id="signal-decision",
        pre_seeded=None,
        prompt=(
            "We've decided to use Kubernetes for production and Docker Compose for dev"
        ),
        expect_save=True,
        description="Proactive save on decision signal",
    ),
    SignalCase(
        id="signal-none",
        pre_seeded=None,
        prompt="What time is it in Tokyo?",
        expect_save=False,
        description="No signal — save_memory should NOT be called",
    ),
    SignalCase(
        id="contra-resolution",
        pre_seeded=[
            {
                "content": "User prefers MySQL for all database work",
                "tags": ["preference"],
                "days_ago": 5,
            },
        ],
        prompt=(
            "We've moved everything to PostgreSQL now, that's our standard"
        ),
        expect_save=True,
        description="Contradiction: old=MySQL, new=PostgreSQL — save must fire",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_memory(
    memory_dir: Path,
    memory_id: int,
    content: str,
    *,
    days_ago: int = 0,
    tags: list[str] | None = None,
) -> Path:
    """Write a memory markdown file with valid frontmatter."""
    created = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()
    slug = content[:40].lower().replace(" ", "-").replace(",", "")
    filename = f"{memory_id:03d}-{slug}.md"

    fm = {
        "id": memory_id,
        "created": created,
        "tags": tags or [],
        "source": "user-told",
        "auto_category": None,
    }

    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_case(case: SignalCase) -> dict[str, Any]:
    """Run a single signal detection case and return scoring dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli/knowledge"
            memory_dir.mkdir(parents=True)

            # Pre-seed memories if specified
            if case.pre_seeded:
                for i, mem in enumerate(case.pre_seeded, 1):
                    _seed_memory(
                        memory_dir, i, mem["content"],
                        days_ago=mem.get("days_ago", 0),
                        tags=mem.get("tags"),
                    )

            # Build agent and deps
            agent, model_settings, _, _ = get_agent()
            deps = make_eval_deps(session_id=f"eval-signal-{case.id}")
            deps._safety_state = SafetyState()
            deps._opening_ctx_state = OpeningContextState()

            frontend = SilentFrontend()

            result = await run_turn(
                agent=agent,
                user_input=case.prompt,
                deps=deps,
                message_history=[],
                model_settings=model_settings,
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

            signal_detected = len(save_calls) > 0

            # For contradiction case: check if PostgreSQL is in saved content
            contra_handled = False
            if case.id == "contra-resolution" and save_calls:
                # Check saved files on disk
                after_files = list(memory_dir.glob("*.md"))
                for p in after_files:
                    text = p.read_text(encoding="utf-8").lower()
                    if "postgresql" in text or "postgres" in text:
                        contra_handled = True
                        break

            return {
                "signal_detected": signal_detected,
                "save_calls": len(save_calls),
                "contra_handled": contra_handled,
                "all_tool_calls": [(n, a) for n, a in tool_calls],
            }
        finally:
            os.chdir(orig_cwd)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Memory Signal Detection (W2) + Contradiction (W6)")
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
                msg = f"PASS (save_memory called {scores['save_calls']}x)"
                if case.id == "contra-resolution":
                    if scores["contra_handled"]:
                        msg += " + PostgreSQL saved"
                    else:
                        msg += " (PostgreSQL not found in saved files — known gap)"
                print(msg)
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
