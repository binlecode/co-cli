import sqlite3
import json
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace import ReadableSpan
from co_cli.config import DATA_DIR


class SQLiteSpanExporter(SpanExporter):
    def __init__(self, db_path: str = str(DATA_DIR / "co-cli.db")):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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

                conn.execute(
                    "INSERT OR REPLACE INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        json.dumps(dict(span.attributes) if span.attributes else {}),
                        json.dumps(events),
                        json.dumps(resource),
                    )
                )
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass
