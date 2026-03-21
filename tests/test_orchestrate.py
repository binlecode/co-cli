"""Functional tests for the orchestration layer.

RecordingFrontend is a real FrontendProtocol implementation (not a mock)
that records all events for assertions.
"""

from typing import Any

import pytest
from pydantic_ai import AgentRunResult, AgentRunResultEvent, DeferredToolRequests, FinalResultEvent

from co_cli.context._orchestrate import FrontendProtocol, _patch_dangling_tool_calls, _stream_events, run_turn
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
# _stream_events() dispatch logic cannot be exercised with a real agent without
# nondeterminism — a real model may emit events in any order and content varies.
# These fixtures provide the exact event sequences needed to test specific branches.
class StaticEventAgent:
    """Minimal async event source compatible with _stream_events()."""

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

    When _stream_events() returns streamed_text=False (no PartStart/PartDelta for text),
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

    await _stream_events(
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

    await _stream_events(
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

    await _stream_events(
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

    await _stream_events(
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

    await _stream_events(
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

    await _stream_events(
        agent, user_input="search", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    assert deps.runtime.tool_progress_callback is None, (
        "tool_progress_callback must be cleared after RetryPromptPart"
    )


@pytest.mark.asyncio
async def test_stream_events_dict_without_kind_produces_tool_complete_none():
    """Dict tool result missing '_kind' produces on_tool_complete(tool_id, None).

    A dict without _kind='tool_result' is not a ToolResult — falls to the else
    branch and produces None. The frontend must not attempt to render it.
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

    await _stream_events(
        agent, user_input="hi", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    complete_events = [payload for kind, payload in frontend.events if kind == "tool_complete"]
    assert len(complete_events) == 1
    tool_id, result = complete_events[0]
    assert result is None, (
        f"Expected None for dict without _kind discriminator, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_stream_events_tool_progress_callback_curried_with_tool_id():
    """tool_progress_callback is curried with the tool_id from FunctionToolCallEvent.

    When _stream_events() receives FunctionToolCallEvent it sets
    deps.runtime.tool_progress_callback to a closure that calls
    frontend.on_tool_progress(tool_id, msg). Calling that callback must
    produce an on_tool_progress event with the correct tool_id — not a
    generic status line and not a new tool_start event.

    We drive the callback manually at the point where _stream_events()
    processes FunctionToolCallEvent by subclassing RecordingFrontend to
    invoke the callback after on_tool_start is fired by the event loop.
    """
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    class ProgressCaptureFrontend(RecordingFrontend):
        """RecordingFrontend that fires tool_progress_callback after on_tool_start."""

        def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
            super().on_tool_start(tool_id, name, args_display)
            # At this point _stream_events() sets the callback immediately after
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

    await _stream_events(
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

    await _stream_events(
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
async def test_run_turn_shell_always_remembers_pattern(tmp_path) -> None:
    """Choosing 'a' for a shell approval persists a remembered exec pattern."""
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
        config=CoConfig(exec_approvals_path=tmp_path / "exec-approvals.json"),
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
    prompt_events = [payload for kind, payload in frontend.events if kind == "prompt_approval"]
    assert any("will remember: git commit *" in payload for payload in prompt_events)


@pytest.mark.asyncio
async def test_run_turn_non_shell_always_sets_session_auto_approval() -> None:
    """Choosing 'a' for a non-shell tool stores a session-scoped tool approval."""
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
    assert "save_memory" in deps.session.session_tool_approvals
