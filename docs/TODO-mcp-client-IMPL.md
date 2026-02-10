# TODO: MCP Client Integration — Implementation Tracking

**Status:** Not Started
**Effort:** 6-8 hours
**Risk:** Medium (process lifecycle, approval inheritance)
**Target:** Phase 1 (stdio transport only)

## Executive Summary

### Goal
Integrate MCP (Model Context Protocol) servers as external tool sources, allowing co-cli to discover and use tools from stdio-based MCP servers without writing custom tool code.

### Problem
Co's tool ecosystem is currently limited to built-in tools registered in `agent.py`. Users cannot extend functionality without modifying co-cli source code. MCP provides a standard protocol for tool discovery and execution across language boundaries.

### Solution
Add MCP client support via pydantic-ai's first-class MCP integration (`MCPServerStdio`, `MCPServerStreamableHTTP`). Phase 1 focuses on stdio transport for local servers. Future phases add HTTP transport and OAuth.

### Scope — Phase 1
- Config-driven server declaration in `settings.json`
- Stdio transport only (subprocess-based, single-client)
- Dynamic tool discovery at agent creation time
- MCP tools inherit host approval model (no MCP-specific approval logic)
- Tool name collision prevention via automatic prefixing
- Server lifecycle management (start at session init, clean shutdown)

### Out of Scope (Future Phases)
- HTTP transport (Phase 2)
- OAuth authentication (Phase 3)
- Runtime tool list updates via `notifications/tools/list_changed` (Phase 3)

### Effort Estimate
- Config schema: 1 hour
- Agent integration: 2 hours
- Lifecycle management: 2 hours
- Testing: 2-3 hours
- Documentation: 1 hour

### Risks
1. **Process lifecycle bugs** — Stdio servers spawn subprocesses; improper shutdown may leak processes
   - Mitigation: Use pydantic-ai's `async with agent` context manager (guaranteed cleanup)
2. **Approval inheritance complexity** — MCP tools must flow through `DeferredToolRequests` like native tools
   - Mitigation: pydantic-ai treats MCP tools identically to native tools in approval pipeline
3. **Tool name collisions** — Multiple servers may expose tools with the same name
   - Mitigation: Auto-prefix with server name (e.g. `github_create_issue`)

## Architecture Overview

### Current State
```
User ──▶ Typer CLI ──▶ Agent ──▶ Native Tools (built-in, registered via agent.tool())
```

All tools are hardcoded in `co_cli/agent.py` and must be written as Python functions.

### New State (Phase 1)
```
User ──▶ Typer CLI ──▶ Agent ──▶ Native Tools (built-in)
                            └──▶ MCP Tools (dynamic, from stdio servers)
```

MCP servers are configured in `settings.json`, launched at agent creation, and their tools are discovered dynamically via the MCP protocol.

### MCP Lifecycle
1. **Session start** (`create_deps()` in `main.py`)
   - Read `settings.mcp_servers` from config
   - Create `MCPServerStdio` instances
   - Pass to `get_agent()` via `toolsets` parameter
2. **First agent run** (`async with agent` in chat loop)
   - pydantic-ai connects to each server (launches subprocess)
   - Queries `tools/list` for available tools
   - Registers tools with auto-generated prefix
3. **Tool execution**
   - LLM calls MCP tool like any native tool
   - pydantic-ai routes to MCP server via stdio JSON-RPC
   - Server returns result
   - If `requires_approval=True`, flows through `DeferredToolRequests`
4. **Session end** (exit chat loop)
   - `async with agent` context exit
   - pydantic-ai sends shutdown signal to each server
   - Subprocesses terminate gracefully

