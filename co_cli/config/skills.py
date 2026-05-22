"""Skills hygiene + self-evolution settings."""

from pydantic import BaseModel, ConfigDict, Field

SKILLS_ENV_MAP: dict[str, str] = {
    "review_enabled": "CO_SKILLS_REVIEW_ENABLED",
    "review_memory_nudge_interval": "CO_SKILLS_REVIEW_MEMORY_NUDGE_INTERVAL",
    "review_skill_nudge_interval": "CO_SKILLS_REVIEW_SKILL_NUDGE_INTERVAL",
    "usage_tracking_enabled": "CO_SKILLS_USAGE_TRACKING_ENABLED",
    "curator_enabled": "CO_SKILLS_CURATOR_ENABLED",
    "curator_interval_hours": "CO_SKILLS_CURATOR_INTERVAL_HOURS",
}

REVIEW_MAX_ITERATIONS: int = 8
REVIEW_TIMEOUT_SECONDS: int = 120

CURATOR_STALE_AFTER_DAYS: int = 30
CURATOR_ARCHIVE_AFTER_DAYS: int = 90
CURATOR_MAX_ITERATIONS: int = 100
CURATOR_TIMEOUT_SECONDS: int = 600


class SkillsSettings(BaseModel):
    """Skills hygiene + self-evolution configuration."""

    model_config = ConfigDict(extra="forbid")

    review_enabled: bool = Field(default=False)
    review_memory_nudge_interval: int = Field(default=10, ge=1)
    review_skill_nudge_interval: int = Field(default=10, ge=1)
    usage_tracking_enabled: bool = Field(default=True)
    curator_enabled: bool = Field(default=False)
    curator_interval_hours: int = Field(default=168, ge=1)
