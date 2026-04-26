"""Memory lifecycle settings (notes with gravity)."""

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS = 30


MEMORY_ENV_MAP: dict[str, str] = {
    "recall_half_life_days": "CO_MEMORY_RECALL_HALF_LIFE_DAYS",
    "extract_every_n_turns": "CO_MEMORY_EXTRACT_EVERY_N_TURNS",
}


class MemorySettings(BaseModel):
    """Memory lifecycle settings (notes with gravity)."""

    model_config = ConfigDict(extra="forbid")

    recall_half_life_days: int = Field(default=DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS, ge=1)
    # Extraction cadence: run extractor every N turns. 0 = disabled. Default 3.
    extract_every_n_turns: int = Field(default=3, ge=0)
