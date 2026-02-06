# Review: sidekick-cli Patterns Analysis

**Source:** `/Users/binle/workspace_genai/sidekick-cli`
**Purpose:** Analyze patterns for co-cli learning journey
**Reviewed:** 2026-02-04

---

## Executive Summary

sidekick-cli is a well-structured pydantic-ai CLI that demonstrates several best practices alongside some anti-patterns. This review identifies what to adopt and what to avoid as we build co-cli.

| Category | Good Patterns | Anti-Patterns |
|----------|--------------|---------------|
| **Architecture** | 5 | 3 |
| **Tool Design** | 4 | 2 |
| **Error Handling** | 3 | 1 |
| **Testing** | 2 | 1 |

---

## Good Patterns to Adopt

### 1. Proper Dependency Injection via `ToolDeps`

**File:** `src/sidekick/deps.py:1-11`

```python
@dataclass
class ToolDeps:
    """Dependencies passed to tools via RunContext."""
    confirm_action: Optional[Callable[[str, str, Optional[str]], Awaitable[bool]]] = None
    display_tool_status: Optional[Callable[[str, Any], Awaitable[None]]] = None
```

**Why it's good:**
- Follows the pydantic-ai `deps_type` pattern from `bank_support.py`
- Injects callbacks rather than hardcoding UI dependencies
- Tools remain testable - you can inject mock callbacks
- Clean separation between tool logic and presentation

**How to adopt in co-cli:**
```python
# co_cli/deps.py
@dataclass
class CoDeps:
    confirm_action: Optional[Callable[[str, str], Awaitable[bool]]] = None
    sandbox: Optional[DockerSandbox] = None  # Our unique addition
    settings: Settings = None
```

---

### 2. Agent Iteration Pattern with Node Processing

**File:** `src/sidekick/agent.py:157-167`

```python
async with agent.iter(message, deps=deps, message_history=mh) as agent_run:
    async for node in agent_run:
        await _process_node(node, message_history)

    usage = agent_run.usage()
    result = agent_run.result.output
```

**Why it's good:**
- Uses `agent.iter()` for fine-grained control over execution
- Enables streaming UI updates (spinner, thinking panels)
- Captures usage metrics per-request
- Allows processing of intermediate nodes (tool calls, retries)

**pydantic-ai source reference:** `pydantic_ai_slim/pydantic_ai/agent/__init__.py`

---

### 3. Tool Factory with Consistent Retry Configuration

**File:** `src/sidekick/tools/wrapper.py:14-27`

```python
TOOL_RETRY_LIMIT = 10

def create_tools():
    """Create Tool instances for all tools."""
    tools = [read_file, write_file, update_file, run_command, ...]
    return [Tool(tool, max_retries=TOOL_RETRY_LIMIT) for tool in tools]
```

**Why it's good:**
- Centralizes retry configuration
- Consistent behavior across all tools
- Easy to adjust globally
- Uses `Tool()` wrapper for explicit configuration

---

### 4. ModelRetry for Recoverable Tool Errors

**File:** `src/sidekick/tools/update_file.py:18-38`

```python
if old_content == new_content:
    raise ModelRetry(
        "The old_content and new_content are identical. "
        "Please provide different content for the replacement."
    )

if old_content not in content:
    raise ModelRetry(
        f"Content to replace not found in {filepath}. "
        "Please re-read the file and ensure the exact content matches."
    )
```

**Why it's good:**
- Gives the LLM actionable feedback to self-correct
- Doesn't crash the agent - allows retry
- Clear, specific error messages guide the model
- Follows pydantic-ai's `ModelRetry` exception pattern

**pydantic-ai source reference:** `pydantic_ai_slim/pydantic_ai/exceptions.py`

---

### 5. Message History Management with Error Patching

**File:** `src/sidekick/messages.py:44-81`

```python
def patch_on_error(self, error_message: str) -> None:
    """Patch the message history with a ToolReturnPart on error.

    LLM models expect to see both a tool call and its corresponding
    response in the history. Without this patch, the next request
    would fail because the model would see an unanswered tool call.
    """
    # ... finds last tool call and adds synthetic response
```

**Why it's good:**
- Maintains valid conversation state after interrupts
- Prevents "dangling tool call" errors
- Documents the *why* clearly
- Enables graceful recovery from Ctrl+C

