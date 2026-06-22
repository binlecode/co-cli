"""Tests for spill_largest_tool_results — per-request size control history processor.

Replaces the old per-batch L2 hook (``_enforce_request_budget``). The processor
runs at every ``ModelRequestNode`` entry, after dedup/evict, before
``proactive_window_processor``. It walks the **full message list** (not a
batch) and force-spills the largest unspilled ``ToolReturnPart``s until total
tokens fall to ``deps.spill_threshold_tokens`` or candidates exhaust.
"""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage, RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.config.tuning import PERSISTED_OUTPUT_TAG
from co_cli.context.history_processors import spill_largest_tool_results
from co_cli.context.summarization import estimate_message_tokens
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.observability import tracing
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(
    tmp_path: Path,
    *,
    threshold_tokens: int,
    model_max_context_tokens: int = 131_072,
) -> CoDeps:
    """Build a minimal CoDeps suitable for spill_largest_tool_results tests."""
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path,
        model_max_context_tokens=model_max_context_tokens,
        spill_threshold_tokens=threshold_tokens,
    )


def _ctx(deps: CoDeps) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _user_request(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _tool_response(tool_name: str, call_id: str, args: dict | None = None) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool_name, args=args or {}, tool_call_id=call_id)]
    )


def _tool_request(tool_name: str, call_id: str, content: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id=call_id)]
    )


def _run_capturing_event(
    deps: CoDeps, messages: list[ModelMessage]
) -> tuple[list[ModelMessage], dict]:
    """Run the processor inside a real span and return (out, terminal event attrs).

    Uses the live tracing stack (push_span/pop_span) — no mocks — so the
    ``tool_budget.spill_largest_tool_results`` event the processor emits via
    ``current_span().add_event`` is captured on the span dict.
    """
    span = tracing.push_span("test.spill")
    try:
        out = spill_largest_tool_results(_ctx(deps), messages)
    finally:
        tracing.pop_span()
    events = [e for e in span["events"] if e["name"] == "tool_budget.spill_largest_tool_results"]
    return out, events[-1]["attributes"]


def _collect_returns(messages: list[ModelMessage]) -> dict[str, str]:
    """Map tool_call_id -> content for every ToolReturnPart in the message list."""
    out: dict[str, str] = {}
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, ToolReturnPart) and isinstance(part.content, str):
                out[part.tool_call_id] = part.content
    return out


@pytest.mark.asyncio
async def test_below_threshold_fast_path(tmp_path: Path):
    """Total tokens below threshold: no rewrite, no mutation."""
    messages: list[ModelMessage] = [
        _user_request("hi"),
        _tool_response("shell_exec", "tc1"),
        _tool_request("shell_exec", "tc1", "a" * 3_000),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=50_000)

    out = spill_largest_tool_results(_ctx(deps), messages)

    assert out is messages
    returns = _collect_returns(out)
    assert PERSISTED_OUTPUT_TAG not in returns["tc1"]
    assert deps.runtime.current_request_tokens_estimate is not None
    assert deps.runtime.current_request_tokens_estimate <= 50_000


@pytest.mark.asyncio
async def test_force_spill_largest_first(tmp_path: Path):
    """Three returns total over threshold: largest two spill, smallest stays."""
    content_small = "s" * 16_000
    content_mid = "m" * 24_000
    content_large = "l" * 32_000

    messages: list[ModelMessage] = [
        _user_request("do stuff"),
        _tool_response("shell_exec", "tc_small"),
        _tool_request("shell_exec", "tc_small", content_small),
        _tool_response("shell_exec", "tc_mid"),
        _tool_request("shell_exec", "tc_mid", content_mid),
        _tool_response("shell_exec", "tc_large"),
        _tool_request("shell_exec", "tc_large", content_large),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=5_000)

    out = spill_largest_tool_results(_ctx(deps), messages)

    returns = _collect_returns(out)
    assert returns["tc_small"] == content_small, "smallest must remain unspilled"
    assert PERSISTED_OUTPUT_TAG in returns["tc_mid"]
    assert PERSISTED_OUTPUT_TAG in returns["tc_large"]
    assert sum(1 for c in returns.values() if PERSISTED_OUTPUT_TAG in c) == 2


