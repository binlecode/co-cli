"""Functional tests for the orchestration layer.

RecordingFrontend is a real FrontendProtocol implementation (not a mock)
that records all events for assertions.
"""

from typing import Any

import pytest
from pydantic_ai import AgentRunResult, AgentRunResultEvent, FinalResultEvent

from co_cli._orchestrate import FrontendProtocol, _patch_dangling_tool_calls, _stream_events, run_turn
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli._shell_backend import ShellBackend
from pydantic_ai._agent_graph import GraphAgentState
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits


# ---------------------------------------------------------------------------
# RecordingFrontend — real FrontendProtocol for tests
# ---------------------------------------------------------------------------


class RecordingFrontend:
    """Records all frontend events as (event_type, payload) tuples.

    Configurable approval_policy: "approve" | "deny".
    """

    def __init__(self, approval_policy: str = "approve") -> None:
        self.events: list[tuple[str, Any]] = []
        self.approval_policy = approval_policy

    def on_text_delta(self, accumulated: str) -> None:
        self.events.append(("text_delta", accumulated))

    def on_text_commit(self, final: str) -> None:
        self.events.append(("text_commit", final))

    def on_thinking_delta(self, accumulated: str) -> None:
        self.events.append(("thinking_delta", accumulated))

    def on_thinking_commit(self, final: str) -> None:
        self.events.append(("thinking_commit", final))

    def on_tool_call(self, name: str, args_display: str) -> None:
        self.events.append(("tool_call", (name, args_display)))

    def on_tool_result(self, title: str, content: str | dict[str, Any]) -> None:
        self.events.append(("tool_result", (title, content)))

    def on_status(self, message: str) -> None:
        self.events.append(("status", message))

    def on_final_output(self, text: str) -> None:
        self.events.append(("final_output", text))

    def prompt_approval(self, description: str) -> str:
        self.events.append(("prompt_approval", description))
        if self.approval_policy == "approve":
            return "y"
        return "n"

    def cleanup(self) -> None:
        self.events.append(("cleanup", None))


# ---------------------------------------------------------------------------
# Streaming test helper
# ---------------------------------------------------------------------------


