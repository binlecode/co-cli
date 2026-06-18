"""Skills hygiene + self-evolution settings."""

from pydantic import BaseModel, ConfigDict, Field

SKILLS_ENV_MAP: dict[str, str] = {
    "review_enabled": "CO_SKILLS_REVIEW_ENABLED",
    "review_memory_nudge_interval": "CO_SKILLS_REVIEW_MEMORY_NUDGE_INTERVAL",
    "review_skill_nudge_interval": "CO_SKILLS_REVIEW_SKILL_NUDGE_INTERVAL",
    "usage_tracking_enabled": "CO_SKILLS_USAGE_TRACKING_ENABLED",
    "recall_protection_days": "CO_SKILLS_RECALL_PROTECTION_DAYS",
    "decay_after_days": "CO_SKILLS_DECAY_AFTER_DAYS",
    "consolidation_similarity_threshold": "CO_SKILLS_CONSOLIDATION_SIMILARITY_THRESHOLD",
}

REVIEW_MAX_ITERATIONS: int = 8


class SkillsSettings(BaseModel):
    """Skills hygiene + self-evolution configuration."""

    model_config = ConfigDict(extra="forbid")

    review_enabled: bool = Field(default=False)
    review_memory_nudge_interval: int = Field(default=10, ge=1)
    review_skill_nudge_interval: int = Field(default=10, ge=1)
    usage_tracking_enabled: bool = Field(default=True)
    recall_protection_days: int = Field(default=30, ge=1)
    decay_after_days: int = Field(default=90, ge=1)
    consolidation_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
