# Repository Guidelines

## Project Structure & Module Organization
- `co_cli/`: main Python package (`co_cli/main.py` Typer app, `co_cli/agent.py` agent factory/orchestration, sandbox/telemetry/display helpers).
- `co_cli/tools/`: tool integrations (`shell.py`, `obsidian.py`, `google_drive.py`, `google_gmail.py`, `google_calendar.py`, `slack.py`, `web.py`) plus shared tool errors in `_errors.py`.
- `tests/`: functional/integration tests only (no mocks/stubs).
- `docs/`: design/research/todo/review docs.
- Root docs: `README.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CHANGELOG.md`.
- Config reference example: `settings.reference.json`.
- Documentation location rule: all design, TODO, implementation notes, fix plans, reviews, and other project markdown docs live under `docs/`.

## Build, Test, and Development Commands
- `uv sync`: install runtime + dev dependencies.
- `uv run co status`: verify model/config/tool health.
- `uv run co chat`: start interactive REPL.
- `uv run co tail`: stream telemetry spans.
- `uv run co traces`: generate/open static HTML trace viewer.
- `uv run co logs`: launch local telemetry viewer (Datasette).
- `uv run pytest`: run full functional suite.
- `uv run pytest tests/test_web.py`: run web tool tests.

## Coding Style & Naming Conventions
- Python 3.12+ with type hints everywhere.
- 4-space indentation, `snake_case` for functions/variables, `PascalCase` for classes.
- Imports must be explicit; never `from X import *`.
- Public tools should include short docstrings.
- Tool pattern: use `agent.tool()` with `RunContext[CoDeps]` and `ctx.deps` resources.
- Side-effect tools must use `requires_approval=True`; approval UX stays in chat loop.
- Tool outputs for user-facing data should return `dict[str, Any]` with `display` + metadata.
- Do not import global settings inside tool modules; inject via `CoDeps`.

## Testing Guidelines
- Frameworks: `pytest` + `pytest-asyncio`.
- Policy: functional/integration tests only; no mocks/stubs.
- API-dependent tests (e.g., Brave/Slack) may use `pytest.mark.skipif` when credentials are missing.
- Docker must be running for sandbox shell tests.
- LLM E2E tests require provider configuration (`LLM_PROVIDER`, model credentials).

## Design Principles
- Best practice + MVP: prioritize patterns that converge across top systems, then ship the smallest change that solves the immediate user problem.
- Keep interfaces stable so post-MVP hardening can be added with minimal caller changes.

## Security & Configuration Tips
- Keep secrets in `~/.config/co-cli/settings.json` or environment variables; never commit keys/tokens.
- XDG paths: config in `~/.config/co-cli/`, data in `~/.local/share/co-cli/`.
- Docker is preferred for shell isolation; fallback mode should be explicit to users.
- For Google tools, ADC bootstrap via `gcloud` is supported; `google_credentials_path` is optional.
