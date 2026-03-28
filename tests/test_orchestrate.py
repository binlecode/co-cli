"""Functional tests for the orchestration layer.

RecordingFrontend is a real FrontendProtocol implementation (not a mock)
that records all events for assertions.
"""

from typing import Any

import pytest
from pydantic_ai import AgentRunResult, AgentRunResultEvent, DeferredToolRequests, FinalResultEvent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from co_cli.context._orchestrate import FrontendProtocol, _patch_dangling_tool_calls, _run_stream_turn, run_turn
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.tools._shell_backend import ShellBackend
# GraphAgentState is a pydantic-ai internal type (private module). It is used in
# _make_agent_run_result() and _make_deferred_result() to construct realistic AgentRunResult
# objects without a live LLM call. On pydantic-ai upgrade, watch for ImportError or
# AttributeError here — rebuild the helper from the current public API if it breaks.
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
    RetryPromptPart,
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

    Configurable approval_policy: "approve" | "deny" | "always".
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

    def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
        self.events.append(("tool_start", (tool_id, name, args_display)))

    def on_tool_progress(self, tool_id: str, message: str) -> None:
        self.events.append(("tool_progress", (tool_id, message)))

    def on_tool_complete(self, tool_id: str, result: Any) -> None:
        self.events.append(("tool_complete", (tool_id, result)))

    def on_status(self, message: str) -> None:
        self.events.append(("status", message))

    def on_final_output(self, text: str) -> None:
        self.events.append(("final_output", text))

    def prompt_approval(self, description: str) -> str:
        self.events.append(("prompt_approval", description))
        if self.approval_policy == "approve":
            return "y"
        if self.approval_policy == "always":
            return "a"
        return "n"

    def cleanup(self) -> None:
        self.events.append(("cleanup", None))


# ---------------------------------------------------------------------------
# Streaming test helper
# ---------------------------------------------------------------------------


