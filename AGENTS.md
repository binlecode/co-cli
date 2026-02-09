# Repository Guidelines

## Project Structure

```
co_cli/                     # Main Python package
├── main.py                 # Typer CLI entry point
├── agent.py                # Pydantic AI agent factory
├── deps.py                 # CoDeps dataclass for tool injection
├── config.py               # Settings management (XDG-compliant)
├── sandbox.py              # Docker/subprocess sandbox backends
├── telemetry.py            # OpenTelemetry SQLite exporter
├── display.py              # Rich console theming
├── status.py               # Health checks
├── tools/                  # Agent tools
│   ├── shell.py            # Docker-sandboxed shell commands
│   ├── obsidian.py         # Notes tool
│   ├── google_drive.py     # Google Drive integration
│   ├── google_gmail.py     # Gmail integration
│   ├── google_calendar.py  # Calendar integration
│   ├── slack.py            # Slack messaging
│   ├── web.py              # Web search/fetch
│   └── _errors.py          # Shared error handling
└── _*.py                   # Internal helpers (private)

tests/                      # Functional tests (no mocks)
docs/                       # DESIGN docs, TODOs, reviews
```

## Build, Test & Development Commands

```bash
# Dependencies
uv sync                              # Install runtime + dev dependencies

# Running the CLI
uv run co chat                       # Start interactive REPL
uv run co status                     # System health check
uv run co tail                       # Stream telemetry spans
uv run co traces                     # Generate HTML trace viewer
uv run co logs                       # Launch Datasette trace viewer

# Testing (functional/integration only — no mocks)
uv run pytest                        # Run all tests
uv run pytest -v                     # Verbose output
uv run pytest tests/test_web.py      # Single test file
uv run pytest tests/test_web.py::test_web_search_empty_query  # Single test
uv run pytest --cov=co_cli           # With coverage

# Test requirements
# - Docker must be running for shell/sandbox tests
# - Set LLM_PROVIDER=gemini/ollama for LLM E2E tests
# - API tests (Brave, Slack) skip when credentials missing
```

## Code Style Guidelines

### Python Standards
- **Python 3.12+** with type hints everywhere
- **4-space indentation**
- **snake_case** for functions/variables, **PascalCase** for classes
- **Max line length**: 100 characters (follow existing patterns)

### Imports
- Always explicit — **never `from X import *`**
- Group imports: stdlib → third-party → local
- Use absolute imports within package
- `__init__.py` should be empty (docstring-only) unless it's a public API facade

### Naming Conventions
- Private helpers: `_prefix.py` files (internal to package, not tools)
- Tool functions: descriptive verbs (`run_shell_command`, `search_notes`)
- Error classes: end with `Error` suffix
- Constants: UPPER_SNAKE_CASE

### Type Hints
- Required on all function parameters and return types
- Use `|` union syntax (e.g., `str | None`)
- Use `from __future__ import annotations` for forward references
- Return `dict[str, Any]` from tools with user-facing data

### Docstrings
- Public tools: short docstring describing purpose and args
- Format: Google-style or plain description
- First line should be imperative mood ("Execute...", "Search...")

## Tool Development Patterns

### Tool Registration
```python
from pydantic_ai import RunContext, ModelRetry
from co_cli.deps import CoDeps

@agent.tool()
async def my_tool(ctx: RunContext[CoDeps], arg: str) -> dict[str, Any]:
    """Short description of what this tool does."""
    # Access resources via ctx.deps, never import settings directly
    result = await ctx.deps.something.run(arg)
    return {"display": formatted_string, "count": len(result)}
```

### Key Rules
- **Always use `agent.tool()`** with `RunContext[CoDeps]` — never `tool_plain()`
- Access runtime resources via `ctx.deps`, never import `settings` directly in tools
- Side-effect tools must use `requires_approval=True`
- Approval UX lives in chat loop, not inside tools
- Tool returns must be `dict[str, Any]` with `display` field (pre-formatted for user)
- Never return raw `list[dict]` — always wrap with metadata

## Error Handling

### Error Patterns
```python
from pydantic_ai import ModelRetry
from co_cli.tools._errors import ToolErrorKind, handle_tool_error

# Transient errors (retryable): rate limits, 5xx, network
raise ModelRetry("Tool: brief problem. Action hint.")

# Terminal errors (non-retryable): auth failures, not configured
return terminal_error("Tool: configuration missing. Run: command to fix.")

# Misuse errors: bad IDs, invalid args
raise ModelRetry("Tool: invalid argument. Verify and try again.")
```

### Error Message Format
`"{Tool}: {problem}. {Action hint}."`

## Testing Guidelines

### Policy
- **Functional/integration tests only** — no mocks or stubs
- Tests must pass or fail, never skip (except API credential skips)
- Docker must be running for shell/sandbox tests

### Test Structure
```python
import pytest
from co_cli.deps import CoDeps

@pytest.mark.asyncio
async def test_feature():
    deps = CoDeps(sandbox=...)
    # Test against real services
```

### Skips
Only use `pytest.mark.skipif` for API-dependent tests when credentials missing:
```python
_skip_no_key = pytest.mark.skipif(
    not settings.brave_search_api_key,
    reason="BRAVE_SEARCH_API_KEY not configured"
)
```

## Configuration & Security

### XDG Paths
- Config: `~/.config/co-cli/settings.json`
- Data: `~/.local/share/co-cli/`
- Never commit secrets — use env vars or settings.json

### Config Precedence
env vars > `.co-cli/settings.json` (project) > `~/.config/co-cli/settings.json` (user) > defaults

### Security
- Shell commands run in Docker sandbox (preferred) or subprocess fallback
- Side-effect tools require approval via `requires_approval=True`
- Never log or expose secrets

## Design Principles

1. **Best practice + MVP**: Prioritize convergent patterns from top systems, ship smallest useful change
2. **Stable interfaces**: Use protocols/abstractions so enhancements don't break callers
3. **Privacy first**: Local LLM preferred, local logs only
4. **Human-in-the-loop**: Confirmations for high-risk operations
5. **No global state**: Inject all dependencies via `CoDeps`

## Anti-Patterns

- Do not use `tool_plain()` — use `agent.tool()` with `RunContext`
- Do not import `settings` directly in tool files — use `ctx.deps`
- Do not put approval prompts inside tools — use `requires_approval=True`
- Do not use mocks in tests
- Do not use `.env` files — use `settings.json` or env vars
- Do not hardcode colors — use semantic styles from `display.py`

## Documentation

- All design docs live in `docs/` (not root)
- DESIGN docs stay in sync with code — no version stamps
- No code paste in DESIGN docs — use pseudocode only
- TODO docs contain work items only (no design content)
