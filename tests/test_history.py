"""Functional tests for history processors.

All tests use real objects — no mocks, no stubs.
LLM tests require a running provider (GEMINI_API_KEY or OLLAMA_HOST).
"""

import json

import pytest

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli._history import (
    _find_first_run_end,
    _static_marker,
    summarize_messages,
    truncate_tool_returns,
    truncate_history_window,
)


# ---------------------------------------------------------------------------
# Helpers — real pydantic-ai message objects
# ---------------------------------------------------------------------------


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_return(name: str, content: str | dict, call_id: str = "c1") -> ModelRequest:
    return ModelRequest(parts=[
        ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id),
    ])


def _tool_call_response(name: str, call_id: str = "c1") -> ModelResponse:
    return ModelResponse(parts=[
        ToolCallPart(tool_name=name, args="{}", tool_call_id=call_id),
    ])


def _real_run_context(model):
    """Build a real RunContext for truncate_history_window tests."""
    from pydantic_ai._run_context import RunContext
    from co_cli.deps import CoDeps
    from co_cli.sandbox import SubprocessBackend

    deps = CoDeps(
        sandbox=SubprocessBackend(),
        session_id="test-history",
    )
    return RunContext(
        deps=deps,
        model=model,
        usage=RunUsage(),
    )


# ---------------------------------------------------------------------------
# _find_first_run_end
# ---------------------------------------------------------------------------


def test_find_first_run_end_simple():
    """First ModelResponse with TextPart is index 1."""
    msgs: list[ModelMessage] = [_user("hi"), _assistant("hello")]
    assert _find_first_run_end(msgs) == 1


def test_find_first_run_end_with_tool_calls():
    """When first run includes tool calls, anchors on final text response."""
    msgs: list[ModelMessage] = [
        _user("search for X"),
        _tool_call_response("search_notes"),
        _tool_return("search_notes", "results..."),
        _assistant("Here are the results"),  # index 3
        _user("thanks"),
        _assistant("You're welcome"),
    ]
    assert _find_first_run_end(msgs) == 3


def test_find_first_run_end_no_text_response():
    """No TextPart response at all → returns 0."""
    msgs: list[ModelMessage] = [
        _user("do something"),
        _tool_call_response("run_shell"),
    ]
    assert _find_first_run_end(msgs) == 0


# ---------------------------------------------------------------------------
# _static_marker
# ---------------------------------------------------------------------------


def test_static_marker_is_valid_request():
    """Static marker is a ModelRequest with UserPromptPart."""
    marker = _static_marker(10)
    assert isinstance(marker, ModelRequest)
    assert len(marker.parts) == 1
    assert isinstance(marker.parts[0], UserPromptPart)
    assert "10 messages" in marker.parts[0].content


# ---------------------------------------------------------------------------
# truncate_tool_returns
# ---------------------------------------------------------------------------


def test_trim_short_content_unchanged(monkeypatch):
    """Content under threshold is not modified."""
    monkeypatch.setattr("co_cli.config.settings.tool_output_trim_chars", 2000)
    short = "x" * 100
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("search_notes", short),
        _assistant("answer"),
        _user("follow-up"),
        _assistant("done"),
    ]
    result = truncate_tool_returns(msgs)
    part = result[1].parts[0]
    assert part.content == short


def test_trim_long_string_truncated(monkeypatch):
    """Long string content is truncated with marker."""
    monkeypatch.setattr("co_cli.config.settings.tool_output_trim_chars", 50)
    long_content = "a" * 200
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("shell", long_content),
        _assistant("ok"),
        _user("next"),
        _assistant("done"),
    ]
    result = truncate_tool_returns(msgs)
    part = result[1].parts[0]
    assert isinstance(part.content, str)
    assert len(part.content) < 200
    assert "truncated" in part.content
    assert "200 chars" in part.content


def test_trim_dict_content_truncated(monkeypatch):
    """Dict content is JSON-serialised, truncated, becomes a string."""
    monkeypatch.setattr("co_cli.config.settings.tool_output_trim_chars", 30)
    big_dict = {"display": "x" * 200, "count": 5}
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("search_drive_files", big_dict),
        _assistant("ok"),
        _user("more"),
        _assistant("done"),
    ]
    result = truncate_tool_returns(msgs)
    part = result[1].parts[0]
    assert isinstance(part.content, str)
    assert "truncated" in part.content


def test_trim_last_exchange_protected(monkeypatch):
    """The last 2 messages (current turn) are never trimmed."""
    monkeypatch.setattr("co_cli.config.settings.tool_output_trim_chars", 10)
    long_content = "z" * 500
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("shell", long_content),
    ]
    result = truncate_tool_returns(msgs)
    part = result[1].parts[0]
    assert part.content == long_content  # unchanged


