"""Dream daemon settings."""

from pydantic import BaseModel, ConfigDict, Field

DREAM_ENV_MAP: dict[str, str] = {
    "enabled": "CO_DREAM_ENABLED",
    "review_timeout_seconds": "CO_DREAM_REVIEW_TIMEOUT_SECONDS",
    "retry_backoff_seconds": "CO_DREAM_RETRY_BACKOFF_SECONDS",
    "max_retry_attempts": "CO_DREAM_MAX_RETRY_ATTEMPTS",
    "poll_interval_seconds": "CO_DREAM_POLL_INTERVAL_SECONDS",
    "run_interval_hours": "CO_DREAM_RUN_INTERVAL_HOURS",
    "run_at": "CO_DREAM_RUN_AT",
    "max_pass_seconds": "CO_DREAM_MAX_PASS_SECONDS",
    "done_retention_days": "CO_DREAM_DONE_RETENTION_DAYS",
}


class DreamSettings(BaseModel):
    """Configuration for the per-CO_HOME dream daemon."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False)
    review_timeout_seconds: int = Field(default=120, ge=1)
    retry_backoff_seconds: int = Field(default=30, ge=1)
    max_retry_attempts: int = Field(default=3, ge=1)
    poll_interval_seconds: int = Field(default=5, ge=1, le=60)
    run_interval_hours: int = Field(default=24, ge=1, le=720)
    run_at: str = Field(default="03:00", pattern=r"^[0-2]\d:[0-5]\d$")
    max_pass_seconds: int = Field(default=600, ge=60)
    done_retention_days: int = Field(default=7, ge=1)
