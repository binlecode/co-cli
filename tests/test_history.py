"""Functional tests for context history processors and compaction."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import RunContext
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
from tests._ollama import ensure_ollama_warm
from tests._settings import make_settings
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

from co_cli.agent._core import build_agent
from co_cli.commands._commands import CommandContext, ReplaceTranscript, dispatch
from co_cli.config._compaction import CompactionSettings
from co_cli.config._core import settings
from co_cli.context._history import (
    _CLEARED_PLACEHOLDER,
    _find_last_turn_start,
    _recall_prompt_text,
    emergency_compact,
    enforce_batch_budget,
    find_first_run_end,
    group_by_turn,
    groups_to_messages,
    plan_compaction_boundaries,
    recover_overflow_history,
    summarize_history_window,
    truncate_tool_results,
)
from co_cli.context.orchestrate import _history_with_pending_user_input
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm._factory import build_model
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG

_CONFIG = settings
_LLM_MODEL = build_model(_CONFIG.llm)
# Cache agent model reference for RunContext construction — no LLM call made here.
_AGENT = build_agent(config=_CONFIG)


def _make_processor_ctx() -> RunContext:
    """Real RunContext for history processor tests (no LLM call).

    Uses a tiny Ollama budget (llm_num_ctx=30) so the char-estimate
    from _make_messages(10) (~33 tokens) exceeds int(30 * 0.75) = 22.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(
            llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
            compaction=CompactionSettings(min_threshold_tokens=0),
        ),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _make_compact_ctx(message_history: list | None = None) -> CommandContext:
    """Real CommandContext with model for /compact dispatch tests."""
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=_CONFIG,
        session=CoSessionState(),
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

    Produces ``n // 2`` turn groups (plus a trailing user prompt if ``n`` is odd).
    Used as input to the token-budget planner, which walks groups from the end
    accumulating their estimated token weight against ``TAIL_FRACTION * budget``.
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
async def test_summarize_history_window_static_marker_when_no_model():
    """model=None → static marker injected (guard path, no LLM call)."""
    msgs = _make_messages(10)
    ctx = _make_processor_ctx()
    # model is None by default — guard skips LLM, uses static marker
    result = await summarize_history_window(ctx, msgs)
    marker_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if hasattr(p, "content") and isinstance(p.content, str)
    ]
    assert any("This session is being continued" in t for t in marker_texts)
    assert len(result) < len(msgs)


# ---------------------------------------------------------------------------
# Circuit breaker — skip LLM after 3 consecutive failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_skips_llm_after_three_failures():
    """compaction_failure_count == 4 (first non-probe skip) → static marker, count becomes 5."""
    msgs = _make_messages(10)
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(
            llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
            compaction=CompactionSettings(min_threshold_tokens=0),
        ),
        model=_LLM_MODEL,
    )
    # count=4: skips_since_trip=1, not a probe cadence point → skip
    deps.runtime.compaction_failure_count = 4
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
    assert any("This session is being continued" in t for t in marker_texts)
    assert len(result) < len(msgs)
    # Skip increments count for probe cadence tracking
    assert deps.runtime.compaction_failure_count == 5


@pytest.mark.asyncio
async def test_circuit_breaker_first_trip_is_skip():
    """compaction_failure_count == 3 (first trip) → skip (no probe), count becomes 4."""
    msgs = _make_messages(10)
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(
            llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
            compaction=CompactionSettings(min_threshold_tokens=0),
        ),
        model=_LLM_MODEL,
    )
    # count=3: skips_since_trip=0 → skip (first probe not due until count==13)
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
    assert any("This session is being continued" in t for t in marker_texts)
    assert len(result) < len(msgs)
    # count=3 is skipped; counter advances for cadence tracking
    assert deps.runtime.compaction_failure_count == 4


@pytest.mark.asyncio
@pytest.mark.local
async def test_circuit_breaker_probes_at_cadence():
    """compaction_failure_count == 13 (3 + 10*1) → probe: LLM is attempted, count changes."""
    msgs = _make_messages(10)
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(
            llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
            compaction=CompactionSettings(min_threshold_tokens=0),
        ),
        model=_LLM_MODEL,
    )
    # count=13: skips_since_trip=10 → probe cadence → LLM attempted
    deps.runtime.compaction_failure_count = 13
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    await ensure_ollama_warm(_CONFIG.llm.model, _CONFIG.llm.host)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        await summarize_history_window(ctx, msgs)
    # After a probe: success resets to 0, failure increments to 14.
    # Count must be 0 or 14 — 13 would mean the skip branch ran (bug).
    assert deps.runtime.compaction_failure_count in (0, 14)


