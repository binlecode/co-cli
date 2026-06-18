"""Observability configuration sub-model."""

import re

from pydantic import BaseModel, ConfigDict, Field

OBSERVABILITY_ENV_MAP: dict[str, str] = {
    "log_level": "CO_LOG_LEVEL",
    "log_max_size_mb": "CO_LOG_MAX_SIZE_MB",
    "log_backup_count": "CO_LOG_BACKUP_COUNT",
    "spans_log_max_size_mb": "CO_SPANS_LOG_MAX_SIZE_MB",
    "spans_log_backup_count": "CO_SPANS_LOG_BACKUP_COUNT",
}

# Default patterns redacted from span attribute values before SQLite storage.
_DEFAULT_REDACT_PATTERNS: list[str] = [
    r"sk-[A-Za-z0-9]{20,}",
    r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}",
    r"ghp_[A-Za-z0-9]{36}",
    r"[Aa][Pp][Ii][_-][Kk][Ee][Yy]\s*[:=]\s*\S{8,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
]


def redact_text(text: str, patterns: list[str]) -> str:
    """Replace credential-matching substrings with [REDACTED]."""
    for pattern in patterns:
        text = re.sub(pattern, "[REDACTED]", text)
    return text


class ObservabilitySettings(BaseModel):
    """Controls structured-log file output and span attribute redaction."""

    model_config = ConfigDict(extra="forbid")

    log_level: str = Field(
        default="INFO",
        description="Minimum log level written to co-cli.jsonl (DEBUG/INFO/WARNING/ERROR).",
    )
    log_max_size_mb: int = Field(
        default=5,
        ge=1,
        le=500,
        description="Maximum size of each application log file (co-cli.jsonl) in MB before rotation.",
    )
    log_backup_count: int = Field(
        default=3,
        ge=0,
        le=20,
        description="Number of rotated application log backups to keep per file.",
    )
    spans_log_max_size_mb: int = Field(
        default=50,
        ge=1,
        le=2000,
        description=(
            "Maximum size of the spans log file (co-cli-spans.jsonl) in MB before rotation. "
            "Defaults higher than the app log because span volume is higher."
        ),
    )
    spans_log_backup_count: int = Field(
        default=5,
        ge=0,
        le=50,
        description="Number of rotated spans log backups to keep.",
    )
    redact_patterns: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_REDACT_PATTERNS),
        description=(
            "Regex patterns applied to span attribute string values before they are written to disk. "
            "Matching substrings are replaced with [REDACTED]. "
            "This list is not exhaustive — users with custom secret formats should extend it via settings.json."
        ),
    )
    redact_summary_output: bool = Field(
        default=True,
        description=(
            "When True, the compaction summary is passed through redact_patterns on the way out "
            "(in addition to the always-on input redaction), guarding against a credential-shaped "
            "string the model emits that the input redaction never saw. Disable where summary "
            "throughput matters more than this output-side defense-in-depth."
        ),
    )