def test_trim_threshold_zero_disables(monkeypatch):
    """threshold=0 disables trimming entirely."""
    monkeypatch.setattr("co_cli.config.settings.tool_output_trim_chars", 0)
    long_content = "b" * 5000
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("shell", long_content),
        _assistant("ok"),
        _user("next"),
        _assistant("done"),
    ]
    result = truncate_tool_returns(msgs)
    part = result[1].parts[0]
    assert part.content == long_content


def test_trim_multiple_tool_returns_across_messages(monkeypatch):
    """Multiple tool returns across different older messages are all trimmed."""
    monkeypatch.setattr("co_cli.config.settings.tool_output_trim_chars", 20)
    msgs: list[ModelMessage] = [
        _user("q1"),
        _tool_return("shell", "x" * 100, call_id="c1"),
        _assistant("ok1"),
        _user("q2"),
        _tool_return("search_drive_files", "y" * 150, call_id="c2"),
        _assistant("ok2"),
        _user("q3"),                # last 2 — protected
        _assistant("final"),
    ]
    result = truncate_tool_returns(msgs)
    # Both old tool returns should be truncated
    part1 = result[1].parts[0]
    assert "truncated" in part1.content
    assert "100 chars" in part1.content
    part2 = result[4].parts[0]
    assert "truncated" in part2.content
    assert "150 chars" in part2.content


def test_trim_mixed_parts_in_request(monkeypatch):
    """A ModelRequest with both UserPromptPart and ToolReturnPart — only tool part trimmed."""
    monkeypatch.setattr("co_cli.config.settings.tool_output_trim_chars", 20)
    big_content = "z" * 300
    mixed_request = ModelRequest(parts=[
        UserPromptPart(content="some user text that should stay"),
        ToolReturnPart(tool_name="shell", content=big_content, tool_call_id="c1"),
    ])
    msgs: list[ModelMessage] = [
        mixed_request,
        _assistant("ok"),
        _user("next"),        # last 2 — protected
        _assistant("done"),
    ]
    result = truncate_tool_returns(msgs)
    # UserPromptPart untouched
    assert result[0].parts[0].content == "some user text that should stay"
    # ToolReturnPart truncated
    assert "truncated" in result[0].parts[1].content
    assert "300 chars" in result[0].parts[1].content


def test_trim_preserves_tool_name_and_call_id(monkeypatch):
    """After truncation, tool_name and tool_call_id are preserved."""
    monkeypatch.setattr("co_cli.config.settings.tool_output_trim_chars", 10)
    msgs: list[ModelMessage] = [
        _user("q"),
        ModelRequest(parts=[
            ToolReturnPart(
                tool_name="run_shell_command",
                content="x" * 500,
                tool_call_id="call_abc123",
            ),
        ]),
        _assistant("ok"),
        _user("next"),
        _assistant("done"),
    ]
    result = truncate_tool_returns(msgs)
    part = result[1].parts[0]
    assert part.tool_name == "run_shell_command"
    assert part.tool_call_id == "call_abc123"
    assert "truncated" in part.content


# ---------------------------------------------------------------------------
# summarize_messages — requires running LLM provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_messages():
    """summarize_messages returns a non-empty string summary.

    Requires a running LLM provider (GEMINI_API_KEY or OLLAMA_HOST).
    """
    from co_cli.agent import get_agent

    agent, _, _ = get_agent()

    msgs: list[ModelMessage] = [
        _user("What is Docker?"),
        _assistant("Docker is a containerisation platform that uses OS-level virtualisation."),
        _user("How do I install it on Ubuntu?"),
        _assistant("Run: sudo apt-get install docker-ce docker-ce-cli containerd.io"),
    ]
    summary = await summarize_messages(msgs, agent.model)
    assert isinstance(summary, str)
    assert len(summary) > 10
    # Summary should reference Docker — the core topic
    assert "docker" in summary.lower() or "container" in summary.lower()


@pytest.mark.asyncio
async def test_summarize_messages_preserves_file_paths():
    """Summary preserves file paths and tool names from the conversation.

    Requires a running LLM provider.
    """
    from co_cli.agent import get_agent

    agent, _, _ = get_agent()

    msgs: list[ModelMessage] = [
        _user("Search my notes for deployment"),
        _assistant("I found 3 notes matching 'deployment'."),
        _user("Read the file docs/deploy-guide.md"),
        _assistant("Here's the content of docs/deploy-guide.md: ...deploy to k8s cluster..."),
        _user("Now search drive for the Q4 report"),
        _assistant("Found 'Q4-2025-report.pdf' in Google Drive."),
    ]
    summary = await summarize_messages(msgs, agent.model)
    assert isinstance(summary, str)
    # The summary should mention at least one specific artifact
    text = summary.lower()
    assert (
        "deploy" in text
        or "q4" in text
        or "docs/" in text
    ), f"Summary should preserve key references, got: {summary}"


