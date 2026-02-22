"""Functional tests for doom loop detection in _history.py.

detect_safety_issues() scans message history for repeated identical tool
calls and injects a SystemPromptPart warning at the configured threshold.
Deterministic — no LLM calls.
"""

from pydantic_ai._run_context import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli._history import SafetyState, detect_safety_issues
from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.shell_backend import ShellBackend


def _make_ctx(threshold: int = 3) -> RunContext[CoDeps]:
    deps = CoDeps(
        shell=ShellBackend(),
        session_id="test-doom-loop",
        doom_loop_threshold=threshold,
        max_reflections=3,
    )
    deps._safety_state = SafetyState()
    agent, _, _ = get_agent()
    return RunContext(deps=deps, model=agent.model, usage=RunUsage())


def _tool_call(name: str, args: dict, call_id: str) -> ToolCallPart:
    return ToolCallPart(tool_name=name, args=args, tool_call_id=call_id)


def _has_doom_injection(messages: list) -> bool:
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    if "repeating the same tool call" in part.content:
                        return True
    return False


def test_below_threshold_no_injection():
    """2 identical calls (below threshold 3) produce no doom injection."""
    ctx = _make_ctx(threshold=3)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="search for cats")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c1")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c2")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c2")]),
    ]
    result = detect_safety_issues(ctx, messages)
    assert not _has_doom_injection(result)


def test_at_threshold_injects():
    """3 identical calls (at threshold 3) triggers doom loop injection."""
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
    assert _has_doom_injection(result)


def test_different_args_no_injection():
    """3 calls with different args do not trigger doom loop (not identical)."""
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
    assert not _has_doom_injection(result)


def test_injection_fires_only_once():
    """Once injected, doom_loop_injected flag prevents re-injection on the same ctx."""
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

    result1 = detect_safety_issues(ctx, messages)
    assert _has_doom_injection(result1), "First call should inject"

    result2 = detect_safety_issues(ctx, messages)
    extra = sum(
        1 for msg in result2
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, SystemPromptPart) and "repeating" in part.content
    )
    assert extra == 0, f"Second call injected {extra} extra doom messages"
