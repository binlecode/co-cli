"""E2E tests for single-tier overflow recovery (recover_overflow_history)."""

from __future__ import annotations

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.context.compaction import (
    STATIC_MARKER_PREFIX,
    SUMMARY_MARKER_PREFIX,
    recover_overflow_history,
)
from co_cli.context.history_processors import strip_all_tool_returns
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend


def _make_ctx(model_max_context_tokens: int) -> RunContext:
    """Build a real RunContext with a tunable budget and no model.

    ``deps.model = None`` (default) closes ``_summarization_gate_open`` so the
    summarize path falls back to a static marker — deterministic, no LLM call.
    """
    deps = CoDeps(shell=ShellBackend(), config=SETTINGS, session=CoSessionState())
    deps.model_max_context_tokens = model_max_context_tokens
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _call_return_pair(
    tool_name: str, call_id: str, args: dict, content: str
) -> tuple[ModelResponse, ModelRequest]:
    """Build a paired (ToolCallPart, ToolReturnPart) cycle as separate messages."""
    return (
        ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=call_id)]),
        ModelRequest(
            parts=[ToolReturnPart(tool_name=tool_name, tool_call_id=call_id, content=content)]
        ),
    )


def _all_tool_call_ids(messages: list) -> tuple[set[str], set[str]]:
    """Return (call_ids, return_ids) for pairing audits."""
    call_ids: set[str] = set()
    return_ids: set[str] = set()
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                call_ids.add(part.tool_call_id)
            elif isinstance(part, ToolReturnPart):
                return_ids.add(part.tool_call_id)
    return call_ids, return_ids


@pytest.mark.asyncio
async def test_recover_strip_only_fits():
    """Strip alone fits the budget — no summarizer runs; markers replace bulky returns.

    Property: when the budget pressure is concentrated in tool-return content,
    strip recovers the turn without dropping any messages or running the planner.
    Also covers the no-filter rule: a non-compactable return (memory_create) is
    stripped just like a compactable one.
    """
    big_payload = "x" * 5000
    call_a, ret_a = _call_return_pair("file_read", "c1", {"path": "/a.py"}, big_payload)
    call_b, ret_b = _call_return_pair(
        "memory_create",
        "c2",
        {"title": "test", "kind": "note"},
        "created article 'test'",
    )
    pending = ModelRequest(parts=[UserPromptPart(content="pending request")])
    messages = [
        ModelRequest(parts=[UserPromptPart(content="t1")]),
        call_a,
        ret_a,
        call_b,
        ret_b,
        pending,
    ]

    ctx = _make_ctx(model_max_context_tokens=2000)
    recovered = await recover_overflow_history(ctx.deps, messages)

    assert recovered is not None
    assert len(recovered) == len(messages), "strip preserves message count"
    # ret_a (file_read) → per-tool marker.
    assert recovered[2].parts[0].content.startswith("[file_read] ")
    # ret_b (memory_create) is also stripped — strip is universal across tool names.
    assert recovered[4].parts[0].content.startswith("[memory_create] ")
    # No compaction marker injected — strip-only path bypasses apply_compaction.
    for msg in recovered:
        for part in msg.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                assert not content.startswith(SUMMARY_MARKER_PREFIX)
                assert not content.startswith(STATIC_MARKER_PREFIX)
    # Pending user input preserved at the tail.
    assert recovered[-1].parts[0].content == "pending request"
    # Runtime state pinned by the strip-only-fits path.
    assert ctx.deps.runtime.compaction_applied_this_turn is True
    assert ctx.deps.runtime.consecutive_low_yield_proactive_compactions == 0


@pytest.mark.asyncio
async def test_recover_strip_plus_summary_fits():
    """Strip alone insufficient — apply_compaction runs and emits a static marker.

    Property: with model=None the summarizer is gated off and apply_compaction's
    static-marker fallback fires deterministically.
    """
    msgs: list = []
    for i in range(8):
        msgs.append(ModelRequest(parts=[UserPromptPart(content=f"turn{i}")]))
        call, ret = _call_return_pair("file_read", f"c{i}", {"path": f"/file{i}.py"}, "x" * 2000)
        msgs.append(call)
        msgs.append(ret)
    msgs.append(ModelRequest(parts=[UserPromptPart(content="pending request")]))

    ctx = _make_ctx(model_max_context_tokens=50)
    recovered = await recover_overflow_history(ctx.deps, msgs)

    assert recovered is not None
    assert len(recovered) < len(msgs), "summarize path drops middle groups"
    has_static_marker = any(
        isinstance(getattr(p, "content", None), str) and p.content.startswith(STATIC_MARKER_PREFIX)
        for m in recovered
        for p in m.parts
    )
    assert has_static_marker, "static marker fired with model=None"
    assert recovered[-1].parts[0].content == "pending request"


@pytest.mark.asyncio
async def test_recover_terminal_when_planner_returns_none():
    """Single-turn history → planner cannot find bounds → recovery returns None."""
    big_call, big_ret = _call_return_pair("file_read", "c1", {"path": "/x.py"}, "x" * 5000)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="t1")]),
        big_call,
        big_ret,
    ]

    # Tiny budget so even after strip the markers exceed; with one turn group,
    # plan_compaction_boundaries returns None (len(groups) < 2).
    ctx = _make_ctx(model_max_context_tokens=1)
    recovered = await recover_overflow_history(ctx.deps, messages)

    assert recovered is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_max_context_tokens",
    [3000, 30],
    ids=["strip_only_fits", "strip_plus_summary"],
)
async def test_recover_preserves_tool_call_id_pairing(model_max_context_tokens: int):
    """Both recovery paths preserve ToolCallPart/ToolReturnPart pairing by tool_call_id.

    Property: every call_id in a remaining ToolCallPart has a paired
    ToolReturnPart with the same id, and vice versa. Strip-only path holds
    by construction; summarize path holds because apply_compaction's middle-drop
    respects UserPromptPart group boundaries (calls and returns sit inside one
    turn group).
    """
    msgs: list = []
    for i in range(5):
        msgs.append(ModelRequest(parts=[UserPromptPart(content=f"t{i}")]))
        call, ret = _call_return_pair("file_read", f"call-{i}", {"path": f"/f{i}.py"}, "x" * 1000)
        msgs.append(call)
        msgs.append(ret)
    msgs.append(ModelRequest(parts=[UserPromptPart(content="pending")]))

    ctx = _make_ctx(model_max_context_tokens=model_max_context_tokens)
    recovered = await recover_overflow_history(ctx.deps, msgs)
    assert recovered is not None

    call_ids, return_ids = _all_tool_call_ids(recovered)
    assert call_ids == return_ids, "orphan call_id or return_id after recovery"


def test_strip_is_idempotent_on_marked_content():
    """Re-running strip on already-marked content preserves the existing marker.

    Property: ``_build_cleared_part``'s short-circuit prevents the size signal
    from degrading when EVICT has already replaced a return with a marker and
    recovery strip then runs over the same history.
    """
    marker = "[file_read] /x.py (full, 8,432 chars)"
    call_part = ToolCallPart(tool_name="file_read", args={"path": "/x.py"}, tool_call_id="c1")
    return_part = ToolReturnPart(tool_name="file_read", tool_call_id="c1", content=marker)
    messages = [
        ModelResponse(parts=[call_part]),
        ModelRequest(parts=[return_part]),
    ]

    stripped = strip_all_tool_returns(messages)

    assert stripped[1].parts[0].content == marker