### Tool Discovery Flow
```python
# main.py — agent creation
mcp_servers = []
for name, cfg in settings.mcp_servers.items():
    server = MCPServerStdio(
        cfg.command,
        args=cfg.args,
        timeout=cfg.timeout,
        env=cfg.env,
        tool_prefix=name,  # Auto-prefix with server name
    )
    mcp_servers.append(server)

agent = Agent(..., toolsets=mcp_servers)  # Pass as toolsets

# Chat loop — lifecycle management
async with agent:  # Connects servers, discovers tools
    result = await run_turn(agent, user_input, deps, ...)
    # ... existing approval flow ...
```

### Approval Inheritance
MCP tools inherit the existing approval model:
- **Read-only assumption**: By default, assume MCP tools are side-effectful and require approval
- **Config override**: Add `"approval": "never"` to server config to mark all tools from that server as read-only (auto-execute)
- **`DeferredToolRequests` flow**: pydantic-ai returns MCP tool calls as deferred requests when `requires_approval=True`
- **Safe-command bypass**: `_is_safe_command()` does not apply to MCP tools (only shell commands)

```python
# Example: Mark filesystem server tools as read-only
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

## Implementation Plan

### Phase 1.1 — Config Schema (1 hour)

**Goal**: Add `mcp_servers` config field with validation.

**Files to modify**:
- `co_cli/config.py`

**Changes**:
1. Add `MCPServerConfig` model:
   ```python
   class MCPServerConfig(BaseModel):
       command: str
       args: list[str] = Field(default_factory=list)
       transport: Literal["stdio"] = "stdio"
       timeout: int = Field(default=10, ge=1, le=60)
       env: dict[str, str] = Field(default_factory=dict)
       approval: Literal["auto", "never"] = Field(default="auto")
   ```
2. Add `mcp_servers` field to `Settings`:
   ```python
   mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
   ```
3. Add env var override `CO_CLI_MCP_SERVERS` in `fill_from_env()`:
   - Parse JSON string: `json.loads(os.getenv("CO_CLI_MCP_SERVERS", "{}"))` if present
   - Merge into `data["mcp_servers"]`

**Validation**:
- `command` must be non-empty
- `timeout` must be 1-60 seconds
- `transport` must be `"stdio"` (Phase 1 only)
- `approval` must be `"auto"` (requires approval for side-effects) or `"never"` (read-only, no approval)

**Test coverage**:
- Valid config loads successfully
- Invalid timeout raises validation error
- Env var override merges correctly

**Success criteria**:
- [ ] `MCPServerConfig` model added to `config.py`
- [ ] `mcp_servers` field added to `Settings`
- [ ] Env var override `CO_CLI_MCP_SERVERS` works
- [ ] Config validation rejects invalid values

---

### Phase 1.2 — Agent Integration (2 hours)

**Goal**: Pass MCP servers as toolsets to agent, with auto-prefixing.

**Files to modify**:
- `co_cli/agent.py`

**Changes**:
1. Import MCP client from pydantic-ai:
   ```python
   from pydantic_ai.mcp import MCPServerStdio
   ```
2. Update `get_agent()` signature to accept MCP servers:
   ```python
   def get_agent(
       *,
       all_approval: bool = False,
       web_policy: WebPolicy | None = None,
       mcp_servers: list[tuple[str, MCPServerConfig]] | None = None,
   ) -> tuple[Agent[CoDeps, str | DeferredToolRequests], ModelSettings | None, list[str]]:
   ```
3. Build MCP toolsets from config:
   ```python
   mcp_toolsets = []
   if mcp_servers:
       for name, cfg in mcp_servers:
           server = MCPServerStdio(
               cfg.command,
               args=cfg.args,
               timeout=cfg.timeout,
               env=cfg.env,
               tool_prefix=name,  # Auto-prefix with server name
           )
           mcp_toolsets.append(server)
   ```
4. Pass toolsets to Agent constructor:
   ```python
   agent = Agent(
       model,
       deps_type=CoDeps,
       system_prompt=system_prompt,
       retries=settings.tool_retries,
       output_type=[str, DeferredToolRequests],
       toolsets=mcp_toolsets,
       history_processors=[truncate_tool_returns, truncate_history_window],
   )
   ```
5. Determine approval requirement per server:
   ```python
   # After agent creation, register MCP tools with approval if needed
   # NOTE: pydantic-ai auto-registers MCP tools from toolsets
   # If server has approval="never", mark all tools from that server as read-only
   # This requires post-registration override — check pydantic-ai API
   ```
   **Research needed**: Does pydantic-ai support per-toolset approval override? If not, all MCP tools inherit `requires_approval=True` by default (safest assumption).

**System prompt additions**:
Add guidance for MCP tools:
```
### MCP Tools
- Tools prefixed with a server name (e.g. `github_create_issue`) come from external MCP servers
- Use them like any other tool — the prefix is just a namespace
```

**Test coverage**:
- Agent creation with no MCP servers (existing behavior)
- Agent creation with one MCP server
- Agent creation with multiple MCP servers (verify prefixing prevents collisions)

**Success criteria**:
- [ ] `MCPServerStdio` instances created from config
- [ ] Passed to `Agent(..., toolsets=[...])` constructor
- [ ] Tool name prefixing works (no collisions)
- [ ] MCP tools registered successfully

---

### Phase 1.3 — Lifecycle Management (2 hours)

**Goal**: Wrap chat loop in `async with agent` to manage server subprocesses.

**Files to modify**:
- `co_cli/main.py`

**Changes**:
1. Update `create_deps()` to pass MCP servers to `get_agent()`:
   ```python
   def create_deps() -> CoDeps:
       # ... existing code ...
       return deps

   def create_agent():
       """Create agent with MCP servers from settings."""
       mcp_servers = list(settings.mcp_servers.items()) if settings.mcp_servers else None
       agent, model_settings, tool_names = get_agent(mcp_servers=mcp_servers)
       return agent, model_settings, tool_names
   ```
2. Wrap chat loop in `async with agent`:
   ```python
   @app.command()
   def chat():
       """Start an interactive chat session."""
       set_theme(settings.theme)
       display_welcome_banner(get_status())

       deps = create_deps()
       agent, model_settings, tool_names = create_agent()

       # ... existing prompt session setup ...

       async def _chat_loop():
           async with agent:  # MCP lifecycle management
               while True:
                   try:
                       user_input = session.prompt(...)
                       # ... existing input dispatch ...
                       result = await run_turn(agent, user_input, deps, frontend, model_settings)
                       # ... existing approval flow ...
                   except KeyboardInterrupt:
                       # ... existing interrupt handling ...
                   except EOFError:
                       break

       asyncio.run(_chat_loop())
       deps.sandbox.cleanup()
   ```

**Error handling**:
- If MCP server fails to start, show clear error with server name and command
- Log server startup errors to telemetry
- Continue with remaining servers (don't abort entire session)

**Test coverage**:
- Chat loop starts/stops cleanly with MCP servers
- Server subprocess cleanup verified (no zombie processes)
- Server startup failure shows helpful error message

**Success criteria**:
- [ ] Chat loop wrapped in `async with agent`
- [ ] MCP servers start at session init
- [ ] MCP servers shut down cleanly on exit
- [ ] Server startup errors handled gracefully

---

### Phase 1.4 — Approval Inheritance (1 hour)

**Goal**: Ensure MCP tools flow through `DeferredToolRequests` like native tools.

**Files to modify**:
- `co_cli/_orchestrate.py` (if needed)
- `co_cli/agent.py` (approval flag wiring)

**Changes**:
1. Default assumption: All MCP tools require approval unless server config says otherwise
2. If server has `approval="never"`, override `requires_approval=False` for all tools from that server
3. Approval prompt shows prefixed tool name (e.g. `github_create_issue`)
4. Safe-command auto-approval does NOT apply to MCP tools (only `run_shell_command`)

**Test coverage**:
- MCP tool call with `approval="auto"` triggers approval prompt
- MCP tool call with `approval="never"` auto-executes
- Approval prompt shows correct prefixed tool name

**Success criteria**:
- [ ] MCP tools with `approval="auto"` require user approval
- [ ] MCP tools with `approval="never"` auto-execute
- [ ] Approval prompt shows prefixed tool name correctly
- [ ] Safe-command logic does not apply to MCP tools

---

### Phase 1.5 — Tool Name Collision Handling (1 hour)

**Goal**: Prevent name collisions when multiple servers expose tools with the same name.

**Files to modify**:
- `co_cli/agent.py` (already handled via `tool_prefix` in Phase 1.2)

**Changes**:
1. Auto-prefix with server name (e.g. `github_create_issue` from server named `github`)
2. Add optional `prefix` field to `MCPServerConfig` to override default prefix:
   ```python
   class MCPServerConfig(BaseModel):
       # ... existing fields ...
       prefix: str | None = Field(default=None)
   ```
3. Use custom prefix if provided, else use server name:
   ```python
   prefix = cfg.prefix or name
   server = MCPServerStdio(..., tool_prefix=prefix)
   ```

**Test coverage**:
- Two servers with same tool name use different prefixes
- Custom prefix overrides default server name

**Success criteria**:
- [ ] Tool names auto-prefixed with server name
- [ ] Custom prefix field supported in config
- [ ] No tool name collisions

---

### Phase 1.6 — Status Health Check (1 hour)

**Goal**: Add MCP server connectivity check to `co status`.

**Files to modify**:
- `co_cli/status.py`

**Changes**:
1. Add `mcp_servers` field to `StatusInfo` dataclass:
   ```python
   @dataclass
   class StatusInfo:
       # ... existing fields ...
       mcp_servers: list[tuple[str, str, bool]]  # (name, transport, reachable)
   ```
2. In `get_status()`, check each configured MCP server:
   ```python
   mcp_status = []
   for name, cfg in settings.mcp_servers.items():
       reachable = _check_mcp_server(cfg)
       mcp_status.append((name, cfg.transport, reachable))
   ```
3. Implement `_check_mcp_server()`:
   ```python
   def _check_mcp_server(cfg: MCPServerConfig) -> bool:
       """Quick connectivity check — spawn server, list tools, shutdown."""
       try:
           # Use pydantic-ai MCPServerStdio for consistency
           server = MCPServerStdio(cfg.command, args=cfg.args, timeout=5)
           # ... check if server responds to tools/list ...
           return True
       except Exception:
           return False
   ```
4. Update `render_status_table()` to show MCP servers:
   ```
   MCP Servers
   - github (stdio): reachable
   - filesystem (stdio): unreachable (timeout)
   ```

**Test coverage**:
- Status check with no MCP servers
- Status check with reachable MCP server
- Status check with unreachable MCP server

**Success criteria**:
- [ ] `co status` shows MCP server status
- [ ] Unreachable servers show error hint
- [ ] Reachable servers show transport type

---

## Code Specifications

### Config Schema

```python
# co_cli/config.py