# ---------------------------------------------------------------------------
# /compact dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.local
async def test_compact_produces_two_message_history():
    """/compact on non-empty history returns ReplaceTranscript with 2 messages and compaction_applied."""
    msgs = _make_messages(6)
    ctx = _make_compact_ctx(message_history=msgs)
    await ensure_ollama_warm(_CONFIG.llm.model, _CONFIG.llm.host)
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
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)]
    )


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
        _user("turn 1"),
        _assistant("resp 1"),
        _user("turn 2"),
        _assistant("resp 2"),
        _user("turn 3"),
        _assistant("resp 3"),
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
        _tool_call("file_read", "c1"),
        _tool_return("file_read", "file content", "c1"),
        _tool_call("grep", "c2"),
        _tool_return("grep", "search results", "c2"),
        _assistant("done"),
    ]
    groups = group_by_turn(msgs)
    assert len(groups) == 1
    assert len(groups[0].messages) == 6


def test_group_by_turn_orphan_prevention():
    """Dropping a whole group never leaves a ToolReturnPart without its ToolCallPart."""
    msgs = [
        _user("turn 1"),
        _tool_call("file_read", "c1"),
        _tool_return("file_read", "content", "c1"),
        _assistant("got it"),
        _user("turn 2"),
        _assistant("ok"),
    ]
    groups = group_by_turn(msgs)
    assert len(groups) == 2
    # Group 0 has both ToolCallPart and ToolReturnPart for read_file
    g0_has_call = any(
        isinstance(p, ToolCallPart)
        for m in groups[0].messages
        if isinstance(m, ModelResponse)
        for p in m.parts
    )
    g0_has_return = any(
        isinstance(p, ToolReturnPart)
        for m in groups[0].messages
        if isinstance(m, ModelRequest)
        for p in m.parts
    )
    assert g0_has_call
    assert g0_has_return
    # Dropping group 0 leaves group 1 with no orphaned ToolReturnPart
    remaining = groups_to_messages(groups[1:])
    for msg in remaining:
        if isinstance(msg, ModelRequest):
            assert not any(isinstance(p, ToolReturnPart) for p in msg.parts)


def test_find_first_run_end_accepts_thinking_only_response():
    """Thinking-only first responses still define the preserved head run boundary."""
    messages = [
        _user("hello"),
        ModelResponse(parts=[ThinkingPart(content="let me think...")]),
        _user("follow up"),
        ModelResponse(parts=[TextPart(content="answer")]),
    ]

    idx = find_first_run_end(messages)

    assert idx == 1


# ---------------------------------------------------------------------------
# plan_compaction_boundaries — token-budget planner
# ---------------------------------------------------------------------------


def _group_of(text_chars: int, turn_idx: int) -> list:
    """Build one turn group (user + assistant) sized to text_chars each."""
    return [
        _user("u" * text_chars + f" #{turn_idx}"),
        _assistant("a" * text_chars + f" #{turn_idx}"),
    ]


def test_planner_tail_scales_with_token_pressure():
    """Same message count; bigger messages → more groups dropped (token-driven tail, not count-driven)."""
    # 5 small groups (~tiny tokens each) vs 5 big groups (large tokens each) — same len()
    small_msgs = []
    for i in range(5):
        small_msgs.extend(_group_of(10, i))
    big_msgs = []
    for i in range(5):
        big_msgs.extend(_group_of(200, i))

    # budget=200 → tail_budget = 80 tokens. small group ≈ 6 tokens, big group ≈ 100 tokens.
    budget = 200
    small_bounds = plan_compaction_boundaries(small_msgs, budget, 0.40)
    big_bounds = plan_compaction_boundaries(big_msgs, budget, 0.40)

    # Big transcript drops more groups than small (tail captures fewer groups under budget).
    assert big_bounds is not None
    small_dropped = small_bounds[2] if small_bounds else 0
    big_dropped = big_bounds[2]
    assert big_dropped > small_dropped


def test_planner_snaps_to_turn_boundary():
    """tail_start is always a turn-group start_index — never mid-turn."""
    msgs = []
    for i in range(5):
        msgs.extend(_group_of(50, i))
    bounds = plan_compaction_boundaries(msgs, 100, 0.40)
    assert bounds is not None
    _, tail_start, _ = bounds
    group_starts = {g.start_index for g in group_by_turn(msgs)}
    assert tail_start in group_starts