@pytest.mark.asyncio
async def test_cross_batch_accumulation(tmp_path: Path):
    """Multiple batches across the message list each modest in size: total trips threshold.

    Three separate ToolReturnPart messages of 24K chars each = 18K tokens total.
    Threshold = 6K tokens. The OLD per-batch L2 enforcer would have skipped each
    batch (each is only 6K tokens). The NEW per-request enforcer sees the
    aggregate and spills the largest until aggregate fits.
    """
    content = "x" * 24_000
    messages: list[ModelMessage] = [
        _user_request("multi-batch"),
        _tool_response("shell_exec", "tc1"),
        _tool_request("shell_exec", "tc1", content),
        _tool_response("shell_exec", "tc2"),
        _tool_request("shell_exec", "tc2", content),
        _tool_response("shell_exec", "tc3"),
        _tool_request("shell_exec", "tc3", content),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=6_000)

    out = spill_largest_tool_results(_ctx(deps), messages)

    returns = _collect_returns(out)
    spilled = sum(1 for c in returns.values() if PERSISTED_OUTPUT_TAG in c)
    assert spilled >= 2, f"expected at least 2 of 3 batches spilled, got {spilled}"


@pytest.mark.asyncio
async def test_all_spilled_bail_out(tmp_path: Path):
    """When every candidate is already a persisted-output stub, skip with all_spilled."""
    stub = (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"This tool result was too large (50000 chars, 48.8 KB).\n"
        f"tool: shell\nfile: /tmp/abc123.txt\npreview:\n{'x' * 800}\n"
        f"</persisted-output>"
    )
    messages: list[ModelMessage] = [
        _user_request("cmd"),
        _tool_response("shell_exec", "tc1"),
        _tool_request("shell_exec", "tc1", stub),
        _tool_response("shell_exec", "tc2"),
        _tool_request("shell_exec", "tc2", stub),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=100)

    out = spill_largest_tool_results(_ctx(deps), messages)

    assert out is messages, "messages must be returned unchanged"
    returns = _collect_returns(out)
    assert returns["tc1"] == stub
    assert returns["tc2"] == stub


@pytest.mark.asyncio
async def test_no_candidates_text_only_history(tmp_path: Path):
    """No ToolReturnParts at all: oversize text history hands off to proactive."""
    big_text = "narrative " * 5_000
    messages: list[ModelMessage] = [
        _user_request(big_text),
        ModelResponse(parts=[TextPart(content=big_text)]),
        _user_request(big_text),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=100)

    out = spill_largest_tool_results(_ctx(deps), messages)

    assert out is messages, "no rewrite when there are no tool returns to spill"


@pytest.mark.asyncio
async def test_already_spilled_excluded_but_counted(tmp_path: Path):
    """Already-spilled stubs count toward tokens_before but aren't re-spilled.

    One persisted stub (excluded from spillable) + one fresh oversized return.
    Threshold tripped by the aggregate; only the fresh return gets spilled.
    """
    stub = (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"tool: shell\nfile: /tmp/abc.txt\n"
        f"preview:\n{'p' * 1_000}\n"
        f"</persisted-output>"
    )
    fresh = "f" * 32_000
    messages: list[ModelMessage] = [
        _user_request("cmd"),
        _tool_response("shell_exec", "tc_stub"),
        _tool_request("shell_exec", "tc_stub", stub),
        _tool_response("shell_exec", "tc_fresh"),
        _tool_request("shell_exec", "tc_fresh", fresh),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=4_000)

    out = spill_largest_tool_results(_ctx(deps), messages)

    returns = _collect_returns(out)
    assert returns["tc_stub"] == stub, "already-spilled stub must not be re-spilled"
    assert PERSISTED_OUTPUT_TAG in returns["tc_fresh"]


