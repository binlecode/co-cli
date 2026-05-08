"""Tests for enforce_request_size — per-request size control history processor.

Replaces the old per-batch L2 hook (``_enforce_request_budget``). The processor
runs at every ``ModelRequestNode`` entry, after dedup/evict, before
``proactive_window_processor``. It walks the **full message list** (not a
batch) and force-spills the largest unspilled ``ToolReturnPart``s until total
tokens fall to ``deps.spill_threshold_tokens`` or candidates exhaust.
"""

from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
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

from co_cli.context.history_processors import enforce_request_size
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG


def _make_deps(
    tmp_path: Path,
    *,
    threshold_tokens: int,
    model_max_ctx: int = 131_072,
) -> CoDeps:
    """Build a minimal CoDeps suitable for enforce_request_size tests."""
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path,
        model_max_ctx=model_max_ctx,
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
        _tool_response("shell", "tc1"),
        _tool_request("shell", "tc1", "a" * 3_000),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=50_000)

    out = enforce_request_size(_ctx(deps), messages)

    assert out is messages
    returns = _collect_returns(out)
    assert PERSISTED_OUTPUT_TAG not in returns["tc1"]
    assert deps.runtime.current_request_tokens_after_spill is not None
    assert deps.runtime.current_request_tokens_after_spill <= 50_000


@pytest.mark.asyncio
async def test_force_spill_largest_first(tmp_path: Path):
    """Three returns total over threshold: largest two spill, smallest stays."""
    content_small = "s" * 16_000
    content_mid = "m" * 24_000
    content_large = "l" * 32_000

    messages: list[ModelMessage] = [
        _user_request("do stuff"),
        _tool_response("shell", "tc_small"),
        _tool_request("shell", "tc_small", content_small),
        _tool_response("shell", "tc_mid"),
        _tool_request("shell", "tc_mid", content_mid),
        _tool_response("shell", "tc_large"),
        _tool_request("shell", "tc_large", content_large),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=5_000)

    out = enforce_request_size(_ctx(deps), messages)

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
        _tool_response("shell", "tc1"),
        _tool_request("shell", "tc1", content),
        _tool_response("shell", "tc2"),
        _tool_request("shell", "tc2", content),
        _tool_response("shell", "tc3"),
        _tool_request("shell", "tc3", content),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=6_000)

    out = enforce_request_size(_ctx(deps), messages)

    returns = _collect_returns(out)
    spilled = sum(1 for c in returns.values() if PERSISTED_OUTPUT_TAG in c)
    assert spilled >= 2, f"expected at least 2 of 3 batches spilled, got {spilled}"


@pytest.mark.asyncio
async def test_uses_cached_threshold(tmp_path: Path):
    """Processor reads deps.spill_threshold_tokens — no recomputation."""
    content = "t" * 52_000
    messages: list[ModelMessage] = [
        _user_request("cmd"),
        _tool_response("shell", "tc1"),
        _tool_request("shell", "tc1", content),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=12_000)

    out = enforce_request_size(_ctx(deps), messages)

    returns = _collect_returns(out)
    assert PERSISTED_OUTPUT_TAG in returns["tc1"]
    assert deps.runtime.current_request_tokens_after_spill is not None
    assert deps.runtime.current_request_tokens_after_spill <= 12_000


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
        _tool_response("shell", "tc1"),
        _tool_request("shell", "tc1", stub),
        _tool_response("shell", "tc2"),
        _tool_request("shell", "tc2", stub),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=100)

    out = enforce_request_size(_ctx(deps), messages)

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

    out = enforce_request_size(_ctx(deps), messages)

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
        _tool_response("shell", "tc_stub"),
        _tool_request("shell", "tc_stub", stub),
        _tool_response("shell", "tc_fresh"),
        _tool_request("shell", "tc_fresh", fresh),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=4_000)

    out = enforce_request_size(_ctx(deps), messages)

    returns = _collect_returns(out)
    assert returns["tc_stub"] == stub, "already-spilled stub must not be re-spilled"
    assert PERSISTED_OUTPUT_TAG in returns["tc_fresh"]


