"""Functional tests for JsonSpanExporter and _JsonRedactingFormatter.

All tests use real objects — real TracerProvider, real SQLite, real filesystem.
No mocks or fakes.
"""

import json
import logging

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from co_cli.observability._file_logging import _JsonRedactingFormatter, setup_file_logging
from co_cli.observability._telemetry import JsonSpanExporter


def _make_span_provider(patterns: list[str] | None = None) -> tuple[TracerProvider, list[str]]:
    """Return a TracerProvider wired to a JsonSpanExporter that captures emitted lines."""
    emitted: list[str] = []

    exporter = JsonSpanExporter(redact_patterns=patterns)

    # Capture output via a handler on the spans logger instead of letting it propagate
    spans_logger = logging.getLogger(JsonSpanExporter._logger_name)
    handler = _CapturingHandler(emitted)
    spans_logger.addHandler(handler)
    spans_logger.propagate = False  # isolate from root during tests

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    return provider, emitted, handler, spans_logger


class _CapturingHandler(logging.Handler):
    def __init__(self, buf: list[str]) -> None:
        super().__init__()
        self._buf = buf

    def emit(self, record: logging.LogRecord) -> None:
        self._buf.append(record.getMessage())


def _teardown_logger(spans_logger: logging.Logger, handler: logging.Handler) -> None:
    spans_logger.removeHandler(handler)
    spans_logger.propagate = True


def test_json_span_exporter_emits_kind_span(tmp_path):
    """Each exported span produces a JSON line with kind='span'."""
    provider, emitted, handler, spans_logger = _make_span_provider()
    try:
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test.op"):
            pass
        provider.force_flush()

        assert len(emitted) == 1
        record = json.loads(emitted[0])
        assert record["kind"] == "span"
        assert record["name"] == "test.op"
    finally:
        _teardown_logger(spans_logger, handler)


def test_json_span_exporter_all_fields_present(tmp_path):
    """Exported span JSON contains all required fields."""
    provider, emitted, handler, spans_logger = _make_span_provider()
    try:
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test.fields") as span:
            span.set_attribute("tool_name", "search")

        provider.force_flush()

        record = json.loads(emitted[0])
        for field in (
            "ts",
            "kind",
            "span_id",
            "trace_id",
            "name",
            "attributes",
            "events",
            "duration_ms",
            "status",
        ):
            assert field in record, f"missing field: {field}"
        assert len(record["span_id"]) == 16
        assert len(record["trace_id"]) == 32
        assert record["attributes"]["tool_name"] == "search"
    finally:
        _teardown_logger(spans_logger, handler)


def test_json_span_exporter_redacts_attribute_value(tmp_path):
    """String attribute matching a redact pattern is stored as [REDACTED]."""
    provider, emitted, handler, spans_logger = _make_span_provider(
        patterns=[r"sk-[A-Za-z0-9]{20,}"]
    )
    try:
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test.redact") as span:
            span.set_attribute("api_key", "sk-abc123abc123abc123abc")

        provider.force_flush()

        record = json.loads(emitted[0])
        assert record["attributes"]["api_key"] == "[REDACTED]"
    finally:
        _teardown_logger(spans_logger, handler)


def test_json_span_exporter_captures_events(tmp_path):
    """Span events (name + attributes) are included in the JSON output."""
    provider, emitted, handler, spans_logger = _make_span_provider()
    try:
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test.events") as span:
            span.add_event("provider_error", {"http.status_code": 429})

        provider.force_flush()

        record = json.loads(emitted[0])
        assert len(record["events"]) == 1
        assert record["events"][0]["name"] == "provider_error"
        assert record["events"][0]["attributes"]["http.status_code"] == 429
    finally:
        _teardown_logger(spans_logger, handler)


def test_json_span_exporter_parent_id_set_for_child(tmp_path):
    """Child span has parent_id set to the parent span's span_id."""
    provider, emitted, handler, spans_logger = _make_span_provider()
    try:
        tracer = provider.get_tracer("test")
        with (
            tracer.start_as_current_span("parent.span"),
            tracer.start_as_current_span("child.span"),
        ):
            pass

        provider.force_flush()

        records = [json.loads(line) for line in emitted]
        child = next(r for r in records if r["name"] == "child.span")
        parent = next(r for r in records if r["name"] == "parent.span")
        assert child["parent_id"] == parent["span_id"]
    finally:
        _teardown_logger(spans_logger, handler)


def test_json_formatter_log_record_structure(tmp_path):
    """A plain log record produces a JSON line with kind='log' and correct fields."""
    formatter = _JsonRedactingFormatter()
    record = logging.LogRecord(
        name="co_cli.test",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="something happened",
        args=(),
        exc_info=None,
    )
    line = formatter.format(record)
    parsed = json.loads(line)

    assert parsed["kind"] == "log"
    assert parsed["level"] == "WARNING"
    assert parsed["logger"] == "co_cli.test"
    assert parsed["msg"] == "something happened"
    assert parsed["ts"].endswith("Z")


def test_json_formatter_passthrough_span_json(tmp_path):
    """A pre-serialised JSON dict (span record) is passed through as-is."""
    formatter = _JsonRedactingFormatter()
    span_dict = {"ts": "2026-01-01T00:00:00.000Z", "kind": "span", "name": "test.op"}
    record = logging.LogRecord(
        name="co_cli.observability.spans",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=json.dumps(span_dict),
        args=(),
        exc_info=None,
    )
    line = formatter.format(record)
    parsed = json.loads(line)

    assert parsed["kind"] == "span"
    assert parsed["name"] == "test.op"


def test_setup_file_logging_writes_jsonl(tmp_path):
    """setup_file_logging writes JSON lines to co-cli.jsonl on the root logger."""
    log_dir = tmp_path / "logs"
    setup_file_logging(log_dir=log_dir, level="DEBUG", max_size_mb=1, backup_count=1)

    test_logger = logging.getLogger("co_cli.test_jsonl_write")
    test_logger.info("jsonl test message")

    # Flush handlers
    for handler in logging.getLogger().handlers:
        handler.flush()

    jsonl_path = log_dir / "co-cli.jsonl"
    assert jsonl_path.exists(), "co-cli.jsonl was not created"

    lines = [ln for ln in jsonl_path.read_text().splitlines() if ln.strip()]
    assert any(
        json.loads(ln).get("msg") == "jsonl test message" for ln in lines if ln.startswith("{")
    ), "expected log message not found in co-cli.jsonl"
