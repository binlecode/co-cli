"""MCP server settings and shipped defaults."""

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPServerSettings(BaseModel):
    """Configuration for a single MCP server (stdio or HTTP transport).

    Stdio: set ``command`` (required). Subprocess launched by pydantic-ai.
    HTTP:  set ``url`` instead. No subprocess — connects to a remote server.
    Exactly one of ``command`` or ``url`` must be provided.
    """

    model_config = ConfigDict(extra="forbid")

    command: str | None = Field(
        default=None,
        description="Executable to launch (e.g. 'npx', 'uvx', 'python'). Required for stdio transport.",
    )
    url: str | None = Field(
        default=None,
        description="Remote server URL for HTTP transport (StreamableHTTP or SSE). Mutually exclusive with command.",
    )
    args: list[str] = Field(
        default_factory=list, description="Command-line arguments (stdio only)"
    )
    timeout: int = Field(default=5, ge=1, le=60)
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra environment variables passed to subprocess (stdio only)",
    )
    approval: Literal["ask", "auto"] = Field(default="ask")
    prefix: str | None = Field(default=None)

    @model_validator(mode="after")
    def _require_command_or_url(self) -> "MCPServerSettings":
        if self.url and self.command:
            raise ValueError("MCPServerSettings: 'url' and 'command' are mutually exclusive")
        if not self.url and not self.command:
            raise ValueError("MCPServerSettings requires either 'command' or 'url'")
        return self


# Default MCP servers — shipped out-of-the-box, skip gracefully when npx absent.
DEFAULT_MCP_SERVERS: dict[str, MCPServerSettings] = {
    "context7": MCPServerSettings(
        command="npx",
        args=["-y", "@upstash/context7-mcp@latest"],
        approval="auto",
    ),
}


MCP_SERVERS_ENV_VAR = "CO_MCP_SERVERS"


def parse_mcp_servers_from_env(env: Mapping[str, str]) -> dict | None:
    """Return the JSON-decoded mcp_servers dict from env, or None if unset."""
    raw = env.get(MCP_SERVERS_ENV_VAR)
    if not raw:
        return None
    return json.loads(raw)
