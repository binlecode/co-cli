#!/usr/bin/env python3
"""Eval: signal-analyzer — verify analyze_for_signals classification accuracy.

Calls analyze_for_signals() directly (not via run_turn) with constructed
message histories and checks that SignalResult fields match expected values.

Case groups:
  high-* — explicit corrections (don't/never/avoid/revert) → confidence="high"
  low-*  — stated preferences and frustrated reactions → confidence="low"
  none-* — capability questions, neutral prompts, guardrail cases → found=False

Critical impact:
  - If high-confidence cases mis-score as low, the auto-save path never fires
    and corrections require user approval instead of saving silently.
  - If low-confidence cases mis-score as high, preferences are saved without
    approval, bypassing the user confirmation step.
  - If guardrail cases fire, noise accumulates in the memory system over time.

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_signal_analyzer.py
"""

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
    "OLLAMA_NUM_CTX": "262144",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from pydantic_ai.messages import ModelRequest, UserPromptPart  # noqa: E402

from co_cli._signal_analyzer import SignalResult, analyze_for_signals  # noqa: E402
from co_cli.agent import get_agent  # noqa: E402


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class SignalCase:
    id: str
    user_message: str
    expect_found: bool
    expect_confidence: str | None  # "high", "low", or None when expect_found=False
    description: str


CASES: list[SignalCase] = [
    # --- High confidence: explicit corrections ---
    SignalCase(
        id="high-dont",
        user_message="don't use trailing comments in the code",
        expect_found=True,
        expect_confidence="high",
        description="'don't use X' → correction, high confidence",
    ),
    SignalCase(
        id="high-stop",
        user_message="stop adding docstrings to every function",
        expect_found=True,
        expect_confidence="high",
        description="'stop doing X' → correction, high confidence",
    ),
    SignalCase(
        id="high-never",
        user_message="never use snake_case for variable names in this project",
        expect_found=True,
        expect_confidence="high",
        description="'never X' → correction, high confidence",
    ),
    SignalCase(
        id="high-avoid",
        user_message="avoid global state in all my scripts",
        expect_found=True,
        expect_confidence="high",
        description="'avoid X' → correction, high confidence",
    ),
    # --- Low confidence: stated preferences and frustrated reactions ---
    SignalCase(
        id="low-preference-hedged",
        user_message="I kind of prefer shorter responses",
        expect_found=True,
        expect_confidence="low",
        description="Hedged preference → low confidence",
    ),
    SignalCase(
        id="low-preference-clear",
        user_message="please use type hints for all function signatures",
        expect_found=True,
        expect_confidence="low",
        description="'please use X' → preference, low confidence (prompt routes all preferences to low)",
    ),
    SignalCase(
        id="low-frustrated",
        user_message="why did you use pytest? I wanted unittest",
        expect_found=True,
        expect_confidence="low",
        description="Frustrated reaction revealing preference → low confidence",
    ),
    # --- No signal: guardrail cases ---
    SignalCase(
        id="none-capability",
        user_message="can you use black for formatting?",
        expect_found=False,
        expect_confidence=None,
        description="Capability question — guardrail: must not flag",
    ),
    SignalCase(
        id="none-neutral",
        user_message="what does this error mean?",
        expect_found=False,
        expect_confidence=None,
        description="General question — no signal",
    ),
    SignalCase(
        id="none-hypothetical",
        user_message="if you were to avoid trailing comments, would the code be cleaner?",
        expect_found=False,
        expect_confidence=None,
        description="Hypothetical — guardrail: must not flag",
    ),
    SignalCase(
        id="none-teaching",
        user_message="here's what NOT to do: avoid global state in Python generally",
        expect_found=False,
        expect_confidence=None,
        description="Teaching moment — guardrail: must not flag",
    ),
    SignalCase(
        id="none-sensitive",
        user_message="my API key is sk-1234, please don't save that anywhere",
        expect_found=False,
        expect_confidence=None,
        description="Sensitive content (credential) — guardrail: must not flag",
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _make_history(user_text: str) -> list:
    """Build a minimal one-turn message history for the mini-agent."""
    return [ModelRequest(parts=[UserPromptPart(content=user_text)])]


async def run_case(case: SignalCase, model: Any) -> dict[str, Any]:
    messages = _make_history(case.user_message)
    result: SignalResult = await analyze_for_signals(messages, model)
    return {
        "found": result.found,
        "confidence": result.confidence,
        "tag": result.tag,
        "candidate": result.candidate,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Signal Analyzer Classification")
    print("=" * 60)
    print()

    agent, _, _ = get_agent()
    model = agent.model

    t0 = time.monotonic()
    passed_count = 0
    total = len(CASES)

    for case in CASES:
        print(f"  [{case.id}] {case.description}")
        print(f'    Prompt: "{case.user_message[:65]}"', end=" ", flush=True)

        try:
            scores = await run_case(case, model)
        except Exception as exc:
            print(f"ERROR ({exc})")
            continue

        found_ok = scores["found"] == case.expect_found
        if case.expect_found:
            confidence_ok = scores["confidence"] == case.expect_confidence
        else:
            confidence_ok = scores["confidence"] is None

        passed = found_ok and confidence_ok

        if passed:
            detail = (
                f" confidence={scores['confidence']} tag={scores['tag']}"
                if case.expect_found
                else ""
            )
            print(f"PASS{detail}")
            passed_count += 1
        else:
            failures = []
            if not found_ok:
                failures.append(
                    f"found={scores['found']} (expected {case.expect_found})"
                )
            if not confidence_ok:
                failures.append(
                    f"confidence={scores['confidence']} (expected {case.expect_confidence})"
                )
            print(f"FAIL ({', '.join(failures)})")
            if scores["candidate"]:
                print(f"    Candidate: {scores['candidate']}")

    elapsed = time.monotonic() - t0
    print(f"\n{'=' * 60}")
    verdict = "PASS" if passed_count == total else "FAIL"
    print(f"  Verdict: {verdict} ({passed_count}/{total} cases, {elapsed:.1f}s)")
    print(f"{'=' * 60}")
    return 0 if passed_count == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
