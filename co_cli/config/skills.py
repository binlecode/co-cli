"""Skills hygiene + self-evolution settings."""

from pydantic import BaseModel, ConfigDict, Field

SKILLS_ENV_MAP: dict[str, str] = {
    "usage_tracking_enabled": "CO_SKILLS_USAGE_TRACKING_ENABLED",
    "review_enabled": "CO_SKILLS_REVIEW_ENABLED",
    "curator_enabled": "CO_SKILLS_CURATOR_ENABLED",
    "curator_interval_hours": "CO_SKILLS_CURATOR_INTERVAL_HOURS",
}

REVIEW_MAX_ITERATIONS: int = 8
REVIEW_TIMEOUT_SECONDS: int = 120
CURATOR_MIN_IDLE_HOURS: int = 2
CURATOR_STALE_AFTER_DAYS: int = 30
CURATOR_ARCHIVE_AFTER_DAYS: int = 90
CURATOR_MAX_ITERATIONS: int = 100
CURATOR_TIMEOUT_SECONDS: int = 600


class SkillsSettings(BaseModel):
    """Skills hygiene + (3.5b) self-evolution configuration.

    Dynamic knobs use lowercase snake_case prefixed by feature area
    (usage_tracking_*, review_*, curator_*). 3.5a contributes one knob.
    """

    model_config = ConfigDict(extra="forbid")

    usage_tracking_enabled: bool = Field(default=True)
    review_enabled: bool = Field(default=False)
    curator_enabled: bool = Field(default=False)
    curator_interval_hours: int = Field(default=168, ge=1)
