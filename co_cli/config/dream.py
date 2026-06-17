"""Dream daemon settings."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

DREAM_ENV_MAP: dict[str, str] = {
    "enabled": "CO_DREAM_ENABLED",
    "review_timeout_seconds": "CO_DREAM_REVIEW_TIMEOUT_SECONDS",
    "retry_backoff_seconds": "CO_DREAM_RETRY_BACKOFF_SECONDS",
    "max_retry_attempts": "CO_DREAM_MAX_RETRY_ATTEMPTS",
    "tick_interval_seconds": "CO_DREAM_TICK_INTERVAL_SECONDS",
    "run_interval_hours": "CO_DREAM_RUN_INTERVAL_HOURS",
    "run_start_at": "CO_DREAM_RUN_START_AT",
    "max_pass_seconds": "CO_DREAM_MAX_PASS_SECONDS",
    "done_retention_days": "CO_DREAM_DONE_RETENTION_DAYS",
    "session_retention_days": "CO_DREAM_SESSION_RETENTION_DAYS",
}


class DreamSettings(BaseModel):
    """Configuration for the per-CO_HOME dream daemon."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False)
    review_timeout_seconds: int = Field(default=120, ge=1)
    retry_backoff_seconds: int = Field(default=30, ge=1)
    max_retry_attempts: int = Field(default=3, ge=1)
    tick_interval_seconds: int = Field(default=5, ge=1, le=60)
    run_interval_hours: int = Field(default=24, ge=1, le=720)
    run_start_at: str = Field(default="03:00", pattern=r"^[0-2]\d:[0-5]\d$")
    max_pass_seconds: int = Field(default=600, ge=60)
    done_retention_days: int = Field(default=7, ge=1)
    session_retention_days: int = Field(
        default=0,
        ge=0,
        description="Delete session transcripts older than N days; 0 disables. Recommended: 30.",
    )

    @field_validator("run_interval_hours")
    @classmethod
    def _interval_must_align_to_daily_grid(cls, value: int) -> int:
        """run_interval_hours must align to the daily grid the run_start_at clamp imposes.

        run_start_at is a once-per-day boundary, so the effective cadence is
        quantized to whole days. Below 24 the value must evenly divide 24
        (1, 2, 3, 4, 6, 8, 12); above 24 it must be a whole multiple of 24
        (48, 72, ...). Either way the configured value stays an honest divisor
        or multiple of the daily grid rather than a figure the clamp rounds.
        """
        if value < 24 and 24 % value != 0:
            raise ValueError(
                f"run_interval_hours below 24 must be a factor of 24 (1, 2, 3, 4, 6, 8, 12); got {value}"
            )
        if value > 24 and value % 24 != 0:
            raise ValueError(
                f"run_interval_hours above 24 must be a multiple of 24 (48, 72, ...); got {value}"
            )
        return value
