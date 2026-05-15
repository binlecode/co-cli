"""Skills hygiene + self-evolution settings."""

from pydantic import BaseModel, ConfigDict, Field

SKILLS_ENV_MAP: dict[str, str] = {
    "review_enabled": "CO_SKILLS_REVIEW_ENABLED",
    "review_nudge_interval": "CO_SKILLS_REVIEW_NUDGE_INTERVAL",
}

REVIEW_MAX_ITERATIONS: int = 8
REVIEW_TIMEOUT_SECONDS: int = 120


class SkillsSettings(BaseModel):
    """Skills hygiene + self-evolution configuration."""

    model_config = ConfigDict(extra="forbid")

    review_enabled: bool = Field(default=False)
    review_nudge_interval: int = Field(default=5, ge=1)
