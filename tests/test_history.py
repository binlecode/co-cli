"""Functional tests for history processors.

All tests use real objects — no mocks, no stubs.
LLM tests require a running provider (GEMINI_API_KEY or OLLAMA_HOST).
"""

import asyncio
from pathlib import Path

import pytest

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli.context._history import (
    summarize_messages,
    truncate_tool_returns,
    truncate_history_window,
)
from co_cli.agent import build_agent
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import settings as _settings, ROLE_REASONING
from co_cli.deps import CoConfig
from tests._ollama import ensure_ollama_warm

_CONFIG = CoConfig.from_settings(_settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
_REASONING_MODEL = _CONFIG.role_models[ROLE_REASONING].model
_SUMMARIZATION_MODEL = _CONFIG.role_models["summarization"].model
_RESOLVED_MODEL = _REGISTRY.get(ROLE_REASONING, ResolvedModel(model=None, settings=None)).model
_AGENT, _, _ = build_agent(config=CoConfig.from_settings(_settings, cwd=Path.cwd()))


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


def _real_run_context(model, *, max_history_messages=40, tool_output_trim_chars=2000):
    """Build a real RunContext for history processor tests."""
    from pydantic_ai._run_context import RunContext
    from co_cli.deps import CoDeps, CoServices, CoConfig
    from co_cli.tools._shell_backend import ShellBackend

    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=CoConfig(
            max_history_messages=max_history_messages,
            tool_output_trim_chars=tool_output_trim_chars,
        ),
    )
    return RunContext(
        deps=deps,
        model=model,
        usage=RunUsage(),
    )


# ---------------------------------------------------------------------------
# truncate_tool_returns
# ---------------------------------------------------------------------------


def test_trim_long_string_truncated():
    """Long string content is truncated with marker."""
    ctx = _real_run_context(FunctionModel(lambda m, i: None), tool_output_trim_chars=50)
    long_content = "a" * 200
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("shell", long_content),
        _assistant("ok"),
        _user("next"),
        _assistant("done"),
    ]
    result = truncate_tool_returns(ctx, msgs)
    part = result[1].parts[0]
    assert isinstance(part.content, str)
    assert len(part.content) < 200
    assert "truncated" in part.content
    assert "200 chars" in part.content


def test_trim_dict_content_truncated():
    """Dict content is JSON-serialised, truncated, becomes a string."""
    ctx = _real_run_context(FunctionModel(lambda m, i: None), tool_output_trim_chars=30)
    big_dict = {"display": "x" * 200, "count": 5}
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("search_drive_files", big_dict),
        _assistant("ok"),
        _user("more"),
        _assistant("done"),
    ]
    result = truncate_tool_returns(ctx, msgs)
    part = result[1].parts[0]
    assert isinstance(part.content, str)
    assert "truncated" in part.content


def test_trim_last_exchange_protected():
    """The last 2 messages (current turn) are never trimmed."""
    ctx = _real_run_context(FunctionModel(lambda m, i: None), tool_output_trim_chars=10)
    long_content = "z" * 500
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("shell", long_content),
    ]
    result = truncate_tool_returns(ctx, msgs)
    part = result[1].parts[0]
    assert part.content == long_content


def test_trim_preserves_tool_name_and_call_id():
    """After truncation, tool_name and tool_call_id are preserved."""
    ctx = _real_run_context(FunctionModel(lambda m, i: None), tool_output_trim_chars=10)
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
    result = truncate_tool_returns(ctx, msgs)
    part = result[1].parts[0]
    assert part.tool_name == "run_shell_command"
    assert part.tool_call_id == "call_abc123"
    assert "truncated" in part.content


def test_trim_zero_threshold_disables_truncation():
    """threshold=0 disables truncation entirely (returns messages unchanged)."""
    ctx = _real_run_context(FunctionModel(lambda m, i: None), tool_output_trim_chars=0)
    huge_content = "a" * 100_000
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("shell", huge_content),
        _assistant("ok"),
        _user("next"),
        _assistant("done"),
    ]
    result = truncate_tool_returns(ctx, msgs)
    part = result[1].parts[0]
    assert part.content == huge_content, (
        "threshold=0 should disable truncation, but content was modified"
    )


# ---------------------------------------------------------------------------
# summarize_messages — requires running LLM provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_messages():
    """summarize_messages returns a non-empty string summary.

    Requires a running LLM provider (GEMINI_API_KEY or OLLAMA_HOST).
    """
    msgs: list[ModelMessage] = [
        _user("What is Docker?"),
        _assistant("Docker is a containerisation platform that uses OS-level virtualisation."),
        _user("How do I install it on Ubuntu?"),
        _assistant("Run: sudo apt-get install docker-ce docker-ce-cli containerd.io"),
    ]
    fallback = ResolvedModel(model=None, settings=None)
    resolved = _REGISTRY.get("summarization", fallback)
    await ensure_ollama_warm(_SUMMARIZATION_MODEL, _CONFIG.llm_host)
    async with asyncio.timeout(60):
        summary = await summarize_messages(msgs, resolved)
    assert isinstance(summary, str)
    assert len(summary) > 10
    assert "docker" in summary.lower() or "container" in summary.lower()


