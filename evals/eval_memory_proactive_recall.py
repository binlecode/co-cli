#!/usr/bin/env python3
"""Eval: memory-proactive-recall — verify inject_opening_context fires.

Pre-seeds memory files on disk, runs run_turn(), checks for SystemPromptPart
injection from inject_opening_context. This is a history processor (not a
tool), so the recall is NOT visible as a ToolCallPart — the eval scans for
SystemPromptPart containing "Relevant memories:".

Target flow:   _history.py:inject_opening_context() → recall_memory() →
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
from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    SystemPromptPart,
)
from pydantic_ai.usage import UsageLimits  # noqa: E402

from co_cli._history import OpeningContextState, SafetyState  # noqa: E402
from co_cli._orchestrate import run_turn  # noqa: E402
from co_cli.agent import get_agent  # noqa: E402

from evals._common import SilentFrontend, make_eval_deps  # noqa: E402


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
            {"content": "Project uses PostgreSQL for the database", "tags": ["decision"], "days_ago": 5},
        ],
        prompt="Set up testing for my Python project",
        expect_injection=True,
        expect_keyword="pytest",
    ),
    RecallCase(
        id="recall-partial-kw",
        memories=[
            {"content": "User prefers vim keybindings in all editors", "tags": ["preference"], "days_ago": 2},
        ],
        prompt="Configure my editor settings",
        expect_injection=True,
        expect_keyword="vim",
    ),
    RecallCase(
        id="recall-no-match",
        memories=[
            {"content": "User prefers dark mode in all applications", "tags": ["preference"], "days_ago": 1},
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


def _find_memory_injection(messages: list[Any]) -> str | None:
    """Find the SystemPromptPart with 'Relevant memories:' in messages."""
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    if "Relevant memories:" in part.content:
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
                _seed_memory(
                    memory_dir, i, mem["content"],
                    days_ago=mem.get("days_ago", 0),
                    tags=mem.get("tags"),
                )

            # Build agent and deps
            agent, model_settings, _, _ = get_agent()
            deps = make_eval_deps(session_id=f"eval-recall-{case.id}")
            deps.runtime.safety_state = SafetyState()
            # Initialize opening context state (normally done by main.py)
            deps.runtime.opening_ctx_state = OpeningContextState()

            frontend = SilentFrontend()

            result = await run_turn(
                agent=agent,
                user_input=case.prompt,
                deps=deps,
                message_history=[],
                model_settings=model_settings,
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
                content_influenced = (
                    case.expect_keyword.lower() in injection.lower()
                )

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
    total = len(CASES)
    print(f"\n{'=' * 60}")
    verdict = "PASS" if all_pass else "FAIL"
    print(f"  Verdict: {verdict} ({elapsed:.1f}s)")
    print(f"{'=' * 60}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
