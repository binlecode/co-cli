"""Observability configuration sub-model."""

from pydantic import BaseModel, ConfigDict, Field

OBSERVABILITY_ENV_MAP: dict[str, str] = {
    "log_level": "CO_LOG_LEVEL",
    "log_max_size_mb": "CO_LOG_MAX_SIZE_MB",
    "log_backup_count": "CO_LOG_BACKUP_COUNT",
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


class ObservabilitySettings(BaseModel):
    """Controls JSONL file logging and OTel span redaction."""

    model_config = ConfigDict(extra="forbid")

    log_level: str = Field(
        default="INFO",
        description="Minimum log level written to co-cli.jsonl (DEBUG/INFO/WARNING/ERROR).",
    )
    log_max_size_mb: int = Field(
        default=5,
        ge=1,
        le=500,
        description="Maximum size of each log file in MB before rotation.",
    )
    log_backup_count: int = Field(
        default=3,
        ge=0,
        le=20,
        description="Number of rotated log backups to keep per file.",
    )
    redact_patterns: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_REDACT_PATTERNS),
        description=(
            "Regex patterns applied to span attribute string values before SQLite storage. "
            "Matching substrings are replaced with [REDACTED]. "
            "This list is not exhaustive — users with custom secret formats should extend it via settings.json."
        ),
    )
