# TODO: MCP Client Support

Allow co-cli to integrate with external MCP (Model Context Protocol) tool servers without custom tool code. See `docs/DESIGN-co-cli.md` §4 for tool architecture.

## Peer Landscape (Claude Code, Gemini CLI, Codex, Goose)

All four systems that ship MCP client support converge on:

1. **Config-driven server declaration** — JSON object in settings file mapping server names to transport config. Claude Code: `mcpServers` in project config; Gemini CLI: settings + CLI flags; Codex: env vars > project > global.
2. **Stdio for local, HTTP for remote** — Every peer defaults to stdio transport for local servers (subprocess, single-client). Streamable HTTP for remote. SSE is deprecated — avoid for new work.
3. **Dynamic tool discovery** — Query each server for available tools at startup. Support `notifications/tools/list_changed` for runtime updates.
4. **Approval integration** — MCP tools inherit the host's existing approval/permission model. Claude Code: allow/ask/deny rules; Gemini CLI: `tools.allowed` prefix matching; Codex: policy engine.
5. **Tool prefixing** — Avoid name collisions when multiple servers expose the same tool name. Prefix with server name (e.g. `github_create_issue`).

**pydantic-ai v1.52+ has first-class MCP client support:**
- `MCPServerStdio(command, args=[], timeout=10)` — stdio transport, launches subprocess
- `MCPServerStreamableHTTP(url)` — Streamable HTTP transport for remote servers
- `Agent(..., toolsets=[server1, server2])` — register MCP servers as toolsets alongside native tools
- Tool prefixing via `tool_prefix` parameter on server instances
- `async with agent` context manager handles server lifecycle (connect/disconnect)

---

## Phase 1 — Stdio Transport (MVP)

Goal: let users configure local MCP servers in settings.json and have their tools available in `co chat`. Ship the smallest thing that works.

### Config schema

Add `mcp_servers` to `settings.json`:

```json
{
  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
      "transport": "stdio",
      "timeout": 10
    },
    "github": {
      "command": "uvx",
      "args": ["mcp-server-github"],
      "transport": "stdio",
      "env": {"GITHUB_TOKEN": "..."}
    }
  }
}
```

Each entry:
- `command` (str, required) — executable to launch
- `args` (list[str], default `[]`) — command-line arguments
- `transport` (literal `"stdio"`, default `"stdio"`) — transport type (Phase 1: stdio only)
- `timeout` (int, default 10) — server startup timeout in seconds
- `env` (dict[str, str], optional) — extra environment variables for the subprocess

### Integration point

```python
# co_cli/agent.py — get_agent()
from pydantic_ai.mcp import MCPServerStdio

mcp_toolsets = []
for name, cfg in settings.mcp_servers.items():
    server = MCPServerStdio(
        cfg.command,
        args=cfg.args,
        timeout=cfg.timeout,
        env=cfg.env,
    )
    mcp_toolsets.append(server)

agent = Agent(
    model,
    deps_type=CoDeps,
    system_prompt=system_prompt,
    retries=settings.tool_retries,
    output_type=[str, DeferredToolRequests],
    toolsets=mcp_toolsets,
)
```

Native tools (`agent.tool(...)`) continue to be registered after agent creation — they coexist with MCP toolsets.

### Chat loop lifecycle

MCP servers need `async with agent` for proper subprocess lifecycle. Update `main.py` chat loop:

```python
async with agent:
    # existing chat loop (prompt → agent.run → approval → display)
    ...
```

This ensures stdio subprocesses are started before the first tool call and cleaned up on exit.

### Approval integration

MCP tools inherit the existing `DeferredToolRequests` flow. No MCP-specific approval code needed — pydantic-ai treats MCP tools identically to native tools in the approval pipeline.

For safe-command auto-approval: MCP tools don't run shell commands, so `_is_safe_command()` doesn't apply. All MCP tool calls go through the standard approval prompt unless `auto_confirm=True`.

### Config additions

```python
# co_cli/config.py

class MCPServerConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    transport: Literal["stdio"] = "stdio"
    timeout: int = Field(default=10, ge=1, le=60)
    env: dict[str, str] = Field(default_factory=dict)

class Settings(BaseModel):
    # ... existing fields ...
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
```

Env var override: `CO_CLI_MCP_SERVERS` (JSON string) for CI/scripting use cases.

### Status health check

Add MCP server connectivity check to `co_cli/status.py`:
- For each configured server, report name + transport + reachable/unreachable
- On failure: show error hint (e.g. "command not found", "timeout")

### Items

- [ ] Add `MCPServerConfig` model to `co_cli/config.py`
- [ ] Add `mcp_servers` field to `Settings` with env var override
- [ ] Update `get_agent()` in `co_cli/agent.py` to create `MCPServerStdio` instances from config
- [ ] Wrap chat loop in `async with agent` for MCP lifecycle management
- [ ] Add MCP server health check to `co_cli/status.py`
- [ ] Add `/tools` command enhancement: show MCP tools alongside native tools
- [ ] Add functional test: configure stdio server, verify tools appear in agent
- [ ] Add functional test: MCP tool call flows through approval loop
- [ ] Update `docs/DESIGN-co-cli.md` §4 to document MCP toolset integration

---

## Phase 2 — Streamable HTTP Transport

Goal: support remote MCP servers over HTTP. Extends Phase 1 config with `"transport": "http"`.

### Config additions

```json
{
  "mcp_servers": {
    "remote-api": {
      "url": "https://api.example.com/mcp",
      "transport": "http",
      "headers": {"Authorization": "Bearer ..."}
    }
  }
}
```

New fields for HTTP transport:
- `url` (str, required) — server endpoint
- `headers` (dict[str, str], optional) — static auth headers
- `transport` becomes `Literal["stdio", "http"]`

### Integration

```python
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

if cfg.transport == "stdio":
    server = MCPServerStdio(cfg.command, args=cfg.args, timeout=cfg.timeout, env=cfg.env)
elif cfg.transport == "http":
    server = MCPServerStreamableHTTP(cfg.url, headers=cfg.headers)
```

### Items

- [ ] Extend `MCPServerConfig` with `url`, `headers` fields and `transport: Literal["stdio", "http"]`
- [ ] Update `get_agent()` factory to handle HTTP transport
- [ ] Add functional test: HTTP server connectivity
- [ ] Document HTTP config examples

---

## Phase 3 — OAuth & Tool Prefixing (post-MVP)

Goal: remote server OAuth and multi-server name collision handling.

### OAuth

Standard OAuth 2.1 with PKCE, matching Claude Code / Gemini CLI patterns:
- Server discovery via `/.well-known/oauth-protected-resource`
- Token storage in `~/.config/co-cli/mcp-tokens.json`
- Automatic refresh on 401
- Dynamic Client Registration (DCR) for zero-config new servers

### Tool prefixing

When multiple servers expose tools with the same name:
- Use pydantic-ai's `tool_prefix` parameter: `MCPServerStdio(..., tool_prefix="github")`
- Auto-prefix with server name from config key
- Configurable: `"prefix": "gh"` in server config to override

### Items

- [ ] Implement OAuth token storage and refresh in `co_cli/_mcp_auth.py`
- [ ] Add `prefix` field to `MCPServerConfig`
- [ ] Pass `tool_prefix` to MCP server constructors
- [ ] Add functional test: OAuth flow (mock server)
- [ ] Add functional test: tool name collision with prefixing
