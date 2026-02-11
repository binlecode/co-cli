---
title: "15 — MCP Client"
parent: Tools
nav_order: 7
---

# Design: MCP Client Integration

## 1. What & How

The MCP client integrates external tool servers via the Model Context Protocol, letting users extend co-cli with community-maintained or custom tools without writing Python code. Native tools remain first-class for core platforms (Google, Slack, Obsidian, Web, Memory, Shell); MCP unlocks the long tail (Jira, Notion, databases, company APIs, etc.).

Built on pydantic-ai's first-class MCP support — `MCPServerStdio` for local subprocess servers. Servers are declared in `settings.json`, launched at session start, and their tools are discovered dynamically. Three default servers ship out-of-the-box: `github` (approval required), `thinking` (sequential-thinking, auto-execute), and `context7` (documentation lookup, auto-execute).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              co-cli                                     │
│                                                                         │
│  settings.json ──▶ config.py ──▶ MCPServerConfig ──────────┐           │
│                     │                                       │           │
│                     │  _DEFAULT_MCP_SERVERS                 │           │
│                     │  (github, thinking, context7)         │           │
│                     │                                       ▼           │
│  main.py ──────────▶ agent.py:get_agent(mcp_servers=...)               │
│  │                   │                                                  │
│  │                   ├── MCPServerStdio(cmd, args, prefix, env, timeout)│
│  │                   │     └── .approval_required() if approval="auto" │
│  │                   │                                                  │
│  │                   └──▶ Agent(toolsets=[...mcp_servers])              │
│  │                        │                                             │
│  │  async with agent ─────┘  (connects servers, discovers tools)       │
│  │  │                                                                   │
│  │  └── _discover_mcp_tools() ──▶ tool_names for /tools display        │
│  │                                                                      │
│  │  chat loop ──▶ run_turn() ──▶ LLM selects tool                     │
│  │                                │                                     │
│  │                    ┌───────────┴───────────┐                         │
│  │                    ▼                       ▼                         │
│  │              Native Tools           MCP Tools                       │
│  │              (agent.tool())         (via toolsets)                   │
│  │              direct execution       JSON-RPC over stdio             │
│  │                    │                       │                         │
│  │                    │            ┌──────────┴──────────┐              │
│  │                    │            ▼          ▼          ▼              │
│  │                    │         github    thinking   context7           │
│  │                    │         (npx)     (npx)      (npx)             │
│  │                    │                                                 │
│  │                    └───────┬───────────────┘                        │
│  │                            ▼                                         │
│  │                    ApprovalRequiredToolset                           │
│  │                    (approval="auto" servers only)                    │
│  │                            │                                         │
│  │                            ▼                                         │
│  │                    DeferredToolRequests ──▶ user approve/deny        │
│  │                                                                      │
│  └── status.py:get_status() ──▶ shutil.which(cfg.command)              │
│                                  └── "ready" | "<cmd> not found"        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    External MCP Server Subprocesses
                    (launched by pydantic-ai, JSON-RPC stdio)
```

### Peer Landscape

All four peer systems (Claude Code, Gemini CLI, Codex, Goose) converge on:

1. **Config-driven server declaration** — JSON object in settings mapping server names to transport config
2. **Stdio for local, HTTP for remote** — Stdio transport for local subprocess servers; Streamable HTTP for remote (SSE is deprecated)
3. **Dynamic tool discovery** — Query `tools/list` at startup; support `notifications/tools/list_changed` for runtime updates
4. **Approval integration** — MCP tools inherit the host's existing approval/permission model
5. **Tool prefixing** — Namespace with server name to prevent collisions (e.g. `github_create_issue`)

### pydantic-ai MCP Support

- `MCPServerStdio(command, args=[], timeout=10)` — stdio transport, launches subprocess
- `server.approval_required()` — wraps server in `ApprovalRequiredToolset` for approval flow
- `Agent(..., toolsets=[server1, server2])` — register MCP servers as toolsets alongside native tools
- `tool_prefix` parameter on server instances for collision prevention
- `async with agent` context manager handles server lifecycle (connect/disconnect)

## 2. Core Logic

### MCP Lifecycle

1. **Session start** (`main.py`)
   - Read `settings.mcp_servers` from config
   - Pass to `get_agent(mcp_servers=...)` which creates `MCPServerStdio` instances
   - Servers with `approval="auto"` are wrapped via `server.approval_required()`
   - GitHub server gets lazy token resolution from `GITHUB_TOKEN_BINLECODE` env var

2. **First agent run** (`async with agent` in chat loop)
   - pydantic-ai connects to each server (launches subprocess)
   - Queries `tools/list` for available tools
   - `_discover_mcp_tools()` in `main.py` enumerates discovered MCP tool names for `/tools` display

3. **Tool execution**
   - LLM calls MCP tool like any native tool
   - pydantic-ai routes to MCP server via stdio JSON-RPC
   - Server returns result
   - If `requires_approval=True`, flows through `DeferredToolRequests`

4. **Session end** (exit chat loop)
   - `async with agent` context exit
   - pydantic-ai sends shutdown signal to each server
   - Subprocesses terminate gracefully

### Tool Discovery

```
for each (name, cfg) in settings.mcp_servers:
    prefix = cfg.prefix or name
    env = resolve_env(name, cfg)     # lazy GitHub token injection
    server = MCPServerStdio(cfg.command, args=cfg.args, timeout=cfg.timeout,
                            env=env, tool_prefix=prefix)
    if cfg.approval == "auto":
        server = server.approval_required()     # wrap for DeferredToolRequests
    toolsets.append(server)