---

### 6. MCP Server Integration with Custom Callbacks

**File:** `src/sidekick/mcp/servers.py:25-54`

```python
async def mcp_tool_confirmation_callback(
    ctx: RunContext[Any],
    original_call_tool,
    tool_name: str,
    arguments: Dict[str, Any],
) -> Any:
    """Process tool callback for MCP tool calls."""
    if hasattr(ctx.deps, "confirm_action") and ctx.deps.confirm_action:
        confirmed = await ctx.deps.confirm_action(f"MCP({tool_name})", args_display)
        if not confirmed:
            raise asyncio.CancelledError("MCP tool execution cancelled")
    return await original_call_tool(tool_name, arguments)
```

**Why it's good:**
- Extends MCP servers with custom confirmation flow
- Uses `process_tool_call` callback pattern
- Integrates cleanly with deps-based confirmation

---

### 7. Comprehensive Error Context with Cleanup

**File:** `src/sidekick/utils/error.py:133-161`

```python
class ErrorContext:
    """Context for error handling with cleanup callbacks."""

    def add_cleanup(self, callback: Callable) -> None:
        self.cleanup_callbacks.append(callback)

    async def handle(self, error: Exception) -> Optional[Any]:
        for callback in self.cleanup_callbacks:
            callback(error)  # e.g., patch message history
```

**Why it's good:**
- Cleanup callbacks ensure consistent state
- Separates error handling from business logic
- Supports both sync and async cleanup
- Reusable across different operations

---

### 8. REPL with Proper Signal Handling

**File:** `src/sidekick/repl.py:75-87`

```python
def _setup_signal_handler(self):
    def signal_handler(signum, frame):
        if self.current_task and not self.current_task.done():
            ui.stop_spinner()
            self._kill_child_processes()
            self.loop.call_soon_threadsafe(self.current_task.cancel)
        else:
            raise KeyboardInterrupt()
    signal.signal(signal.SIGINT, signal_handler)
```

**Why it's good:**
- Clean Ctrl+C handling during async operations
- Kills child processes (important for shell commands)
- Uses `call_soon_threadsafe` for async cancellation
- Falls back to normal KeyboardInterrupt when idle

---

## Anti-Patterns to Avoid

### 1. Global Mutable Session Singleton

**File:** `src/sidekick/session.py:22-23`

```python
# Create global session instance
session = Session()
```

**Why it's bad:**
- Global state makes testing difficult
- Hidden dependency - not explicit in function signatures
- Race conditions possible with concurrent access
- Violates dependency injection principles

**Used throughout:** `agent.py`, `repl.py`, `main.py`, `run_command.py`

**Better approach for co-cli:**
```python
# Pass session as part of deps
@dataclass
class CoDeps:
    session: SessionState
    confirm_action: ...

# Or use RunContext directly
@agent.tool
async def my_tool(ctx: RunContext[CoDeps]) -> str:
    model = ctx.deps.session.current_model  # Explicit dependency
```

---

### 2. Shell Commands Without Sandbox

**File:** `src/sidekick/tools/run_command.py:32-40`

```python
result = subprocess.run(
    command,
    shell=True,      # Dangerous: allows shell injection
    capture_output=True,
    text=True,
    timeout=30,
)
```

**Why it's bad:**
- `shell=True` with user/LLM input is a security risk
- Commands run directly on host system
- No isolation from sensitive files/data
- No resource limits (CPU, memory, disk)

**co-cli does this better:**
```python
# co_cli/sandbox.py uses Docker
docker run --rm -v "$PWD:/workspace" python:3.12-slim sh -c "command"
```

---

### 3. Global Usage Tracker Singleton

**File:** `src/sidekick/usage.py:102-103`

```python
# Global usage tracker instance
usage_tracker = UsageTracker()
```

**Why it's bad:**
- Same issues as session singleton
- Can't easily track usage per-conversation
- Testing requires resetting global state

**Better approach:**
```python
# Include in deps or return from agent run
@dataclass
class CoDeps:
    usage_tracker: UsageTracker
```

---

### 4. Tool Registration via Imports (Implicit)

**File:** `src/sidekick/tools/__init__.py`

```python
from .wrapper import create_tools
TOOLS = create_tools()
```

