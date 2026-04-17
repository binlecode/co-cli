import json
import logging
import re
import sqlite3
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

from co_cli.config._core import LOGS_DB

logger = logging.getLogger(__name__)

# WAL concurrency settings
_BUSY_TIMEOUT_MS = 5000
_EXPORT_MAX_RETRIES = 3
_EXPORT_RETRY_BASE_SECONDS = 0.1

# Values longer than this are stored unredacted — scanning multi-hundred-KB
# JSON blobs is not justified for the default pattern set.
_MAX_REDACT_LEN = 65536


def _redact(value: str, patterns: list[re.Pattern]) -> str:
    """Replace all matches of each pattern in value with [REDACTED].

    Values exceeding _MAX_REDACT_LEN are returned unchanged.
    """
    if len(value) > _MAX_REDACT_LEN:
        return value
    for pattern in patterns:
        value = pattern.sub("[REDACTED]", value)
    return value


class SQLiteSpanExporter(SpanExporter):
    def __init__(
        self,
        db_path: str = str(LOGS_DB),
        redact_patterns: list[str] | None = None,
    ):
        self.db_path = db_path
        self._patterns: list[re.Pattern] = [re.compile(p) for p in (redact_patterns or [])]
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with WAL mode and busy_timeout for concurrent access."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            # Main spans table with parent tracking
            # Schema follows OTel data model: https://opentelemetry.io/docs/specs/otel/trace/api/
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spans (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_id TEXT,
                    name TEXT NOT NULL,
                    kind TEXT,
                    start_time INTEGER NOT NULL,
                    end_time INTEGER,
                    duration_ms REAL,
                    status_code TEXT,
                    status_description TEXT,
                    attributes TEXT,
                    events TEXT,
                    resource TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_parent ON spans(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_start ON spans(start_time DESC)")

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        rows = []
        for span in spans:
            if span.context is None:
                continue
            # Standard OTel span ID formats
            span_id = format(span.context.span_id, "016x")
            trace_id = format(span.context.trace_id, "032x")
            parent_id = format(span.parent.span_id, "016x") if span.parent else None

            # Duration calculation
            duration_ms = None
            if span.end_time and span.start_time:
                duration_ms = (span.end_time - span.start_time) / 1_000_000

            # Status with description
            status_code = "UNSET"
            status_description = None
            if span.status:
                status_code = span.status.status_code.name
                status_description = span.status.description

            # Span attributes — redact string values before storage
            raw_attrs = dict(span.attributes) if span.attributes else {}
            attrs = {
                k: _redact(v, self._patterns) if isinstance(v, str) else v
                for k, v in raw_attrs.items()
            }

            # Events with attributes (OTel events can have attributes)
            events = []
            for e in span.events:
                raw_event_attrs = dict(e.attributes) if e.attributes else {}
                event_attrs = {
                    k: _redact(v, self._patterns) if isinstance(v, str) else v
                    for k, v in raw_event_attrs.items()
                }
                event = {
                    "name": e.name,
                    "timestamp": e.timestamp,
                    "attributes": event_attrs,
                }
                events.append(event)

            # Resource attributes (service.name, etc.)
            resource = {}
            if span.resource:
                resource = dict(span.resource.attributes)

            rows.append(
                (
                    span_id,
                    trace_id,
                    parent_id,
                    span.name,
                    span.kind.name if span.kind else "INTERNAL",
                    span.start_time,
                    span.end_time,
                    duration_ms,
                    status_code,
                    status_description,
                    json.dumps(attrs),
                    json.dumps(events),
                    json.dumps(resource),
                )
            )

        # Retry with exponential backoff for transient lock contention
        for attempt in range(_EXPORT_MAX_RETRIES):
            try:
                with self._connect() as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                return SpanExportResult.SUCCESS
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc) and attempt < _EXPORT_MAX_RETRIES - 1:
                    delay = _EXPORT_RETRY_BASE_SECONDS * (2**attempt)
                    logger.warning(
                        "telemetry export retry %d/%d after lock: %s",
                        attempt + 1,
                        _EXPORT_MAX_RETRIES,
                        exc,
                    )
                    time.sleep(delay)
                else:
                    logger.error("telemetry export failed: %s", exc)
                    return SpanExportResult.FAILURE
        return SpanExportResult.FAILURE

    def shutdown(self):
        pass


class JsonSpanExporter(SpanExporter):
    """Serialises OTel spans as JSONL records to ``co-cli.jsonl``.

    Emits 100% of span content (attributes, events, IDs, duration, status)
    via a propagating logger — records flow to the root logger and land in
    the same ``co-cli.jsonl`` file as Python ``logging`` records.

    ``setup_file_logging()`` must be called before the first span is exported
    so the root logger has a handler to receive these records.
    """

    _logger_name = "co_cli.observability.spans"

    def __init__(self, redact_patterns: list[str] | None = None) -> None:
        self._patterns: list[re.Pattern] = [re.compile(p) for p in (redact_patterns or [])]
        self._logger = logging.getLogger(self._logger_name)
        self._logger.setLevel(logging.DEBUG)
        # Propagate to root so records reach the co-cli.jsonl handler — no own handler.
        self._logger.propagate = True

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            if span.context is None:
                continue
            self._logger.info(json.dumps(self._serialize_span(span)))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def _serialize_span(self, span: ReadableSpan) -> dict:
        span_id = format(span.context.span_id, "016x")
        trace_id = format(span.context.trace_id, "032x")
        parent_id = format(span.parent.span_id, "016x") if span.parent else None

        duration_ms = None
        if span.end_time and span.start_time:
            duration_ms = (span.end_time - span.start_time) / 1_000_000

        ts = datetime.fromtimestamp(span.start_time / 1_000_000_000, tz=UTC)
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"

        raw_attrs = dict(span.attributes) if span.attributes else {}
        attrs = {
            k: _redact(v, self._patterns) if isinstance(v, str) else v
            for k, v in raw_attrs.items()
        }

        events = []
        for e in span.events:
            raw_event_attrs = dict(e.attributes) if e.attributes else {}
            event_attrs = {
                k: _redact(v, self._patterns) if isinstance(v, str) else v
                for k, v in raw_event_attrs.items()
            }
            events.append({"name": e.name, "ts": e.timestamp, "attributes": event_attrs})

        return {
            "ts": ts_str,
            "kind": "span",
            "span_id": span_id,
            "trace_id": trace_id,
            "parent_id": parent_id,
            "name": span.name,
            "attributes": attrs,
            "events": events,
            "duration_ms": duration_ms,
            "status": span.status.status_code.name if span.status else "UNSET",
            "status_description": span.status.description if span.status else None,
        }


def setup_tracer_provider(
    service_name: str,
    service_version: str,
    *,
    redact_patterns: list[str] | None = None,
    skip_if_installed: bool = False,
) -> TracerProvider:
    """Create a TracerProvider with SQLite + JSONL span exporters.

    Installs it as the global OTel provider via ``trace.set_tracer_provider``.
    ``JsonSpanExporter`` emits span records via a propagating logger — records
    land in ``co-cli.jsonl`` alongside Python ``logging`` records when
    ``setup_file_logging()`` has been called first.

    When ``skip_if_installed=True`` and a ``TracerProvider`` is already
    installed, the existing provider is returned without modification.

    Args:
        service_name: OTel ``service.name`` resource attribute.
        service_version: OTel ``service.version`` resource attribute.
        redact_patterns: Regex patterns applied to string span attribute values before storage.
        skip_if_installed: When True, skip setup if a provider is already set.

    Returns the active ``TracerProvider`` (new or pre-existing).
    """
    if skip_if_installed:
        current = trace.get_tracer_provider()
        if isinstance(current, TracerProvider):
            return current

    resource = Resource.create({"service.name": service_name, "service.version": service_version})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        SimpleSpanProcessor(SQLiteSpanExporter(redact_patterns=redact_patterns))
    )
    provider.add_span_processor(
        SimpleSpanProcessor(JsonSpanExporter(redact_patterns=redact_patterns))
    )
    trace.set_tracer_provider(provider)
    return provider
