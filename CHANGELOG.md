# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.5] - 2026-02-06

### Added
- **`co tail` command**: Real-time span viewer (`co_cli/tail.py`, 260 lines) — tail agent spans live from a second terminal with `--tools-only`, `-v` verbose, and `-n`/`-l` non-follow modes.
- **`docs/DESIGN-tail-viewer.md`**: Tail viewer design doc with span attribute reference and troubleshooting guide.
- **`docs/TODO-tool-call-stability.md`**: Comprehensive stability doc — ModelRetry design principle, retry budget, shell error propagation, system prompt, Obsidian display migration, loop guard, sandbox hardening.

### Changed
- **Inference params**: GLM-4.7-Flash switched from general conversation profile (`temp=1.0, top_p=0.95`) to Terminal/SWE-Bench Verified profile (`temp=0.7, top_p=1.0`) for better tool-call accuracy.
- **Agent retry budget**: `retries=settings.tool_retries` set at agent level (was default 1).
- **`README.md`**: Expanded usage section with `co` command explanation, added `co tail`/`co traces` docs.
- **`AGENTS.md`**: Updated commands, testing guidance, and security tips to reflect current state.
- **`docs/TODO-approval-flow.md`**: Expanded with post-session-yolo context.
- **`docs/TODO-streaming-tool-output.md`**: Expanded with event_stream_handler design.
- **`docs/DESIGN-co-cli.md`**: Updated architecture with display/banner/tail modules.

### Removed
- **`docs/TODO-retry-design.md`**: Merged into `TODO-tool-call-stability.md`.
- **`docs/TODO-session-yolo.md`**: Superseded by implemented session-yolo in v0.2.0.

---

## [0.2.4] - 2026-02-06

### Added
- **Theming**: Light/dark color themes with `--theme`/`-t` flag, `CO_CLI_THEME` env var, and `theme` setting in `settings.json`. Light theme uses blue accents and dark orange status; dark theme uses cyan accents and yellow status.
- **ASCII art banner**: Theme-aware welcome banner — block characters (`█▀▀`) for dark, box-drawing characters (`┌─┐`) for light — rendered as a Rich Panel with model info and version.
- **`co_cli/display.py`**: Shared `Console` instance, `_COLORS` theme dict, `_c()` color resolver, Unicode indicators (`❯ ▸ ✦ ✖ ◈`), and display helpers (`display_status`, `display_error`, `display_info`).
- **`co_cli/banner.py`**: `display_welcome_banner()` with per-theme ASCII art selection.
- **`docs/DESIGN-theming-ascii.md`**: Comprehensive design doc covering architecture, color semantics, module layout, and a-cli reference.

### Changed
- **`main.py`**: Uses shared `console` from `display.py` (was local `Console()`), themed welcome banner (was two inline `console.print` lines), `Co ❯` prompt (was `Co > `).
- **`tools/_confirm.py`**: Uses shared `console` from `display.py` (was private `_console = Console()`).
- **`config.py`**: Added `theme` field (default: `"light"`) with `CO_CLI_THEME` env var mapping.
- **`CLAUDE.md`**: Added `display.py`/`banner.py` to Core Flow, color semantics to Coding Standards, updated design doc list.

---

## [0.2.2] - 2026-02-06

### Fixed
- **search_drive empty-result crash**: `search_drive` no longer raises `ModelRetry` on zero results — returns `{"count": 0}` instead. Previously, two empty searches could exhaust the retry budget and crash the agent with `UnexpectedModelBehavior`.
- **Google test skip bug**: `HAS_GCP` now checks all three credential sources (explicit path, `google_token.json`, ADC) instead of only `settings.google_credentials_path`. Google tests no longer skip when credentials exist.
- **Stale test assertions**: Removed `try/except ModelRetry` workarounds in Drive tests that masked the old empty-result behavior.

### Added
- **`test_drive_search_empty_result`**: Functional test hitting real Drive API with a nonsense query, asserting `count=0` dict return (no exception).
- **`docs/TODO-retry-design.md`**: Design doc covering ModelRetry semantics (retry vs return empty), industry best practices across pydantic-ai/Anthropic/OpenAI/LangGraph, and full tool audit.

