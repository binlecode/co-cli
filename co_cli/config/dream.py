"""Dream daemon settings."""

from pydantic import BaseModel, ConfigDict, Field

DREAM_ENV_MAP: dict[str, str] = {
    "enabled": "CO_DREAM_ENABLED",
    "review_timeout_seconds": "CO_DREAM_REVIEW_TIMEOUT_SECONDS",
    "retry_backoff_seconds": "CO_DREAM_RETRY_BACKOFF_SECONDS",
    "max_retry_attempts": "CO_DREAM_MAX_RETRY_ATTEMPTS",
    "poll_interval_seconds": "CO_DREAM_POLL_INTERVAL_SECONDS",
}


class DreamSettings(BaseModel):
    """Configuration for the per-CO_HOME dream daemon."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False)
    review_timeout_seconds: int = Field(default=120, ge=1)
    retry_backoff_seconds: int = Field(default=30, ge=1)
    max_retry_attempts: int = Field(default=3, ge=1)
    poll_interval_seconds: int = Field(default=5, ge=1, le=60)