agent = Agent(..., toolsets=toolsets)

async with agent:                    # connects servers, discovers tools
    tool_names = _discover_mcp_tools(agent, native_tool_names)
    # tool_names now includes prefixed MCP tools (e.g. "github_search_repositories")
```

### Approval Inheritance

MCP tools inherit the existing approval model — no MCP-specific approval logic:

- **Default**: All MCP tools require approval — `server.approval_required()` wraps the server in `ApprovalRequiredToolset`
- **Config override**: `"approval": "never"` on a server passes the raw `MCPServerStdio` (no wrapping, auto-execute)
- **`DeferredToolRequests` flow**: wrapped MCP tool calls return as deferred requests through the standard approval pipeline
- **Safe-command bypass**: `_is_safe_command()` does NOT apply to MCP tools (shell commands only)

Example — mark filesystem server as read-only:
```json
{
  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
      "approval": "never"
    }
  }
}
```

### Error Handling

- Server startup failures log a warning and continue — one failed server does not abort the session
- MCP tool errors map to the existing `ToolErrorKind` classification (TERMINAL, TRANSIENT, MISUSE)
- pydantic-ai auto-traces MCP tool calls via OTel; custom spans added for server start/stop

### Status Health Check

`co status` checks each configured MCP server's command availability via `shutil.which()`. Reports `"ready"` when the command is on PATH, or `"<command> not found"` otherwise.

### Env Var Merge Semantics

Three layers affect MCP server configuration. Each uses **replacement**, not deep merge, consistent with the rest of co-cli's config system:

1. **Project config overrides user config** — If project `.co-cli/settings.json` contains `mcp_servers`, it replaces the entire user-level `mcp_servers` dict (shallow `|=` merge on top-level keys). To keep defaults alongside project servers, redeclare them in the project config.

2. **`CO_CLI_MCP_SERVERS` env var overrides file config** — Setting this env var (JSON string) replaces the entire `mcp_servers` dict from files. To add a server without losing defaults, include all desired servers in the JSON.

3. **Per-server `env` merges with safe system defaults** — The MCP SDK merges `cfg.env` on top of a safe whitelist (`PATH`, `HOME`, `USER`, `SHELL`, `TERM`, `LOGNAME`). You do not need to include `PATH` in `env` — it is always inherited. Your entries override defaults if keys collide.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `mcp_servers` | `CO_CLI_MCP_SERVERS` (JSON) | 3 defaults (github, thinking, context7) | Map of server name to `MCPServerConfig` |
| `mcp_servers.<name>.command` | — | (required) | Executable to launch (e.g. `npx`, `uvx`, `python`) |
| `mcp_servers.<name>.args` | — | `[]` | Command-line arguments |
| `mcp_servers.<name>.timeout` | — | `10` | Server startup timeout in seconds (1–60) |
| `mcp_servers.<name>.env` | — | `{}` | Extra environment variables passed to subprocess |
| `mcp_servers.<name>.approval` | — | `"auto"` | `"auto"` (requires approval) or `"never"` (read-only) |
| `mcp_servers.<name>.prefix` | — | server name | Custom tool name prefix (overrides server name) |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/config.py` | `MCPServerConfig` model, `_DEFAULT_MCP_SERVERS`, `mcp_servers` field on `Settings` |
| `co_cli/agent.py` | Build `MCPServerStdio` toolsets from config, approval wrapping, GitHub token resolution |
| `co_cli/main.py` | `async with agent` lifecycle, `_discover_mcp_tools()` for tool name enumeration |
| `co_cli/status.py` | MCP server health check via `shutil.which()` in `get_status()` |
| `tests/test_mcp.py` | Config, agent integration, status display, E2E server tests (30+ tests) |
