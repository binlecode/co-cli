"""Functional tests for the orchestration layer.

RecordingFrontend is a real Frontend implementation (not a mock)
that records all events for assertions.
"""

from typing import Any

import pytest
from pydantic_ai import AgentRunResult, AgentRunResultEvent, DeferredToolRequests

from co_cli.context._orchestrate import _TurnState, _execute_stream_segment, run_turn, _run_approval_loop
from co_cli.config import DEFAULT_REASONING_DISPLAY
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.tools._shell_backend import ShellBackend
# GraphAgentState is a pydantic-ai internal type (private module). It is used in
# _make_agent_run_result() and _make_deferred_result() to construct realistic AgentRunResult
# objects without a live LLM call. On pydantic-ai upgrade, watch for ImportError or
# AttributeError here — rebuild the helper from the current public API if it breaks.
from pydantic_ai._agent_graph import GraphAgentState
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)


# ---------------------------------------------------------------------------
# RecordingFrontend — real Frontend for tests
# ---------------------------------------------------------------------------


# StaticEventAgent and RecordingFrontend are deliberate minimal dispatch fixtures.
# They are not mocks (no unittest.mock, no monkeypatch). They implement the real
# interface and exist because the agent's stream events cannot be exercised with a
# real LLM call without nondeterminism.
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

    def on_reasoning_progress(self, text: str) -> None:
        self.events.append(("reasoning_progress", text))

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


class StaticEventAgent:
    """Async event source that yields a fixed event sequence on every call.

    Use for single-segment tests where the same deterministic event list is sufficient.
    """

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def run_stream_events(self, *_: Any, **__: Any):
        for event in self._events:
            yield event


# ---------------------------------------------------------------------------
# Test helpers — construct realistic AgentRunResult without a live LLM call
# ---------------------------------------------------------------------------


def _make_agent_run_result(text: str, finish_reason: str) -> AgentRunResult:
    """Construct a minimal AgentRunResult with the given finish_reason."""
    state = GraphAgentState(message_history=[
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[TextPart(content=text)], finish_reason=finish_reason),
    ])
    return AgentRunResult(output=text, _state=state)


def _make_deferred_result(tool_name: str, args: str, tool_call_id: str) -> AgentRunResult:
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


# ---------------------------------------------------------------------------
# _run_approval_loop — active_tool_filter lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_loop_sets_active_filter():
    """_run_approval_loop clears active_tool_filter to None after the loop exits.

    Verifies the filter lifecycle: set per-hop inside the loop, cleared when
    DeferredToolRequests is exhausted. The filter mechanism itself (get_tools
    respects the filter) is exercised by test_agent.py::test_active_filter_*.
    """
    deferred_result = _make_deferred_result("run_shell_command", "{}", "tc1")
    text_result = _make_agent_run_result("done", "stop")

    frontend = RecordingFrontend(approval_policy="approve")
    deps = CoDeps(services=CoServices(shell=ShellBackend()), config=CoConfig())

    # Resume segment yields a string result — loop exits after one iteration
    resume_agent = StaticEventAgent([AgentRunResultEvent(result=text_result)])

    turn_state = _TurnState(
        current_input=None,
        current_history=[],
        latest_result=deferred_result,
    )

    await _run_approval_loop(
        turn_state, resume_agent, deps, None, DEFAULT_REASONING_DISPLAY, frontend
    )

    assert deps.runtime.active_tool_filter is None