class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""
    command: str = Field(description="Executable to launch (e.g. 'npx', 'uvx', 'python')")
    args: list[str] = Field(default_factory=list, description="Command-line arguments")
    transport: Literal["stdio"] = Field(default="stdio", description="Transport type (Phase 1: stdio only)")
    timeout: int = Field(default=10, ge=1, le=60, description="Server startup timeout in seconds")
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables")
    approval: Literal["auto", "never"] = Field(
        default="auto",
        description="Approval policy: 'auto' (default, requires approval for side-effects), 'never' (read-only, no approval)"
    )
    prefix: str | None = Field(default=None, description="Custom tool name prefix (defaults to server name)")

class Settings(BaseModel):
    # ... existing fields ...
    mcp_servers: dict[str, MCPServerConfig] = Field(
        default_factory=dict,
        description="MCP servers to integrate (key = server name)"
    )
```

### Agent Integration

```python
# co_cli/agent.py

from pydantic_ai.mcp import MCPServerStdio

def get_agent(
    *,
    all_approval: bool = False,
    web_policy: WebPolicy | None = None,
    mcp_servers: list[tuple[str, MCPServerConfig]] | None = None,
) -> tuple[Agent[CoDeps, str | DeferredToolRequests], ModelSettings | None, list[str]]:
    """
    Create agent with LLM model, native tools, and optional MCP servers.

    Args:
        all_approval: Force approval for all tools (eval mode)
        web_policy: Web tool permission policy override
        mcp_servers: List of (name, config) tuples for MCP servers
    """
    # ... existing model selection ...

    # Build MCP toolsets
    mcp_toolsets = []
    if mcp_servers:
        for name, cfg in mcp_servers:
            prefix = cfg.prefix or name
            server = MCPServerStdio(
                cfg.command,
                args=cfg.args,
                timeout=cfg.timeout,
                env=cfg.env,
                tool_prefix=prefix,
            )
            mcp_toolsets.append(server)

    # Create agent with toolsets
    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        model,
        deps_type=CoDeps,
        system_prompt=system_prompt,
        retries=settings.tool_retries,
        output_type=[str, DeferredToolRequests],
        toolsets=mcp_toolsets,
        history_processors=[truncate_tool_returns, truncate_history_window],
    )

    # Register native tools
    agent.tool(run_shell_command, requires_approval=True)
    # ... remaining native tools ...

    # Collect all tool names (native + MCP)
    native_tool_names = [fn.__name__ for fn in [...]]
    # TODO: How to introspect MCP tool names from pydantic-ai?
    # For now, just return native tool names

    return agent, model_settings, native_tool_names
