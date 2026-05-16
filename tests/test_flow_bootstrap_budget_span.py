"""Bootstrap ``tool_budget.resolved`` span emission contract."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from co_cli.bootstrap.core import _emit_tool_budget_span
from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN
from co_cli.tools.tool_io import SPILL_THRESHOLD_CHARS


def test_tool_budget_resolved_span_emits_all_attrs():
    """_emit_tool_budget_span fires exactly one span with all five budget attributes."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("co-cli.tool_budget")

    model_max_ctx = 32768
    spill_ratio = 0.5
    spill_threshold_tokens = int(spill_ratio * model_max_ctx)
    _emit_tool_budget_span(
        model_max_ctx=model_max_ctx,
        spill_ratio=spill_ratio,
        spill_threshold_tokens=spill_threshold_tokens,
        _tracer=tracer,
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1, f"Expected 1 span, got {len(spans)}"
    span = spans[0]
    assert span.name == "tool_budget.resolved"
    attrs = dict(span.attributes)
    assert attrs["budget.context_window_tokens"] == model_max_ctx
    assert attrs["budget.spill_ratio"] == spill_ratio
    assert attrs["budget.tool_call_limit"] == MAX_TOOL_CALLS_PER_MODEL_TURN
    assert attrs["budget.spill_threshold_chars"] == SPILL_THRESHOLD_CHARS
    assert attrs["budget.spill_threshold_tokens"] == spill_threshold_tokens
