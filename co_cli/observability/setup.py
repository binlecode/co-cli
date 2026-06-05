"""Shared observability bootstrap for every co-cli process.

Wiring the app log, the span stream, and third-party noise suppression in one
place keeps separate processes (the main app and the detached dream daemon)
from re-diverging in how they configure logging — the divergence that left the
daemon's spans mangled into plain-text files in the first place.

Each process passes its own filenames; the main app uses ``co-cli*`` and the
dream daemon uses ``co-dream*`` so their rotating handlers never share a file
(``RotatingFileHandler`` is not multi-process safe).
"""

import logging
from pathlib import Path

from co_cli.config.core import Settings
from co_cli.observability.file_logging import setup_file_logging
from co_cli.observability.tracing import setup_log as setup_spans_log

SUPPRESS_LOGGERS = ["openai", "httpx", "anthropic", "hpack"]


def setup_observability(
    log_dir: Path,
    *,
    app_log_name: str,
    spans_log_name: str,
    settings: Settings,
    errors_log_name: str | None = None,
) -> None:
    """Wire the full observability stack for the calling process.

    Attaches the rotating JSONL app log and (optionally) errors log to the root
    logger, attaches the separate rotating span stream to the spans logger
    (``propagate=False`` so spans never leak into the app log), and raises the
    noisy third-party loggers to WARNING.

    Idempotent — ``setup_file_logging`` and ``setup_spans_log`` both dedupe by
    target filename, so calling this more than once in a process is safe.

    Args:
        log_dir: Directory where all log files are written.
        app_log_name: Filename of the INFO+ app log under ``log_dir``.
        spans_log_name: Filename of the span stream under ``log_dir``.
        settings: Source of levels, rotation sizes/backups, and redact patterns.
        errors_log_name: Filename of the WARNING+ errors log, or ``None`` to skip it.
    """
    setup_file_logging(
        log_dir=log_dir,
        level=settings.observability.log_level,
        max_size_mb=settings.observability.log_max_size_mb,
        backup_count=settings.observability.log_backup_count,
        app_log_name=app_log_name,
        errors_log_name=errors_log_name,
    )
    setup_spans_log(
        log_path=log_dir / spans_log_name,
        max_size_mb=settings.observability.spans_log_max_size_mb,
        backup_count=settings.observability.spans_log_backup_count,
        redact_patterns=settings.observability.redact_patterns,
    )
    for logger_name in SUPPRESS_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
