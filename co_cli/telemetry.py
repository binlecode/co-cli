import logging
import sqlite3
import json
import time
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace import ReadableSpan
from co_cli.config import DATA_DIR

logger = logging.getLogger(__name__)

# WAL concurrency settings
_BUSY_TIMEOUT_MS = 5000
_EXPORT_MAX_RETRIES = 3
_EXPORT_RETRY_BASE_SECONDS = 0.1


class SQLiteSpanExporter(SpanExporter):
    def __init__(self, db_path: str = str(DATA_DIR / "co-cli.db")):
        self.db_path = db_path
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

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        rows = []
        for span in spans:
            # Standard OTel span ID formats
            span_id = format(span.context.span_id, '016x')
            trace_id = format(span.context.trace_id, '032x')
            parent_id = format(span.parent.span_id, '016x') if span.parent else None

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

            # Events with attributes (OTel events can have attributes)
            events = []
            for e in span.events:
                event = {
                    "name": e.name,
                    "timestamp": e.timestamp,
                    "attributes": dict(e.attributes) if e.attributes else {}
                }
                events.append(event)

            # Resource attributes (service.name, etc.)
            resource = {}
            if span.resource:
                resource = dict(span.resource.attributes)

            rows.append((
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
                json.dumps(dict(span.attributes) if span.attributes else {}),
                json.dumps(events),
                json.dumps(resource),
            ))

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
                    delay = _EXPORT_RETRY_BASE_SECONDS * (2 ** attempt)
                    logger.warning("telemetry export retry %d/%d after lock: %s", attempt + 1, _EXPORT_MAX_RETRIES, exc)
                    time.sleep(delay)
                else:
                    logger.error("telemetry export failed: %s", exc)
                    return SpanExportResult.FAILURE
        return SpanExportResult.FAILURE

    def shutdown(self):
        pass