def test_otel_span_emitted(tmp_path: Path):
    """tool_budget.enforce_request_size span fires with required attributes."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("co-cli.tool_budget")

    content = "z" * 32_000
    messages: list[ModelMessage] = [
        _user_request("cmd"),
        _tool_response("shell", "tc1"),
        _tool_request("shell", "tc1", content),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=4_000)

    enforce_request_size(_ctx(deps), messages, _tracer=tracer)

    spans = [
        s for s in exporter.get_finished_spans() if s.name == "tool_budget.enforce_request_size"
    ]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes)
    assert attrs["request.threshold_tokens"] == 4_000
    assert attrs["request.tokens_before"] >= 8_000
    assert attrs["request.spill_fired"] is True
    assert attrs["request.spilled_count"] == 1
    assert attrs["request.skip_reason"] == ""


def test_high_reported_emits_local_and_reported_attrs(tmp_path: Path):
    """Provider-reported tokens dominate local estimate: span emits both attributes.

    When reported >> local and local content is small enough that spilling won't
    close the gap, the processor punts to fallback_to_summarize while still
    emitting the new local_tokens / reported_tokens span attributes.  This
    verifies the trigger uses max(local, reported) and the loop accumulator
    stays in local space — so no spill fires when local is already under
    threshold even though reported is far above it.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("co-cli.tool_budget")

    small_content = "result: " + "x" * 200
    messages: list[ModelMessage] = [
        _user_request("run"),
        # Provider reports 20K input tokens; local content is far below threshold.
        ModelResponse(
            parts=[ToolCallPart(tool_name="shell", args={}, tool_call_id="tc1")],
            usage=RequestUsage(input_tokens=20_000),
        ),
        _tool_request("shell", "tc1", small_content),
    ]
    # threshold = 8K; local ≈ 50 tokens < threshold < reported = 20K.
    # trigger fires (max(50, 20K) > 8K), but loop exits without spilling
    # because local_total < threshold → effective_after = max(50, 20K) → fallback.
    deps = _make_deps(tmp_path, threshold_tokens=8_000)

    result = enforce_request_size(_ctx(deps), messages, _tracer=tracer)

    # Content unchanged: local space had nothing to spill.
    returns = _collect_returns(result)
    assert not returns["tc1"].startswith(PERSISTED_OUTPUT_TAG)

    spans = [
        s for s in exporter.get_finished_spans() if s.name == "tool_budget.enforce_request_size"
    ]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes)
    assert attrs["request.reported_tokens"] == 20_000
    assert attrs["request.local_tokens"] < 8_000
    assert attrs["request.spill_fired"] is False
    assert attrs["request.skip_reason"] == "fallback_to_summarize"


def test_high_reported_spill_uses_local_loop_space(tmp_path: Path):
    """Loop accumulator stays in local space even when reported >> local.

    Large local content (spill can close the local gap) + high reported tokens.
    Spill fires because local_total > threshold. After spill the loop exits when
    local_after <= threshold, then effective_after = max(local_after, reported)
    is used for the span and skip_reason.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("co-cli.tool_budget")

    # 40K chars ≈ 10K tokens > 8K threshold in local space.
    big_content = "data: " + "y" * 40_000
    messages: list[ModelMessage] = [
        _user_request("run"),
        # Provider also reports high tokens — reported > threshold too.
        ModelResponse(
            parts=[ToolCallPart(tool_name="shell", args={}, tool_call_id="tc1")],
            usage=RequestUsage(input_tokens=20_000),
        ),
        _tool_request("shell", "tc1", big_content),
    ]
    deps = _make_deps(tmp_path, threshold_tokens=8_000)

    result = enforce_request_size(_ctx(deps), messages, _tracer=tracer)

    # Spill fires on the large return.
    returns = _collect_returns(result)
    assert returns["tc1"].startswith(PERSISTED_OUTPUT_TAG)

    spans = [
        s for s in exporter.get_finished_spans() if s.name == "tool_budget.enforce_request_size"
    ]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes)
    assert attrs["request.reported_tokens"] == 20_000
    assert attrs["request.local_tokens"] > 8_000
    assert attrs["request.spill_fired"] is True
    # effective_after = max(local_after_small, 20K) still > 8K threshold →
    # proactive will handle the remaining reported pressure.
    assert attrs["request.skip_reason"] == "fallback_to_summarize"
