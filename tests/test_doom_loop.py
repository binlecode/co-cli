"""Functional tests for doom loop detection in _history.py.

detect_safety_issues() scans message history for repeated identical tool
calls and injects a SystemPromptPart warning at the configured threshold.
Deterministic — no LLM calls.
"""

from dataclasses import replace
from pydantic_ai._run_context import RunContext
from pathlib import Path
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli.context._history import SafetyState, detect_safety_issues
from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoServices, CoConfig, CoRuntimeState
from co_cli.tools._shell_backend import ShellBackend


def _make_ctx(threshold: int = 3) -> RunContext[CoDeps]:
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            doom_loop_threshold=threshold,
            max_reflections=3,
        ),
        runtime=CoRuntimeState(safety_state=SafetyState()),
    )
    agent, _, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
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


# ---------------------------------------------------------------------------
# Bug-finding: reverse-scan reset problems
# ---------------------------------------------------------------------------


def _has_reflection_injection(messages: list) -> bool:
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    if "reflection limit" in part.content.lower():
                        return True
    return False


def test_doom_loop_preceded_by_different_call():
    """3 identical calls preceded by 1 different call must still trigger doom loop.

    Bug: detect_safety_issues scans messages in reverse and resets consecutive_same
    to 1 when it encounters the older different call. After the loop, consecutive_same
    equals 1 (from the old call), not 3, so the doom loop check never fires.

    Root cause: the algorithm tracks a running streak but does not preserve the
    maximum streak seen during the scan. Any non-matching older call resets the
    counter and erases the already-observed streak of 3.
    """
    ctx = _make_ctx(threshold=3)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="search for cats")]),
        # one different initial call — represents the model's first attempt
        ModelResponse(parts=[_tool_call("web_search", {"query": "initial different query"}, "c0")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="initial results", tool_call_id="c0")]),
        # 3 identical subsequent calls — the actual doom loop
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c1")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c2")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c2")]),
        ModelResponse(parts=[_tool_call("web_search", {"query": "cats"}, "c3")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="results", tool_call_id="c3")]),
    ]
    result = detect_safety_issues(ctx, messages)
    assert _has_doom_injection(result), (
        "Doom loop not detected: 3 identical calls preceded by 1 different call "
        "should still trigger at threshold=3. The reverse scanner resets "
        "consecutive_same to 1 when it hits the older different call, erasing the "
        "already-counted streak of 3."
    )


def test_shell_reflection_does_not_trigger_on_broken_streak():
    """Shell reflection must not fire when a non-shell call breaks the error streak.

    Scenario: 3 old shell errors, then a successful recall_memory call (breaks
    the streak), then 1 new shell error. The most-recent consecutive shell error
    count is 1, not 3 — the reflection cap must not fire.

    Bug: scanning in reverse, the code resets consecutive_shell_errors to 0 when
    it reaches the recall_memory success. It then re-accumulates 3 from the older
    shell errors, ending the scan at count 3 — a false positive that incorrectly
    triggers the reflection injection.
    """
    ctx = _make_ctx(threshold=3)
    ctx.deps.config = replace(ctx.deps.config, max_reflections=3)
    ctx.deps.runtime.safety_state = SafetyState()
    messages = [
        ModelRequest(parts=[UserPromptPart(content="run the tests")]),
        # 3 old shell errors
        ModelResponse(parts=[_tool_call("run_shell_command", {"cmd": "npm test"}, "c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="run_shell_command", content="error: module not found", tool_call_id="c1")]),
        ModelResponse(parts=[_tool_call("run_shell_command", {"cmd": "npm test"}, "c2")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="run_shell_command", content="error: module not found", tool_call_id="c2")]),
        ModelResponse(parts=[_tool_call("run_shell_command", {"cmd": "npm test"}, "c3")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="run_shell_command", content="error: module not found", tool_call_id="c3")]),
        # success — model switched strategies, broke the streak
        ModelResponse(parts=[_tool_call("recall_memory", {}, "c4")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="recall_memory", content="no matching memories", tool_call_id="c4")]),
        # 1 new shell error (most recent) — streak reset, count should be 1
        ModelResponse(parts=[_tool_call("run_shell_command", {"cmd": "npm test"}, "c5")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="run_shell_command", content="error: still failing", tool_call_id="c5")]),
    ]
    result = detect_safety_issues(ctx, messages)
    assert not _has_reflection_injection(result), (
        "Shell reflection triggered incorrectly: the most-recent consecutive shell "
        "error streak is 1 (broken by recall_memory success), not 3. "
        "The reverse scanner re-accumulates the 3 old errors after the reset, "
        "producing a false positive."
    )


def test_shell_reflection_false_positive_on_informational_error_word():
    """Shell reflection must not fire when output contains 'error' as a word, not an error.

    Bug: the heuristic `"error" in content.lower()[:50]` is a substring match.
    Legitimate shell output like "3 tests passed, 0 errors" or "no errors found"
    contains the substring "error" and falsely triggers is_error=True.  Three such
    successful returns then accumulate consecutive_shell_errors=3 and fire the
    reflection cap incorrectly.
    """
    ctx = _make_ctx(threshold=3)
    ctx.deps.config = replace(ctx.deps.config, max_reflections=3)
    ctx.deps.runtime.safety_state = SafetyState()
    messages = [
        ModelRequest(parts=[UserPromptPart(content="run the tests")]),
        # 3 successful shell returns — output mentions 'error' as a noun, not a failure
        ModelResponse(parts=[_tool_call("run_shell_command", {"cmd": "pytest"}, "c1")]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="run_shell_command",
            content="3 tests passed, 0 errors",
            tool_call_id="c1",
        )]),
        ModelResponse(parts=[_tool_call("run_shell_command", {"cmd": "pytest"}, "c2")]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="run_shell_command",
            content="no errors found in build output",
            tool_call_id="c2",
        )]),
        ModelResponse(parts=[_tool_call("run_shell_command", {"cmd": "pytest"}, "c3")]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="run_shell_command",
            content="error count: 0",
            tool_call_id="c3",
        )]),
    ]
    result = detect_safety_issues(ctx, messages)
    assert not _has_reflection_injection(result), (
        "Shell reflection triggered incorrectly: outputs like '3 tests passed, 0 errors' "
        "contain 'error' as a substring but are NOT failures. The heuristic "
        "'\"error\" in content.lower()[:50]' is a false positive for informational text."
    )
