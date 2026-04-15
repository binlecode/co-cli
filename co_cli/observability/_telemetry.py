import json
import logging
import logging.handlers
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
from co_cli.observability._viewer import extract_span_attrs, format_duration, get_span_type

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


# Maximum characters for tool result in spans.log (passed to extract_span_attrs).
_RESULT_TRUNCATE = 400


class TextSpanExporter(SpanExporter):
    """Appends OTel spans as human-readable lines to spans.log.

    One line per completed span: timestamp, type, name, key attributes, duration.
    Enables cat/tail/grep debugging without requiring a DB query.
    Uses a dedicated logger (``co_cli.observability.spans``) with ``propagate=False``
    so span lines never bleed into ``co-cli.log``.
    """

    _logger_name = "co_cli.observability.spans"

    def __init__(self, log_dir: Path, max_size_mb: int = 10, backup_count: int = 3) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "spans.log"

        self._logger = logging.getLogger(self._logger_name)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

        target = str(log_path)
        for existing in self._logger.handlers:
            if (
                isinstance(existing, logging.handlers.RotatingFileHandler)
                and existing.baseFilename == target
            ):
                return  # idempotent

        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            line = self._format_span(span)
            if line:
                self._logger.info(line)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def _format_span(self, span: ReadableSpan) -> str:
        if span.context is None:
            return ""

        attrs: dict = dict(span.attributes) if span.attributes else {}
        span_type = get_span_type(span.name)

        ts = datetime.fromtimestamp(span.start_time / 1_000_000_000, tz=UTC)
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}"

        duration_ms = None
        if span.end_time and span.start_time:
            duration_ms = (span.end_time - span.start_time) / 1_000_000

        status = " ERROR" if span.status and span.status.status_code.name == "ERROR" else ""
        attr_parts = extract_span_attrs(
            span_type, attrs, include_result=True, result_truncate=_RESULT_TRUNCATE
        )
        attr_str = "  ".join(attr_parts)

        parts = [ts_str, f"[{span_type:<6}]", f"{span.name:<48}"]
        if attr_str:
            parts.append(attr_str)
        parts.append(format_duration(duration_ms) + status)

        return "  ".join(parts)


def setup_tracer_provider(
    service_name: str,
    service_version: str,
    log_dir: Path,
    max_size_mb: int,
    backup_count: int,
    *,
    redact_patterns: list[str] | None = None,
    skip_if_installed: bool = False,
) -> TracerProvider:
    """Create a TracerProvider with SQLite + text file span exporters.

    Installs it as the global OTel provider via ``trace.set_tracer_provider``.
    When ``skip_if_installed=True`` and a ``TracerProvider`` is already
    installed, the existing provider is returned without modification.

    Args:
        service_name: OTel ``service.name`` resource attribute.
        service_version: OTel ``service.version`` resource attribute.
        log_dir: Directory for ``spans.log`` (text exporter output).
        max_size_mb: Rotating log file size limit in MB.
        backup_count: Number of rotated backup files to keep.
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
        SimpleSpanProcessor(TextSpanExporter(log_dir, max_size_mb, backup_count))
    )
    trace.set_tracer_provider(provider)
    return provider