def test_planner_returns_none_below_structural_floor():
    """len(groups) <= min_groups_tail → None; never even consider walking."""
    # 1 group
    msgs_one = [_user("only turn"), _assistant("only reply")]
    assert plan_compaction_boundaries(msgs_one, 1000, 0.40) is None

    # empty
    assert plan_compaction_boundaries([], 1000, 0.40) is None


def test_planner_returns_none_on_head_tail_overlap():
    """When tail captures everything (all groups fit under tail_budget), head/tail overlap → None."""
    msgs = []
    for i in range(3):
        msgs.extend(_group_of(10, i))
    # budget=1_000_000 → tail_budget=400K; all 3 small groups fit easily → acc_groups=[G0,G1,G2].
    # tail_start=G0.start_index=0 <= head_end=2 → None.
    assert plan_compaction_boundaries(msgs, 1_000_000, 0.40) is None


def test_planner_min_groups_tail_keeps_last_group():
    """Gap A regression guard: last group alone > tail_budget → still kept (clamp wins)."""
    msgs = []
    # 3 small groups + one huge last group
    for i in range(3):
        msgs.extend(_group_of(10, i))
    # Huge last group: 4000 chars ≈ 1000 tokens
    msgs.extend(_group_of(2000, 3))
    # tail_fraction*budget = 40 tokens; last group alone is ~1000 tokens
    bounds = plan_compaction_boundaries(msgs, 100, 0.40)
    assert bounds is not None
    head_end, tail_start, _ = bounds
    # Last group must be kept — tail_start is the last group's start_index
    groups = group_by_turn(msgs)
    assert tail_start == groups[-1].start_index
    assert tail_start > head_end


@pytest.mark.asyncio
async def test_compaction_output_preserves_orphan_search_tools_return():
    """Gap L: after compaction, a search_tools ToolReturnPart from the dropped range
    survives in the output, even though its matching ToolCallPart was in dropped.

    Verifies the documented invariant: search_tools breadcrumbs are orphan returns
    by design — the SDK handles them without rejecting the request. This test
    checks the structural preservation; provider acceptance is exercised by the
    LLM-backed /compact integration test and production.
    """
    msgs = []
    # Head run
    msgs.append(_user("start"))
    msgs.append(_assistant("ok"))
    # Dropped middle with a search_tools call/return (the call will be dropped;
    # the return will be preserved as an orphan by _preserve_search_tool_breadcrumbs)
    msgs.append(_user("search for something"))
    msgs.append(
        ModelResponse(
            parts=[ToolCallPart(tool_name="search_tools", args={"q": "foo"}, tool_call_id="st-1")]
        )
    )
    msgs.append(
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search_tools",
                    content="discovered [foo_tool]",
                    tool_call_id="st-1",
                )
            ]
        )
    )
    msgs.append(_assistant("got it"))
    # Pad middle to force a drop
    for i in range(3):
        msgs.append(_user(f"mid {i} " + "x" * 500))
        msgs.append(_assistant(f"mid reply {i} " + "y" * 500))
    # Tail
    msgs.append(_user("final"))
    msgs.append(_assistant("done"))

    ctx = _make_processor_ctx()
    result = await summarize_history_window(ctx, msgs)

    # search_tools return must be present in the compacted output
    returns = [
        part
        for msg in result
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "search_tools"
    ]
    assert len(returns) == 1
    assert returns[0].tool_call_id == "st-1"

    # The matching ToolCallPart must NOT be present (confirming it's an orphan)
    calls = [
        part
        for msg in result
        if isinstance(msg, ModelResponse)
        for part in msg.parts
        if isinstance(part, ToolCallPart) and part.tool_name == "search_tools"
    ]
    assert len(calls) == 0


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
        msgs.append(
            ModelResponse(parts=[ToolCallPart(tool_name="file_read", args={}, tool_call_id=cid)])
        )
        msgs.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name="file_read", content=f"content {i}", tool_call_id=cid)
                ]
            )
        )
        msgs.append(_assistant(f"got file {i}"))
        call_id += 1
    for i in range(n_save_memory):
        cid = f"sm{call_id}"
        msgs.append(_user(f"save memory {i}"))
        msgs.append(
            ModelResponse(parts=[ToolCallPart(tool_name="save_memory", args={}, tool_call_id=cid)])
        )
        msgs.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name="save_memory", content=f"saved {i}", tool_call_id=cid)
                ]
            )
        )
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
                if isinstance(part, ToolReturnPart) and part.tool_name == "file_read":
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
    for i in range(7):
        cid = f"rf{i}"
        msgs.append(_user(f"read file {i}"))
        msgs.append(
            ModelResponse(parts=[ToolCallPart(tool_name="file_read", args={}, tool_call_id=cid)])
        )
        msgs.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name="file_read", content=f"content {i}", tool_call_id=cid)
                ]
            )
        )
        msgs.append(_assistant(f"got file {i}"))
    # Final turn with multiple tool calls (should all be protected)
    msgs.append(_user("read three files"))
    for i in range(3):
        cid = f"final{i}"
        msgs.append(
            ModelResponse(parts=[ToolCallPart(tool_name="file_read", args={}, tool_call_id=cid)])
        )
        msgs.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="file_read", content=f"final content {i}", tool_call_id=cid
                    )
                ]
            )
        )
    msgs.append(_assistant("done with all three"))

    ctx = _make_processor_ctx()
    result = truncate_tool_results(ctx, msgs)

    # The 3 read_file returns in the last turn must be intact
    boundary = _find_last_turn_start(result)
    tail_returns = [
        part
        for msg in result[boundary:]
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(tail_returns) == 3
    assert all(r.content != _CLEARED_PLACEHOLDER for r in tail_returns)


# ---------------------------------------------------------------------------
# _gather_compaction_context — context enrichment tests
# ---------------------------------------------------------------------------


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
    # Marker should contain context-continuation text
    marker_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
        and isinstance(p.content, str)
        and "This session is being continued" in p.content
    ]
    assert len(marker_texts) == 1