```

### Lifecycle Management

```python
# co_cli/main.py

def create_agent():
    """Create agent with MCP servers from settings."""
    mcp_servers = list(settings.mcp_servers.items()) if settings.mcp_servers else None
    return get_agent(mcp_servers=mcp_servers)

@app.command()
def chat():
    """Start an interactive chat session."""
    set_theme(settings.theme)
    display_welcome_banner(get_status())

    deps = create_deps()
    agent, model_settings, tool_names = create_agent()

    # ... existing prompt session setup ...

    async def _chat_loop():
        async with agent:  # MCP server lifecycle management
            while True:
                try:
                    user_input = session.prompt(...)
                    # ... existing input dispatch ...
                    result = await run_turn(agent, user_input, deps, frontend, model_settings)
                    # ... existing approval flow ...
                except KeyboardInterrupt:
                    # ... existing interrupt handling ...
                except EOFError:
                    break

    asyncio.run(_chat_loop())
    deps.sandbox.cleanup()
```

### Error Handling

```python
# Error categories for MCP integration

class MCPError(Exception):
    """Base class for MCP-related errors."""
    pass

class MCPServerStartupError(MCPError):
    """Server failed to start (command not found, timeout, etc.)."""
    pass

class MCPToolDiscoveryError(MCPError):
    """Failed to discover tools from server."""
    pass