class StaticEventAgent:
    """Minimal async event source compatible with _stream_events()."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def run_stream_events(self, *_: Any, **__: Any):
        for event in self._events:
            yield event


# ---------------------------------------------------------------------------
# _stream_events regression coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_events_preserves_text_from_part_start_event():
    """Text emitted in PartStartEvent must not be dropped."""
    frontend = RecordingFrontend()
    agent = StaticEventAgent([
        PartStartEvent(index=0, part=TextPart(content="Hel")),
        PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="lo")),
    ])

    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    _, streamed_text = await _stream_events(
        agent,
        user_input="hello",
        deps=deps,
        message_history=[],
        model_settings={},
        usage_limits=UsageLimits(request_limit=5),
        usage=None,
        deferred_tool_results=None,
        verbose=False,
        frontend=frontend,
    )

    assert streamed_text is True
    assert ("text_commit", "Hello") in frontend.events


@pytest.mark.asyncio
async def test_stream_events_preserves_thinking_from_part_start_event():
    """Thinking emitted in PartStartEvent must not be dropped in verbose mode."""
    frontend = RecordingFrontend()
    agent = StaticEventAgent([
        PartStartEvent(index=0, part=ThinkingPart(content="Sure")),
        PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=", thing")),
    ])

    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    _, streamed_text = await _stream_events(
        agent,
        user_input="why",
        deps=deps,
        message_history=[],
        model_settings={},
        usage_limits=UsageLimits(request_limit=5),
        usage=None,
        deferred_tool_results=None,
        verbose=True,
        frontend=frontend,
    )

    assert streamed_text is False
    assert ("thinking_commit", "Sure, thing") in frontend.events


@pytest.mark.asyncio
async def test_stream_events_does_not_commit_text_on_final_result_event():
    """FinalResultEvent between text chunks must not split committed output."""
    frontend = RecordingFrontend()
    agent = StaticEventAgent([
        PartStartEvent(index=0, part=TextPart(content="The")),
        FinalResultEvent(tool_name=None, tool_call_id=None),
        PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" sky")),
    ])

    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    _, streamed_text = await _stream_events(
        agent,
        user_input="why",
        deps=deps,
        message_history=[],
        model_settings={},
        usage_limits=UsageLimits(request_limit=5),
        usage=None,
        deferred_tool_results=None,
        verbose=False,
        frontend=frontend,
    )

    assert streamed_text is True
    commits = [payload for kind, payload in frontend.events if kind == "text_commit"]
    assert commits == ["The sky"]


# ---------------------------------------------------------------------------
# finish_reason == "length" detection in run_turn()
# ---------------------------------------------------------------------------


def _make_agent_run_result(text: str, finish_reason: str) -> AgentRunResult:
    """Construct a minimal AgentRunResult with the given finish_reason."""
    state = GraphAgentState(message_history=[
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[TextPart(content=text)], finish_reason=finish_reason),
    ])
    return AgentRunResult(output=text, _state=state)


@pytest.mark.asyncio
async def test_run_turn_emits_truncation_status_on_finish_reason_length():
    """run_turn() must emit a status warning when finish_reason is 'length'."""
    result = _make_agent_run_result("partial answer", finish_reason="length")
    frontend = RecordingFrontend()
    agent = StaticEventAgent([AgentRunResultEvent(result=result)])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    turn = await run_turn(
        agent=agent,
        user_input="give me a very long answer",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    status_messages = [msg for kind, msg in frontend.events if kind == "status"]
    assert any("truncated" in msg for msg in status_messages)


@pytest.mark.asyncio
async def test_run_turn_silent_on_normal_finish_reason():
    """run_turn() must not emit a truncation warning when finish_reason is 'stop'."""
    result = _make_agent_run_result("complete answer", finish_reason="stop")
    frontend = RecordingFrontend()
    agent = StaticEventAgent([AgentRunResultEvent(result=result)])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    turn = await run_turn(
        agent=agent,
        user_input="tell me something",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    status_messages = [msg for kind, msg in frontend.events if kind == "status"]
    assert not any("truncated" in msg for msg in status_messages)


@pytest.mark.asyncio
async def test_stream_events_injects_status_before_first_tool_call():
    """When first event is a tool call with no prior text, on_status fires before on_tool_call."""
    frontend = RecordingFrontend()
    tool_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c1")
    agent = StaticEventAgent([FunctionToolCallEvent(part=tool_part)])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _stream_events(
        agent, user_input="hello", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    event_types = [e[0] for e in frontend.events]
    assert "status" in event_types
    assert "tool_call" in event_types
    status_idx = next(i for i, e in enumerate(frontend.events) if e[0] == "status")
    tool_call_idx = next(i for i, e in enumerate(frontend.events) if e[0] == "tool_call")
    assert status_idx < tool_call_idx


@pytest.mark.asyncio
async def test_stream_events_no_status_when_text_preceded_tool():
    """When model emits text before tool call, no fallback status injected."""
    frontend = RecordingFrontend()
    tool_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c2")
    agent = StaticEventAgent([
        PartStartEvent(index=0, part=TextPart(content="Let me check.")),
        FunctionToolCallEvent(part=tool_part),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _stream_events(
        agent, user_input="hello", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    assert all(e[0] != "status" for e in frontend.events)


# ---------------------------------------------------------------------------
# Bug-finding: preamble emitted exactly once for multiple tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_events_preamble_emitted_exactly_once_for_multiple_tools():
    """When model calls two tools with no preceding text, preamble fires exactly once.

    The tool_preamble_emitted flag guards subsequent calls. If the guard is
    broken, users would see two 'Co is thinking...' status lines.
    """
    frontend = RecordingFrontend()
    tool1 = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c1")
    tool2 = ToolCallPart(tool_name="web_search", args="{}", tool_call_id="c2")
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=tool1),
        FunctionToolCallEvent(part=tool2),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _stream_events(
        agent, user_input="hello", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    status_events = [e for e in frontend.events if e[0] == "status"]
    assert len(status_events) == 1, (
        f"Expected exactly 1 preamble status for 2 tool calls with no preceding text, "
        f"got {len(status_events)}: {status_events}"
    )


# ---------------------------------------------------------------------------
# Bug-finding: _patch_dangling_tool_calls edge cases
# ---------------------------------------------------------------------------


def test_patch_dangling_calls_already_answered_not_patched():
    """Calls that already have a ToolReturnPart are not patched again."""
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="recall_memory", content="memories", tool_call_id="c1")]),
    ]
    result = _patch_dangling_tool_calls(msgs)
    # No new messages added — c1 is already answered
    assert len(result) == len(msgs), (
        "Patching added messages for an already-answered tool call. "
        "The answered_ids check should have prevented this."
    )


def test_patch_dangling_calls_empty_returns_unchanged():
    """Empty message list is returned unchanged without error."""
    result = _patch_dangling_tool_calls([])
    assert result == []


def test_patch_dangling_calls_no_model_responses_unchanged():
    """Message list with no ModelResponse is returned unchanged."""
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
    ]
    result = _patch_dangling_tool_calls(msgs)
    assert result == msgs


def test_patch_dangling_calls_multiple_unanswered_in_one_response():
    """Multiple dangling calls from one ModelResponse all get patched.

    When a model response contains two tool calls and neither has a return,
    both must be patched in a single appended ModelRequest — not two separate ones.
    """
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[
            ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c1"),
            ToolCallPart(tool_name="web_search", args="{}", tool_call_id="c2"),
        ]),
    ]
    result = _patch_dangling_tool_calls(msgs)
    assert len(result) == len(msgs) + 1, "Expected exactly one appended ModelRequest"
    patch_msg = result[-1]
    assert isinstance(patch_msg, ModelRequest)
    patched_ids = {p.tool_call_id for p in patch_msg.parts if isinstance(p, ToolReturnPart)}
    assert patched_ids == {"c1", "c2"}, (
        f"Expected both c1 and c2 to be patched, got: {patched_ids}"
    )


def test_patch_dangling_calls_partial_answered():
    """Only the unanswered call is patched when one of two calls already has a return."""
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[
            ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c1"),
            ToolCallPart(tool_name="web_search", args="{}", tool_call_id="c2"),
        ]),
        # c1 answered, c2 dangling
        ModelRequest(parts=[ToolReturnPart(tool_name="recall_memory", content="result", tool_call_id="c1")]),
    ]
    result = _patch_dangling_tool_calls(msgs)
    assert len(result) == len(msgs) + 1
    patch_msg = result[-1]
    patched_ids = {p.tool_call_id for p in patch_msg.parts if isinstance(p, ToolReturnPart)}
    assert patched_ids == {"c2"}, (
        f"Expected only c2 (unanswered) to be patched, got: {patched_ids}"
    )


# ---------------------------------------------------------------------------
# Bug-finding: FunctionToolResultEvent display routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_events_shell_result_uses_command_as_title():
    """Shell command tool result panel title is the command, not 'run_shell_command'.

    When model calls run_shell_command(cmd='ls -la'), the result panel should be
    titled 'ls -la', not 'run_shell_command'. This requires pending_cmds lookup to
    work correctly between FunctionToolCallEvent and FunctionToolResultEvent.
    """
    frontend = RecordingFrontend()
    call_part = ToolCallPart(
        tool_name="run_shell_command", args={"cmd": "ls -la"}, tool_call_id="shell1"
    )
    return_part = ToolReturnPart(
        tool_name="run_shell_command", content="file.txt", tool_call_id="shell1"
    )
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=return_part),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _stream_events(
        agent, user_input="list files", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    result_events = [payload for kind, payload in frontend.events if kind == "tool_result"]
    assert len(result_events) == 1
    title, content = result_events[0]
    assert title == "ls -la", (
        f"Expected shell command as panel title, got {title!r}. "
        "pending_cmds lookup may be broken."
    )
    assert content == "file.txt"


@pytest.mark.asyncio
async def test_stream_events_empty_tool_result_not_shown():
    """Empty string tool result produces no on_tool_result call.

    A tool returning '' or whitespace has nothing to display.
    The panel must be silently suppressed, not shown with empty content.
    """
    frontend = RecordingFrontend()
    call_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="m1")
    return_part = ToolReturnPart(tool_name="recall_memory", content="", tool_call_id="m1")
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=return_part),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _stream_events(
        agent, user_input="hi", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    result_events = [e for e in frontend.events if e[0] == "tool_result"]
    assert result_events == [], (
        f"Expected no tool_result event for empty content, got: {result_events}"
    )


@pytest.mark.asyncio
async def test_stream_events_dict_result_without_display_not_shown():
    """Dict tool result missing 'display' key is silently suppressed.

    Per convention, tools returning dict must include 'display'. A dict
    without it violates the contract and the UI must not attempt to render it.
    """
    frontend = RecordingFrontend()
    call_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="m1")
    return_part = ToolReturnPart(
        tool_name="recall_memory",
        content={"count": 0, "items": []},  # valid but missing "display"
        tool_call_id="m1",
    )
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=return_part),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _stream_events(
        agent, user_input="hi", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    result_events = [e for e in frontend.events if e[0] == "tool_result"]
    assert result_events == [], (
        f"Expected no tool_result event for dict without 'display', got: {result_events}"
    )


def test_skill_grant_log(caplog: pytest.LogCaptureFixture) -> None:
    import logging as _logging
    from co_cli._orchestrate import _check_skill_grant
    from co_cli.deps import CoSessionState

    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
        session=CoSessionState(skill_tool_grants={"run_shell_command"}),
    )
    with caplog.at_level(_logging.DEBUG, logger="co_cli._orchestrate"):
        result = _check_skill_grant("run_shell_command", deps)
    assert result is True
    assert any(
        "Skill grant" in r.message and "run_shell_command" in r.message
        for r in caplog.records
    )
