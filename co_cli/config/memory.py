"""Memory lifecycle settings (notes with gravity)."""

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS = 30


MEMORY_ENV_MAP: dict[str, str] = {
    "recall_half_life_days": "CO_MEMORY_RECALL_HALF_LIFE_DAYS",
}


class MemorySettings(BaseModel):
    """Memory lifecycle settings (notes with gravity)."""

    model_config = ConfigDict(extra="forbid")

    recall_half_life_days: int = Field(default=DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS, ge=1)
