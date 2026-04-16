"""Sub-agent scope and request budgets."""

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SUBAGENT_SCOPE_CHARS = 120
DEFAULT_SUBAGENT_MAX_REQUESTS_RESEARCH = 10
DEFAULT_SUBAGENT_MAX_REQUESTS_ANALYSIS = 8
DEFAULT_SUBAGENT_MAX_REQUESTS_THINKING = 3


class SubagentSettings(BaseModel):
    """Sub-agent scope and request budgets."""

    model_config = ConfigDict(extra="ignore")

    scope_chars: int = Field(default=DEFAULT_SUBAGENT_SCOPE_CHARS, ge=10)
    max_requests_research: int = Field(default=DEFAULT_SUBAGENT_MAX_REQUESTS_RESEARCH, ge=1)
    max_requests_analysis: int = Field(default=DEFAULT_SUBAGENT_MAX_REQUESTS_ANALYSIS, ge=1)
    max_requests_thinking: int = Field(default=DEFAULT_SUBAGENT_MAX_REQUESTS_THINKING, ge=1)