def test_emergency_compact_two_groups_returns_none():
    """emergency_compact with <=2 groups → returns None."""
    msgs = [
        _user("turn 1"),
        _assistant("response 1"),
        _user("turn 2"),
        _assistant("response 2"),
    ]
    groups = group_by_turn(msgs)
    assert len(groups) == 2
    result = emergency_compact(msgs)
    assert result is None


@pytest.mark.asyncio
async def test_recover_overflow_history_preserves_pending_user_turn():
    """Overflow recovery materializes the in-flight prompt into the kept tail group."""
    ctx = _make_processor_ctx()
    # Four prior turns → five groups after pending input materializes. With num_ctx=30
    # (tail_budget ≈ 12 tokens) the planner must drop at least one middle group.
    turn_state = type(
        "_TurnStateStub",
        (),
        {
            "current_input": "current request",
            "current_history": [
                _user("turn 1"),
                _assistant("response 1"),
                _user("turn 2"),
                _assistant("response 2"),
                _user("turn 3"),
                _assistant("response 3"),
                _user("turn 4"),
                _assistant("response 4"),
            ],
        },
    )()
    recovery_history = _history_with_pending_user_input(turn_state)
    result = await recover_overflow_history(ctx, recovery_history)

    assert result is not None
    user_texts = [
        part.content
        for msg in result
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    ]
    assert "current request" in user_texts
    assert ctx.deps.runtime.history_compaction_applied is True


# ---------------------------------------------------------------------------
# _recall_prompt_text — per-turn dynamic instruction (date + personality + knowledge recall)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_prompt_text_includes_personality_memories():
    """When personality is set, _recall_prompt_text includes the personality block in its output."""
    from co_cli.prompts.personalities import _injector as _injector_module
    from co_cli.prompts.personalities._injector import invalidate_personality_cache

    sentinel = "## Learned Context\n\n- personality-tail-sentinel-XYZ789"
    invalidate_personality_cache()
    try:
        _injector_module._personality_cache = sentinel
        deps = CoDeps(
            shell=ShellBackend(),
            config=make_settings().model_copy(update={"personality": "finch"}),
            model=_LLM_MODEL,
            session=CoSessionState(),
        )
        ctx = RunContext(
            deps=deps, model=_AGENT.model, usage=RunUsage(), messages=[_user("hello")]
        )
        result = await _recall_prompt_text(ctx)

        assert "personality-tail-sentinel-XYZ789" in result, (
            f"personality memories missing from recall text; got: {result!r}"
        )
    finally:
        invalidate_personality_cache()


