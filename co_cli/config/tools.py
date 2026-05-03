"""Tool result persistence threshold.

Controls when tool outputs are spilled to disk and replaced with a file
reference + preview in the model context.

Per-tool registry overrides (ToolInfo.max_result_size) take precedence over result_persist_chars.
"""

from pydantic import BaseModel, ConfigDict, Field

TOOLS_ENV_MAP: dict[str, str] = {
    "result_persist_chars": "CO_TOOLS_RESULT_PERSIST_CHARS",
}


class ToolsSettings(BaseModel):
    """Tool result persistence threshold."""

    model_config = ConfigDict(extra="forbid")

    result_persist_chars: int = Field(
        default=50_000,
        ge=1_000,
        description=(
            "Default per-tool persist threshold in chars. Above this, persist_if_oversized "
            "writes content to disk. Per-tool registry entries may override via ToolInfo.max_result_size."
        ),
    )