**Why it's suboptimal:**
- Tools are bare functions, wrapped later
- No decorator-based registration pattern
- Less cohesive than `@agent.tool` approach

**pydantic-ai best practice:**
```python
@agent.tool
async def read_file(ctx: RunContext[CoDeps], filepath: str) -> str:
    """Read file contents."""
    ...
```

---

### 5. Mixing Concerns in Agent Module

**File:** `src/sidekick/agent.py:75-109`

The confirmation callback is defined inline in `agent.py`:

```python
def _create_confirmation_callback():
    async def confirm(title, preview, footer=None):
        if not session.confirmation_enabled:  # Uses global session
            return True
        ui.stop_spinner()  # UI logic in agent module
        ...
```

**Why it's suboptimal:**
- Agent module handles UI concerns
- Depends on global `session`
- Callback logic should be injected, not defined here

**Better separation:**
```python
# ui/confirmations.py - define the callback
# deps.py - type the callback interface
# main.py - wire them together at startup
```

---

### 6. Config Validation Without Pydantic

**File:** `src/sidekick/config.py:57-79`

```python
def validate_config_structure(config: Dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ConfigValidationError("Config must be a JSON object")
    if "default_model" not in config:
        raise ConfigValidationError("Config missing required field")
    ...
```

**Why it's suboptimal:**
- Manual validation when Pydantic exists
- No type coercion or defaults
- Error messages less informative

**Better approach (used in co-cli):**
```python
from pydantic import BaseModel, Field

class Settings(BaseModel):
    llm_provider: str = "gemini"
    gemini_api_key: str | None = Field(default=None, env="GEMINI_API_KEY")
    # Automatic validation, env var support, type coercion
```

---

## Structural Comparison

| Aspect | sidekick-cli | co-cli (Target) |
|--------|--------------|-----------------|
| **Config** | Manual JSON validation | Pydantic `BaseModel` |
| **Session** | Global singleton | Via deps injection |
| **Shell** | Direct `subprocess.run` | Docker sandbox |
| **Tools** | Wrapped bare functions | `@agent.tool` decorator |
| **Confirmation** | Callback in deps | Callback in deps |
| **MCP** | Custom `SilentMCPServerStdio` | Standard + config |
| **Telemetry** | In-memory usage tracker | OpenTelemetry + SQLite |

---

## Recommendations for co-cli

### Adopt These Patterns:
1. `ToolDeps` dataclass with callback injection
2. `agent.iter()` for streaming/node processing
3. `ModelRetry` for recoverable errors
4. `ErrorContext` with cleanup callbacks
5. Message history patching for interrupts
6. Signal handler for graceful Ctrl+C

### Avoid/Improve:
1. No global singletons - pass everything via deps
2. Keep Docker sandbox for shell commands
3. Use Pydantic for config validation (already doing this)
4. Use `@agent.tool` decorator pattern
5. Separate UI concerns from agent module

### Unique co-cli Advantages to Preserve:
1. Docker sandbox isolation
2. OpenTelemetry + SQLite telemetry
3. Pydantic-based settings with env var fallbacks
4. XDG-compliant paths

---

## File-by-File Reference

### Worth Studying
| File | Pattern | Notes |
|------|---------|-------|
| `deps.py` | Dependency injection | Clean, minimal |
| `agent.py:139-177` | Agent iteration | `iter()` pattern |
| `messages.py` | History management | Error patching |
| `tools/update_file.py` | ModelRetry usage | Self-correction |
| `utils/error.py` | Error context | Cleanup callbacks |
| `mcp/servers.py` | MCP integration | Custom callbacks |

### Cautionary Examples
| File | Anti-Pattern | Issue |
|------|--------------|-------|
| `session.py:23` | Global singleton | Hidden dependency |
| `usage.py:103` | Global singleton | Testing issues |
| `tools/run_command.py:32` | No sandbox | Security risk |
| `config.py` | Manual validation | Should use Pydantic |

---

## Next Steps

1. **Phase 1:** Refactor `co_cli/agent.py` to use `deps_type=CoDeps`
2. **Phase 2:** Add `agent.iter()` loop for streaming
3. **Phase 3:** Implement `ModelRetry` in tools
4. **Phase 4:** Add message history with error patching
5. **Phase 5:** Integrate MCP server support
