"""REPL input-queue settings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

REPL_ENV_MAP: dict[str, str] = {
    "queue_cap": "CO_REPL_QUEUE_CAP",
    "drop_policy": "CO_REPL_DROP_POLICY",
}


class ReplSettings(BaseModel):
    """Configuration for the REPL input queue (bounded-queue policy)."""

    model_config = ConfigDict(extra="forbid")

    queue_cap: int = Field(default=0, ge=0)
    drop_policy: Literal["oldest", "newest"] = Field(default="oldest")
