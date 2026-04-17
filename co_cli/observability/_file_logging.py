"""Rotating JSONL log handler for Python ``logging`` output.

Writes a single ``co-cli.jsonl`` file under ``log_dir``.
Each line is a JSON object: ``{"ts", "kind": "log", "level", "logger", "msg"}``,
plus ``"exc_info"`` when a record carries exception info.

OTel span output (agent/model/tool events) is also written to ``co-cli.jsonl``
via ``JsonSpanExporter`` in ``_telemetry.py``, which emits through a propagating
logger so all output converges in one file.
"""

import json
import logging
import logging.handlers
import re
from datetime import UTC, datetime
from pathlib import Path

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), r"\1***"),
    (re.compile(r"\b(sk-[A-Za-z0-9]{6})[A-Za-z0-9\-]{10,}"), r"\1***"),
    (re.compile(r"\b(sk-ant-[A-Za-z0-9]{6})[A-Za-z0-9\-]{10,}"), r"\1***"),
    (re.compile(r"\b(ghp_[A-Za-z0-9]{6})[A-Za-z0-9]{30,}"), r"\1***"),
    (re.compile(r"\b(AIza[A-Za-z0-9]{6})[A-Za-z0-9\-_]{25,}"), r"\1***"),
    (
        re.compile(
            r'("(?:api_?key|token|secret|password|credential)[^"]*"\s*:\s*")[^"]{8,}(")',
            re.IGNORECASE,
        ),
        r"\1***\2",
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
        "***PRIVATE KEY***",
    ),
]


class _JsonRedactingFormatter(logging.Formatter):
    """Formats log records as redacted single-line JSON objects.

    If the message is already a valid JSON dict (e.g. a span record from
    ``JsonSpanExporter``), it is passed through after redaction. Otherwise
    the record is wrapped in a standard envelope with ``"kind": "log"``.
    """

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        for pattern, replacement in _REDACT_PATTERNS:
            msg = pattern.sub(replacement, msg)

        # Pass-through pre-serialised JSON dicts (span records from JsonSpanExporter)
        try:
            parsed = json.loads(msg)
            if isinstance(parsed, dict):
                return msg
        except (json.JSONDecodeError, ValueError):
            pass

        ts = (
            datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{int(record.msecs):03d}Z"
        )
        entry: dict = {
            "ts": ts,
            "kind": "log",
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def setup_file_logging(
    log_dir: Path,
    level: str = "INFO",
    max_size_mb: int = 5,
    backup_count: int = 3,
) -> None:
    """Attach a rotating JSONL handler to the root logger.

    Writes ``co-cli.jsonl`` — one JSON object per line. Python ``logging``
    records have ``"kind": "log"``; OTel span records from ``JsonSpanExporter``
    have ``"kind": "span"`` and arrive via logger propagation.

    Idempotent — calling more than once with the same ``log_dir`` is safe;
    duplicate handlers are not added.

    Args:
        log_dir: Directory where ``co-cli.jsonl`` is written.
        level: Minimum level for the log (e.g. ``"INFO"``, ``"DEBUG"``).
        max_size_mb: Maximum file size in MB before rotation.
        backup_count: Number of rotated backup files to keep.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    max_bytes = max_size_mb * 1024 * 1024

    formatter = _JsonRedactingFormatter()
    root = logging.getLogger()

    if root.level == logging.NOTSET or root.level > numeric_level:
        root.setLevel(numeric_level)

    _attach_handler(
        root,
        log_dir / "co-cli.jsonl",
        level=numeric_level,
        max_bytes=max_bytes,
        backup_count=backup_count,
        formatter=formatter,
    )

    # Dedicated WARNING+ file for fast error triage without wading through span JSON
    _attach_handler(
        root,
        log_dir / "errors.jsonl",
        level=logging.WARNING,
        max_bytes=2 * 1024 * 1024,
        backup_count=2,
        formatter=formatter,
    )


def _attach_handler(
    logger: logging.Logger,
    log_path: Path,
    *,
    level: int,
    max_bytes: int,
    backup_count: int,
    formatter: logging.Formatter,
) -> None:
    """Add a ``RotatingFileHandler`` to ``logger`` — skip if already present."""
    target = str(log_path)
    for existing in logger.handlers:
        if (
            isinstance(existing, logging.handlers.RotatingFileHandler)
            and existing.baseFilename == target
        ):
            return

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
