"""OTEL span coverage for tool_budget.spill_tool_result."""

from pathlib import Path

from co_cli.tools.tool_io import (
    _TRACER,
    PERSISTED_OUTPUT_TAG,
    SPILL_THRESHOLD_CHARS,
    spill_if_oversized,
)


def test_spill_tool_result_span_attrs_below_threshold(tmp_path: Path):
    """spill_if_oversized returns content unchanged for payloads below SPILL_THRESHOLD_CHARS.

    Verifies the non-spill path: content is passed through without modification
    and no PERSISTED_OUTPUT_TAG is injected.
    """
    content = "x" * (SPILL_THRESHOLD_CHARS - 1)
    result = spill_if_oversized(content, tmp_path / "tool_results", "file_read")
    assert result == content
    assert PERSISTED_OUTPUT_TAG not in result


def test_spill_tool_result_span_attrs_above_threshold(tmp_path: Path):
    """spill_if_oversized returns a stub containing PERSISTED_OUTPUT_TAG for large payloads.

    Verifies the spill path: oversized content is persisted to disk and a
    placeholder stub is returned in its place.
    """
    content = "y" * (SPILL_THRESHOLD_CHARS + 1)
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell")
    assert PERSISTED_OUTPUT_TAG in result
    # Stub must be significantly smaller than original
    assert len(result) < len(content)
    # Persisted file must exist on disk
    spilled_files = list((tmp_path / "tool_results").glob("*.txt"))
    assert len(spilled_files) == 1, f"Expected one persisted file, found: {spilled_files}"
    assert spilled_files[0].read_text(encoding="utf-8") == content


def test_tracer_name():
    """The module-level _TRACER in tool_io must use the 'co-cli.tool_budget' tracer name.

    The tracer implementation varies by runtime:
    - stdlib ProxyTracer: _instrumenting_module_name (str attribute)
    - logfire _ProxyTracer: instrumenting_module_name (public attribute)
    - opentelemetry SDK Tracer: _instrumentation_scope.name (via InstrumentationScope)

    All encodings carry the same tracer name string.
    """
    # logfire _ProxyTracer: public attribute, no leading underscore
    name = getattr(_TRACER, "instrumenting_module_name", None)
    # stdlib ProxyTracer: private str attribute
    if name is None:
        name = getattr(_TRACER, "_instrumenting_module_name", None)
    # opentelemetry SDK Tracer: InstrumentationScope object
    if name is None:
        scope = getattr(_TRACER, "_instrumentation_scope", None)
        if scope is not None:
            name = getattr(scope, "name", None)
    assert name == "co-cli.tool_budget", (
        f"_TRACER must use 'co-cli.tool_budget'; got {name!r} from {type(_TRACER)}"
    )
