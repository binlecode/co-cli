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
- **deps.py**: `CoDeps` dataclass — runtime dependencies injected into tools via `RunContext[CoDeps]` (sandbox, auto_confirm, session_id, obsidian_vault_path, google_drive, google_gmail, google_calendar, slack_client)
- **google_auth.py**: `get_google_credentials()` + `build_google_service()` — loads authorized-user credentials (or ADC fallback) and builds Google API clients
- **config.py**: `Settings` (Pydantic BaseModel) loaded from `~/.config/co-cli/settings.json` with env var fallback. Exported as module-level `settings` singleton
- **sandbox.py**: Docker wrapper — creates a persistent `co-runner` container per session, mounts CWD to `/workspace`
- **telemetry.py**: Custom `SQLiteSpanExporter` writes OTel spans to `~/.local/share/co-cli/co-cli.db`
- **trace_viewer.py**: Generates static HTML with collapsible span trees

### Tools (`co_cli/tools/`)
| File | Functions | Pattern |
|------|-----------|---------|
| `shell.py` | `run_shell_command` | `RunContext[CoDeps]` — uses sandbox, human-in-the-loop confirm |
| `obsidian.py` | `search_notes`, `list_notes`, `read_note` | `RunContext[CoDeps]` — uses `ctx.deps.obsidian_vault_path` |
| `google_drive.py` | `search_drive`, `read_drive_file` | `RunContext[CoDeps]` — uses `ctx.deps.google_drive` + `ModelRetry` |
| `google_gmail.py` | `list_emails`, `search_emails`, `draft_email` | `RunContext[CoDeps]` — uses `ctx.deps.google_gmail` + human-in-the-loop confirm |
| `google_calendar.py` | `list_calendar_events`, `search_calendar_events` | `RunContext[CoDeps]` — uses `ctx.deps.google_calendar` + `ModelRetry` |
| `slack.py` | `post_slack_message` | `RunContext[CoDeps]` — uses `ctx.deps.slack_client` + human-in-the-loop confirm |

### Migration Status
All tools use `agent.tool()` with `RunContext[CoDeps]` pattern. Remaining: migrate approval flow from `Confirm.ask` to `requires_approval=True` — see `docs/TODO-approval-flow.md`.

## Coding Standards

- **Python 3.12+** with type hints everywhere
- **Imports**: Always explicit — never `from X import *`
- **`__init__.py`**: Prefer empty (docstring-only) — no re-exports unless the module is a public API facade
- **`_prefix.py` helpers**: Internal/shared helpers in a package use leading underscore (e.g. `tools/_confirm.py`). These are private to the package — not registered as tools, not part of the public API
- **Tool pattern**: New tools must use `RunContext[CoDeps]`, access runtime resources via `ctx.deps`
- **Tool return type**: Tools that return data for the user to see MUST return structured output (`dict[str, Any]`) with a `display` field (pre-formatted string with URLs baked in) and metadata fields (e.g. `count`, `next_page_token`). This ensures the LLM shows URLs/links instead of reformatting into tables that drop them. Never return raw `list[dict]` — the LLM will reformat and lose data.
- **No global state in tools**: Settings are injected through `CoDeps`, not imported directly in tool files
- **Config precedence**: env vars > `~/.config/co-cli/settings.json` > built-in defaults
- **XDG paths**: Config in `~/.config/co-cli/`, data in `~/.local/share/co-cli/`

## Testing Policy

- **Functional tests only** — no mocks or stubs. Tests hit real services.
- Tests skip gracefully when services are unavailable (Docker, Ollama, Slack)
- **Google tests resolve credentials automatically**: explicit `google_credentials_path` in settings, `~/.config/co-cli/google_token.json`, or ADC at `~/.config/gcloud/application_default_credentials.json` — tests must NOT skip when any of these exist
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
- `docs/DESIGN-tool-google.md` — Google tools design (Drive, Gmail, Calendar, auth factory)
- `docs/DESIGN-tool-slack.md` — Slack tool design
- `docs/DESIGN-llm-models.md` — LLM model configuration (Ollama GLM-4.7-Flash parameters, Gemini)
- `docs/DESIGN-tail-viewer.md` — Real-time span tail viewer, span attribute reference, troubleshooting guide
- `docs/TODO-approval-flow.md` — Migrate to pydantic-ai `requires_approval` + `DeferredToolRequests`
- `docs/TODO-streaming-tool-output.md` — Migrate chat loop to `run_stream` + `event_stream_handler` for direct tool output display
- `docs/TODO-retry-design.md` — ModelRetry semantics: when to retry vs return empty, industry best practices
- `docs/TODO-theming-ascii.md` — Theming (light/dark), ASCII art banner, display helpers, color semantics

## Reference Implementation

**BERA CLI** (`/Users/binle/workspace_bera/a-cli`) — mini-CLI with polished terminal UX. Use as reference for:

- **Theming**: Dual light/dark theme with Unicode indicator variants (`cli/config.py` `THEME_INDICATORS`, `cli/display.py` `set_theme()`)
- **ASCII art banner**: Theme-aware welcome art — block chars for dark, box-drawing for light (`cli/qa_chat.py` `_display_welcome_banner()`)
- **Display utilities**: `display_status()`, `display_error()` with recovery hints, `display_info()`, `status_spinner()` context manager (`cli/display.py`)
- **Color semantics**: cyan=selection/commands, yellow=status, red=errors, green=success, dim=secondary
- **TTY detection**: Rich auto-detection with graceful degradation for piped/CI output
- **Rich Panels**: Bordered panels for help, errors, session status with `border_style="cyan"`
