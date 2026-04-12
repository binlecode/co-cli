"""Memory lifecycle settings (notes with gravity)."""

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS = 30
DEFAULT_MEMORY_INJECTION_MAX_CHARS = 2000


class MemorySettings(BaseModel):
    """Memory lifecycle settings (notes with gravity)."""

    model_config = ConfigDict(extra="ignore")

    recall_half_life_days: int = Field(default=DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS, ge=1)
    injection_max_chars: int = Field(default=DEFAULT_MEMORY_INJECTION_MAX_CHARS, ge=100)
