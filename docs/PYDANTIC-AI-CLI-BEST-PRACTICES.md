# Pydantic AI Best Practices for Agentic CLI Systems

> **Distilled from**: pydantic-ai source code, official examples, documentation, and sidekick-cli patterns.
> **Verified against**: pydantic-ai v1.x (Feb 2026)

---

## 1. Core Principles

### The Pydantic AI Philosophy
1. **Type Safety First** - Enforce structure with Pydantic models, not prompt engineering
2. **Stateless Agents** - Agents are definitions; runtime state flows through `deps`
3. **Code over Prompts** - Python logic > "magic" prompt chains
4. **Observable by Default** - If you can't trace it, you can't ship it

### CLI-Specific Considerations
- **Streaming** - Use `agent.iter()` for responsive terminal output
- **Human-in-the-loop** - Use `requires_approval=True` for dangerous tools
- **Graceful degradation** - Handle missing configs with `ModelRetry`
- **Session isolation** - Each run gets fresh deps instance

---

## 2. Dependency Injection (The Foundation)

### What Goes in Deps

**DO include:**
```python
@dataclass
class CLIDeps:
    # Pre-built clients (created from config in main)
    http_client: AsyncClient
    db_pool: asyncpg.Pool

    # Resolved paths/tokens (not the Settings object)
    workspace_path: Path

    # Callbacks for CLI interaction
    confirm_action: Callable[[str], Awaitable[bool]] | None = None
    display_status: Callable[[str], None] | None = None

    # Run context
    session_id: str = ""
```

**DON'T include:**
```python
# ❌ Anti-pattern: Passing entire Settings object
@dataclass
class BadDeps:
    settings: Settings  # Tools don't need ALL settings
```

### Why This Matters
From `rag.py` and `weather_agent.py` examples:
- Settings creates clients → clients go into deps
- Tools access pre-built resources via `ctx.deps`
- Easier to test (mock individual clients, not Settings)

### Creating Deps (main.py pattern)
```python
async def main():
    # Create resources from config ONCE
    async with AsyncClient() as http_client:
        deps = CLIDeps(
            http_client=http_client,
            workspace_path=Path(settings.workspace_dir),
            confirm_action=create_confirm_callback(),
            session_id=uuid4().hex,
        )

        # Run agent with deps
        await run_chat_loop(deps)
```

---

## 3. Tool Patterns

### Tool Function Signature
Tools are plain async functions with `RunContext[Deps]` as first param:

```python
from pydantic_ai import RunContext, ModelRetry

async def read_file(ctx: RunContext[CLIDeps], path: str) -> str:
    """Read a file from the workspace."""
    full_path = ctx.deps.workspace_path / path

    # Validate within workspace (security)
    if not full_path.is_relative_to(ctx.deps.workspace_path):
        raise ModelRetry("Access denied: path outside workspace")

    if not full_path.exists():
        raise ModelRetry(f"File not found: {path}. Use list_files first.")

    return full_path.read_text()
```

### Tool Registration (agent.py)
```python
from pydantic_ai import Agent
from myapp.deps import CLIDeps
from myapp.tools import read_file, write_file, run_command

agent = Agent(
    'google-gla:gemini-2.0-flash',
    deps_type=CLIDeps,
    system_prompt="You are a CLI assistant...",
)

# Register after agent creation (avoids circular imports)
agent.tool(read_file)
agent.tool(write_file)
agent.tool(run_command, requires_approval=True)  # Dangerous!
```

### Self-Healing with ModelRetry
Don't return error strings. Raise `ModelRetry` with guidance:

```python
# ❌ Bad - LLM can't recover
async def find_user(ctx: RunContext[Deps], name: str) -> dict:
    user = db.find(name)
    if not user:
        return {"error": "User not found"}  # LLM sees error, gives up

# ✅ Good - LLM retries with better input
async def find_user(ctx: RunContext[Deps], name: str) -> dict:
    user = db.find(name)
    if not user:
        available = db.list_users()[:5]
        raise ModelRetry(
            f"User '{name}' not found. "
            f"Available: {available}. Use exact name."
        )
    return user
```

---

## 4. CLI Streaming with agent.iter()

For responsive CLI output, use `agent.iter()` instead of `agent.run()`:

```python
from pydantic_ai import Agent, CallToolsNode, End

async def chat(prompt: str, deps: CLIDeps):
    async with agent.iter(prompt, deps=deps) as run:
        async for node in run:
            # Handle different node types
            if isinstance(node, CallToolsNode):
                for part in node.model_response.parts:
                    if isinstance(part, TextPart):
                        # Stream text to terminal
                        print(part.content, end="", flush=True)
                    elif isinstance(part, ToolCallPart):
                        # Show tool being called
                        print(f"\n[Calling {part.tool_name}...]")

            elif isinstance(node, End):
                # Final result
                return run.result.output
```

---

## 5. Human-in-the-Loop (Tool Approval)

### Using requires_approval
```python
# Register tool with approval requirement
agent.tool(run_shell_command, requires_approval=True)
agent.tool(send_email, requires_approval=True)
```

### Handling Deferred Tools
```python
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied

async def chat_with_approval(prompt: str, deps: CLIDeps):
    result = await agent.run(prompt, deps=deps)

    # Check if tools need approval
    if isinstance(result.output, DeferredToolRequests):
        approvals = {}
        for call in result.output.approvals:
            # Show tool call to user
            print(f"Tool: {call.tool_name}")
            print(f"Args: {call.args}")

            if await deps.confirm_action(f"Execute {call.tool_name}?"):
                approvals[call.tool_call_id] = ToolApproved()
            else:
                approvals[call.tool_call_id] = ToolDenied("User cancelled")

        # Continue with approvals
        result = await agent.run(
            prompt,
            deps=deps,
            message_history=result.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals=approvals),
        )

    return result.output
```