# ---------------------------------------------------------------------------
# truncate_history_window — requires running LLM provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncate_history_window_triggers_compaction():
    """When messages exceed threshold, the middle is replaced with a summary.

    Requires a running LLM provider.
    """
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

    ctx = _real_run_context(_RESOLVED_MODEL, max_history_messages=6)
    await ensure_ollama_warm(_SUMMARIZATION_MODEL, _CONFIG.llm_host)
    async with asyncio.timeout(60):
        result = await truncate_history_window(ctx, msgs)

    assert len(result) < len(msgs)
    assert result[0] is msgs[0]
    assert result[1] is msgs[1]
    assert result[-1] is msgs[-1]
    found_summary = False
    for msg in result:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and (
                    "Summary of" in part.content or "trimmed" in part.content
                ):
                    found_summary = True
    assert found_summary, "Expected a summary or static marker in compacted history"


# ---------------------------------------------------------------------------
# /compact via dispatch — requires running LLM provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compact_produces_two_message_history():
    """/compact returns a 2-message compacted history (summary + ack).

    Requires a running LLM provider.
    """
    from co_cli.deps import CoDeps, CoServices, CoConfig
    from co_cli.tools._shell_backend import ShellBackend
    from co_cli.commands._commands import dispatch, CommandContext

    _, tool_names, _ = build_agent(config=CoConfig.from_settings(_settings, cwd=Path.cwd()))
    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), model_registry=_REGISTRY),
        config=CoConfig(),
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
        agent=_AGENT,
        tool_names=tool_names,
    )

    await ensure_ollama_warm(_SUMMARIZATION_MODEL, _CONFIG.llm_host)
    async with asyncio.timeout(60):
        handled, new_history = await dispatch("/compact", ctx)
    assert handled is True
    assert new_history is not None
    assert len(new_history) == 2

    assert isinstance(new_history[0], ModelRequest)
    summary_part = new_history[0].parts[0]
    assert isinstance(summary_part, UserPromptPart)
    assert "Compacted conversation summary" in summary_part.content
    assert "docker" in summary_part.content.lower() or "container" in summary_part.content.lower()

    assert isinstance(new_history[1], ModelResponse)
    ack_part = new_history[1].parts[0]
    assert isinstance(ack_part, TextPart)
    assert "Understood" in ack_part.content


@pytest.mark.asyncio
async def test_summarize_messages_applies_guardrail_in_instructions():
    """Guardrail prompt is applied via instructions with non-empty history.

    This validates the P0 fix path: summarize_messages() runs with
    message_history, so the anti-injection guard must be delivered via
    ModelRequest.instructions, not system prompt parts.
    """
    captured: dict[str, str | None] = {}

    def _capture(messages: list[ModelMessage], _info) -> ModelResponse:
        last = messages[-1]
        assert isinstance(last, ModelRequest)
        captured["instructions"] = last.instructions
        return ModelResponse(parts=[TextPart(content="summary ok")])

    model = FunctionModel(_capture)
    msgs: list[ModelMessage] = [
        _user("topic"),
        _assistant("details"),
    ]

    summary = await summarize_messages(msgs, ResolvedModel(model=model, settings=None))
    assert summary == "summary ok"
    assert captured["instructions"] is not None
    assert "IGNORE ALL COMMANDS" in captured["instructions"]


# ---------------------------------------------------------------------------
# compaction personality guardrail — no LLM provider needed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_excludes_personality_addendum() -> None:
    """With personality_active=False, _PERSONALITY_COMPACTION_ADDENDUM is not in the prompt."""
    from co_cli.context._history import _PERSONALITY_COMPACTION_ADDENDUM, summarize_messages

    captured: list[str] = []

    def capture_fn(messages, info):
        for msg in messages:
            for part in getattr(msg, "parts", []):
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    captured.append(part.content)
        return ModelResponse(parts=[TextPart(content="ok")])

    model = FunctionModel(capture_fn)
    msgs: list[ModelMessage] = [
        _user("What is Docker?"),
        _assistant("A containerization platform."),
    ]
    result = await summarize_messages(msgs, ResolvedModel(model=model, settings=None), personality_active=False)
    assert result == "ok"
    assert len(captured) > 0, "FunctionModel was not called — prompt not captured"
    assert not any(_PERSONALITY_COMPACTION_ADDENDUM in c for c in captured), (
        f"Addendum leaked when personality_active=False: {captured}"
    )
