"""Functional tests for SQLiteSpanExporter span attribute redaction.

Tests use a real SQLiteSpanExporter with a real SQLite DB (tmp_path).
No mocks or fakes.
"""

import json
import sqlite3

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from co_cli.observability._telemetry import _MAX_REDACT_LEN, SQLiteSpanExporter


def _make_provider(db_path: str, patterns: list[str] | None = None) -> TracerProvider:
    exporter = SQLiteSpanExporter(db_path=db_path, redact_patterns=patterns)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def _fetch_span(db_path: str, span_name: str) -> dict:
    """Return the first span row matching span_name as a parsed dict."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT trace_id, parent_id, name, attributes, events FROM spans WHERE name = ?",
        (span_name,),
    ).fetchone()
    conn.close()
    assert row is not None, f"No span named {span_name!r} found in {db_path}"
    return {
        "trace_id": row[0],
        "parent_id": row[1],
        "name": row[2],
        "attributes": json.loads(row[3]),
        "events": json.loads(row[4]),
    }


def test_sk_api_key_attribute_redacted(tmp_path):
    """An sk- API key in a span attribute is stored as [REDACTED]."""
    db = str(tmp_path / "spans.db")
    provider = _make_provider(db, patterns=[r"sk-[A-Za-z0-9]{20,}"])
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("redact.api_key") as span:
        span.set_attribute("secret_value", "sk-abc123abc123abc123abc")

    provider.force_flush()

    row = _fetch_span(db, "redact.api_key")
    assert row["attributes"]["secret_value"] == "[REDACTED]"


def test_bearer_token_in_event_attribute_redacted(tmp_path):
    """A Bearer token in a span event attribute is stored as [REDACTED]."""
    db = str(tmp_path / "spans.db")
    provider = _make_provider(db, patterns=[r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}"])
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("redact.bearer") as span:
        span.add_event("auth_attempt", {"auth_header": "Bearer abc123abc123abc123abc"})

    provider.force_flush()

    row = _fetch_span(db, "redact.bearer")
    assert len(row["events"]) == 1
    assert row["events"][0]["attributes"]["auth_header"] == "[REDACTED]"


def test_non_sensitive_value_passes_through_unchanged(tmp_path):
    """A span attribute with no sensitive value is stored verbatim."""
    db = str(tmp_path / "spans.db")
    provider = _make_provider(db, patterns=[r"sk-[A-Za-z0-9]{20,}"])
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("redact.clean") as span:
        span.set_attribute("tool_name", "memory_search")
        span.set_attribute("result_count", 5)

    provider.force_flush()

    row = _fetch_span(db, "redact.clean")
    assert row["attributes"]["tool_name"] == "memory_search"
    assert row["attributes"]["result_count"] == 5


def test_span_identity_fields_not_modified(tmp_path):
    """trace_id, parent_id, and span name are never touched by redaction."""
    db = str(tmp_path / "spans.db")
    provider = _make_provider(db, patterns=[r"sk-[A-Za-z0-9]{20,}"])
    tracer = provider.get_tracer("test")

    expected_name = "redact.identity"
    with tracer.start_as_current_span(expected_name) as span:
        span.set_attribute("api_token", "sk-abc123abc123abc123abc")

    provider.force_flush()

    row = _fetch_span(db, expected_name)
    assert row["name"] == expected_name
    # trace_id is a 32-hex string — must remain a valid hex value
    assert len(row["trace_id"]) == 32
    assert all(c in "0123456789abcdef" for c in row["trace_id"])


def test_value_exceeding_max_redact_len_stored_unredacted(tmp_path):
    """Values longer than _MAX_REDACT_LEN bypass redaction (performance guard)."""
    db = str(tmp_path / "spans.db")
    provider = _make_provider(db, patterns=[r"sk-[A-Za-z0-9]{20,}"])
    tracer = provider.get_tracer("test")

    # Build a value that embeds a secret but exceeds the size threshold
    large_value = "sk-abc123abc123abc123abc " + "x" * _MAX_REDACT_LEN

    with tracer.start_as_current_span("redact.large") as span:
        span.set_attribute("large_attr", large_value)

    provider.force_flush()

    row = _fetch_span(db, "redact.large")
    # Must be stored unchanged — secret NOT redacted for oversized values
    assert row["attributes"]["large_attr"] == large_value
