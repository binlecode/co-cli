"""Bootstrap tool_budget.resolved span emission and Ollama num_ctx floor contract."""

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from co_cli.agent.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN
from co_cli.bootstrap.core import _check_ollama_num_ctx_floor, _emit_tool_budget_span
from co_cli.tools.tool_io import SPILL_THRESHOLD_CHARS


def test_tool_budget_resolved_span_emits_all_attrs():
    """_emit_tool_budget_span fires exactly one span with all five budget attributes."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("co-cli.tool_budget")

    model_max_ctx = 32768
    tail_fraction = 0.8
    _emit_tool_budget_span(
        model_max_ctx=model_max_ctx, tail_fraction=tail_fraction, _tracer=tracer
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1, f"Expected 1 span, got {len(spans)}"
    span = spans[0]
    assert span.name == "tool_budget.resolved"
    attrs = dict(span.attributes)
    assert attrs["budget.context_window_tokens"] == model_max_ctx
    assert attrs["budget.tail_fraction"] == tail_fraction
    assert attrs["budget.tool_call_limit"] == MAX_TOOL_CALLS_PER_MODEL_TURN
    assert attrs["budget.spill_threshold_chars"] == SPILL_THRESHOLD_CHARS
    assert attrs["budget.turn_aggregate_threshold_tokens"] == int(tail_fraction * model_max_ctx)


def test_ollama_num_ctx_floor_raises_when_undercut():
    """Floor check raises ValueError naming both values when num_ctx < max_ctx."""
    with pytest.raises(ValueError, match="num_ctx=32,768") as exc_info:
        _check_ollama_num_ctx_floor(32_768, "mymodel:7b", 65_536)
    assert "65,536" in str(exc_info.value)


def test_ollama_num_ctx_floor_passes_at_and_above_max_ctx():
    """Floor check does not raise when num_ctx equals or exceeds max_ctx."""
    _check_ollama_num_ctx_floor(65_536, "mymodel:7b", 65_536)
    _check_ollama_num_ctx_floor(131_072, "mymodel:7b", 65_536)