# In create_agent():
try:
    server = MCPServerStdio(...)
    mcp_toolsets.append(server)
except Exception as e:
    console.print(f"[yellow]Warning: MCP server '{name}' failed to start: {e}[/yellow]")
    # Continue with remaining servers
```

## Test Specifications

### Test File: `tests/test_mcp.py`

```python
"""Functional tests for MCP client integration."""

import pytest
from co_cli.config import MCPServerConfig, Settings, load_config
from co_cli.agent import get_agent
from co_cli.deps import CoDeps


class TestMCPConfig:
    """Test MCP configuration schema and validation."""

    def test_valid_config(self):
        """Valid MCP server config loads successfully."""
        cfg = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            transport="stdio",
            timeout=10,
        )
        assert cfg.command == "npx"
        assert cfg.transport == "stdio"
        assert cfg.approval == "auto"  # default

    def test_invalid_timeout(self):
        """Invalid timeout raises validation error."""
        with pytest.raises(ValueError):
            MCPServerConfig(command="npx", timeout=100)  # exceeds max 60

    def test_env_var_override(self, monkeypatch):
        """CO_CLI_MCP_SERVERS env var overrides config."""
        import json
        monkeypatch.setenv(
            "CO_CLI_MCP_SERVERS",
            json.dumps({
                "test": {
                    "command": "echo",
                    "args": ["hello"]
                }
            })
        )
        settings = load_config()
        assert "test" in settings.mcp_servers
        assert settings.mcp_servers["test"].command == "echo"


