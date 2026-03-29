"""Functional tests for history processors.

All tests use real objects — no mocks, no stubs.
LLM tests require a running provider (GEMINI_API_KEY or OLLAMA_HOST).
"""

import asyncio
import re
from pathlib import Path

import pytest
import yaml

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli.context._history import (
    CompactionResult,
    summarize_messages,
    truncate_tool_returns,
    truncate_history_window,
    inject_opening_context,
)
from co_cli.context._types import MemoryRecallState
from co_cli.agent import build_agent
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.config import settings as _settings, ROLE_REASONING
from co_cli.deps import CoConfig, CoRuntimeState
from tests._ollama import ensure_ollama_warm
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS, FILE_DB_TIMEOUT_SECS

_CONFIG = CoConfig.from_settings(_settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
_REASONING_MODEL = _CONFIG.role_models[ROLE_REASONING].model
_SUMMARIZATION_MODEL = _CONFIG.role_models["summarization"].model
_RESOLVED_MODEL = _REGISTRY.get(ROLE_REASONING, ResolvedModel(model=None, settings=None)).model
_AGENT = build_agent(config=CoConfig.from_settings(_settings, cwd=Path.cwd())).agent


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
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
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
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
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


@pytest.mark.asyncio
async def test_truncate_history_window_uses_precomputed_result():
    """When precomputed_compaction holds a matching snapshot, its summary is used
    directly — no LLM call is made.

    Failure mode: snapshot guard broken, precomputed_compaction not read, or
    summary not substituted → the precomputed text would not appear in output.
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

    # Boundaries for 10 plain messages with max_history_messages=6:
    # head_end=2 (first TextPart response at index 1), tail_start=6 (index 10-4)
    precomputed = CompactionResult(
        summary_text="PRECOMPUTED_SUMMARY_MARKER",
        head_end=2,
        tail_start=6,
        message_count=10,
    )
    ctx = _real_run_context(FunctionModel(lambda m, i: None), max_history_messages=6)
    ctx.deps.runtime.precomputed_compaction = precomputed

    # No LLM is called in the precomputed path — no asyncio.timeout needed.
    result = await truncate_history_window(ctx, msgs)

    assert len(result) < len(msgs)
    assert result[0] is msgs[0]
    assert result[-1] is msgs[-1]
    found = any(
        isinstance(part, UserPromptPart) and "PRECOMPUTED_SUMMARY_MARKER" in part.content
        for msg in result
        if isinstance(msg, ModelRequest)
        for part in msg.parts
    )
    assert found, "Expected precomputed summary text in compacted history"


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
    from co_cli.commands._commands import dispatch, CommandContext, ReplaceTranscript

    tool_names = build_agent(config=CoConfig.from_settings(_settings, cwd=Path.cwd())).tool_names
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
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await dispatch("/compact", ctx)
    assert isinstance(result, ReplaceTranscript)
    assert len(result.history) == 2

    assert isinstance(result.history[0], ModelRequest)
    summary_part = result.history[0].parts[0]
    assert isinstance(summary_part, UserPromptPart)
    assert "Compacted conversation summary" in summary_part.content
    assert "docker" in summary_part.content.lower() or "container" in summary_part.content.lower()

    assert isinstance(result.history[1], ModelResponse)
    ack_part = result.history[1].parts[0]
    assert isinstance(ack_part, TextPart)
    assert "Understood" in ack_part.content


# ---------------------------------------------------------------------------
# truncate_tool_returns — no mutation
# ---------------------------------------------------------------------------


def test_trim_does_not_mutate_input():
    """truncate_tool_returns must not mutate the original messages or their parts."""
    ctx = _real_run_context(FunctionModel(lambda m, i: None), tool_output_trim_chars=50)
    long_content = "a" * 200
    msgs: list[ModelMessage] = [
        _user("q"),
        _tool_return("shell", long_content),
        _assistant("ok"),
        _user("next"),
        _assistant("done"),
    ]
    original_content = msgs[1].parts[0].content
    result = truncate_tool_returns(ctx, msgs)
    # Original message must be unchanged
    assert msgs[1].parts[0].content == original_content
    # Returned result must be truncated
    assert len(result[1].parts[0].content) < len(long_content)
    assert "truncated" in result[1].parts[0].content


# ---------------------------------------------------------------------------
# truncate_history_window — static marker fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncate_history_window_static_marker_when_no_precomputed():
    """When precomputed_compaction is None and history exceeds threshold, static marker is used."""
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
    ctx = _real_run_context(FunctionModel(lambda m, i: None), max_history_messages=6)
    # precomputed_compaction is None by default
    assert ctx.deps.runtime.precomputed_compaction is None
    result = await truncate_history_window(ctx, msgs)
    assert len(result) < len(msgs)
    found = any(
        isinstance(part, UserPromptPart)
        and re.search(r"\[Earlier conversation trimmed", part.content)
        for msg in result
        if isinstance(msg, ModelRequest)
        for part in msg.parts
    )
    assert found, "Expected static marker when precomputed_compaction is None"


# ---------------------------------------------------------------------------
# inject_opening_context — memory_injection_max_chars cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_opening_context_caps_memory_content(tmp_path: Path):
    """inject_opening_context truncates injected content to memory_injection_max_chars."""
    from dataclasses import replace as dc_replace
    from pydantic_ai._run_context import RunContext
    from co_cli.deps import CoDeps, CoServices

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Write 5 memories with 1000-char bodies matching query keyword "kw-cap-test"
    for i in range(1, 6):
        long_body = "x" * 1000
        fm: dict = {"id": i, "created": "2026-01-01T00:00:00+00:00", "tags": ["kw-cap-test"]}
        md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\nkw-cap-test {long_body}\n"
        (memory_dir / f"{i:03d}-kw-cap-test.md").write_text(md, encoding="utf-8")

    max_chars = 200
    config = dc_replace(
        CoConfig(knowledge_search_backend="grep", memory_injection_max_chars=max_chars),
        memory_dir=memory_dir,
    )
    runtime = CoRuntimeState()
    from co_cli.tools._shell_backend import ShellBackend
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=config,
        runtime=runtime,
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    msgs: list[ModelMessage] = [_user("kw-cap-test")]
    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await inject_opening_context(ctx, msgs)

    injected_parts = [
        part
        for msg in result
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, SystemPromptPart)
    ]
    assert len(injected_parts) >= 1, "Expected at least one injected SystemPromptPart"
    prefix = "Relevant memories:\n"
    for part in injected_parts:
        assert len(part.content) <= max_chars + len(prefix), (
            f"Injected content length {len(part.content)} exceeds cap {max_chars + len(prefix)}"
        )