def test_small_realtime_no_spill_despite_high_provider_usage(tmp_path: Path):
    """Trigger keys off the realtime payload, not a stale-high provider count.

    The prior ModelResponse reports ``input_tokens=20_000`` (what the old
    ``last_reported_input_tokens`` floor read from), but the actual string content
    is tiny. The realtime trigger is below threshold, so nothing spills — proving
    the removed ``max(.., reported)`` floor no longer inflates the decision.
    """
    small_content = "result: " + "x" * 200
    messages: list[ModelMessage] = [
        _user_request("run"),
        ModelResponse(
            parts=[ToolCallPart(tool_name="shell_exec", args={}, tool_call_id="tc1")],
            usage=RequestUsage(input_tokens=20_000),
        ),
        _tool_request("shell_exec", "tc1", small_content),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=8_000)

    result = spill_largest_tool_results(_ctx(deps), messages)

    assert result is messages
    returns = _collect_returns(result)
    assert not returns["tc1"].startswith(PERSISTED_OUTPUT_TAG)
    assert deps.runtime.current_request_tokens_estimate <= 8_000


@pytest.mark.asyncio
async def test_fresh_tail_return_survives_while_aged_return_spills(tmp_path: Path):
    """The freshest read (last turn group) is preserved; an equally large aged read spills.

    Two turn groups, each with a 32K-char (~8K-token) tool return. Aggregate
    (~16K tokens) trips the 5K threshold. The protected tail spans the last
    turn group, so the fresh return at index 5 must NOT be stubbed even under
    pressure, while the aged return at index 2 (before ``tail_start``) does.
    This is the model-visibility invariant: a read is seen once before it
    becomes spill-eligible.
    """
    aged = "a" * 32_000
    fresh = "f" * 32_000
    messages: list[ModelMessage] = [
        _user_request("turn one — old work"),
        _tool_response("shell_exec", "tc_aged"),
        _tool_request("shell_exec", "tc_aged", aged),
        _user_request("turn two — read this"),
        _tool_response("shell_exec", "tc_fresh"),
        _tool_request("shell_exec", "tc_fresh", fresh),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=5_000)

    out = spill_largest_tool_results(_ctx(deps), messages)

    returns = _collect_returns(out)
    assert not returns["tc_fresh"].startswith(PERSISTED_OUTPUT_TAG), (
        "freshest read in the last turn group must survive L2"
    )
    assert PERSISTED_OUTPUT_TAG in returns["tc_aged"], (
        "aged read before tail_start must still spill"
    )


@pytest.mark.asyncio
async def test_protected_tail_alone_over_threshold_defers_without_stubbing(tmp_path: Path):
    """When the only large return is the protected tail, L2 leaves it for the overflow path.

    The older turn's return is already a persisted stub, so nothing before
    ``tail_start`` is spillable. The fresh return (~15K tokens) sits in the
    protected tail and alone exceeds the 4K threshold. L2 must return without
    stubbing it — deferring to the HTTP-400 overflow-recovery path — rather
    than spilling the content the model is about to read.
    """
    stub = (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"tool: shell\nfile: /tmp/old.txt\npreview:\n{'p' * 800}\n"
        f"</persisted-output>"
    )
    fresh = "f" * 60_000
    messages: list[ModelMessage] = [
        _user_request("turn one"),
        _tool_response("shell_exec", "tc_old"),
        _tool_request("shell_exec", "tc_old", stub),
        _user_request("turn two — read this big file"),
        _tool_response("shell_exec", "tc_fresh"),
        _tool_request("shell_exec", "tc_fresh", fresh),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=4_000)

    out = spill_largest_tool_results(_ctx(deps), messages)

    returns = _collect_returns(out)
    assert not returns["tc_fresh"].startswith(PERSISTED_OUTPUT_TAG), (
        "protected tail must not be stubbed even when it alone exceeds the threshold"
    )


@pytest.mark.asyncio
async def test_single_turn_group_has_no_tail_protection(tmp_path: Path):
    """With only one turn group the planner returns None: a fresh large read still spills.

    ``plan_compaction_boundaries`` needs at least two turn groups to form a
    tail. A single-turn transcript yields ``None``, so ``tail_start`` falls back
    to ``len(messages)`` and every candidate is spillable — pre-tail-protection
    behavior. This pins the second clause of TASK-1's done_when.
    """
    fresh = "f" * 40_000
    messages: list[ModelMessage] = [
        _user_request("only turn — read this"),
        _tool_response("shell_exec", "tc_fresh"),
        _tool_request("shell_exec", "tc_fresh", fresh),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=4_000)

    out = spill_largest_tool_results(_ctx(deps), messages)

    returns = _collect_returns(out)
    assert PERSISTED_OUTPUT_TAG in returns["tc_fresh"], (
        "without a second turn group there is no tail to protect — the read spills"
    )