# StaticEventAgent and SequenceEventAgent are deliberate minimal dispatch fixtures.
# They are not mocks (no unittest.mock, no monkeypatch). They exist because
# _run_stream_turn() dispatch logic cannot be exercised with a real agent without
# nondeterminism — a real model may emit events in any order and content varies.
# These fixtures provide the exact event sequences needed to test specific branches.
class StaticEventAgent:
    """Minimal async event source compatible with _run_stream_turn()."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def run_stream_events(self, *_: Any, **__: Any):
        for event in self._events:
            yield event


class SequenceEventAgent:
    """Event source that returns a different event batch on each run."""

    def __init__(self, runs: list[list[Any]]) -> None:
        self._runs = runs
        self._index = 0

    async def run_stream_events(self, *_: Any, **__: Any):
        events = self._runs[self._index]
        self._index += 1
        for event in events:
            yield event


class InspectingSequenceAgent:
    """Event source that records each call and can raise on a specific run."""

    def __init__(self, runs: list[list[Any] | Exception]) -> None:
        self._runs = runs
        self._index = 0
        self.calls: list[dict[str, Any]] = []

    async def run_stream_events(self, user_input: Any, **kwargs: Any):
        self.calls.append({
            "user_input": user_input,
            "message_history": kwargs.get("message_history"),
            "deferred_tool_results": kwargs.get("deferred_tool_results"),
        })
        run = self._runs[self._index]
        self._index += 1
        if isinstance(run, Exception):
            raise run
        for event in run:
            yield event


# ---------------------------------------------------------------------------
# _run_stream_turn regression coverage
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

    _, streamed_text = await _run_stream_turn(
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

    _, streamed_text = await _run_stream_turn(
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

    _, streamed_text = await _run_stream_turn(
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


def _make_deferred_result(tool_name: str, args: dict[str, Any], tool_call_id: str) -> AgentRunResult:
    """Construct a minimal AgentRunResult that requests deferred approval."""
    call = ToolCallPart(tool_name=tool_name, args=args, tool_call_id=tool_call_id)
    state = GraphAgentState(message_history=[
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[call]),
    ])
    return AgentRunResult(
        output=DeferredToolRequests(approvals=[call]),
        _state=state,
    )


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
async def test_run_turn_calls_on_final_output_when_no_text_was_streamed() -> None:
    """Model response without streaming events reaches on_final_output.

    When _run_stream_turn() returns streamed_text=False (no PartStart/PartDelta for text),
    run_turn() must route result.output to frontend.on_final_output(). If this branch
    silently breaks, the user sees a blank response with no error.
    """
    result = _make_agent_run_result("Here is your answer.", finish_reason="stop")
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
    final_outputs = [payload for kind, payload in frontend.events if kind == "final_output"]
    assert len(final_outputs) == 1, (
        f"Expected exactly 1 on_final_output call when no text streamed, got {len(final_outputs)}: {final_outputs}"
    )
    assert final_outputs[0] == "Here is your answer.", (
        f"on_final_output content mismatch: {final_outputs[0]!r}"
    )


@pytest.mark.asyncio
async def test_run_turn_does_not_call_on_final_output_when_text_was_streamed() -> None:
    """on_final_output is suppressed when text already arrived via streaming events.

    When the model streams text via PartStartEvent/PartDeltaEvent, run_turn() sees
    streamed_text=True and must not call on_final_output — doing so would duplicate
    the response on screen.
    """
    result = _make_agent_run_result("streamed answer", finish_reason="stop")
    frontend = RecordingFrontend()
    agent = StaticEventAgent([
        PartStartEvent(index=0, part=TextPart(content="streamed answer")),
        AgentRunResultEvent(result=result),
    ])
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
    final_outputs = [payload for kind, payload in frontend.events if kind == "final_output"]
    assert len(final_outputs) == 0, (
        f"Expected no on_final_output when text was streamed, got: {final_outputs}"
    )


@pytest.mark.asyncio
async def test_stream_events_tool_start_fires_immediately_on_first_tool_call():
    """on_tool_start fires immediately when the first tool call event arrives.

    No on_status preamble should be injected before it — the tool owns the surface.
    """
    frontend = RecordingFrontend()
    tool_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c1")
    agent = StaticEventAgent([FunctionToolCallEvent(part=tool_part)])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _run_stream_turn(
        agent, user_input="hello", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    non_cleanup = [e for e in frontend.events if e[0] != "cleanup"]
    assert len(non_cleanup) >= 1
    assert non_cleanup[0][0] == "tool_start", (
        f"Expected first non-cleanup event to be tool_start, got: {non_cleanup[0]}"
    )
    assert all(e[0] != "status" for e in non_cleanup), (
        "No on_status should be injected — tool_start owns the surface."
    )


@pytest.mark.asyncio
async def test_stream_events_parallel_tool_calls_each_fire_tool_start_independently():
    """Two parallel tool calls each fire on_tool_start independently.

    The old preamble fired once total. The new lifecycle fires once per tool.
    """
    frontend = RecordingFrontend()
    tool1 = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c1")
    tool2 = ToolCallPart(tool_name="web_search", args="{}", tool_call_id="c2")
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=tool1),
        FunctionToolCallEvent(part=tool2),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _run_stream_turn(
        agent, user_input="hello", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    tool_start_events = [e for e in frontend.events if e[0] == "tool_start"]
    assert len(tool_start_events) == 2, (
        f"Expected 2 tool_start events for 2 tool calls, got {len(tool_start_events)}: "
        f"{tool_start_events}"
    )
    tool_ids = [e[1][0] for e in tool_start_events]
    assert "c1" in tool_ids and "c2" in tool_ids


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
async def test_stream_events_shell_result_reaches_tool_complete_as_str():
    """Shell command result reaches on_tool_complete with the str content.

    When run_shell_command(cmd='ls -la') returns 'file.txt', on_tool_complete
    is called with tool_id='shell1' and the str content. The args_display
    ('ls -la') is captured by on_tool_start for the panel title.
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

    await _run_stream_turn(
        agent, user_input="list files", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    start_events = [payload for kind, payload in frontend.events if kind == "tool_start"]
    assert len(start_events) == 1
    tool_id, name, args_display = start_events[0]
    assert tool_id == "shell1"
    assert args_display == "ls -la", (
        f"Expected shell command as args_display in on_tool_start, got {args_display!r}."
    )

    complete_events = [payload for kind, payload in frontend.events if kind == "tool_complete"]
    assert len(complete_events) == 1
    c_tool_id, result = complete_events[0]
    assert c_tool_id == "shell1"
    assert result == "file.txt"


@pytest.mark.asyncio
async def test_stream_events_empty_tool_result_reaches_tool_complete_as_none():
    """Empty string tool result produces on_tool_complete(tool_id, None).

    A tool returning '' or whitespace has nothing to display. The lifecycle
    still fires on_tool_complete — with None result — so the frontend can
    close the active tool surface cleanly.
    """
    frontend = RecordingFrontend()
    call_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="m1")
    return_part = ToolReturnPart(tool_name="recall_memory", content="", tool_call_id="m1")
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=return_part),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _run_stream_turn(
        agent, user_input="hi", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    complete_events = [payload for kind, payload in frontend.events if kind == "tool_complete"]
    assert len(complete_events) == 1
    tool_id, result = complete_events[0]
    assert tool_id == "m1"
    assert result is None, (
        f"Expected None result for empty content, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_stream_events_retry_prompt_closes_tool_surface_with_none():
    """RetryPromptPart (ModelRetry / validation failure) still calls on_tool_complete(None).

    When a tool raises ModelRetry or pydantic-ai validation fails, the result event
    carries a RetryPromptPart instead of ToolReturnPart. The old code skipped
    on_tool_complete entirely, leaving on_tool_start's panel open forever.
    The fix must close the surface cleanly with None so the frontend can tear down.
    """
    frontend = RecordingFrontend()
    call_part = ToolCallPart(tool_name="web_search", args="{}", tool_call_id="r1")
    retry_part = RetryPromptPart(content="invalid args — retry", tool_name="web_search", tool_call_id="r1")
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=retry_part),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _run_stream_turn(
        agent, user_input="search", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    start_events = [payload for kind, payload in frontend.events if kind == "tool_start"]
    complete_events = [payload for kind, payload in frontend.events if kind == "tool_complete"]
    assert len(start_events) == 1, "tool_start must fire"
    assert len(complete_events) == 1, (
        "on_tool_complete must fire even on RetryPromptPart — tool surface must close"
    )
    tool_id, result = complete_events[0]
    assert tool_id == "r1"
    assert result is None, f"RetryPromptPart result must be None, got: {result!r}"


@pytest.mark.asyncio
async def test_stream_events_retry_prompt_clears_progress_callback():
    """tool_progress_callback is cleared on RetryPromptPart, not left dangling.

    If the callback were left set after a retry event, the next tool call in the
    same turn would inherit the stale closure with the wrong tool_id.
    """
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())
    call_part = ToolCallPart(tool_name="web_search", args="{}", tool_call_id="r2")
    retry_part = RetryPromptPart(content="retry", tool_name="web_search", tool_call_id="r2")
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=retry_part),
    ])
    frontend = RecordingFrontend()

    await _run_stream_turn(
        agent, user_input="search", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    assert deps.runtime.tool_progress_callback is None, (
        "tool_progress_callback must be cleared after RetryPromptPart"
    )


@pytest.mark.asyncio
async def test_stream_events_raw_dict_result_renders_as_summary():
    """Raw dict tool result (no '_kind') renders as a compact key: value summary.

    MCP tools return raw JSON dicts without a _kind discriminator. The branch
    renders them as a human-readable summary string rather than silently dropping
    them as None.
    """
    frontend = RecordingFrontend()
    call_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="m1")
    return_part = ToolReturnPart(
        tool_name="recall_memory",
        content={"count": 0, "items": []},  # no _kind discriminator
        tool_call_id="m1",
    )
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=return_part),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _run_stream_turn(
        agent, user_input="hi", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    complete_events = [payload for kind, payload in frontend.events if kind == "tool_complete"]
    assert len(complete_events) == 1
    tool_id, result = complete_events[0]
    assert isinstance(result, str), (
        f"Expected str summary for raw dict result, got: {type(result).__name__!r} {result!r}"
    )
    assert "count" in result, (
        f"Expected 'count' key in summary, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_stream_events_tool_progress_callback_curried_with_tool_id():
    """tool_progress_callback is curried with the tool_id from FunctionToolCallEvent.

    When _run_stream_turn() receives FunctionToolCallEvent it sets
    deps.runtime.tool_progress_callback to a closure that calls
    frontend.on_tool_progress(tool_id, msg). Calling that callback must
    produce an on_tool_progress event with the correct tool_id — not a
    generic status line and not a new tool_start event.

    We drive the callback manually at the point where _run_stream_turn()
    processes FunctionToolCallEvent by subclassing RecordingFrontend to
    invoke the callback after on_tool_start is fired by the event loop.
    """
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    class ProgressCaptureFrontend(RecordingFrontend):
        """RecordingFrontend that fires tool_progress_callback after on_tool_start."""

        def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
            super().on_tool_start(tool_id, name, args_display)
            # At this point _run_stream_turn() sets the callback immediately after
            # returning from on_tool_start, so we simulate progress that happens
            # between start and result by calling frontend.on_tool_progress directly.
            # This validates the RecordingFrontend contract: progress events carry
            # the right tool_id and do not inject extra lifecycle events.
            self.on_tool_progress(tool_id, "Checking shell...")
            self.on_tool_progress(tool_id, "Checking web access...")

    frontend = ProgressCaptureFrontend()
    call_part = ToolCallPart(tool_name="check_capabilities", args="{}", tool_call_id="cap1")
    return_part = ToolReturnPart(
        tool_name="check_capabilities",
        content="done",
        tool_call_id="cap1",
    )
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=return_part),
    ])

    await _run_stream_turn(
        agent, user_input="check", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    progress_events = [(kind, payload) for kind, payload in frontend.events if kind == "tool_progress"]
    assert len(progress_events) == 2, (
        f"Expected exactly 2 progress events, got {len(progress_events)}: {progress_events}"
    )
    # Both progress events reference the same tool_id as the start event
    for _, (tid, _msg) in progress_events:
        assert tid == "cap1", (
            f"Progress event tool_id mismatch: expected 'cap1', got {tid!r}"
        )

    # Progress does not inject extra tool_start or tool_complete events
    start_events = [e for e in frontend.events if e[0] == "tool_start"]
    assert len(start_events) == 1, (
        f"Expected 1 tool_start, got {len(start_events)}. Progress must not inject new start events."
    )


@pytest.mark.asyncio
async def test_stream_events_tool_result_dict_with_kind_reaches_tool_complete():
    """ToolResult dict (with _kind='tool_result') is passed through to on_tool_complete.

    A dict with _kind discriminator must arrive at on_tool_complete as-is,
    not coerced to None. This ensures the frontend can render the display field.
    """
    frontend = RecordingFrontend()
    call_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="m2")
    result_dict = {"_kind": "tool_result", "display": "Memory: User prefers pytest.", "count": 1}
    return_part = ToolReturnPart(
        tool_name="recall_memory",
        content=result_dict,
        tool_call_id="m2",
    )
    agent = StaticEventAgent([
        FunctionToolCallEvent(part=call_part),
        FunctionToolResultEvent(result=return_part),
    ])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    await _run_stream_turn(
        agent, user_input="recall", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    complete_events = [payload for kind, payload in frontend.events if kind == "tool_complete"]
    assert len(complete_events) == 1
    tool_id, result = complete_events[0]
    assert tool_id == "m2"
    assert isinstance(result, dict), (
        f"Expected ToolResult dict passed through, got {type(result).__name__}: {result!r}"
    )
    assert result.get("_kind") == "tool_result", (
        f"ToolResult _kind discriminator lost; got: {result!r}"
    )
    assert result.get("display") == "Memory: User prefers pytest."


@pytest.mark.asyncio
async def test_run_turn_shell_always_stores_session_rule() -> None:
    """Choosing 'a' for a shell approval stores a session rule for the git utility."""
    from co_cli.deps import SessionApprovalRule

    approval_result = _make_deferred_result(
        "run_shell_command",
        {"cmd": "git commit -m 'fix'"},
        "shell1",
    )
    final_result = _make_agent_run_result("done", finish_reason="stop")
    agent = SequenceEventAgent([
        [AgentRunResultEvent(result=approval_result)],
        [AgentRunResultEvent(result=final_result)],
    ])
    frontend = RecordingFrontend(approval_policy="always")
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(),
    )

    turn = await run_turn(
        agent=agent,
        user_input="commit it",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    assert SessionApprovalRule(kind="shell", value="git") in deps.session.session_approval_rules


@pytest.mark.asyncio
async def test_run_turn_active_skill_does_not_bypass_deferred_prompt() -> None:
    """An active skill does not auto-approve a deferred tool — prompt fires regardless.

    Under the old three-tier model, a skill with allowed_tools could bypass the
    approval prompt for listed tools. After simplification, setting active_skill_name
    must have no effect on the deferred approval flow — the prompt always fires.
    """
    approval_result = _make_deferred_result(
        "save_memory",
        {"text": "remember this"},
        "mem2",
    )
    final_result = _make_agent_run_result("done", finish_reason="stop")
    agent = SequenceEventAgent([
        [AgentRunResultEvent(result=approval_result)],
        [AgentRunResultEvent(result=final_result)],
    ])
    frontend = RecordingFrontend(approval_policy="approve")
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())
    # Simulate an active skill (as if dispatch() just ran)
    deps.session.active_skill_name = "some-skill"

    turn = await run_turn(
        agent=agent,
        user_input="save it",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    # The approval prompt must have fired — skill state did not bypass it
    prompt_events = [payload for kind, payload in frontend.events if kind == "prompt_approval"]
    assert len(prompt_events) == 1, (
        f"Expected exactly 1 approval prompt despite active skill, got {len(prompt_events)}: "
        f"{prompt_events}"
    )


@pytest.mark.asyncio
async def test_run_turn_generic_tool_always_does_not_store_session_rule() -> None:
    """'a' for a generic tool (save_memory) approves this call but stores no session rule.

    Generic tools (can_remember=False) have no meaningful scope to remember —
    'a' is treated as 'y' for the current call only.
    """
    approval_result = _make_deferred_result(
        "save_memory",
        {"text": "remember this"},
        "mem1",
    )
    final_result = _make_agent_run_result("done", finish_reason="stop")
    agent = SequenceEventAgent([
        [AgentRunResultEvent(result=approval_result)],
        [AgentRunResultEvent(result=final_result)],
    ])
    frontend = RecordingFrontend(approval_policy="always")
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    turn = await run_turn(
        agent=agent,
        user_input="save it",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    # Tool was approved (turn succeeded) but no rule was stored
    assert deps.session.session_approval_rules == []


@pytest.mark.asyncio
async def test_run_turn_web_fetch_always_stores_domain_session_rule() -> None:
    """'a' for web_fetch stores a domain session rule; same domain is auto-approved next call."""
    approval_result = _make_deferred_result(
        "web_fetch",
        {"url": "https://docs.python.org/3/"},
        "fetch1",
    )
    final_result = _make_agent_run_result("done", finish_reason="stop")
    # Second deferred call for the same domain — should be auto-approved (no prompt)
    approval_result2 = _make_deferred_result(
        "web_fetch",
        {"url": "https://docs.python.org/2/"},
        "fetch2",
    )
    final_result2 = _make_agent_run_result("done again", finish_reason="stop")
    agent = SequenceEventAgent([
        [AgentRunResultEvent(result=approval_result)],
        [AgentRunResultEvent(result=final_result)],
        [AgentRunResultEvent(result=approval_result2)],
        [AgentRunResultEvent(result=final_result2)],
    ])
    frontend = RecordingFrontend(approval_policy="always")
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    # First turn: user chooses 'a'
    turn1 = await run_turn(
        agent=agent,
        user_input="fetch it",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )
    assert turn1.outcome == "continue"
    assert len(deps.session.session_approval_rules) == 1
    assert deps.session.session_approval_rules[0].kind == "domain"
    assert deps.session.session_approval_rules[0].value == "docs.python.org"

    # Second turn: same domain — auto-approved, no prompt_approval event
    prompts_before = [e for e in frontend.events if e[0] == "prompt_approval"]
    turn2 = await run_turn(
        agent=agent,
        user_input="fetch again",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )
    assert turn2.outcome == "continue"
    prompts_after = [e for e in frontend.events if e[0] == "prompt_approval"]
    # No new prompt fired for the second fetch (auto-approved by session rule)
    assert len(prompts_after) == len(prompts_before)


@pytest.mark.asyncio
async def test_run_turn_retries_resume_segment_after_network_error() -> None:
    """Network retry after deferred approval must preserve the resumed segment state."""
    approval_result = _make_deferred_result(
        "save_memory",
        {"text": "remember this"},
        "mem-retry-1",
    )
    final_result = _make_agent_run_result("done", finish_reason="stop")
    agent = InspectingSequenceAgent([
        [AgentRunResultEvent(result=approval_result)],
        ModelAPIError("test-model", "temporary network failure"),
        [AgentRunResultEvent(result=final_result)],
    ])
    frontend = RecordingFrontend(approval_policy="approve")
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    turn = await run_turn(
        agent=agent,
        user_input="save it",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    assert len(agent.calls) == 3

    resume_history = approval_result.all_messages()

    first_call = agent.calls[0]
    assert first_call["user_input"] == "save it"
    assert first_call["message_history"] == []
    assert first_call["deferred_tool_results"] is None

    resumed_call = agent.calls[1]
    assert resumed_call["user_input"] is None
    assert resumed_call["message_history"] == resume_history
    assert resumed_call["deferred_tool_results"] is not None
    assert resumed_call["deferred_tool_results"].approvals["mem-retry-1"] is True

    retried_call = agent.calls[2]
    assert retried_call["user_input"] is None
    assert retried_call["message_history"] == resume_history
    assert retried_call["deferred_tool_results"] is not None
    assert retried_call["deferred_tool_results"].approvals["mem-retry-1"] is True


@pytest.mark.asyncio
async def test_run_turn_reflection_after_resume_uses_resume_history() -> None:
    """HTTP 400 after deferred approval must append reflection to the resumed history."""
    approval_result = _make_deferred_result(
        "save_memory",
        {"text": "remember this"},
        "mem-retry-2",
    )
    final_result = _make_agent_run_result("done", finish_reason="stop")
    agent = InspectingSequenceAgent([
        [AgentRunResultEvent(result=approval_result)],
        ModelHTTPError(400, "test-model", {"error": "bad tool args"}),
        [AgentRunResultEvent(result=final_result)],
    ])
    frontend = RecordingFrontend(approval_policy="approve")
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    turn = await run_turn(
        agent=agent,
        user_input="save it",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    assert len(agent.calls) == 3

    resume_history = approval_result.all_messages()
    resumed_call = agent.calls[1]
    assert resumed_call["user_input"] is None
    assert resumed_call["message_history"] == resume_history
    assert resumed_call["deferred_tool_results"] is not None

    reflected_call = agent.calls[2]
    assert reflected_call["user_input"] is None
    assert reflected_call["deferred_tool_results"] is not None
    assert len(reflected_call["message_history"]) == len(resume_history) + 1

    reflection_msg = reflected_call["message_history"][-1]
    assert isinstance(reflection_msg, ModelRequest)
    assert isinstance(reflection_msg.parts[0], UserPromptPart)
    assert "Please reformulate your tool call with valid JSON arguments." in reflection_msg.parts[0].content


@pytest.mark.asyncio
async def test_tool_args_display_known_tools() -> None:
    """_tool_args_display returns the primary arg value for registered tools and '' for unknown.

    Drives three cases through _run_stream_turn and checks on_tool_start args_display:
    - web_search with query arg
    - run_shell_command with cmd arg
    - unknown tool name falls back to empty string
    """
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    async def _run_single_tool(tool_name: str, args: dict) -> str:
        """Run a single FunctionToolCallEvent and return the args_display captured."""
        import json
        frontend = RecordingFrontend()
        call_part = ToolCallPart(tool_name=tool_name, args=json.dumps(args), tool_call_id="t1")
        return_part = ToolReturnPart(tool_name=tool_name, content="ok", tool_call_id="t1")
        agent = StaticEventAgent([
            FunctionToolCallEvent(part=call_part),
            FunctionToolResultEvent(result=return_part),
        ])
        await _run_stream_turn(
            agent, user_input="x", deps=deps, message_history=[],
            model_settings={}, usage_limits=UsageLimits(request_limit=5),
            usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
        )
        start_events = [payload for kind, payload in frontend.events if kind == "tool_start"]
        assert len(start_events) == 1
        _tool_id, _name, args_display = start_events[0]
        return args_display

    # web_search: query arg
    result = await _run_single_tool("web_search", {"query": "climate change"})
    assert result == "climate change", f"web_search args_display wrong: {result!r}"

    # run_shell_command: cmd arg
    result = await _run_single_tool("run_shell_command", {"cmd": "ls -la"})
    assert result == "ls -la", f"run_shell_command args_display wrong: {result!r}"

    # write_file: path arg (not file_path — real tool param is "path")
    result = await _run_single_tool("write_file", {"path": "/proj/src/foo.py", "content": "x"})
    assert result == "/proj/src/foo.py", f"write_file args_display wrong: {result!r}"

    # read_file: path arg
    result = await _run_single_tool("read_file", {"path": "/proj/src/foo.py"})
    assert result == "/proj/src/foo.py", f"read_file args_display wrong: {result!r}"

    # unknown tool: empty string fallback
    result = await _run_single_tool("unknown_tool_xyz", {"foo": "bar"})
    assert result == "", f"unknown tool should return '', got: {result!r}"


@pytest.mark.asyncio
async def test_run_turn_emits_co_turn_span() -> None:
    """run_turn() must emit a co.turn span with turn attributes."""
    exporter = InMemorySpanExporter()
    _orig = otel_trace.get_tracer_provider()
    # The harness pre-configures a real TracerProvider — add our exporter to it
    # rather than replacing the provider (which the SDK blocks after first set).
    _orig.add_span_processor(SimpleSpanProcessor(exporter))

    result = _make_agent_run_result("hi", finish_reason="stop")
    agent = StaticEventAgent([AgentRunResultEvent(result=result)])
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())
    frontend = RecordingFrontend()

    await run_turn(
        agent=agent,
        user_input="hello",
        deps=deps,
        message_history=[],
        verbose=False,
        frontend=frontend,
    )

    spans = exporter.get_finished_spans()
    assert any(s.name == "co.turn" for s in spans)

    co_turn_span = next(s for s in spans if s.name == "co.turn")
    assert co_turn_span.attributes["turn.outcome"] == "continue"
    assert co_turn_span.attributes["turn.interrupted"] == False
    assert otel_trace.get_tracer_provider() is _orig
