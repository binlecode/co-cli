#!/usr/bin/env python3
"""Eval: Ollama deferred tool discovery via search_tools guidance.

Verifies that the static prompt rules, especially the 04_tool_protocol.md
deferred-discovery section, cause the model to prefer specialist deferred
tools over generic shell.

The model may reach specialist tools via search_tools (discovery path) or
directly by name (when named in the category-awareness prompt). Both are
valid — the behavioral goal is specialist tool over shell, not a specific
discovery mechanism.

Four cases:
  background_task_positive  — model uses task_start (not shell bg)
  file_create_competition   — model uses write_file (not shell redirection alone)
  shell_negative_control    — model calls shell directly for git
  unsupported_capability    — model does NOT loop search_tools > once on no-match

Prerequisites: settings.llm.provider == "ollama"

Skip: non-ollama providers exit 0 with a SKIPPED message.

Usage:
    uv run python evals/eval_ollama_tool_search.py
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._frontend import SilentFrontend
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS as _EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import ModelResponse, ToolCallPart

from co_cli.agent._core import build_agent, build_tool_registry
from co_cli.config._core import settings
from co_cli.context.orchestrate import run_turn

_EVAL_FILE = Path("/tmp/co_eval_test_file.txt")


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------


def _extract_tool_calls(messages: list[Any]) -> list[str]:
    """Return ordered list of tool_name values from all ToolCallPart in messages."""
    tool_calls: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tool_calls.append(part.tool_name)
    return tool_calls


# ---------------------------------------------------------------------------
# Agent / deps factory (MCP disabled)
# ---------------------------------------------------------------------------


def _build_eval_agent_and_deps():
    """Build agent and deps with MCP servers disabled to prevent connector noise."""
    config = settings.model_copy(update={"mcp_servers": []})
    reg = build_tool_registry(config)
    agent = build_agent(config=config, tool_registry=reg)
    deps = make_eval_deps()
    # Wire tool_index so search_tools can discover registered tools
    deps.tool_index = reg.tool_index
    deps.tool_registry = reg
    return agent, deps


# ---------------------------------------------------------------------------
# Case runners
# ---------------------------------------------------------------------------


async def run_background_task_positive() -> dict[str, Any]:
    """Model must use task_start (not shell backgrounding)."""
    case_id = "background_task_positive"
    t0 = time.monotonic()

    agent, deps = _build_eval_agent_and_deps()
    prompt = "Start a long-running 5-second background sleep and return the task id."

    async with asyncio.timeout(_EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=agent,
            user_input=prompt,
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    tool_calls = _extract_tool_calls(result.messages)
    # Pass: specialist tool used (via direct call or search_tools discovery)
    has_bg_task = "task_start" in tool_calls

    verdict = "PASS" if has_bg_task else "FAIL"
    failure = None if has_bg_task else f"task_start not called; tool_calls={tool_calls}"

    return {
        "id": case_id,
        "verdict": verdict,
        "failure": failure,
        "tool_calls": tool_calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


async def run_file_create_competition() -> dict[str, Any]:
    """Model must use write_file, not shell redirection, for file creation."""
    case_id = "file_create_competition"
    t0 = time.monotonic()

    agent, deps = _build_eval_agent_and_deps()
    prompt = "Create a file at /tmp/co_eval_test_file.txt with exactly this content: hello eval"

    try:
        async with asyncio.timeout(_EVAL_TURN_TIMEOUT_SECS):
            result = await run_turn(
                agent=agent,
                user_input=prompt,
                deps=deps,
                message_history=[],
                frontend=SilentFrontend(),
            )
    finally:
        _EVAL_FILE.unlink(missing_ok=True)

    tool_calls = _extract_tool_calls(result.messages)
    has_write_file = "write_file" in tool_calls
    # Fail if model solved via shell redirection without ever calling write_file
    shell_only = "shell" in tool_calls and not has_write_file

    # Pass: specialist tool used (via direct call or search_tools discovery)
    passed = has_write_file and not shell_only
    verdict = "PASS" if passed else "FAIL"
    failure = None if passed else (f"has_write_file={has_write_file} shell_only={shell_only}")

    return {
        "id": case_id,
        "verdict": verdict,
        "failure": failure,
        "tool_calls": tool_calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


async def run_shell_negative_control() -> dict[str, Any]:
    """shell called directly — search_tools must NOT precede it."""
    case_id = "shell_negative_control"
    t0 = time.monotonic()

    agent, deps = _build_eval_agent_and_deps()
    prompt = "Run git status and show the output."

    async with asyncio.timeout(_EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=agent,
            user_input=prompt,
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    tool_calls = _extract_tool_calls(result.messages)
    has_shell = "shell" in tool_calls
    # search_tools must NOT appear before shell (or at all for this simple task)
    search_before_shell = (
        "search_tools" in tool_calls
        and has_shell
        and tool_calls.index("search_tools") < tool_calls.index("shell")
    )

    passed = has_shell and not search_before_shell
    verdict = "PASS" if passed else "FAIL"
    failure = (
        None if passed else (f"has_shell={has_shell} search_before_shell={search_before_shell}")
    )

    return {
        "id": case_id,
        "verdict": verdict,
        "failure": failure,
        "tool_calls": tool_calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


async def run_unsupported_capability_boundary() -> dict[str, Any]:
    """Model must NOT loop search_tools more than once when no matching tool exists."""
    case_id = "unsupported_capability_boundary"
    t0 = time.monotonic()

    agent, deps = _build_eval_agent_and_deps()
    prompt = "Send a Slack message to #general saying hello."

    async with asyncio.timeout(_EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=agent,
            user_input=prompt,
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    tool_calls = _extract_tool_calls(result.messages)
    search_count = tool_calls.count("search_tools")

    # Pass: at most one search_tools call (no looping after no-match)
    passed = search_count <= 1
    verdict = "PASS" if passed else "FAIL"
    failure = None if passed else f"search_tools called {search_count} times (expected ≤1)"

    return {
        "id": case_id,
        "verdict": verdict,
        "failure": failure,
        "tool_calls": tool_calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    if settings.llm.provider != "ollama":
        print(
            f"SKIPPED: provider is '{settings.llm.provider}', "
            "not 'ollama' — Ollama tool-search eval requires Ollama."
        )
        return 0

    print("=" * 60)
    print("  Eval: Ollama Deferred Tool Discovery (search_tools steering)")
    print(f"  Model: {settings.llm.model or 'default'}")
    print("=" * 60)

    cases = [
        ("background_task_positive", run_background_task_positive),
        ("file_create_competition", run_file_create_competition),
        ("shell_negative_control", run_shell_negative_control),
        ("unsupported_capability_boundary", run_unsupported_capability_boundary),
    ]

    all_results: list[dict[str, Any]] = []
    t0 = time.monotonic()

    for case_id, runner in cases:
        print(f"\n  [{case_id}]", end=" ", flush=True)
        try:
            result = await runner()
        except Exception as exc:
            result = {
                "id": case_id,
                "verdict": "ERROR",
                "failure": str(exc),
                "tool_calls": [],
                "duration_ms": 0,
            }

        all_results.append(result)
        tool_summary = ", ".join(result["tool_calls"]) if result["tool_calls"] else "(none)"
        print(f"tool_calls: {tool_summary} → {result['verdict']} ({result['duration_ms']:.0f}ms)")
        if result.get("failure"):
            print(f"    failure: {result['failure']}")

    total_ms = (time.monotonic() - t0) * 1000
    passed = sum(1 for r in all_results if r["verdict"] == "PASS")
    overall = "PASS" if passed == len(all_results) else "FAIL"

    print(f"\n{'=' * 60}")
    print(f"  Verdict: {overall} ({passed}/{len(all_results)} cases, {total_ms:.0f}ms)")
    print(f"{'=' * 60}")

    return 0 if passed == len(all_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