def test_large_realtime_spills_and_post_spill_estimate_is_realtime(tmp_path: Path):
    """Spill fires on realtime payload; post-spill estimate is realtime, not floored.

    Exercises the post-spill ``effective_after`` site: after the large return spills
    and the realtime payload drops under threshold, the recorded estimate reflects
    that realtime value (no ``max(.., reported)`` pinning it high). A stale-high
    provider count on the prior response must not force a fallback-to-summarize.
    """
    big_content = "data: " + "y" * 40_000
    messages: list[ModelMessage] = [
        _user_request("run"),
        ModelResponse(
            parts=[ToolCallPart(tool_name="shell_exec", args={}, tool_call_id="tc1")],
            usage=RequestUsage(input_tokens=20_000),
        ),
        _tool_request("shell_exec", "tc1", big_content),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=8_000)

    result = spill_largest_tool_results(_ctx(deps), messages)

    returns = _collect_returns(result)
    assert returns["tc1"].startswith(PERSISTED_OUTPUT_TAG)
    assert deps.runtime.current_request_tokens_estimate <= 8_000


def _spill_content_lengths_for_drift(tmp_path: Path) -> list[int]:
    """Pick three content lengths whose freed-char remainders sum to a clean multiple of 4.

    The persisted-stub length depends on the (path-dependent) tool_results_dir,
    so it is measured at runtime. Freed-char remainders are tuned to ``1, 1, 2``
    (mod 4): the old per-item floor-division discards each remainder separately
    (losing a whole token), while a single final division recovers it — a
    1-token gap the threshold is wedged into. Because the remainders sum to a
    multiple of 4, the single-division aggregate equals a fresh full recount
    exactly (within 0 tokens), pinning the success_signal.
    """
    from co_cli.fileio.spill import spill_if_oversized

    probe = "z" * 30_000
    stub_len = len(spill_if_oversized(probe, tmp_path, "shell_exec", force=True))
    base = 30_000
    target_remainders = [1, 1, 2]
    lengths: list[int] = []
    for i, remainder in enumerate(target_remainders):
        length = base + i * 1_000
        freed = length - stub_len
        length += (remainder - freed % 4) % 4
        lengths.append(length)
    return lengths


@pytest.mark.asyncio
async def test_three_spills_under_threshold_classified_below_not_fallback(tmp_path: Path):
    """Three spills land just under threshold: classified done (empty skip_reason).

    Content lengths are tuned (at runtime, accounting for the path-dependent
    stub size) so each freed amount is ``≡ 3 (mod 4)``. With the old per-item
    floor-division the three terms each drop 3 chars, leaving the aggregate 2
    tokens HIGHER than a single final division. The threshold is wedged into
    that drift window — exactly one below the drift-free aggregate — so the
    buggy per-item path would misclassify this as ``fallback_to_summarize``
    while the correct single-division path classifies it done (empty
    skip_reason). Single turn group → no tail protection, all three spill.
    """
    lengths = _spill_content_lengths_for_drift(tmp_path)
    chars = ["a", "b", "c"]
    messages: list[ModelMessage] = [_user_request("do stuff")]
    for i, (length, ch) in enumerate(zip(lengths, chars, strict=True)):
        cid = f"tc{i}"
        messages.append(_tool_response("shell_exec", cid))
        messages.append(_tool_request("shell_exec", cid, ch * length))

    probe_messages = [type(m)(parts=list(m.parts)) for m in messages]
    probe_deps = _make_deps(tmp_path, threshold_tokens=1)
    probe_out, _ = _run_capturing_event(probe_deps, probe_messages)
    drift_free_aggregate = estimate_message_tokens(probe_out)

    deps = _make_deps(tmp_path, threshold_tokens=drift_free_aggregate)

    out, attrs = _run_capturing_event(deps, messages)

    spilled = sum(1 for c in _collect_returns(out).values() if PERSISTED_OUTPUT_TAG in c)
    assert spilled == 3, "all three returns spill in a single un-protected turn group"
    assert attrs["request.skip_reason"] == "", (
        "drift-free single division lands at threshold — must classify done, "
        "not fallback_to_summarize"
    )
    assert estimate_message_tokens(out) == deps.runtime.current_request_tokens_estimate, (
        "spill terminal decision must match a fresh full recount within 0 tokens"
    )


