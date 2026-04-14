"""Observability configuration sub-model."""

from pydantic import BaseModel, Field


class ObservabilityConfig(BaseModel):
    """Controls file logging behaviour (dual-write alongside the SQLite OTel DB)."""

    log_level: str = Field(
        default="INFO",
        description="Minimum log level written to co-cli.log (DEBUG/INFO/WARNING/ERROR).",
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
