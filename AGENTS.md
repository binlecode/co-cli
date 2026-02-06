# Repository Guidelines

## Project Structure & Module Organization
- `co_cli/`: main Python package (CLI entry in `co_cli/main.py`, agent logic in `co_cli/agent.py`, sandbox/telemetry helpers).
- `co_cli/tools/`: tool integrations (`shell.py`, `obsidian.py`, `google_drive.py`, `google_gmail.py`, `google_calendar.py`, `slack.py`).
- `tests/`: functional test suite (real services, no mocks).
- `docs/`: design and process docs (`docs/DESIGN-co-cli.md`, `docs/DESIGN-otel-logging.md`, `docs/TODO-approval-flow.md`).
- Root docs: `README.md` for usage and setup.
- Config example: `settings.example.json`.

## Build, Test, and Development Commands
- `uv sync`: install runtime + dev dependencies.
- `uv run python -m co_cli.main status`: verify Ollama/Docker/config health.
- `uv run python -m co_cli.main chat`: start the interactive REPL.
- `uv run python -m co_cli.main logs`: launch local telemetry viewer (Datasette).
- `uv run pytest`: run functional tests.

## Coding Style & Naming Conventions
- Python 3.12+ with **type hints everywhere** (see `docs/DESIGN-co-cli.md`).
- 4-space indentation, `snake_case` for functions/variables, `PascalCase` for classes.
- Public tools should include short docstrings.
- Configuration must come from `~/.config/co-cli/settings.json` or environment variables (no hardcoded secrets).

## Testing Guidelines
- Frameworks: `pytest` + `pytest-asyncio`.
- Policy: functional/integration tests only; do not use mocks or stubs.
- Test naming: `tests/test_*.py` with descriptive, end-to-end behavior.
- Example: `uv run pytest tests/test_sandbox.py` for container execution checks.

## Commit & Pull Request Guidelines
- No established commit convention yet (repo has no commits). Prefer short, imperative subjects, e.g., "Add sandbox cleanup".
- PRs should include a clear description of behavior changes.
- PRs should include test results (`uv run pytest`) or a reason tests weren’t run.
- PRs should document any new config keys in `README.md` and `settings.example.json`.

## Security & Configuration Tips
- Keep secrets in `~/.config/co-cli/settings.json` or environment variables; never commit API keys or tokens.
- Docker must be running for shell tooling; Ollama/Gemini credentials must be set before running tests.