class TestMCPAgentIntegration:
    """Test MCP server integration with agent."""

    def test_agent_creation_no_mcp(self):
        """Agent creation with no MCP servers (existing behavior)."""
        agent, model_settings, tool_names = get_agent()
        assert agent is not None
        assert len(tool_names) > 0  # native tools registered

    def test_agent_creation_with_mcp(self):
        """Agent creation with one MCP server."""
        cfg = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        agent, model_settings, tool_names = get_agent(mcp_servers=[("filesystem", cfg)])
        assert agent is not None
        # TODO: Verify MCP tools registered (how to introspect?)

    def test_tool_name_prefixing(self):
        """Multiple servers with same tool name use different prefixes."""
        cfg1 = MCPServerConfig(command="server1")
        cfg2 = MCPServerConfig(command="server2")
        agent, _, _ = get_agent(mcp_servers=[("srv1", cfg1), ("srv2", cfg2)])
        # TODO: Verify srv1_toolname and srv2_toolname exist (how to introspect?)


class TestMCPLifecycle:
    """Test MCP server lifecycle management."""

    @pytest.mark.asyncio
    async def test_server_startup_shutdown(self):
        """Server subprocess starts and stops cleanly."""
        cfg = MCPServerConfig(
            command="python",
            args=["-m", "http.server", "0"],  # Dummy server
        )
        agent, _, _ = get_agent(mcp_servers=[("test", cfg)])

        async with agent:  # Should start server
            # Verify server is running (how?)
            pass

        # After exit, verify server subprocess terminated (no zombies)
        # TODO: Check process list for leaked subprocesses

    @pytest.mark.asyncio
    async def test_server_startup_failure(self):
        """Server startup failure shows helpful error."""
        cfg = MCPServerConfig(command="nonexistent-command")
        agent, _, _ = get_agent(mcp_servers=[("bad", cfg)])

        with pytest.raises(Exception):  # TODO: Specific exception type
            async with agent:
                pass


class TestMCPApproval:
    """Test MCP tool approval inheritance."""

    @pytest.mark.asyncio
    async def test_approval_required(self):
        """MCP tool with approval='auto' requires user approval."""
        cfg = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            approval="auto",
        )
        agent, _, _ = get_agent(mcp_servers=[("fs", cfg)])

        # TODO: Trigger MCP tool call, verify DeferredToolRequests returned

    @pytest.mark.asyncio
    async def test_approval_never(self):
        """MCP tool with approval='never' auto-executes."""
        cfg = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            approval="never",
        )
        agent, _, _ = get_agent(mcp_servers=[("fs", cfg)])

        # TODO: Trigger MCP tool call, verify it auto-executes


class TestMCPStatus:
    """Test MCP server status checks."""

    def test_status_with_no_servers(self):
        """Status check with no MCP servers."""
        from co_cli.status import get_status
        status = get_status()
        assert status.mcp_servers == []

    def test_status_with_reachable_server(self):
        """Status check with reachable MCP server."""
        # TODO: Spin up test MCP server, verify status shows reachable
        pass

    def test_status_with_unreachable_server(self):
        """Status check with unreachable MCP server."""
        # TODO: Configure invalid server, verify status shows unreachable + error hint
        pass
