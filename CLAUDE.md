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

See `docs/DESIGN-co-cli.md` for module descriptions, processing flows, and approval pattern.

## Coding Standards

- **Python 3.12+** with type hints everywhere
- **Imports**: Always explicit — never `from X import *`
- **`__init__.py`**: Prefer empty (docstring-only) — no re-exports unless the module is a public API facade
- **`_prefix.py` helpers**: Internal/shared helpers in a package use leading underscore. Private to the package — not registered as tools, not part of the public API
- **Tool pattern**: New tools must use `agent.tool()` with `RunContext[CoDeps]`, access runtime resources via `ctx.deps`
- **Tool approval**: Side-effectful tools use `requires_approval=True`. Approval UX lives in the chat loop, not inside tools
- **Tool return type**: Tools returning data for the user MUST return `dict[str, Any]` with a `display` field (pre-formatted string with URLs baked in) and metadata fields (e.g. `count`, `next_page_token`). Never return raw `list[dict]`
- **No global state in tools**: Settings are injected through `CoDeps`, not imported directly in tool files
- **Config precedence**: env vars > `.co-cli/settings.json` (project) > `~/.config/co-cli/settings.json` (user) > built-in defaults
- **XDG paths**: Config in `~/.config/co-cli/`, data in `~/.local/share/co-cli/`
- **Versioning**: `MAJOR.MINOR.PATCH` — patch digit: odd = bugfix, even = feature. Bump in `pyproject.toml` only — version is read via `tomllib` from `pyproject.toml` at runtime
- **Status checks**: All environment/health probes live in `co_cli/status.py` (`get_status() → StatusInfo` dataclass). Callers (banner, `co status` command) handle display only
- **Display**: Use `co_cli.display.console` for all terminal output. Use semantic style names — never hardcode color names at callsites. See `docs/DESIGN-theming-ascii.md` for style inventory and theme architecture

## Testing Policy

- **Functional tests only** — no mocks or stubs. Tests hit real services.
- **No skips** — tests must pass or fail, never skip. If a service is required, let the test fail when unavailable.
- **Google tests resolve credentials automatically**: explicit `google_credentials_path` in settings, `~/.config/co-cli/google_token.json`, or ADC at `~/.config/gcloud/application_default_credentials.json`
- Framework: `pytest` + `pytest-asyncio`
- Docker must be running for shell/sandbox tests
- Set `LLM_PROVIDER=gemini` or `LLM_PROVIDER=ollama` env var for LLM E2E tests

## Design Principles

- **Best practice + MVP**: When researching peer systems, focus on best practices (what 2+ top systems converge on), not volume or scale. Design for MVP first — ship the smallest thing that solves the user problem. Use protocols/abstractions so post-MVP enhancements require zero caller changes.

## Anti-Patterns

- Do not use `tool_plain()` for new tools — use `agent.tool()` with `RunContext`
- Do not import `settings` directly in tool files — use `ctx.deps`
- Do not put approval prompts inside tools — use `requires_approval=True` and handle in the chat loop
- Do not use mocks in tests
- Do not use `.env` files — use `settings.json` or env vars

## Docs

### Design (architecture and implementation details, kept in sync with code)
- `docs/DESIGN-co-cli.md` — Overall architecture, processing flows, approval pattern, security model
- `docs/DESIGN-otel-logging.md` — Telemetry architecture, SQLite schema, viewers
- `docs/DESIGN-tool-shell.md` — Shell tool, sandbox backends (Docker primary, subprocess fallback), security model
- `docs/DESIGN-tool-obsidian.md` — Obsidian/notes tool design
- `docs/DESIGN-tool-google.md` — Google tools design (Drive, Gmail, Calendar, lazy auth)
- `docs/DESIGN-tool-slack.md` — Slack tool design
- `docs/DESIGN-llm-models.md` — LLM model configuration
- `docs/DESIGN-tail-viewer.md` — Real-time span tail viewer
- `docs/DESIGN-theming-ascii.md` — Theming, ASCII art banner, display helpers
- `docs/DESIGN-conversation-memory.md` — Context governance (history processors, sliding window, summarisation) and session persistence

### TODO (remaining work items only — no design content, no status tracking)
- `docs/TODO-approval-flow-extraction.md` — Extract approval loop from main.py into orchestration module
- `docs/TODO-subprocess-fallback-policy.md` — Tighten sandbox fallback: fail-fast option, persistent warning
- `docs/TODO-approval-interrupt-tests.md` — Regression tests for approval flow, interrupt patching, safe-command checks
- `docs/TODO-modelretry-normalization.md` — Cross-tool ModelRetry message consistency pass
- `docs/TODO-streaming-tool-output.md` — Migrate chat loop to `run_stream` + `event_stream_handler`
- `docs/TODO-mcp-client.md` — MCP client support (stdio → HTTP → OAuth), pydantic-ai toolsets integration
- `docs/TODO-cross-tool-rag.md` — Cross-tool RAG: SearchDB shared service (FTS5 → hybrid → reranker)
- `docs/TODO-slack-tooling.md` — Slack tool enhancements

### Reviews
- `docs/REVIEW-sidekick-cli-good-and-bad.md` — sidekick-cli patterns analysis (adopted vs pending vs anti-patterns)

### Skills
- `/release <version|feature|bugfix>` — Full release workflow: tests, version bump, changelog, design doc sync, TODO cleanup, commit

## Reference Repos (local, for design research)

Peer CLI tools cloned in `~/workspace_genai/` for studying shell safety, approval flows, sandbox designs, and UX patterns:

| Repo | Language | Key files for shell safety / approval |
|------|----------|--------------------------------------|
| `codex` | Rust | `codex-rs/core/src/command_safety/` — deepest: tokenizes cmds, inspects flags, recursive shell wrapper parsing. Also `codex-rs/linux-sandbox/src/bwrap.rs` — vendored bubblewrap |
| `gemini-cli` | TypeScript | `tools.allowed` prefix matching in settings, tool executor middleware |
| `opencode` | Go | Multi-provider, flexible model switching |
| `claude-code` | TypeScript | `packages/core/src/scheduler/policy.ts` — hook-based permission engine; `packages/cli/src/config/settings.ts` — allow/deny rules (post-CVE-2025-66032); `packages/core/src/utils/sandbox.ts` |
| `aider` | Python | Simplest model — no sandbox, `io.confirm_ask()` for everything; proves you can ship without a sandbox if approval gate is strict |
