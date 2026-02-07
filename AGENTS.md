# Repository Guidelines

## Project Structure & Module Organization
- `co_cli/`: main Python package (`co_cli/main.py` Typer app, `co_cli/agent.py` orchestration, sandbox/telemetry/display helpers).
- `co_cli/tools/`: tool integrations (`shell.py`, `obsidian.py`, `google_drive.py`, `google_gmail.py`, `google_calendar.py`, `slack.py`).
- `tests/`: functional/integration test suite (no mocks; exercises real services and sandbox behavior).
- `docs/`: design/research/todo notes (for example `docs/DESIGN-co-cli.md`, `docs/DESIGN-tail-viewer.md`, `docs/DESIGN-tool-google.md`, `docs/TODO-approval-flow.md`).
- Root docs: `README.md` for usage and setup.
- Config example: `settings.example.json`.
- Documentation location rule: all design, TODO, implementation notes, fix plans, reviews, and other project markdown docs must live under `docs/`. Keep root markdown files only for top-level project docs like `README.md`, `AGENTS.md`, `CHANGELOG.md`, `CLAUDE.md`, and `GEMINI.md`.

## Build, Test, and Development Commands
- `uv sync`: install runtime + dev dependencies.
- `uv run co status`: verify model provider/config health and local setup.
- `uv run co chat`: start the interactive REPL.
- `uv run co tail`: stream spans from local telemetry DB.
- `uv run co traces`: generate/open static HTML trace viewer.
- `uv run co logs`: launch local telemetry viewer (Datasette).
- `uv run pytest`: run functional tests.

## Coding Style & Naming Conventions
- Python 3.12+ with **type hints everywhere** (see `docs/DESIGN-co-cli.md`).
- 4-space indentation, `snake_case` for functions/variables, `PascalCase` for classes.
- Public tools should include short docstrings.
- Configuration should come from `~/.config/co-cli/settings.json` or environment variables (no hardcoded secrets).

## Testing Guidelines
- Frameworks: `pytest` + `pytest-asyncio`.
- Policy: functional/integration tests only; do not use mocks or stubs.
- Test naming: `tests/test_*.py` with descriptive, end-to-end behavior.
- Example: `uv run pytest tests/test_sandbox.py` for container execution checks.

## Commit & Pull Request Guidelines
- Use short, imperative commit subjects, e.g., "Add sandbox cleanup".
- PRs should include a clear description of behavior changes.
- PRs should include test results (`uv run pytest`) or a reason tests weren’t run.
- PRs should document any new config keys in `README.md` and `settings.example.json`.

## Security & Configuration Tips
- Keep secrets in `~/.config/co-cli/settings.json` or environment variables; never commit API keys or tokens.
- Docker must be running for shell tooling.
- For Google tools, Co can auto-bootstrap credentials via `gcloud`/ADC on first `co chat`; manual `google_credentials_path` is optional.
- Set `LLM_PROVIDER` and corresponding credentials (`GEMINI_API_KEY` or local Ollama setup) before LLM-dependent tests.
