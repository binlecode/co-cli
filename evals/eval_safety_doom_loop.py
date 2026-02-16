#!/usr/bin/env python3
"""E2E: Doom loop detection — injects system message after 3 identical tool calls.

Constructs a message history with repeated identical ToolCallParts, then runs
detect_safety_issues to verify the system message injection.

This tests the processor directly with synthetic messages because triggering
3+ identical LLM tool calls reliably in E2E is non-deterministic.

Usage:
    uv run python scripts/eval_e2e_doom_loop.py
"""

import json
import os
import sys

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from pydantic_ai._run_context import RunContext  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage  # noqa: E402

from co_cli._history import SafetyState, detect_safety_issues  # noqa: E402
from co_cli.deps import CoDeps  # noqa: E402
from co_cli.shell_backend import ShellBackend  # noqa: E402


def _make_ctx(threshold: int = 3) -> RunContext[CoDeps]:
    """Build a RunContext with safety state."""
    deps = CoDeps(
        shell=ShellBackend(),
        session_id="e2e-doom-loop",
        doom_loop_threshold=threshold,
        max_reflections=3,
    )
    deps._safety_state = SafetyState()
    from co_cli.agent import get_agent
    agent, _, _ = get_agent()
    return RunContext(deps=deps, model=agent.model, usage=RunUsage())


def _tool_call(name: str, args: dict, call_id: str) -> ToolCallPart:
    """Build a ToolCallPart with given args."""
    return ToolCallPart(
        tool_name=name,
        args=args,
        tool_call_id=call_id,
    )


def _has_doom_injection(messages: list) -> bool:
    """Check if doom loop system message was injected."""
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    if "repeating the same tool call" in part.content:
                        return True
    return False


def test_below_threshold():
    """2 identical calls (below threshold 3) — no injection."""
    print("  Test: 2 identical calls (below threshold)...", end=" ")
    ctx = _make_ctx(threshold=3)

    messages = [
        ModelRequest(parts=[UserPromptPart(content="search for cats")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c1")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c2")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c2")]),
    ]

    result = detect_safety_issues(ctx, messages)
    injected = _has_doom_injection(result)

    if not injected:
        print("PASS")
        return True
    else:
        print("FAIL (injected when it shouldn't)")
        return False


def test_at_threshold():
    """3 identical calls (at threshold 3) — injection fires."""
    print("  Test: 3 identical calls (at threshold)...", end=" ")
    ctx = _make_ctx(threshold=3)

    messages = [
        ModelRequest(parts=[UserPromptPart(content="search for cats")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c1")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c2")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c2")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c3")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c3")]),
    ]

    result = detect_safety_issues(ctx, messages)
    injected = _has_doom_injection(result)

    if injected:
        print("PASS")
        return True
    else:
        print("FAIL (no injection at threshold)")
        return False


def test_different_args_no_trigger():
    """3 calls with different args — no injection (not identical)."""
    print("  Test: 3 calls, different args (no trigger)...", end=" ")
    ctx = _make_ctx(threshold=3)

    messages = [
        ModelRequest(parts=[UserPromptPart(content="search stuff")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c1")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "dogs"}, "c2")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c2")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "birds"}, "c3")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c3")]),
    ]

    result = detect_safety_issues(ctx, messages)
    injected = _has_doom_injection(result)

    if not injected:
        print("PASS")
        return True
    else:
        print("FAIL (injected for different args)")
        return False


def test_injection_only_once():
    """Once injected, doom_loop_injected flag prevents re-injection."""
    print("  Test: injection fires only once per turn...", end=" ")
    ctx = _make_ctx(threshold=3)

    messages = [
        ModelRequest(parts=[UserPromptPart(content="search")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "x"}, "c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="r", tool_call_id="c1")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "x"}, "c2")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="r", tool_call_id="c2")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "x"}, "c3")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="r", tool_call_id="c3")]),
    ]

    # First call — should inject
    result1 = detect_safety_issues(ctx, messages)
    first_injected = _has_doom_injection(result1)

    # Second call with same ctx — should NOT inject again
    result2 = detect_safety_issues(ctx, messages)
    extra_injections = sum(
        1 for msg in result2
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, SystemPromptPart) and "repeating" in part.content
    )

    if first_injected and extra_injections == 0:
        print("PASS")
        return True
    else:
        print(f"FAIL (first={first_injected}, extra={extra_injections})")
        return False


def main() -> int:
    print("=" * 60)
    print("  E2E: Doom Loop Detection")
    print("=" * 60)
    print()

    results = [
        test_below_threshold(),
        test_at_threshold(),
        test_different_args_no_trigger(),
        test_injection_only_once(),
    ]

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  Verdict: {passed}/{total} passed")
    print(f"{'=' * 60}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
