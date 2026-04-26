#!/usr/bin/env python3
"""Eval: proactive recall tool selection — memory_search vs knowledge_search disambiguation.

Validates that the agent correctly disambiguates between memory_search (past conversation)
and knowledge_search (saved artifact) based on prompt intent and the tie-breaker rule
embedded in each tool's schema description.

Three cases:
  past_conversation   — "what did we figure out about docker last time?" → memory_search
  saved_preference    — "what was my preferred test runner?" → knowledge_search
  saved_convention    — "what's our convention for logging?" → knowledge_search (tie-breaker)

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_proactive_recall.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._frontend import SilentFrontend
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import ModelResponse, ToolCallPart

from co_cli.agent._core import build_agent, build_tool_registry
from co_cli.config._core import settings
from co_cli.context.orchestrate import run_turn

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-proactive-recall.md"
_REPORT_HEADER = "# Eval Report: Proactive Recall Disambiguation"


def _extract_tool_calls(messages: list[Any]) -> list[str]:
    """Return ordered list of tool names from ToolCallPart in all response messages."""
    tool_calls: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tool_calls.append(part.tool_name)
    return tool_calls


def _build_eval_agent_and_deps():
    """Build agent and deps with MCP servers disabled to prevent connector noise."""
    config = settings.model_copy(update={"mcp_servers": []})
    reg = build_tool_registry(config)
    agent = build_agent(config=config, tool_registry=reg)
    deps = make_eval_deps()
    deps.tool_index = reg.tool_index
    deps.tool_registry = reg
    return agent, deps


async def _run_case(
    case_id: str,
    prompt: str,
    *,
    expect_tool: str,
    reject_tool: str | None = None,
) -> dict[str, Any]:
    """Run one disambiguation case and return a result dict."""
    t0 = time.monotonic()

    agent, deps = _build_eval_agent_and_deps()

    try:
        async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
            result = await run_turn(
                agent=agent,
                user_input=prompt,
                deps=deps,
                message_history=[],
                frontend=SilentFrontend(),
            )
    except TimeoutError:
        return {
            "id": case_id,
            "verdict": "FAIL",
            "failure": f"Turn timed out after {EVAL_TURN_TIMEOUT_SECS}s",
            "tool_calls": [],
            "duration_ms": (time.monotonic() - t0) * 1000,
        }
    except Exception as e:
        return {
            "id": case_id,
            "verdict": "FAIL",
            "failure": f"Exception: {e}",
            "tool_calls": [],
            "duration_ms": (time.monotonic() - t0) * 1000,
        }

    tool_calls = _extract_tool_calls(result.messages)

    called_expected = expect_tool in tool_calls
    called_rejected = reject_tool is not None and reject_tool in tool_calls

    if called_expected and not called_rejected:
        verdict = "PASS"
        failure = None
    elif not called_expected:
        verdict = "FAIL"
        failure = f"{expect_tool!r} not called; tool_calls={tool_calls}"
    else:
        verdict = "FAIL"
        failure = f"Called rejected tool {reject_tool!r} (should have used {expect_tool!r} only); tool_calls={tool_calls}"

    return {
        "id": case_id,
        "verdict": verdict,
        "failure": failure,
        "tool_calls": tool_calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


async def run_all_cases() -> list[dict[str, Any]]:
    """Run all three disambiguation cases sequentially."""
    results = []

    # Case 1: past-conversation phrasing → memory_search
    results.append(
        await _run_case(
            "past_conversation",
            "what did we figure out about docker last time?",
            expect_tool="memory_search",
        )
    )

    # Case 2: saved-preference phrasing → knowledge_search (not memory_search)
    results.append(
        await _run_case(
            "saved_preference",
            "what was my preferred test runner?",
            expect_tool="knowledge_search",
        )
    )

    # Case 3: convention phrasing → knowledge_search (tie-breaker rule)
    results.append(
        await _run_case(
            "saved_convention",
            "what's our convention for logging?",
            expect_tool="knowledge_search",
        )
    )

    return results


def _build_report_section(cases: list[dict[str, Any]], run_at: datetime) -> str:
    """Build a dated markdown section for one eval run."""
    pass_count = sum(1 for c in cases if c["verdict"] == "PASS")
    fail_count = sum(1 for c in cases if c["verdict"] == "FAIL")
    overall = "PASS" if fail_count == 0 else "FAIL"

    lines = [
        f"## Run: {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  [{overall}]",
        "",
        f"**Overall:** {overall} — {pass_count}/{len(cases)} cases passed",
        "",
        "| Case | Verdict | Tool calls | Duration |",
        "|------|---------|------------|----------|",
    ]
    for c in cases:
        tool_str = ", ".join(c["tool_calls"]) or "—"
        lines.append(f"| `{c['id']}` | {c['verdict']} | {tool_str} | {c['duration_ms']:.0f} ms |")

    failures = [c for c in cases if c["verdict"] == "FAIL"]
    if failures:
        lines.append("")
        lines.append("**Failures:**")
        for c in failures:
            lines.append(f"- `{c['id']}`: {c['failure']}")

    return "\n".join(lines)


def _load_compatible_sections() -> list[str]:
    """Return prior run sections that match the current schema."""
    if not _REPORT_PATH.exists():
        return []
    existing = _REPORT_PATH.read_text(encoding="utf-8")
    if not existing.startswith(_REPORT_HEADER):
        return []
    split = existing.split("\n\n", 1)
    body = split[1] if len(split) > 1 else ""
    raw = [s.strip() for s in body.split("\n---\n") if s.strip()]
    return [s for s in raw if "past_conversation" in s and "saved_preference" in s]


def _write_report(section: str) -> None:
    """Prepend the new run section to the report file."""
    prior = _load_compatible_sections()
    kept = prior[:9]  # keep last 9 + new = 10 total
    body_parts = [section, *kept]
    content = _REPORT_HEADER + "\n\n" + "\n\n---\n\n".join(body_parts) + "\n"
    _REPORT_PATH.write_text(content, encoding="utf-8")


async def main() -> None:
    run_at = datetime.now(UTC)
    print(f"[eval_proactive_recall] running disambiguation cases at {run_at.strftime('%H:%M:%S')}")

    cases = await run_all_cases()

    pass_count = sum(1 for c in cases if c["verdict"] == "PASS")
    fail_count = sum(1 for c in cases if c["verdict"] == "FAIL")
    overall = "PASS" if fail_count == 0 else "FAIL"

    print(f"\n[eval_proactive_recall] {overall} — {pass_count}/{len(cases)} cases passed")
    for c in cases:
        status = "✓" if c["verdict"] == "PASS" else "✗"
        calls_str = ", ".join(c["tool_calls"]) or "—"
        print(
            f"  {status} {c['id']}: {c['verdict']} | tools={calls_str} | {c['duration_ms']:.0f}ms"
        )
        if c["failure"]:
            print(f"    → {c['failure']}")

    section = _build_report_section(cases, run_at)
    _write_report(section)
    print(f"\n[eval_proactive_recall] report written to {_REPORT_PATH}")

    sys.exit(0 if overall == "PASS" else 1)


if __name__ == "__main__":
    asyncio.run(main())