### Callback Pattern (from sidekick-cli)
```python
@dataclass
class CLIDeps:
    confirm_action: Callable[[str, str], Awaitable[bool]] | None = None

# In tool
async def run_command(ctx: RunContext[CLIDeps], cmd: str) -> str:
    if ctx.deps.confirm_action:
        if not await ctx.deps.confirm_action("Execute command", cmd):
            raise ModelRetry("User cancelled. Ask what they'd like instead.")
    return subprocess.run(cmd, capture_output=True).stdout
```

---

## 6. Testing

### Unit Tests with TestModel
```python
import pytest
from pydantic_ai.models.test import TestModel

@pytest.fixture
def test_deps():
    return CLIDeps(
        workspace_path=Path("/tmp/test"),
        session_id="test",
    )

@pytest.mark.asyncio
async def test_file_tool_called(test_deps):
    with agent.override(model=TestModel()):
        result = await agent.run("Read config.json", deps=test_deps)
        # TestModel simulates responses for testing logic
        assert result.output
```

### FunctionModel for Specific Responses
```python
from pydantic_ai.models.function import FunctionModel

async def mock_response(messages, info):
    return ModelResponse(parts=[TextPart("Mocked response")])

async def test_parsing():
    with agent.override(model=FunctionModel(mock_response)):
        result = await agent.run("Hello", deps=test_deps)
        assert result.output == "Mocked response"
```

---

## 7. Observability

### Logfire Integration
```python
import logfire

# Configure once at startup
logfire.configure(send_to_logfire='if-token-present')
logfire.instrument_pydantic_ai()

# Optional: instrument other libraries
logfire.instrument_httpx(http_client)
logfire.instrument_asyncpg()
```

### Custom Spans
```python
async def complex_operation(ctx: RunContext[CLIDeps]):
    with logfire.span("complex_operation"):
        # Operations are traced
        result = await ctx.deps.http_client.get(url)
        logfire.info("Fetched {url}", url=url)
        return result
```

---

## 8. Model Configuration

### Using GoogleModel (not deprecated GeminiModel)
```python
from pydantic_ai import Agent

# Option 1: Model string
agent = Agent('google-gla:gemini-2.0-flash', ...)

# Option 2: Explicit model
from pydantic_ai.models.google import GoogleModel

model = GoogleModel('gemini-2.0-flash', api_key=os.getenv('GEMINI_API_KEY'))
agent = Agent(model, ...)
```

### Ollama via OpenAI-Compatible API
```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

provider = OpenAIProvider(
    base_url="http://localhost:11434/v1",
    api_key="ollama"  # Ollama doesn't need real key
)
model = OpenAIChatModel("llama3", provider=provider)
```

---

## 9. Dynamic System Prompts

### Context-Aware Instructions
```python
@agent.instructions
async def add_context(ctx: RunContext[CLIDeps]) -> str:
    parts = [f"Working directory: {ctx.deps.workspace_path}"]

    if ctx.deps.workspace_path.exists():
        files = list(ctx.deps.workspace_path.glob("*"))[:10]
        parts.append(f"Files: {[f.name for f in files]}")

    return "\n".join(parts)
```

---

## 10. Anti-Patterns to Avoid

| Anti-Pattern | Problem | Solution |
|-------------|---------|----------|
| Global `settings` import in tools | Hidden dependency, hard to test | Pass via `ctx.deps` |
| `typer.confirm()` inside tools | Bypasses pydantic-ai flow | Use `requires_approval=True` |
| Return error strings | LLM can't self-correct | Raise `ModelRetry` |
| Mutable default in dataclass | Shared state bugs | Use `field(default_factory=...)` |
| `GeminiModel` | Deprecated | Use `GoogleModel` |
| Creating clients inside tools | Inefficient, hard to test | Create in main, pass via deps |

---

## Quick Reference

### Minimal CLI Agent Structure
```
myapp/
├── __init__.py
├── agent.py      # Agent definition + tool registration
├── deps.py       # CLIDeps dataclass
├── main.py       # CLI entry, creates deps, runs agent
└── tools/        # Tool implementations (plain functions)
    ├── __init__.py
    ├── files.py
    └── shell.py
```

### Deps Checklist
- [ ] Pre-built API clients (not config objects)
- [ ] Resolved paths/tokens
- [ ] Callbacks for CLI interaction (confirm, display)
- [ ] Session/run ID for telemetry
- [ ] No mutable defaults

### Tool Checklist
- [ ] First param is `RunContext[Deps]`
- [ ] Async function
- [ ] Uses `ModelRetry` for recoverable errors
- [ ] Registered with `agent.tool()` in agent.py
- [ ] Dangerous tools have `requires_approval=True`

---

## Sources

- [Pydantic AI Dependencies](https://ai.pydantic.dev/dependencies/)
- [Pydantic AI Deferred Tools](https://ai.pydantic.dev/deferred-tools/)
- pydantic-ai source: `pydantic_ai_slim/pydantic_ai/_run_context.py`
- Examples: `bank_support.py`, `rag.py`, `weather_agent.py`, `flight_booking.py`
- sidekick-cli: `src/sidekick/deps.py`, `src/sidekick/agent.py`
