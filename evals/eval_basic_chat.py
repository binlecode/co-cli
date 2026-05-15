#!/usr/bin/env python3
"""Eval: basic chat — text-only turns, multi-turn context, instruction following.

Sub-cases:
  C1  factual_question       Agent answers a simple factual question with text only —
                             no tool calls, outcome 'continue'
  C2  multi_turn_context     Second turn asks a follow-up; agent references a unique token
                             planted in the first turn — proves message_history is passed
  C3  instruction_following  Agent follows an explicit format instruction: reply in exactly
                             3 bullet points starting with '- '

All cases use a real LLM and no persistent stores.

Writes: docs/REPORT-eval-basic-chat.md (prepends dated section each run).

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_basic_chat.py
"""

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from evals._deps import make_eval_agent, make_eval_deps
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from co_cli.config.core import settings
from co_cli.context.orchestrate import run_turn
from co_cli.display.headless import HeadlessFrontend

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-basic-chat.md"

_AGENT = make_eval_agent(settings)


def _response_text(result: Any) -> str:
    """Extract all assistant text parts from a TurnResult."""
    parts = []
    for msg in result.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content)
    return " ".join(parts)


def _has_tool_calls(result: Any) -> bool:
    """Return True if any ModelResponse in messages contains a ToolCallPart."""
    for msg in result.messages:
        if isinstance(msg, ModelResponse) and any(isinstance(p, ToolCallPart) for p in msg.parts):
            return True
    return False


# ---------------------------------------------------------------------------
# C1: factual_question
# ---------------------------------------------------------------------------


async def run_factual_question() -> dict[str, Any]:
    """Agent answers a simple factual question — no tool calls, outcome 'continue'."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    frontend = HeadlessFrontend(approval_response="n")  # sentinel: must never fire
    deps = make_eval_deps()

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=_AGENT,
            user_input="What is the capital of France? Reply with just the city name.",
            deps=deps,
            message_history=[],
            frontend=frontend,
        )
    steps.append(
        {
            "name": "run_turn",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"outcome={result.outcome} approval_calls={len(frontend.approval_calls)}",
        }
    )

    text = _response_text(result)
    tool_calls_fired = _has_tool_calls(result)
    steps.append(
        {
            "name": "response_analysis",
            "ms": 0,
            "detail": f"tool_calls={tool_calls_fired} paris_in_response={'paris' in text.lower()} preview={text[:200]!r}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif tool_calls_fired:
        verdict, failure = "FAIL", "agent made unexpected tool calls for a simple factual question"
    elif "paris" not in text.lower():
        verdict, failure = "FAIL", f"'Paris' not found in response: {text[:200]!r}"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "factual_question",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# C2: multi_turn_context
# ---------------------------------------------------------------------------


async def run_multi_turn_context() -> dict[str, Any]:
    """Second turn references a unique token planted in the first turn."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    secret = f"EVALTOKEN{int(time.monotonic() * 1000) % 100000:05d}"
    frontend = HeadlessFrontend()
    deps = make_eval_deps()

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result1 = await run_turn(
            agent=_AGENT,
            user_input=f"Remember this secret word for our conversation: {secret}. Just acknowledge you have it.",
            deps=deps,
            message_history=[],
            frontend=frontend,
        )
    steps.append(
        {
            "name": "turn1",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"outcome={result1.outcome}",
        }
    )

    if result1.outcome == "error":
        text1 = _response_text(result1)
        return {
            "id": "multi_turn_context",
            "verdict": "FAIL",
            "failure": f"turn1 error: {text1[:200]}",
            "steps": steps,
            "duration_ms": (time.monotonic() - case_t0) * 1000,
        }

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result2 = await run_turn(
            agent=_AGENT,
            user_input="What was the secret word I gave you? Repeat it exactly.",
            deps=deps,
            message_history=result1.messages,
            frontend=frontend,
        )
    steps.append(
        {
            "name": "turn2",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"outcome={result2.outcome}",
        }
    )

    text2 = _response_text(result2)
    steps.append(
        {
            "name": "response_analysis",
            "ms": 0,
            "detail": f"secret_in_response={secret in text2} preview={text2[:200]!r}",
        }
    )

    if result2.outcome == "error":
        verdict, failure = "FAIL", f"turn2 error: {text2[:200]}"
    elif secret not in text2:
        verdict, failure = "FAIL", f"secret token {secret!r} not found in turn2 response"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "multi_turn_context",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# C3: instruction_following
