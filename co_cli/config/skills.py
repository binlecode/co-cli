"""Skills hygiene + self-evolution settings."""

from pydantic import BaseModel, ConfigDict, Field

SKILLS_ENV_MAP: dict[str, str] = {
    "usage_tracking_enabled": "CO_SKILLS_USAGE_TRACKING_ENABLED",
}


class SkillsSettings(BaseModel):
    """Skills hygiene + (3.5b) self-evolution configuration.

    Dynamic knobs use lowercase snake_case prefixed by feature area
    (usage_tracking_*, review_*, curator_*). 3.5a contributes one knob.
    """

    model_config = ConfigDict(extra="forbid")

    usage_tracking_enabled: bool = Field(default=True)
