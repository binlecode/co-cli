"""Consolidated E2E tests for test_flow_observability_redaction."""

import json
import sqlite3

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from co_cli.observability.telemetry import SQLiteSpanExporter


def _make_provider(db_path: str, patterns: list[str] | None = None) -> TracerProvider:
    exporter = SQLiteSpanExporter(db_path=db_path, redact_patterns=patterns)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def test_sk_api_key_attribute_redacted(tmp_path):
    """An sk- API key in a span attribute is stored as [REDACTED]."""
    db = str(tmp_path / "spans.db")
    provider = _make_provider(db, patterns=[r"sk-[A-Za-z0-9]{20,}"])
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("redact.api_key") as span:
        span.set_attribute("secret_value", "sk-abc123abc123abc123abc")

    provider.force_flush()

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT attributes FROM spans WHERE name = 'redact.api_key'").fetchone()
    conn.close()

    attributes = json.loads(row[0])
    assert attributes["secret_value"] == "[REDACTED]"
