"""Functional tests for the orchestration layer.

RecordingFrontend is a real FrontendProtocol implementation (not a mock)
that records all events for assertions.
"""

from typing import Any

import pytest
from pydantic_ai import FinalResultEvent

from co_cli._orchestrate import FrontendProtocol, _stream_events
from co_cli.deps import CoDeps
from co_cli.sandbox import SubprocessBackend
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
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
# Protocol compliance
# ---------------------------------------------------------------------------


def test_terminal_frontend_is_protocol_compliant():
    """TerminalFrontend satisfies FrontendProtocol at runtime."""
    from co_cli.display import TerminalFrontend
    frontend = TerminalFrontend()
    assert isinstance(frontend, FrontendProtocol)


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

    deps = CoDeps(sandbox=SubprocessBackend())

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

    deps = CoDeps(sandbox=SubprocessBackend())

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

    deps = CoDeps(sandbox=SubprocessBackend())

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
