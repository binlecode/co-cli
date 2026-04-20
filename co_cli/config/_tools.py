"""Tool result persistence and spill thresholds.

Governs the two-layer tool-output defense:
- result_persist_chars: per-tool persist-at-write threshold (see tool_io.persist_if_oversized)
- batch_spill_chars:    per-batch aggregate spill threshold (see _history.enforce_batch_budget)

Per-tool registry overrides (ToolInfo.max_result_size) take precedence over result_persist_chars.
"""

from pydantic import BaseModel, ConfigDict, Field


class ToolsSettings(BaseModel):
    """Tool result persistence and spill thresholds."""

    model_config = ConfigDict(extra="ignore")

    result_persist_chars: int = Field(
        default=50_000,
        ge=1_000,
        description=(
            "Default per-tool persist threshold in chars. Above this, persist_if_oversized "
            "writes content to disk. Per-tool registry entries may override via ToolInfo.max_result_size."
        ),
    )
    batch_spill_chars: int = Field(
        default=200_000,
        ge=10_000,
        description=(
            "Per-batch aggregate spill threshold in chars. Above this, enforce_batch_budget "
            "evicts the largest non-persisted tool returns from the message list."
        ),
    )
