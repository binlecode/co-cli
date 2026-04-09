"""Memory lifecycle settings (notes with gravity)."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_MEMORY_MAX_COUNT = 200
DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS = 30
DEFAULT_MEMORY_INJECTION_MAX_CHARS = 2000
DEFAULT_MEMORY_AUTO_SAVE_TAGS: list[str] = ["user", "feedback", "project", "reference"]


class MemorySettings(BaseModel):
    """Memory lifecycle settings (notes with gravity)."""

    model_config = ConfigDict(extra="ignore")

    max_count: int = Field(default=DEFAULT_MEMORY_MAX_COUNT, ge=10)
    recall_half_life_days: int = Field(default=DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS, ge=1)
    auto_save_tags: list[str] = Field(default=DEFAULT_MEMORY_AUTO_SAVE_TAGS)
    injection_max_chars: int = Field(default=DEFAULT_MEMORY_INJECTION_MAX_CHARS, ge=100)

    @field_validator("auto_save_tags", mode="before")
    @classmethod
    def _parse_auto_save_tags(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v