```

### Test Coverage Requirements

| Area | Test Count | Coverage Target |
|------|-----------|-----------------|
| Config validation | 3 | 100% of `MCPServerConfig` fields |
| Agent integration | 3 | Server creation, toolset registration, prefixing |
| Lifecycle | 2 | Startup, shutdown, error handling |
| Approval | 2 | Auto vs never modes |
| Status checks | 3 | Reachable, unreachable, no servers |
| **Total** | **13** | **~90% code coverage** |

## Verification Procedures

### Manual Testing Checklist

1. **Basic Integration**
   - [ ] Start co-cli with MCP server configured
   - [ ] Verify server starts (no errors in output)
   - [ ] Run `/tools` command, verify MCP tools listed
   - [ ] Call MCP tool from chat, verify it executes
   - [ ] Exit co-cli, verify server subprocess terminates (check `ps aux | grep <server>`)

2. **Approval Flow**
   - [ ] Configure server with `approval="auto"`
   - [ ] Call MCP tool, verify approval prompt appears
   - [ ] Approve with `y`, verify tool executes
   - [ ] Call same tool, deny with `n`, verify tool doesn't execute
   - [ ] Configure server with `approval="never"`
   - [ ] Call MCP tool, verify it auto-executes without prompt

3. **Error Handling**
   - [ ] Configure server with invalid command (e.g. `nonexistent`)
   - [ ] Start co-cli, verify error message shows
   - [ ] Verify other tools still work
   - [ ] Configure server with long startup time, verify timeout error

4. **Tool Name Collisions**
   - [ ] Configure two servers that expose same tool name
   - [ ] Run `/tools`, verify both show with different prefixes
   - [ ] Call prefixed tool (e.g. `server1_read`), verify correct server handles it

5. **Status Check**
   - [ ] Run `co status` with MCP servers configured
   - [ ] Verify reachable servers show as reachable
   - [ ] Stop MCP server externally, run `co status`, verify unreachable

### Test MCP Server (for manual testing)

Use `@modelcontextprotocol/server-filesystem` as reference implementation:

```bash
# Install
npm install -g @modelcontextprotocol/server-filesystem

# Test standalone
npx @modelcontextprotocol/server-filesystem /tmp

# Configure in co-cli
cat > ~/.config/co-cli/settings.json <<EOF
{
  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "approval": "never"
    }
  }
}
EOF

# Start co-cli and test
uv run co chat
> /tools
> list files in /tmp
```

### Process Cleanup Verification

After exiting co-cli, verify no zombie processes:

```bash
# Before starting co-cli
ps aux | grep -E "(npx|node.*mcp)" | wc -l  # Note count

# Start co-cli, use MCP tools, exit

