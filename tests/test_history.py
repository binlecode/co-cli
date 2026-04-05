"""Functional tests for context history processors and compaction."""

import asyncio
from pathlib import Path

import pytest

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli._model_factory import ModelRegistry
from co_cli.agent import build_agent
from co_cli.commands._commands import CommandContext, ReplaceTranscript, dispatch
from co_cli.config import settings
from co_cli.context._history import (
    _CLEARED_PLACEHOLDER,
    _compute_compaction_boundaries,
    group_by_turn,
    groups_to_messages,
    truncate_tool_returns,
    truncate_history_window,
)
from co_cli.deps import CoDeps, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
# Cache agent model reference for RunContext construction — no LLM call made here.
_AGENT = build_agent(config=_CONFIG)


def _make_processor_ctx() -> RunContext:
    """Real RunContext for history processor tests (no LLM call).

    Uses a tiny Ollama budget (llm_num_ctx=30) so the char-estimate
    from _make_messages(10) (~33 tokens) exceeds int(30 * 0.85) = 25.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(llm_provider="ollama-openai", llm_num_ctx=30),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _make_compact_ctx(message_history: list | None = None) -> CommandContext:
    """Real CommandContext with model registry for /compact dispatch tests."""
    deps = CoDeps(
        shell=ShellBackend(), model_registry=_REGISTRY,
        config=_CONFIG,
        session=CoSessionState(session_id="test-history"),
    )
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=_AGENT,
    )


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _make_messages(n: int) -> list:
    """Alternating user/assistant messages; index 1 is always an assistant with TextPart.

    With n=10 and tail_count = max(4, 10//2) = 5:
      find_first_run_end → 1  (first ModelResponse with TextPart)
      head_end = 2
      tail_start = max(2, 10-5) = 5
      dropped = messages[2:5] (3 messages)
    """
    msgs = []
    for i in range(n // 2):
        msgs.append(_user(f"user turn {i}"))
        msgs.append(_assistant(f"assistant turn {i}"))
    if n % 2:
        msgs.append(_user(f"user turn {n // 2}"))
    return msgs


# ---------------------------------------------------------------------------
# truncate_history_window — inline summarisation and guard paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncate_history_window_static_marker_when_no_model_registry():
    """model_registry=None → static marker injected (guard path, no LLM call)."""
    msgs = _make_messages(10)
    ctx = _make_processor_ctx()
    # model_registry is None by default — guard skips LLM, uses static marker
    result = await truncate_history_window(ctx, msgs)
    marker_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if hasattr(p, "content") and isinstance(p.content, str)
    ]
    assert any("[Earlier conversation trimmed" in t for t in marker_texts)
    assert len(result) < len(msgs)


# ---------------------------------------------------------------------------
# Circuit breaker — skip LLM after 3 consecutive failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_skips_llm_after_three_failures():
    """compaction_failure_count >= 3 → static marker without LLM call."""
    msgs = _make_messages(10)
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(llm_provider="ollama-openai", llm_num_ctx=30),
        model_registry=_REGISTRY,
    )
    deps.runtime.compaction_failure_count = 3
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    result = await truncate_history_window(ctx, msgs)
    marker_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if hasattr(p, "content") and isinstance(p.content, str)
    ]
    # Circuit breaker active → static marker, no LLM call
    assert any("[Earlier conversation trimmed" in t for t in marker_texts)
    assert len(result) < len(msgs)
    # Failure count unchanged (no LLM attempt was made)
    assert deps.runtime.compaction_failure_count == 3


# ---------------------------------------------------------------------------
# /compact dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compact_produces_two_message_history():
    """/compact on non-empty history returns ReplaceTranscript with 2 messages and compaction_applied."""
    msgs = _make_messages(6)
    ctx = _make_compact_ctx(message_history=msgs)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await dispatch("/compact", ctx)
    assert isinstance(result, ReplaceTranscript)
    assert len(result.history) == 2
    assert result.compaction_applied is True


# ---------------------------------------------------------------------------
# group_by_turn — foundation tests (TASK-4b)
# ---------------------------------------------------------------------------


def _tool_call(name: str, call_id: str = "c1") -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args={}, tool_call_id=call_id)])


def _tool_return(name: str, content: str = "ok", call_id: str = "c1") -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)])


def test_group_by_turn_single_turn():
    """Single user/assistant pair = 1 group."""
    msgs = [_user("hello"), _assistant("hi")]
    groups = group_by_turn(msgs)
    assert len(groups) == 1
    assert groups[0].start_index == 0
    assert len(groups[0].messages) == 2


def test_group_by_turn_multi_turn():
    """N user turns = N groups."""
    msgs = [
        _user("turn 1"), _assistant("resp 1"),
        _user("turn 2"), _assistant("resp 2"),
        _user("turn 3"), _assistant("resp 3"),
    ]
    groups = group_by_turn(msgs)
    assert len(groups) == 3
    assert groups[0].start_index == 0
    assert groups[1].start_index == 2
    assert groups[2].start_index == 4


def test_group_by_turn_multi_tool_turn_stays_one_group():
    """Multiple tool calls within one user turn stay in one group."""
    msgs = [
        _user("do stuff"),
        _tool_call("read_file", "c1"),
        _tool_return("read_file", "file content", "c1"),
        _tool_call("find_in_files", "c2"),
        _tool_return("find_in_files", "search results", "c2"),
        _assistant("done"),
    ]
    groups = group_by_turn(msgs)
    assert len(groups) == 1
    assert len(groups[0].messages) == 6
    assert groups[0].tool_names == frozenset({"read_file", "find_in_files"})


def test_group_by_turn_orphan_prevention():
    """Dropping a whole group never leaves a ToolReturnPart without its ToolCallPart."""
    msgs = [
        _user("turn 1"),
        _tool_call("read_file", "c1"),
        _tool_return("read_file", "content", "c1"),
        _assistant("got it"),
        _user("turn 2"),
        _assistant("ok"),
    ]
    groups = group_by_turn(msgs)
    assert len(groups) == 2
    # Group 0 has both ToolCallPart and ToolReturnPart for read_file
    g0_has_call = any(
        isinstance(p, ToolCallPart)
        for m in groups[0].messages if isinstance(m, ModelResponse)
        for p in m.parts
    )
    g0_has_return = any(
        isinstance(p, ToolReturnPart)
        for m in groups[0].messages if isinstance(m, ModelRequest)
        for p in m.parts
    )
    assert g0_has_call and g0_has_return
    # Dropping group 0 leaves group 1 with no orphaned ToolReturnPart
    remaining = groups_to_messages(groups[1:])
    for msg in remaining:
        if isinstance(msg, ModelRequest):
            assert not any(isinstance(p, ToolReturnPart) for p in msg.parts)


def test_compute_compaction_boundaries_equivalence():
    """Refactored boundaries produce identical results to known expectations.

    With 10 messages: raw_tail_start = max(2, 10-5) = 5. msg[5] is a
    ModelResponse so alignment snaps forward to the next group boundary
    at index 6.  head_end=2, tail_start=6, dropped=4.
    """
    msgs = _make_messages(10)
    bounds = _compute_compaction_boundaries(msgs)
    assert bounds.head_end == 2
    assert bounds.tail_start == 6
    assert bounds.dropped_count == 4
    assert bounds.valid is True


# ---------------------------------------------------------------------------
# truncate_tool_returns — compactable-set micro-compact tests (TASK-4b)
# ---------------------------------------------------------------------------


def _make_tool_conversation(n_read_file: int, n_save_memory: int = 0) -> list:
    """Build a conversation with n_read_file read_file calls and n_save_memory save_memory calls.

    Each tool call is in its own user turn so they form separate groups.
    """
    msgs = []
    call_id = 0
    for i in range(n_read_file):
        cid = f"rf{call_id}"
        msgs.append(_user(f"read file {i}"))
        msgs.append(ModelResponse(parts=[ToolCallPart(tool_name="read_file", args={}, tool_call_id=cid)]))
        msgs.append(ModelRequest(parts=[ToolReturnPart(tool_name="read_file", content=f"content {i}", tool_call_id=cid)]))
        msgs.append(_assistant(f"got file {i}"))
        call_id += 1
    for i in range(n_save_memory):
        cid = f"sm{call_id}"
        msgs.append(_user(f"save memory {i}"))
        msgs.append(ModelResponse(parts=[ToolCallPart(tool_name="save_memory", args={}, tool_call_id=cid)]))
        msgs.append(ModelRequest(parts=[ToolReturnPart(tool_name="save_memory", content=f"saved {i}", tool_call_id=cid)]))
        msgs.append(_assistant(f"saved {i}"))
        call_id += 1
    # Final user turn (becomes the protected tail group)
    msgs.append(_user("final question"))
    msgs.append(_assistant("final answer"))
    return msgs


def test_compactable_older_than_5_cleared():
    """Compactable tool returns older than 5 most recent are content-cleared."""
    msgs = _make_tool_conversation(n_read_file=8)
    ctx = _make_processor_ctx()
    result = truncate_tool_returns(ctx, msgs)

    # Count read_file returns in result
    read_file_contents = []
    for msg in result:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "read_file":
                    read_file_contents.append(part.content)

    # 8 total, 5 most recent kept, 3 cleared (last group is protected so
    # the 8th read_file is in the second-to-last group, not the tail)
    cleared = [c for c in read_file_contents if c == _CLEARED_PLACEHOLDER]
    intact = [c for c in read_file_contents if c != _CLEARED_PLACEHOLDER]
    assert len(cleared) == 3
    assert len(intact) == 5


def test_non_compactable_pass_through():
    """Non-compactable tool returns pass through intact regardless of count."""
    msgs = _make_tool_conversation(n_read_file=0, n_save_memory=10)
    ctx = _make_processor_ctx()
    result = truncate_tool_returns(ctx, msgs)

    save_memory_contents = []
    for msg in result:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "save_memory":
                    save_memory_contents.append(part.content)

    # All 10 save_memory returns should be intact
    assert all(c != _CLEARED_PLACEHOLDER for c in save_memory_contents)
    assert len(save_memory_contents) == 10


def test_current_turn_protection_multi_tool():
    """Compactable tool results in the last turn group are never cleared."""
    # Build: 7 read_file turns + 1 multi-tool final turn with 3 read_files
    msgs = []
    call_id = 0
    for i in range(7):
        cid = f"rf{call_id}"
        msgs.append(_user(f"read file {i}"))
        msgs.append(ModelResponse(parts=[ToolCallPart(tool_name="read_file", args={}, tool_call_id=cid)]))
        msgs.append(ModelRequest(parts=[ToolReturnPart(tool_name="read_file", content=f"content {i}", tool_call_id=cid)]))
        msgs.append(_assistant(f"got file {i}"))
        call_id += 1
    # Final turn with multiple tool calls (should all be protected)
    msgs.append(_user("read three files"))
    for i in range(3):
        cid = f"final{i}"
        msgs.append(ModelResponse(parts=[ToolCallPart(tool_name="read_file", args={}, tool_call_id=cid)]))
        msgs.append(ModelRequest(parts=[ToolReturnPart(tool_name="read_file", content=f"final content {i}", tool_call_id=cid)]))
    msgs.append(_assistant("done with all three"))

    ctx = _make_processor_ctx()
    result = truncate_tool_returns(ctx, msgs)

    # The 3 read_file returns in the last turn group must be intact
    groups = group_by_turn(result)
    last_group = groups[-1]
    tail_returns = [
        part for msg in last_group.messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(tail_returns) == 3
    assert all(r.content != _CLEARED_PLACEHOLDER for r in tail_returns)