### Removed
- **`tests/test_agent.py`**: Unit tests checking model types and settings values with monkeypatch — replaced by LLM E2E tests.
- **`tests/test_batch1_integration.py`**: Unit tests checking CoDeps construction with monkeypatch.

### Changed
- **`docs/DESIGN-llm-models.md`**: Updated Testing and Files sections to reference `test_llm_e2e.py` instead of deleted `test_agent.py`.
- **`CLAUDE.md`**: Added credential resolution note to Testing Policy; added `TODO-retry-design.md` to Design Docs list.

---

## [0.2.0] - 2026-02-05

### Added
- **Gmail inbox tools**: `list_emails` and `search_emails` for reading and searching Gmail (Gmail was previously write-only via `draft_email`).
- **Calendar search**: `search_calendar_events` tool with keyword search, configurable date range, and max results.
- **Google auth auto-setup**: `ensure_google_credentials()` in `google_auth.py` — automatically runs `gcloud auth application-default login` on first use if no token exists.
- **Design docs**: `DESIGN-tool-google.md` (Google tools architecture + setup guide) and `DESIGN-tool-slack.md`.
- **Research doc**: `RESEARCH-cli-agent-tools-landscape-2026.md` — 10-agent competitive analysis, tool roadmap (Batches 7-12), and agentic patterns survey.

### Changed
- **RunContext migration (Batch 3-4)**: All Google and Slack tools migrated from `tool_plain()` to `agent.tool()` with `RunContext[CoDeps]` pattern.
- **File layout**: Extracted `comm.py` junk drawer into separate `google_drive.py`, `google_gmail.py`, `google_calendar.py`, `slack.py` modules.
- **Calendar tool refactored**: Extracted `_get_calendar_service`, `_format_events`, `_handle_calendar_error` helpers for reuse across `list_calendar_events` and `search_calendar_events`.
- **Google auth centralized**: Single `google_auth.py` module with shared credentials and service builder (was duplicated across tool files).
- **CoDeps expanded**: Added `google_drive`, `google_gmail`, `google_calendar`, `slack_client` fields — all API clients built once at startup via `create_deps()`.

### Fixed
- **API-not-enabled errors**: All Google tools now detect "API not enabled" (`accessNotConfigured`) errors and return actionable `ModelRetry` messages with the exact `gcloud services enable` command for each API.
- **Google setup docs**: Added step-by-step guide covering token acquisition, GCP project discovery, API enablement, and troubleshooting table with 7 common failure scenarios.

---

## [0.1.0] - 2026-02-03

### Added
- **Core CLI**: Interactive chat loop using `typer`, `rich`, and `prompt_toolkit`.
- **Intelligence**: Dual-engine LLM support using `pydantic-ai`.
    - **Local**: Ollama (Llama 3 default) for privacy-first operations.
    - **Cloud**: Google Gemini (via `google-genai`) for complex reasoning.
- **Configuration**:
    - Centralized `settings.json` following XDG standards (`~/.config/co-cli/`).
    - Robust fallback to environment variables (`.env`) for backward compatibility.
- **Sandboxing**: Docker-based execution environment for safe shell command running (`python:3.12-slim`).
- **Tools & Skills**:
    - **Obsidian**: RAG over local Markdown notes (`list_notes`, `read_note`).
    - **Google Drive**: Hybrid semantic/metadata search and file reading.
    - **Communication**: Slack message sending and Gmail drafting (with human-in-the-loop confirmation).
    - **Calendar**: Listing today's events.
- **Observability**:
    - Full OpenTelemetry tracing stored in a local SQLite database (`~/.local/share/co-cli/co-cli.db`).
    - `co logs` command to launch a local Datasette dashboard for trace inspection.
- **System Health**: `co status` command to verify tool connections and configuration.

### Security
- **Privacy**: Local-first design; logs and vector search (if added later) stay on-device.
- **Safety**: High-risk actions (sending emails, posting to Slack, shell commands) require explicit user confirmation.