@pytest.mark.asyncio
async def test_recall_prompt_text_always_includes_date():
    """_recall_prompt_text always includes a 'Today is' date line in its output."""
    from co_cli.prompts.personalities._injector import invalidate_personality_cache

    invalidate_personality_cache()
    try:
        deps = CoDeps(
            shell=ShellBackend(),
            config=make_settings().model_copy(update={"personality": None}),
            model=_LLM_MODEL,
            session=CoSessionState(),
            knowledge_dir=Path("/nonexistent-test-dir"),
        )
        msgs = [_user("ping"), _assistant("pong"), _user("ping again")]
        ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage(), messages=msgs)
        result = await _recall_prompt_text(ctx)

        assert "Today is " in result, f"date missing from recall text; got: {result!r}"
    finally:
        invalidate_personality_cache()


# ---------------------------------------------------------------------------
# enforce_batch_budget — per-batch aggregate spill
# ---------------------------------------------------------------------------


def _make_batch_ctx(tmp_path: Path, batch_spill_chars: int) -> RunContext:
    """RunContext with a controlled batch_spill_chars and tmp tool_results_dir."""
    config = make_settings(
        tools=make_settings().tools.model_copy(update={"batch_spill_chars": batch_spill_chars})
    )
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_results_dir=tmp_path / "tool-results",
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _tool_call_msg(name: str, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args={}, tool_call_id=call_id)])


def _tool_return_msg(name: str, content: str, call_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)]
    )


def test_enforce_batch_budget_under_cap_unchanged(tmp_path: Path) -> None:
    """enforce_batch_budget returns messages unchanged when aggregate is below threshold."""
    content = "x" * 100
    msgs = [
        _user("go"),
        _tool_call_msg("tool_a", "c1"),
        _tool_return_msg("tool_a", content, "c1"),
    ]
    ctx = _make_batch_ctx(tmp_path, batch_spill_chars=500)
    result = enforce_batch_budget(ctx, msgs)
    part = result[2].parts[0]
    assert part.content == content
    assert not (tmp_path / "tool-results").exists()


def test_enforce_batch_budget_over_cap_single_spilled(tmp_path: Path) -> None:
    """enforce_batch_budget spills the largest tool return when aggregate exceeds threshold."""
    big = "y" * 10_000
    msgs = [
        _user("read"),
        _tool_call_msg("read_file", "c1"),
        _tool_return_msg("read_file", big, "c1"),
    ]
    ctx = _make_batch_ctx(tmp_path, batch_spill_chars=100)
    result = enforce_batch_budget(ctx, msgs)
    part = result[2].parts[0]
    assert PERSISTED_OUTPUT_TAG in part.content


def test_enforce_batch_budget_evicts_largest_first(tmp_path: Path) -> None:
    """enforce_batch_budget evicts the largest tool return first until under threshold."""
    big = "y" * 10_000
    small = "z" * 1_000
    call_msg = ModelResponse(
        parts=[
            ToolCallPart(tool_name="tool_a", args={}, tool_call_id="c1"),
            ToolCallPart(tool_name="tool_b", args={}, tool_call_id="c2"),
        ]
    )
    ret_msg = ModelRequest(
        parts=[
            ToolReturnPart(tool_name="tool_a", content=big, tool_call_id="c1"),
            ToolReturnPart(tool_name="tool_b", content=small, tool_call_id="c2"),
        ]
    )
    msgs = [_user("go"), call_msg, ret_msg]
    # threshold=5_000 → aggregate=11_000 > 5_000; evict big (10K) → aggregate falls below 5K
    ctx = _make_batch_ctx(tmp_path, batch_spill_chars=5_000)
    result = enforce_batch_budget(ctx, msgs)
    parts = result[2].parts
    assert PERSISTED_OUTPUT_TAG in parts[0].content
    assert parts[1].content == small


def test_enforce_batch_budget_skips_already_persisted(tmp_path: Path) -> None:
    """enforce_batch_budget skips tool returns that already contain PERSISTED_OUTPUT_TAG."""
    already_persisted = f"{PERSISTED_OUTPUT_TAG}\ntool: read_file\nfile: /tmp/x.txt\n..."
    msgs = [
        _user("go"),
        _tool_call_msg("read_file", "c1"),
        _tool_return_msg("read_file", already_persisted, "c1"),
    ]
    ctx = _make_batch_ctx(tmp_path, batch_spill_chars=10)
    result = enforce_batch_budget(ctx, msgs)
    part = result[2].parts[0]
    # Already-persisted content must not be modified
    assert part.content == already_persisted


