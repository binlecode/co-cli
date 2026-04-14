"""Rotating file log handler setup — dual-write alongside the SQLite OTel exporter.

Writes two files under ``log_dir``:
- ``co-cli.log``  — INFO and above (all operational events)
- ``errors.log``  — WARNING and above (quick triage)

Both files use ``RotatingFileHandler`` and a ``RedactingFormatter`` that strips
common secret patterns before anything reaches disk.
"""

import logging
import logging.handlers
import re
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Patterns that look like secrets — matched against each formatted line.
# Each entry is (pattern, replacement).
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Generic bearer / auth headers
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), r"\1***"),
    # OpenAI / Anthropic / common API key prefixes
    (re.compile(r"\b(sk-[A-Za-z0-9]{6})[A-Za-z0-9\-]{10,}"), r"\1***"),
    (re.compile(r"\b(sk-ant-[A-Za-z0-9]{6})[A-Za-z0-9\-]{10,}"), r"\1***"),
    # GitHub tokens
    (re.compile(r"\b(ghp_[A-Za-z0-9]{6})[A-Za-z0-9]{30,}"), r"\1***"),
    # Google / AIza tokens
    (re.compile(r"\b(AIza[A-Za-z0-9]{6})[A-Za-z0-9\-_]{25,}"), r"\1***"),
    # JSON fields that typically carry secrets
    (
        re.compile(
            r'("(?:api_?key|token|secret|password|credential)[^"]*"\s*:\s*")[^"]{8,}(")',
            re.IGNORECASE,
        ),
        r"\1***\2",
    ),
    # Private key blocks
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
        "***PRIVATE KEY***",
    ),
]


class _RedactingFormatter(logging.Formatter):
    """Formatter that scrubs common secret patterns from each log record."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        for pattern, replacement in _REDACT_PATTERNS:
            line = pattern.sub(replacement, line)
        return line


def setup_file_logging(
    log_dir: Path,
    level: str = "INFO",
    max_size_mb: int = 5,
    backup_count: int = 3,
) -> None:
    """Attach rotating file handlers to the root logger.

    Idempotent — calling more than once with the same ``log_dir`` is safe;
    duplicate handlers are not added.

    Args:
        log_dir: Directory where ``co-cli.log`` and ``errors.log`` are written.
        level: Minimum level for the main log (e.g. ``"INFO"``, ``"DEBUG"``).
        max_size_mb: Maximum file size in MB before rotation.
        backup_count: Number of rotated backup files to keep.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    max_bytes = max_size_mb * 1024 * 1024

    formatter = _RedactingFormatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    root = logging.getLogger()

    # Ensure root logger passes everything through to handlers.
    if root.level == logging.NOTSET or root.level > numeric_level:
        root.setLevel(numeric_level)

    _attach_handler(
        root,
        log_dir / "co-cli.log",
        level=numeric_level,
        max_bytes=max_bytes,
        backup_count=backup_count,
        formatter=formatter,
    )
    _attach_handler(
        root,
        log_dir / "errors.log",
        level=logging.WARNING,
        max_bytes=max_bytes // 2,
        backup_count=max(1, backup_count - 1),
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
