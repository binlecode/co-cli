"""Functional tests for context history processors and compaction."""

import asyncio
from pathlib import Path

import pytest

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
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
    _CONTEXT_MAX_CHARS,
    _SUMMARY_MARKER_PREFIX,
    _find_last_turn_start,
    FILE_TOOLS,
    OLDER_MSG_MAX_CHARS,
    _compute_compaction_boundaries,
    _gather_compaction_context,
    _truncate_proportional,
    compact_assistant_responses,
    emergency_compact,
    group_by_turn,
    groups_to_messages,
    truncate_tool_results,
    summarize_history_window,
)
from co_cli.context._orchestrate import _is_context_overflow
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
# summarize_history_window — inline summarisation and guard paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_history_window_static_marker_when_no_model_registry():
    """model_registry=None → static marker injected (guard path, no LLM call)."""
    msgs = _make_messages(10)
    ctx = _make_processor_ctx()
    # model_registry is None by default — guard skips LLM, uses static marker
    result = await summarize_history_window(ctx, msgs)
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
    result = await summarize_history_window(ctx, msgs)
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
    assert bounds is not None
    head_end, tail_start, dropped_count = bounds
    assert head_end == 2
    assert tail_start == 6
    assert dropped_count == 4


# ---------------------------------------------------------------------------
# truncate_tool_results — compactable-set micro-compact tests (TASK-4b)
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
    result = truncate_tool_results(ctx, msgs)

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
    result = truncate_tool_results(ctx, msgs)

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
    result = truncate_tool_results(ctx, msgs)

    # The 3 read_file returns in the last turn must be intact
    boundary = _find_last_turn_start(result)
    tail_returns = [
        part for msg in result[boundary:]
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(tail_returns) == 3
    assert all(r.content != _CLEARED_PLACEHOLDER for r in tail_returns)


# ---------------------------------------------------------------------------
# _gather_compaction_context — context enrichment tests
# ---------------------------------------------------------------------------


def _make_gather_ctx(
    memory_dir: Path | None = None,
    session_todos: list[dict] | None = None,
) -> RunContext:
    """RunContext for _gather_compaction_context tests."""
    config = CoConfig(
        llm_provider="ollama-openai",
        llm_num_ctx=30,
        memory_dir=memory_dir or Path("/nonexistent-test-dir"),
    )
    session = CoSessionState(session_id="test-gather")
    if session_todos is not None:
        session.session_todos = session_todos
    deps = CoDeps(shell=ShellBackend(), config=config, session=session)
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def test_gather_context_extracts_file_paths():
    """_gather_compaction_context extracts file paths from ToolCallPart.args_as_dict()."""
    msgs = [
        _user("read some files"),
        ModelResponse(parts=[
            ToolCallPart(tool_name="read_file", args={"file_path": "/src/main.py"}, tool_call_id="c1"),
            ToolCallPart(tool_name="edit_file", args={"path": "/src/utils.py"}, tool_call_id="c2"),
        ]),
        _tool_return("read_file", "content", "c1"),
    ]
    ctx = _make_gather_ctx()
    result = _gather_compaction_context(ctx, msgs, dropped=[])
    assert result is not None
    assert "/src/main.py" in result
    assert "/src/utils.py" in result
    assert "Files touched:" in result


def test_gather_context_includes_pending_todos():
    """_gather_compaction_context includes pending session todos, filters out done."""
    todos = [
        {"content": "Fix the bug", "status": "pending"},
        {"content": "Write tests", "status": "completed"},
        {"content": "Deploy", "status": "in-progress"},
    ]
    ctx = _make_gather_ctx(session_todos=todos)
    result = _gather_compaction_context(ctx, [_user("hello"), _assistant("hi")], dropped=[])
    assert result is not None
    assert "Fix the bug" in result
    assert "Deploy" in result
    # Completed todo should be filtered out
    assert "Write tests" not in result


def test_gather_context_extracts_prior_summary():
    """_gather_compaction_context extracts prior-summary text from dropped messages."""
    prior_summary = f"{_SUMMARY_MARKER_PREFIX} 5 earlier messages]\nGoal: build a CLI tool"
    dropped = [
        ModelRequest(parts=[UserPromptPart(content=prior_summary)]),
    ]
    ctx = _make_gather_ctx()
    result = _gather_compaction_context(ctx, [_user("hello"), _assistant("hi")], dropped=dropped)
    assert result is not None
    assert "Prior summary:" in result
    assert "build a CLI tool" in result


def test_gather_context_returns_none_when_empty():
    """_gather_compaction_context returns None when no context sources produce data."""
    msgs = [_user("hello"), _assistant("hi")]
    ctx = _make_gather_ctx()
    result = _gather_compaction_context(ctx, msgs, dropped=[])
    assert result is None


def test_gather_context_always_on_memories(tmp_path: Path):
    """_gather_compaction_context includes always-on memories from real .md files."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    # Create an always-on memory file with YAML frontmatter
    mem_file = mem_dir / "always-on-test.md"
    mem_file.write_text(
        "---\n"
        "id: 9001\n"
        "kind: memory\n"
        "tags: [test]\n"
        "created: '2025-01-01T00:00:00Z'\n"
        "always_on: true\n"
        "---\n"
        "Important standing context for the session.\n"
    )
    # Create a non-always-on memory (should NOT appear)
    normal_mem = mem_dir / "normal-test.md"
    normal_mem.write_text(
        "---\n"
        "id: 9002\n"
        "kind: memory\n"
        "tags: [test]\n"
        "created: '2025-01-01T00:00:00Z'\n"
        "---\n"
        "This is a normal memory.\n"
    )
    ctx = _make_gather_ctx(memory_dir=mem_dir)
    result = _gather_compaction_context(ctx, [_user("hello"), _assistant("hi")], dropped=[])
    assert result is not None
    assert "Standing memories:" in result
    assert "Important standing context" in result


def test_gather_context_truncates_to_max_chars():
    """_gather_compaction_context with >4K combined sources → output truncated to _CONTEXT_MAX_CHARS."""
    # Create a huge prior summary in dropped messages
    huge_text = f"{_SUMMARY_MARKER_PREFIX} 100 messages]\n" + "x" * 5000
    dropped = [
        ModelRequest(parts=[UserPromptPart(content=huge_text)]),
    ]
    ctx = _make_gather_ctx()
    result = _gather_compaction_context(ctx, [_user("hello"), _assistant("hi")], dropped=dropped)
    assert result is not None
    assert len(result) <= _CONTEXT_MAX_CHARS


# ---------------------------------------------------------------------------
# emergency_compact — overflow recovery
# ---------------------------------------------------------------------------


def test_emergency_compact_5_groups():
    """emergency_compact with 5 turn groups → 2 groups + static marker."""
    msgs = []
    for i in range(5):
        msgs.append(_user(f"turn {i}"))
        msgs.append(_assistant(f"response {i}"))
    groups = group_by_turn(msgs)
    assert len(groups) == 5

    result = emergency_compact(msgs)
    assert result is not None
    # Should have: first group msgs + marker + last group msgs
    result_groups = group_by_turn(result)
    # First group + marker (which has UserPromptPart so it's a group) + last group
    assert len(result_groups) == 3
    # Marker should contain "[Earlier conversation trimmed"
    marker_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart) and isinstance(p.content, str)
        and "Earlier conversation trimmed" in p.content
    ]
    assert len(marker_texts) == 1


def test_emergency_compact_two_groups_returns_none():
    """emergency_compact with <=2 groups → returns None."""
    msgs = [
        _user("turn 1"), _assistant("response 1"),
        _user("turn 2"), _assistant("response 2"),
    ]
    groups = group_by_turn(msgs)
    assert len(groups) == 2
    result = emergency_compact(msgs)
    assert result is None


# ---------------------------------------------------------------------------
# _is_context_overflow — overflow detection
# ---------------------------------------------------------------------------


def test_is_context_overflow_413():
    """413 with context-length pattern → True."""
    e = ModelHTTPError(status_code=413, model_name="test", body="prompt is too long")
    assert _is_context_overflow(e) is True


def test_is_context_overflow_400_context_length():
    """400 with context_length_exceeded in body → True."""
    e = ModelHTTPError(status_code=400, model_name="test", body={"error": {"code": "context_length_exceeded"}})
    assert _is_context_overflow(e) is True


def test_is_context_overflow_str_body():
    """400 with string body containing 'maximum context length' → True."""
    e = ModelHTTPError(status_code=400, model_name="test", body="Error: maximum context length exceeded")
    assert _is_context_overflow(e) is True


def test_is_context_overflow_bare_400_no_pattern():
    """Bare 400 without context-length body → False."""
    e = ModelHTTPError(status_code=400, model_name="test", body="invalid json in tool call")
    assert _is_context_overflow(e) is False


def test_is_context_overflow_500():
    """500 status code → False (not overflow)."""
    e = ModelHTTPError(status_code=500, model_name="test", body="prompt is too long")
    assert _is_context_overflow(e) is False


# ---------------------------------------------------------------------------
# _truncate_proportional — proportional truncation
# ---------------------------------------------------------------------------


def test_truncate_proportional_preserves_head_tail():
    """_truncate_proportional preserves 20% head + 80% tail + marker."""
    text = "A" * 1000
    result = _truncate_proportional(text, max_chars=200)
    assert len(result) <= 200
    assert "[...truncated...]" in result
    # Head should be ~20% of available space, tail ~80% (aligned with gemini-cli)
    marker = "\n[...truncated...]\n"
    available = 200 - len(marker)
    head_size = int(available * 0.20)
    tail_size = available - head_size
    assert result[:head_size] == "A" * head_size
    assert result.endswith("A" * tail_size)


def test_truncate_proportional_no_truncation_needed():
    """Short text below max_chars → returned unchanged."""
    text = "Short text"
    result = _truncate_proportional(text, max_chars=200)
    assert result == text


# ---------------------------------------------------------------------------
# compact_assistant_responses — assistant response compaction
# ---------------------------------------------------------------------------


def test_compact_assistant_responses_large_text_truncated():
    """Old 50K TextPart → truncated to OLDER_MSG_MAX_CHARS; last turn group untouched."""
    big_text = "x" * 50_000
    msgs = [
        _user("turn 1"),
        ModelResponse(parts=[TextPart(content=big_text)]),
        _user("turn 2"),
        _assistant("short response"),
    ]
    ctx = _make_processor_ctx()
    result = compact_assistant_responses(ctx, msgs)
    # Find the old response (first ModelResponse)
    old_response = [m for m in result if isinstance(m, ModelResponse)][0]
    old_text = old_response.parts[0].content
    assert len(old_text) <= OLDER_MSG_MAX_CHARS
    assert "[...truncated...]" in old_text
    # Last turn group's response untouched
    last_response = [m for m in result if isinstance(m, ModelResponse)][-1]
    assert last_response.parts[0].content == "short response"


def test_compact_assistant_responses_tool_return_untouched():
    """ToolReturnPart and UserPromptPart are not affected by compact_assistant_responses."""
    big_tool_return = "y" * 50_000
    big_user_text = "z" * 50_000
    msgs = [
        ModelRequest(parts=[UserPromptPart(content=big_user_text)]),
        ModelResponse(parts=[ToolCallPart(tool_name="read_file", args={}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="read_file", content=big_tool_return, tool_call_id="c1")]),
        ModelResponse(parts=[TextPart(content="done")]),
        _user("turn 2"),
        _assistant("ok"),
    ]
    ctx = _make_processor_ctx()
    result = compact_assistant_responses(ctx, msgs)
    # Find the ToolReturnPart — should be untouched
    tool_returns = [
        part for msg in result if isinstance(msg, ModelRequest)
        for part in msg.parts if isinstance(part, ToolReturnPart)
    ]
    assert len(tool_returns) == 1
    assert tool_returns[0].content == big_tool_return
    # The big UserPromptPart in the first group should also be untouched
    first_request = result[0]
    assert isinstance(first_request, ModelRequest)
    user_part = first_request.parts[0]
    assert isinstance(user_part, UserPromptPart)
    assert user_part.content == big_user_text