# ---------------------------------------------------------------------------
# truncate_history_window — requires running LLM provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncate_history_window_under_threshold(monkeypatch):
    """When messages are under the threshold, history is returned unchanged."""
    monkeypatch.setattr("co_cli.config.settings.max_history_messages", 100)

    from co_cli.agent import get_agent
    agent, _, _ = get_agent()

    msgs: list[ModelMessage] = [
        _user("hi"),
        _assistant("hello"),
        _user("how are you"),
        _assistant("fine"),
    ]

    ctx = _real_run_context(agent.model)
    result = await truncate_history_window(ctx, msgs)
    assert result is msgs  # identity — no copy, no change


@pytest.mark.asyncio
async def test_truncate_history_window_triggers_compaction(monkeypatch):
    """When messages exceed threshold, the middle is replaced with a summary.

    Requires a running LLM provider.
    """
    monkeypatch.setattr("co_cli.config.settings.max_history_messages", 6)
    monkeypatch.setattr("co_cli.config.settings.summarization_model", "")

    from co_cli.agent import get_agent
    agent, _, _ = get_agent()

    # Build 10 messages: first run (2) + 8 more
    msgs: list[ModelMessage] = [
        _user("What is Docker?"),
        _assistant("Docker is a containerisation platform."),
        _user("Tell me about images"),
        _assistant("Images are read-only templates."),
        _user("What about containers?"),
        _assistant("Containers are running instances of images."),
        _user("How do volumes work?"),
        _assistant("Volumes persist data outside containers."),
        _user("What about networks?"),
        _assistant("Networks connect containers together."),
    ]
    assert len(msgs) == 10

    ctx = _real_run_context(agent.model)
    result = await truncate_history_window(ctx, msgs)

    # Result must be shorter
    assert len(result) < len(msgs)
    # First two messages (head = first run) preserved
    assert result[0] is msgs[0]
    assert result[1] is msgs[1]
    # Last message preserved
    assert result[-1] is msgs[-1]
    # Summary marker exists in the middle
    found_summary = False
    for msg in result:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and (
                    "Summary of" in part.content or "trimmed" in part.content
                ):
                    found_summary = True
    assert found_summary, "Expected a summary or static marker in compacted history"


@pytest.mark.asyncio
async def test_truncate_history_window_output_is_valid_history(monkeypatch):
    """Compacted history is structurally valid — alternating request/response pattern.

    Requires a running LLM provider.
    """
    monkeypatch.setattr("co_cli.config.settings.max_history_messages", 6)
    monkeypatch.setattr("co_cli.config.settings.summarization_model", "")

    from co_cli.agent import get_agent
    agent, _, _ = get_agent()

    msgs: list[ModelMessage] = [
        _user("question 1"),
        _assistant("answer 1"),
        _user("question 2"),
        _assistant("answer 2"),
        _user("question 3"),
        _assistant("answer 3"),
        _user("question 4"),
        _assistant("answer 4"),
        _user("question 5"),
        _assistant("answer 5"),
    ]

    ctx = _real_run_context(agent.model)
    result = await truncate_history_window(ctx, msgs)

    # Every message is a real ModelRequest or ModelResponse
    for msg in result:
        assert isinstance(msg, (ModelRequest, ModelResponse)), (
            f"Expected ModelRequest or ModelResponse, got {type(msg).__name__}"
        )

    # Last message must be ModelResponse (so the next agent.run() can append a new request)
    assert isinstance(result[-1], ModelResponse), (
        "Last message in compacted history must be ModelResponse"
    )


# ---------------------------------------------------------------------------
# /compact via dispatch — requires running LLM provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compact_produces_two_message_history():
    """/compact returns a 2-message compacted history (summary + ack).

    Requires a running LLM provider.
    """
    from co_cli.agent import get_agent
    from co_cli.deps import CoDeps
    from co_cli.sandbox import SubprocessBackend
    from co_cli._commands import dispatch, CommandContext

    agent, _, tool_names = get_agent()
    deps = CoDeps(
        sandbox=SubprocessBackend(),
        session_id="test-compact",
    )

    history: list[ModelMessage] = [
        _user("What is Docker?"),
        _assistant("Docker is a containerisation platform."),
        _user("How do I install it?"),
        _assistant("Use apt-get install docker-ce on Ubuntu."),
        _user("What about volumes?"),
        _assistant("Volumes persist data outside containers."),
    ]
    ctx = CommandContext(
        message_history=history,
        deps=deps,
        agent=agent,
        tool_names=tool_names,
    )

    handled, new_history = await dispatch("/compact", ctx)
    assert handled is True
    assert new_history is not None
    assert len(new_history) == 2

    # First message: ModelRequest with summary content
    assert isinstance(new_history[0], ModelRequest)
    summary_part = new_history[0].parts[0]
    assert isinstance(summary_part, UserPromptPart)
    assert "Compacted conversation summary" in summary_part.content
    # Summary should reference Docker
    assert "docker" in summary_part.content.lower() or "container" in summary_part.content.lower()

    # Second message: ModelResponse acknowledgement
    assert isinstance(new_history[1], ModelResponse)
    ack_part = new_history[1].parts[0]
    assert isinstance(ack_part, TextPart)
    assert "Understood" in ack_part.content
