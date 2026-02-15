#!/usr/bin/env python3
"""E2E: Shell reflection cap — injects system message after 3 consecutive shell errors.

Constructs message histories with shell error patterns and runs
detect_safety_issues to verify the reflection cap injection.

Usage:
    uv run python scripts/eval_e2e_shell_reflection.py
"""

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
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage  # noqa: E402

from co_cli._history import SafetyState, detect_safety_issues  # noqa: E402
from co_cli.deps import CoDeps  # noqa: E402
from co_cli.shell_backend import ShellBackend  # noqa: E402


def _make_ctx(max_reflections: int = 3) -> RunContext[CoDeps]:
    """Build a RunContext with safety state."""
    deps = CoDeps(
        shell=ShellBackend(),
        session_id="e2e-shell-reflection",
        doom_loop_threshold=10,
        max_reflections=max_reflections,
    )
    deps._safety_state = SafetyState()
    from co_cli.agent import get_agent
    agent, _, _ = get_agent()
    return RunContext(deps=deps, model=agent.model, usage=RunUsage())


def _shell_call(cmd: str, call_id: str) -> ToolCallPart:
    return ToolCallPart(
        tool_name="run_shell_command",
        args={"cmd": cmd},
        tool_call_id=call_id,
    )


def _shell_error(content: str, call_id: str) -> ToolReturnPart:
    return ToolReturnPart(
        tool_name="run_shell_command",
        content={"display": content, "exit_code": 1, "error": True},
        tool_call_id=call_id,
    )


def _shell_success(content: str, call_id: str) -> ToolReturnPart:
    return ToolReturnPart(
        tool_name="run_shell_command",
        content={"display": content, "exit_code": 0, "error": False},
        tool_call_id=call_id,
    )


def _has_reflection_injection(messages: list) -> bool:
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    if "Shell reflection limit" in part.content:
                        return True
    return False


def test_below_cap():
    """2 consecutive shell errors (below cap 3) — no injection."""
    print("  Test: 2 shell errors (below cap)...", end=" ")
    ctx = _make_ctx(max_reflections=3)

    messages = [
        ModelRequest(parts=[UserPromptPart(content="run something")]),
        ModelResponse(parts=[_shell_call("bad1", "c1")]),
        ModelRequest(parts=[_shell_error("command not found", "c1")]),
        ModelResponse(parts=[_shell_call("bad2", "c2")]),
        ModelRequest(parts=[_shell_error("command not found", "c2")]),
    ]

    result = detect_safety_issues(ctx, messages)
    injected = _has_reflection_injection(result)

    if not injected:
        print("PASS")
        return True
    else:
        print("FAIL (injected when it shouldn't)")
        return False


def test_at_cap():
    """3 consecutive shell errors (at cap 3) — injection fires."""
    print("  Test: 3 shell errors (at cap)...", end=" ")
    ctx = _make_ctx(max_reflections=3)

    messages = [
        ModelRequest(parts=[UserPromptPart(content="run something")]),
        ModelResponse(parts=[_shell_call("bad1", "c1")]),
        ModelRequest(parts=[_shell_error("error: not found", "c1")]),
        ModelResponse(parts=[_shell_call("bad2", "c2")]),
        ModelRequest(parts=[_shell_error("error: not found", "c2")]),
        ModelResponse(parts=[_shell_call("bad3", "c3")]),
        ModelRequest(parts=[_shell_error("error: not found", "c3")]),
    ]

    result = detect_safety_issues(ctx, messages)
    injected = _has_reflection_injection(result)

    if injected:
        print("PASS")
        return True
    else:
        print("FAIL (no injection at cap)")
        return False


def test_success_resets_counter():
    """Success between errors resets the consecutive count."""
    print("  Test: success between errors resets counter...", end=" ")
    ctx = _make_ctx(max_reflections=3)

    messages = [
        ModelRequest(parts=[UserPromptPart(content="run something")]),
        ModelResponse(parts=[_shell_call("bad1", "c1")]),
        ModelRequest(parts=[_shell_error("error", "c1")]),
        ModelResponse(parts=[_shell_call("bad2", "c2")]),
        ModelRequest(parts=[_shell_error("error", "c2")]),
        # Success resets
        ModelResponse(parts=[_shell_call("good", "c3")]),
        ModelRequest(parts=[_shell_success("ok", "c3")]),
        # Two more errors — still below cap
        ModelResponse(parts=[_shell_call("bad3", "c4")]),
        ModelRequest(parts=[_shell_error("error", "c4")]),
        ModelResponse(parts=[_shell_call("bad4", "c5")]),
        ModelRequest(parts=[_shell_error("error", "c5")]),
    ]

    result = detect_safety_issues(ctx, messages)
    injected = _has_reflection_injection(result)

    if not injected:
        print("PASS")
        return True
    else:
        print("FAIL (injected despite success resetting counter)")
        return False


def test_non_shell_errors_ignored():
    """Errors from non-shell tools don't count toward the cap."""
    print("  Test: non-shell errors ignored...", end=" ")
    ctx = _make_ctx(max_reflections=3)

    messages = [
        ModelRequest(parts=[UserPromptPart(content="search stuff")]),
        ModelResponse(parts=[ToolCallPart(
            tool_name="web_search",
            args={"query": "test"},
            tool_call_id="c1",
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="web_search",
            content={"display": "error", "error": True},
            tool_call_id="c1",
        )]),
        ModelResponse(parts=[ToolCallPart(
            tool_name="web_search",
            args={"query": "test"},
            tool_call_id="c2",
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="web_search",
            content={"display": "error", "error": True},
            tool_call_id="c2",
        )]),
        ModelResponse(parts=[ToolCallPart(
            tool_name="web_search",
            args={"query": "test"},
            tool_call_id="c3",
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="web_search",
            content={"display": "error", "error": True},
            tool_call_id="c3",
        )]),
    ]

    result = detect_safety_issues(ctx, messages)
    injected = _has_reflection_injection(result)

    if not injected:
        print("PASS")
        return True
    else:
        print("FAIL (non-shell errors triggered reflection cap)")
        return False


def main() -> int:
    print("=" * 60)
    print("  E2E: Shell Reflection Cap")
    print("=" * 60)
    print()

    results = [
        test_below_cap(),
        test_at_cap(),
        test_success_resets_counter(),
        test_non_shell_errors_ignored(),
    ]

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  Verdict: {passed}/{total} passed")
    print(f"{'=' * 60}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