def test_enforce_batch_budget_no_batch_unchanged(tmp_path: Path) -> None:
    """enforce_batch_budget is a no-op when no ToolCallPart exists in history."""
    msgs = [_user("hello"), _assistant("world")]
    ctx = _make_batch_ctx(tmp_path, batch_spill_chars=10)
    result = enforce_batch_budget(ctx, msgs)
    assert result is msgs


def test_enforce_batch_budget_fail_open_on_oserror(tmp_path: Path) -> None:
    """enforce_batch_budget returns original messages when persist fails (fail-open)."""
    big = "y" * 10_000
    msgs = [
        _user("go"),
        _tool_call_msg("tool_a", "c1"),
        _tool_return_msg("tool_a", big, "c1"),
    ]
    # Place a file at tool_results_dir path so mkdir() raises FileExistsError (OSError subclass)
    bad_dir = tmp_path / "tool-results"
    bad_dir.write_text("blocking file")
    config = make_settings(
        tools=make_settings().tools.model_copy(update={"batch_spill_chars": 100})
    )
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_results_dir=bad_dir,
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    result = enforce_batch_budget(ctx, msgs)
    # Fail-open: persist failed → content unchanged
    part = result[2].parts[0]
    assert part.content == big


# ---------------------------------------------------------------------------
# TASK-3 regression tests: soft-overrun + active-user anchoring
# ---------------------------------------------------------------------------


def test_planner_soft_overrun_retains_oversized_last_group():
    """Soft-overrun: last group > tail_budget is still retained (clamp to _MIN_RETAINED_TURN_GROUPS).

    Verifies that when a single group exceeds the soft-overrun cap, the planner
    keeps it rather than dropping it entirely — the minimum-retained invariant wins.
    Uses tail_soft_overrun_multiplier=1.0 (no slack) to hit the path clearly.
    """
    msgs = []
    # 3 small turn groups (~6 tokens each)
    for i in range(3):
        msgs.extend([_user(f"small user {i}"), _assistant(f"small reply {i}")])
    # One large last group: 2000 chars ≈ 500 tokens — far above any reasonable tail_budget
    msgs.extend([_user("big user " + "z" * 2000), _assistant("big reply " + "z" * 2000)])

    # budget=100 → tail_budget=40 tokens; last group is ~500 tokens >> soft-overrun
    bounds = plan_compaction_boundaries(msgs, 100, 0.40, tail_soft_overrun_multiplier=1.0)
    assert bounds is not None
    head_end, tail_start, _ = bounds
    groups = group_by_turn(msgs)
    # Last group must be in the tail (tail_start <= last group's start_index)
    assert tail_start <= groups[-1].start_index
    assert tail_start > head_end


def test_planner_active_user_anchoring_pulls_latest_user_into_tail():
    """Active-user anchoring: latest user turn in the dropped middle gets pulled into the tail.

    Constructs a history where the budget-driven tail would start at the penultimate group
    but the actual last user turn is in the middle (just before the tail boundary).
    Anchoring must advance tail_start to include that user turn.
    """
    msgs = []
    # Groups 0-2: small head groups
    for i in range(3):
        msgs.extend([_user(f"head user {i}"), _assistant(f"head reply {i}")])
    # Group 3: large assistant-only response that lands in the would-be tail
    # (No user turn here — assistant continues from previous)
    msgs.append(_assistant("large assistant monologue " + "x" * 400))
    # Group 4 (last): another user turn + reply that forms the final group
    msgs.extend([_user("FINAL USER TURN"), _assistant("final reply")])

    # budget=200 → tail_budget=80 tokens. The large assistant group at index 3 (~100 tokens)
    # exceeds tail_budget alone, so the planner might not include the final user group.
    # Anchoring must ensure the final user turn is always kept.
    bounds = plan_compaction_boundaries(msgs, 200, 0.40)
    if bounds is None:
        # Everything fits → no compaction needed, invariant trivially satisfied
        return
    _, tail_start, _ = bounds
    # Find the index of "FINAL USER TURN" message
    final_user_idx = next(
        idx
        for idx, msg in enumerate(msgs)
        if isinstance(msg, ModelRequest)
        and any(
            isinstance(p, UserPromptPart) and "FINAL USER TURN" in str(p.content)
            for p in msg.parts
        )
    )
    # The final user turn must be in the tail (not dropped)
    assert final_user_idx >= tail_start