@pytest.mark.asyncio
async def test_nonzero_static_floor_keeps_spilling_until_floor_inclusive_fits(tmp_path: Path):
    """With a positive static floor, the loop spills until the floor-INCLUSIVE total fits.

    Regression for the frame-mismatch under-spill: the fire check and the
    done/fallback verdict both count ``static_floor_tokens + message_tokens``,
    but the spill loop used to be seeded with the message-only count. With a
    nonzero floor it therefore stopped ``static_floor_tokens`` short of the
    verdict's goal and reported ``fallback_to_summarize`` while cheap spill
    capacity remained.

    Sizes are measured at runtime and the threshold is pinned to the loop's
    post-first-spill local count, so a floor-blind loop spills only the larger
    return and lands above threshold once the floor is added (fallback). The
    floor-inclusive loop sees it is still over, spills the second return too,
    and lands done (empty skip_reason). Single turn group → no tail protection,
    both returns are spillable.
    """
    from co_cli.fileio.spill import spill_if_oversized

    big_len, small_len = 40_000, 30_000
    user_text = "do stuff"
    stub_big = len(spill_if_oversized("a" * big_len, tmp_path, "shell_exec", force=True))
    stub_small = len(spill_if_oversized("b" * small_len, tmp_path, "shell_exec", force=True))

    local_total = (big_len + small_len + len(user_text)) // 4
    freed_big = big_len - stub_big
    freed_small = small_len - stub_small
    local_after_big = local_total - freed_big // 4
    local_after_both = local_total - (freed_big + freed_small) // 4

    threshold = local_after_big
    static_floor = max(1, (threshold - local_after_both) // 2)

    messages: list[ModelMessage] = [
        _user_request(user_text),
        _tool_response("shell_exec", "tc_big"),
        _tool_request("shell_exec", "tc_big", "a" * big_len),
        _tool_response("shell_exec", "tc_small"),
        _tool_request("shell_exec", "tc_small", "b" * small_len),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=threshold)
    deps.static_floor_tokens = static_floor

    out, attrs = _run_capturing_event(deps, messages)

    returns = _collect_returns(out)
    assert returns["tc_big"].startswith(PERSISTED_OUTPUT_TAG)
    assert returns["tc_small"].startswith(PERSISTED_OUTPUT_TAG), (
        "floor-inclusive loop must spill the second return too — a floor-blind "
        "loop would stop after the first and defer to the summarizer"
    )
    assert attrs["request.skip_reason"] == "", (
        "floor-inclusive total fits after both spills — classify done, not fallback_to_summarize"
    )
    assert attrs["request.tokens_after"] <= threshold


@pytest.mark.asyncio
async def test_many_small_returns_emit_zero_spill_errors(tmp_path: Path):
    """Many ≤1500-char tool returns are not spill candidates: spill_errors stays 0.

    Each return is below ``SPILL_PREVIEW_CHARS`` (1500), so
    ``spill_if_oversized`` would return it unchanged — formerly counted as a
    spill I/O error per candidate. With the size pre-filter these are excluded
    from the spillable set entirely, so the terminal reason is ``all_spilled``
    (nothing spillable) and zero errors are recorded. Threshold is forced low
    so the processor proceeds past the fast path.
    """
    messages: list[ModelMessage] = [_user_request("do stuff")]
    for i in range(8):
        cid = f"tc{i}"
        messages.append(_tool_response("shell_exec", cid))
        messages.append(_tool_request("shell_exec", cid, "x" * 1_400))
    deps = _make_deps(tmp_path, threshold_tokens=100)

    out, attrs = _run_capturing_event(deps, messages)

    assert out is messages, "no return is large enough to spill — messages unchanged"
    assert attrs["request.spill_errors"] == 0, (
        "too-small returns must not count as spill I/O errors"
    )
    returns = _collect_returns(out)
    assert all(PERSISTED_OUTPUT_TAG not in c for c in returns.values())