# ---------------------------------------------------------------------------


async def run_instruction_following() -> dict[str, Any]:
    """Agent follows explicit format instruction — response has exactly 3 bullet points."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    frontend = HeadlessFrontend()
    deps = make_eval_deps()

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=_AGENT,
            user_input=(
                "Reply with exactly 3 bullet points and nothing else. "
                "Each bullet must start with '- '. "
                "List three primary colors."
            ),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )
    steps.append(
        {
            "name": "run_turn",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"outcome={result.outcome}",
        }
    )

    text = _response_text(result)
    bullet_count = sum(1 for line in text.split("\n") if line.strip().startswith("- "))
    steps.append(
        {
            "name": "response_analysis",
            "ms": 0,
            "detail": f"bullet_count={bullet_count} preview={text[:300]!r}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif bullet_count != 3:
        verdict, failure = (
            "FAIL",
            f"expected 3 bullet points starting with '- ', got {bullet_count}: {text[:300]!r}",
        )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "instruction_following",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(cases: list[dict[str, Any]], total_ms: float) -> None:
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    passed = sum(1 for c in cases if c["verdict"] in ("PASS", "SOFT PASS"))

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Model:** {settings.llm.provider} / {settings.llm.model or 'default'}  ",
        f"**Total runtime:** {total_ms:.0f}ms  ",
        f"**Result:** {passed}/{len(cases)} passed",
        "",
        "### Summary",
        "",
        "| Case | Verdict | Duration |",
        "|------|---------|----------|",
    ]
    for c in cases:
        lines.append(f"| `{c['id']}` | {c['verdict']} | {c['duration_ms']:.0f}ms |")

    lines += ["", "### Step Traces", ""]
    for c in cases:
        lines.append(f"#### `{c['id']}` — {c['verdict']}")
        for step in c["steps"]:
            lines.append(f"- **{step['name']}** ({step['ms']:.0f}ms): {step['detail']}")
        if c.get("failure"):
            lines.append(f"- **Failure:** {c['failure']}")
        lines.append("")

    lines += ["---", ""]
    section = "\n".join(lines)

    if _REPORT_PATH.exists():
        existing = _REPORT_PATH.read_text(encoding="utf-8")
        split = existing.split("\n", 2)
        updated = split[0] + "\n\n" + section + ("\n".join(split[1:]) if len(split) > 1 else "")
    else:
        updated = "# Eval Report: Basic Chat\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Basic Chat")
    print("=" * 60)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    runners = [
        ("C1: factual_question", run_factual_question),
        ("C2: multi_turn_context", run_multi_turn_context),
        ("C3: instruction_following", run_instruction_following),
    ]

    for label, fn in runners:
        print(f"\n  [{label}]", flush=True)
        try:
            result = await fn()
        except Exception as exc:
            result = {
                "id": label.split(": ", 1)[1].replace(" ", "_").lower()[:30],
                "verdict": "ERROR",
                "failure": f"{type(exc).__name__}: {exc}",
                "steps": [],
                "duration_ms": 0,
            }
        all_cases.append(result)
        print(f"  → {result['verdict']} ({result['duration_ms']:.0f}ms)")
        if result.get("failure"):
            print(f"    {result['failure']}")

    total_ms = (time.monotonic() - t0) * 1000
    passed = sum(1 for c in all_cases if c["verdict"] in ("PASS", "SOFT PASS"))
    _write_report(all_cases, total_ms)

    print(f"\n{'=' * 60}")
    verdict = "PASS" if passed == len(all_cases) else "FAIL"
    print(f"  Verdict: {verdict} ({passed}/{len(all_cases)} cases, {total_ms:.0f}ms)")
    print(f"{'=' * 60}")
    return 0 if passed == len(all_cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
