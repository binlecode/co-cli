# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
uv sync                          # Install all dependencies (runtime + dev)
uv run co chat                   # Interactive REPL
uv run co status                 # System health check
uv run co logs                   # Datasette trace viewer (table)
uv run co traces                 # Nested HTML trace viewer

uv run pytest                    # Run all functional tests
uv run pytest -v                 # Verbose output
uv run pytest tests/test_tools.py            # Single test file
uv run pytest tests/test_tools.py::test_name # Single test function
uv run pytest --cov=co_cli                   # With coverage
```

## Architecture

```
User ──▶ Typer CLI (main.py) ──▶ Agent (pydantic-ai) ──▶ Tools (RunContext[CoDeps])
              │                        │
              │                   instrument_all()
              ▼                        │
         prompt-toolkit           SQLiteSpanExporter ──▶ co-cli.db
         + rich console
```

### Core Flow
- **main.py**: CLI entry point (Typer app), sets up OpenTelemetry + TracerProvider, calls `Agent.instrument_all()`, runs async chat loop
- **agent.py**: `get_agent()` factory creates a `pydantic-ai` Agent with model selection (Gemini or Ollama) and registers all tools
- **deps.py**: `CoDeps` dataclass — runtime dependencies injected into tools via `RunContext[CoDeps]` (sandbox, auto_confirm, session_id, obsidian_vault_path)
- **config.py**: `Settings` (Pydantic BaseModel) loaded from `~/.config/co-cli/settings.json` with env var fallback. Exported as module-level `settings` singleton
- **sandbox.py**: Docker wrapper — creates a persistent `co-runner` container per session, mounts CWD to `/workspace`
- **telemetry.py**: Custom `SQLiteSpanExporter` writes OTel spans to `~/.local/share/co-cli/co-cli.db`
- **trace_viewer.py**: Generates static HTML with collapsible span trees

### Tools (`co_cli/tools/`)
| File | Functions | Pattern |
|------|-----------|---------|
| `shell.py` | `run_shell_command` | `RunContext[CoDeps]` — uses sandbox, human-in-the-loop confirm |
| `obsidian.py` | `search_notes`, `list_notes`, `read_note` | `RunContext[CoDeps]` — uses `ctx.deps.obsidian_vault_path` |
| `drive.py` | `search_drive`, `read_drive_file` | `tool_plain` (migration pending to RunContext) |
| `comm.py` | `post_slack_message`, `draft_email`, `list_calendar_events` | `tool_plain` (migration pending to RunContext) |

### Migration Status
Tools in `drive.py` and `comm.py` still use `agent.tool_plain()`. New tools must use `agent.tool()` with `RunContext[CoDeps]` pattern. See `docs/TODO-pydantic-ai-best-practices.md` for the migration roadmap.

## Coding Standards

- **Python 3.12+** with type hints everywhere
- **Imports**: Always explicit — never `from X import *`
- **Tool pattern**: New tools must use `RunContext[CoDeps]`, access runtime resources via `ctx.deps`
- **No global state in tools**: Settings are injected through `CoDeps`, not imported directly in tool files
- **Config precedence**: env vars > `~/.config/co-cli/settings.json` > built-in defaults
- **XDG paths**: Config in `~/.config/co-cli/`, data in `~/.local/share/co-cli/`

## Testing Policy

- **Functional tests only** — no mocks or stubs. Tests hit real services.
- Tests skip gracefully when services are unavailable (Docker, Ollama, GCP, Slack)
- Framework: `pytest` + `pytest-asyncio`
- Docker must be running for shell/sandbox tests
- Set `LLM_PROVIDER=gemini` or `LLM_PROVIDER=ollama` env var for LLM E2E tests

## Anti-Patterns

- Do not use `tool_plain()` for new tools — use `agent.tool()` with `RunContext`
- Do not import `settings` directly in tool files — use `ctx.deps`
- Do not use mocks in tests
- Do not use `.env` files — use `settings.json` or env vars

## Design Docs

- `docs/DESIGN-co-cli.md` — Overall architecture and design decisions
- `docs/DESIGN-otel-logging.md` — Telemetry architecture, SQLite schema, viewers
- `docs/DESIGN-tool-shell-sandbox.md` — Docker sandbox design
- `docs/DESIGN-tool-obsidian.md` — Obsidian/notes tool design
- `docs/TODO-pydantic-ai-best-practices.md` — RunContext migration roadmap
- `docs/FIX-gemini-summary-on-shell.md` — Known issue: Gemini summarizes tool output instead of showing it directly