# After exiting co-cli
ps aux | grep -E "(npx|node.*mcp)" | wc -l  # Should match before count
```

## Success Criteria

### Functional Requirements
1. [ ] MCP servers can be configured in `settings.json`
2. [ ] MCP servers start automatically when co-cli launches
3. [ ] MCP tools appear in `/tools` output with prefixed names
4. [ ] MCP tools can be called from chat like native tools
5. [ ] MCP tool results render correctly in terminal
6. [ ] MCP servers shut down cleanly when co-cli exits
7. [ ] No zombie processes after co-cli exit
8. [ ] Server startup failures show helpful error messages
9. [ ] Other tools continue working if one MCP server fails

### Approval Requirements
10. [ ] MCP tools with `approval="auto"` trigger approval prompt
11. [ ] MCP tools with `approval="never"` auto-execute
12. [ ] Approval prompt shows prefixed tool name
13. [ ] Safe-command bypass does NOT apply to MCP tools

### Config Requirements
14. [ ] Env var `CO_CLI_MCP_SERVERS` overrides file config
15. [ ] Invalid config values raise validation errors
16. [ ] Custom tool prefix overrides default server name

### Status Requirements
17. [ ] `co status` shows MCP server connectivity
18. [ ] Unreachable servers show error hint (command not found, timeout, etc.)
19. [ ] Reachable servers show transport type (stdio)

### Testing Requirements
20. [ ] All functional tests pass (13+ tests)
21. [ ] Code coverage >90% for MCP integration code
22. [ ] Manual testing checklist completed

### Documentation Requirements
23. [ ] `DESIGN-00-co-cli.md` §4 updated with MCP toolset integration
24. [ ] Example config added to `README.md` or new `docs/MCP.md`
25. [ ] Config field descriptions clear and complete

## Open Questions

1. **pydantic-ai MCP API**: Does pydantic-ai support per-toolset approval override, or do all MCP tools inherit the same `requires_approval` setting?
   - **Resolution strategy**: Check pydantic-ai v1.52+ docs and source code. If not supported, file feature request.

2. **Tool name introspection**: How to get the list of MCP tool names from a pydantic-ai agent for the `/tools` command?
   - **Resolution strategy**: Check pydantic-ai `Agent.tools` API. If not exposed, prefix all MCP tool names manually.

3. **Server health check**: What's the most efficient way to check if an MCP server is reachable for `co status`?
   - **Resolution strategy**: Use pydantic-ai `MCPServerStdio` to connect, call `tools/list`, disconnect. Set 5-second timeout.

4. **Error classification**: Should MCP tool errors use the existing `ToolErrorKind` enum (TERMINAL, TRANSIENT, MISUSE)?
   - **Resolution strategy**: Yes, treat MCP errors the same as native tool errors. Map MCP JSON-RPC error codes to `ToolErrorKind`.

5. **Telemetry**: Should MCP server lifecycle events (start, stop, tool call) be traced?
   - **Resolution strategy**: Yes, pydantic-ai auto-traces MCP tool calls. Add custom spans for server start/stop in `async with agent`.

## References

### Design Docs
- [TODO-mcp-client.md](TODO-mcp-client.md) — Original design document (Phase 1 stdio transport)
- [DESIGN-00-co-cli.md](DESIGN-00-co-cli.md) — Architecture overview, tool conventions
- [DESIGN-01-agent.md](DESIGN-01-agent.md) — Agent factory, `CoDeps`, tool registration
- [DESIGN-02-chat-loop.md](DESIGN-02-chat-loop.md) — Approval flow, `DeferredToolRequests`

### External References
- [MCP Specification](https://spec.modelcontextprotocol.io/) — Official protocol spec
- [pydantic-ai MCP docs](https://ai.pydantic.dev/mcp/) — First-class MCP client support
- [@modelcontextprotocol/server-filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) — Reference MCP server implementation

### Peer Implementations
- Claude Code: `packages/core/src/mcp/` — MCP client with OAuth support
- Gemini CLI: `src/mcp.ts` — Stdio + HTTP transports
- Codex: `codex-rs/core/src/mcp/` — Rust MCP client with policy engine

## Notes

### Why pydantic-ai MCP support is ideal for co-cli
- **First-class integration**: `Agent(..., toolsets=[...])` treats MCP tools identically to native tools
- **Lifecycle management**: `async with agent` handles server start/stop automatically
- **Tool prefixing**: Built-in collision prevention via `tool_prefix` parameter
- **Approval inheritance**: MCP tools flow through `DeferredToolRequests` with zero custom code
- **Telemetry**: Auto-traced like native tools via OTel

### Phase 1 scope rationale
- **Stdio only**: Covers 90% of local MCP use cases (filesystem, git, project-specific tools)
- **No OAuth**: Stdio servers typically use env vars or config files for auth
- **No runtime updates**: Dynamic tool list changes rare in local servers; can wait for Phase 3
- **MVP-first**: Ship smallest thing that works, validate with users, iterate

### Future phases (not in scope)
- **Phase 2**: HTTP transport for remote MCP servers
- **Phase 3**: OAuth 2.1 with PKCE for authenticated remote servers
- **Phase 4**: Runtime tool list updates via `notifications/tools/list_changed`
- **Phase 5**: MCP prompt templates and resources (beyond tools)
