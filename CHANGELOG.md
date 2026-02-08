# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] - 2026-02-07

### Added
- **Streaming output**: Replaced `agent.run()` + post-hoc `_display_tool_outputs()` with `agent.run_stream_events()` in new `_stream_agent_run()` helper. Tool calls/results display in real time, text streams token-by-token with `rich.Live` + `rich.Markdown` at 20 FPS throttle. Both the main chat loop and `_handle_approvals` resume path use the same streaming codepath.
- **E2E streaming tests**: `scripts/e2e_streaming.py` — two tests (plain text streaming, Markdown rendering via Live) that exercise the full streaming pipeline against a real LLM.
- **`docs/DESIGN-streaming-output.md`**: Streaming design doc — pydantic-ai API comparison (4 APIs evaluated), decision rationale for `run_stream_events()`, peer CLI analysis (Aider, Codex, Gemini CLI, OpenCode), Markdown rendering approach.
- **`docs/TODO-tool-naming.md`**: Tool naming standardisation TODO.

### Fixed
- **`usage_limits` hardcoded in `_stream_agent_run`**: Was reading `settings.max_request_limit` directly. Now accepts `usage_limits` as a parameter; `_handle_approvals` also threads it through. The `settings` read happens only in `chat_loop`.

### Changed
- **Tool naming standardised**: `search_drive` → `search_drive_files`, `draft_email` → `create_email_draft`, `post_slack_message` → `send_slack_message`, `get_slack_channel_history` → `list_slack_messages`, `get_slack_thread_replies` → `list_slack_replies`. Converged on `verb_noun` pattern. Updated agent registration, tests, and docstrings.
- **`_display_tool_outputs` removed**: Superseded by inline display in `_stream_agent_run` — tool output now appears in real time during streaming instead of post-hoc.
- **`_handle_approvals` resumes via streaming**: Was calling `agent.run()` (non-streaming). Now calls `_stream_agent_run()` so post-approval tool results and LLM follow-up also stream.
- **`docs/TODO-approval-flow-extraction.md`**: Updated line references, coupling table, and added Issues section reflecting streaming design tensions (`DisplayCallback` protocol needed for extraction).
- **`docs/DESIGN-co-cli.md`**: Updated for streaming architecture and tool renames.
- **`CLAUDE.md`**: Updated docs inventory — `TODO-streaming-tool-output.md` → `DESIGN-streaming-output.md`, added `TODO-tool-naming.md`.

### Removed
- **`docs/TODO-streaming-tool-output.md`**: Replaced by `docs/DESIGN-streaming-output.md` (broader scope — covers full streaming architecture, not just tool output).

---

## [0.3.0] - 2026-02-07

### Added
- **Automatic context governance**: Two `history_processors` registered on the agent (`co_cli/_history.py`): `trim_old_tool_output` (sync, truncates large `ToolReturnPart.content` in older messages) and `sliding_window` (async, drops middle messages and replaces with LLM summary). Prevents silent context overflow without manual intervention.
- **`summarize_messages()` shared utility**: Disposable `Agent(model, output_type=str)` with zero tools — used by both `sliding_window` (automatic) and `/compact` (user-initiated). Configurable summarisation model via `summarization_model` setting.
- **3 new config fields**: `tool_output_trim_chars` (default 2000), `max_history_messages` (default 40), `summarization_model` (default `""` = primary model). Env vars: `CO_CLI_TOOL_OUTPUT_TRIM_CHARS`, `CO_CLI_MAX_HISTORY_MESSAGES`, `CO_CLI_SUMMARIZATION_MODEL`.
- **`docs/DESIGN-conversation-memory.md`**: Full design doc — peer landscape (Aider, Codex, Claude Code, Gemini CLI), gap analysis table, processor architecture, summarisation agent details (prompts, callsites, error handling), configuration reference with model resolution and disable semantics, session persistence roadmap.
- **18 functional tests** (`tests/test_history.py`): 13 pure tests (trim processor edge cases, static marker, find_first_run_end) + 5 LLM tests (summarise, sliding window compaction, structural validity, /compact end-to-end). No mocks — real `RunContext`, real `SubprocessBackend`, real LLM calls.

### Changed
- **`/compact` refactored**: Now calls `summarize_messages()` with primary model and builds a minimal 2-message history (summary `ModelRequest` + ack `ModelResponse`). Previously used `agent.run()` which could trigger tools and returned full history with summary appended.
- **`docs/DESIGN-co-cli.md`**: Updated §7.4 (history processors architecture), Settings class diagram, env var table, and module summary with `_history.py`.
- **`CLAUDE.md`**: Reorganised docs inventory — `DESIGN-conversation-memory.md` in Design section, new TODO entries, removed completed review.
- **`docs/REVIEW-sidekick-cli-good-and-bad.md`**: Rewritten to reflect current co-cli state — pattern-by-pattern adopted/partial/pending status instead of aspirational recommendations.

### Removed
- **`docs/TODO-conversation-memory.md`**: Replaced by `docs/DESIGN-conversation-memory.md`.
- **`docs/PYDANTIC-AI-CLI-BEST-PRACTICES.md`**: Content consolidated elsewhere.
- **`docs/REVIEW-co-cli-design-team-view.md`**: All P0/P1/P2 findings resolved; remaining items tracked in dedicated docs.

---

## [0.2.16] - 2026-02-07

### Added
- **No-sandbox subprocess fallback**: Shell tool no longer hard-requires Docker. New `SubprocessBackend` runs commands via `asyncio.create_subprocess_exec` with sanitized environment when Docker is unavailable. Automatic fallback with `sandbox_backend=auto` (default), explicit selection via `sandbox_backend` setting or `CO_CLI_SANDBOX_BACKEND` env var.
- **`SandboxProtocol` abstraction** (`co_cli/sandbox.py`): Runtime-checkable protocol with `isolation_level`, `run_command()`, `cleanup()`. `DockerSandbox` (full isolation) and `SubprocessBackend` (no isolation) both satisfy it. Zero caller changes — `tools/shell.py` and `agent.py` untouched.
- **Environment sanitization** (`co_cli/_sandbox_env.py`): `restricted_env()` allowlist (10 safe vars only) blocks CVE-2025-66032 pager/editor hijacking vectors. Forces `PAGER=cat`, `GIT_PAGER=cat`. `kill_process_tree()` sends SIGTERM→200ms→SIGKILL via `os.killpg()` process group.
- **Partial output on subprocess timeout**: After killing a timed-out subprocess, reads any buffered stdout before raising `RuntimeError` — matches Docker backend's behavior, gives the LLM context for self-correction.
- **Safe-command guard on isolation level**: `_is_safe_command()` auto-approval disabled when `isolation_level == "none"` — approval becomes the security layer without a sandbox.
- **19 new functional tests**: Subprocess backend execution, timeout, exit code, pipe, env sanitization, dangerous env blocking, stderr merge, workspace dir, isolation level, protocol conformance, config field, env var override, factory function, cleanup no-op, variable expansion, custom workspace dir.

### Changed
- **`co_cli/sandbox.py`**: Renamed `Sandbox` → `DockerSandbox`. All `import docker` moved inside class methods (lazy import — module loads without `docker` package). Backward-compatible `Sandbox = DockerSandbox` alias preserved.
- **`co_cli/deps.py`**: `sandbox: Sandbox` → `sandbox: SandboxProtocol`.
- **`co_cli/main.py`**: New `_create_sandbox()` factory with auto-detection (try Docker ping, fall back to subprocess with warning). `_handle_approvals()` checks `deps.sandbox.isolation_level != "none"` before auto-approving safe commands.
- **`co_cli/status.py`**: `StatusInfo.docker` field → `sandbox` field. `get_status()` reports active backend: `"Docker (full isolation)"` / `"subprocess (no isolation)"` / `"unavailable"`. `render_status_table()` shows "Sandbox" row with status and backend detail.
- **`co_cli/banner.py`**: `info.docker` → `info.sandbox`.
- **`co_cli/config.py`**: Added `sandbox_backend: Literal["auto", "docker", "subprocess"]` with `CO_CLI_SANDBOX_BACKEND` env var.
- **`docs/DESIGN-tool-shell.md`**: Status updated to reflect MVP complete. Future enhancements table: subprocess fallback, env sanitization, process group kill all marked Done. Integration section updated with `_create_sandbox()` factory.
- **`docs/DESIGN-co-cli.md`**: Added `sandbox_backend` to Settings class diagram and env var mapping table. Updated `CoDeps.sandbox` type to `SandboxProtocol`.
- **`docs/DESIGN-llm-models.md`**: "Docker sandbox" → "sandboxed environment" in profile description.
- **`docs/DESIGN-tool-google.md`**: `Sandbox.ensure_container()` → `DockerSandbox.ensure_container()` in analogy references.
- **`docs/DESIGN-tool-slack.md`**: `deps.py` import reference updated from `Sandbox` to `SandboxProtocol`.
- **`docs/TODO-shell-safety.md`**: Removed — all MVP items complete, only aspirational post-MVP enhancements remained.
- **`settings.example.json`** → **`settings.reference.json`**: Renamed to reflect its role as a user-facing schema reference with default values. Not loaded by code — copy to `~/.config/co-cli/settings.json` and customize.

---

## [0.2.14] - 2026-02-07

### Added
- **Shell safe-command whitelist**: `co_cli/_approval.py` with `_is_safe_command()` — auto-approves read-only shell commands (e.g. `ls`, `cat`, `git status`) matching `shell_safe_commands` prefixes without shell operators (`;`, `&`, `|`, `>`, `<`, `` ` ``, `$(`). UX convenience on top of Docker sandbox isolation.
- **`shell_safe_commands` setting**: New `config.py` field with 30 conservative defaults, `CO_CLI_SHELL_SAFE_COMMANDS` env var (comma-separated), and `CoDeps` field for injection into the approval flow.
- **`render_status_table()`**: Extracted status table rendering from `main.py` and `_commands.py` into `status.py`. Uses semantic style names (`accent`, `info`, `success`) instead of hardcoded colors.
- **`set_theme()`**: Runtime theme switching in `display.py` (called from `--theme` flag). Expanded theme palettes with `error`, `success`, `warning`, `hint` semantic styles.
- **`get_agent()` returns `tool_names`**: 3-tuple return `(agent, model_settings, tool_names)` — eliminates private `_function_toolset` access throughout codebase.
- **Approval flow tests**: 10 new tests in `tests/test_commands.py` — `_is_safe_command` unit tests covering prefix matching, multi-word prefixes, chaining/redirection/backgrounding rejection, exact match, partial-name rejection, empty safe list.
- **Shell hardening tests**: 20+ new functional tests in `tests/test_shell.py` — timeout, pipe, non-root, network isolation, capability drop, redirect, variable expansion, subshell, heredoc, stderr merge, Python script lifecycle, special chars, large output, empty output, workspace mapping.
- **`/release` skill**: `.claude/skills/release/release.md` — versioned release workflow invokable as `/release <version|feature|bugfix>`.

### Changed
- **`_commands.py`**: `CommandContext.tool_count` → `tool_names: list[str]`. `/status` and `/tools` use `render_status_table()` and sorted `tool_names` respectively.
- **`_approval.py`**: Hardened rejection list — added `&` (backgrounding), `>` / `<` (redirection), `\n` (embedded newlines) alongside original chaining operators.
- **`main.py`**: Adapted to 3-tuple `get_agent()`, uses `set_theme()` for `--theme` flag, uses `render_status_table()` for `co status`.
- **`docs/DESIGN-co-cli.md`**: Updated sandbox diagram, `CoDeps` class diagram, config table, dependency flow.
- **`docs/DESIGN-tool-google.md`**: Rewritten auth architecture — lazy credential resolution via `get_cached_google_creds()`.
- **`CLAUDE.md`**: Added design principles section, reference repos table, updated doc references.

### Renamed
- `docs/DESIGN-tool-shell-sandbox.md` → `docs/DESIGN-tool-shell.md` — broadened scope (sandbox backends, security model).
- `docs/TODO-approval-flow.md` → `docs/TODO-shell-safety.md` — refocused on shell execution safety (safe-prefix done, no-sandbox fallback scoped).

### Removed
- **`docs/TODO-tool-call-stability.md`** (previous commit): All items implemented.

---

## [0.2.12] - 2026-02-07

### Added
- **Project-level configuration**: `.co-cli/settings.json` in cwd overrides user config (`~/.config/co-cli/settings.json`). Shallow per-key merge via dict `|=`. `find_project_config()` checks cwd only — no upward directory walk. `project_config_path` module-level variable exposed for status display.
- **Project config in `co status`**: New `project_config` field in `StatusInfo`. Status table shows "Project Config: Active" row when `.co-cli/settings.json` is detected.
- **New tests**: `tests/test_config.py` — 6 functional tests covering project-overrides-user, env-overrides-project, missing config no-op, path detection, and malformed file handling.

### Fixed
- **Env var precedence**: `fill_from_env` model validator now always overrides file values. Previously env vars only filled missing fields, contradicting the documented precedence (`env vars > settings.json > defaults`).

### Changed
- **`DESIGN-co-cli.md`**: Updated §4.2 class diagram (added `sandbox_*` fields, `shell_safe_commands`, `Functions` class), §9.1 security diagram (env vars override, project config layer), §10.1 XDG directory structure (project config path).
- **`CLAUDE.md`**: Config precedence updated to 4-layer model.

---

## [0.2.10] - 2026-02-07

### Added
- **Slash commands**: 7 REPL commands (`/help`, `/clear`, `/status`, `/tools`, `/history`, `/compact`, `/yolo`) that bypass the LLM and execute instantly. New `co_cli/_commands.py` module with `CommandContext` / `SlashCommand` dataclasses, `COMMANDS` registry, and `dispatch()` function.
- **Tab completion**: `WordCompleter` with `complete_while_typing=False` for slash command names in the REPL. Triggered by Tab press — correct UX for a natural language input loop.
- **`/compact` command**: Summarizes conversation via LLM to reduce context window usage. Calls `agent.run()` with a summarization prompt, replaces history with compacted messages.
- **`/yolo` command**: Toggles `deps.auto_confirm` — same effect as picking `a` in the approval prompt, but available as a standalone toggle.
- **New tests**: `tests/test_commands.py` — 13 functional tests covering dispatch routing, all 7 handlers, yolo toggle, compact with LLM, and registry completeness.

### Changed
- **Banner hint**: Updated from `"Type 'exit' to quit"` to `"Type /help for commands, 'exit' to quit"`.
- **`DESIGN-co-cli.md`**: Added REPL Input Flow diagram, slash command architecture section (§4.5), and `_commands.py` to module summary (§13).

---

## [0.2.8] - 2026-02-06

### Added
- **Slack read tools**: `list_slack_channels`, `get_slack_channel_history`, `get_slack_thread_replies`, `list_slack_users` — four read-only tools (no approval) with `dict[str, Any]` + `display` return convention. Shared helpers: `_get_slack_client`, `_format_message`, `_format_ts`. Refactored `post_slack_message` to use `_get_slack_client`. New scopes: `channels:read`, `channels:history`, `users:read`.
- **Sandbox hardening**: Non-root execution (`user=1000:1000`), network isolation (`network_mode="none"` default, configurable to `"bridge"`), resource limits (`mem_limit=1g`, 1 CPU, `pids_limit=256`), privilege hardening (`cap_drop=["ALL"]`, `no-new-privileges`). Three new config settings: `sandbox_network`, `sandbox_mem_limit`, `sandbox_cpus` with env var overrides.
- **Custom sandbox image (`co-cli-sandbox`)**: New `Dockerfile.sandbox` based on `python:3.12-slim` with dev tools pre-installed (curl, git, jq, tree, file, less, zip/unzip, nano, wget). Default `docker_image` setting changed from `python:3.12-slim` to `co-cli-sandbox`.
- **Shell `sh -c` wrapping**: `Sandbox.run_command()` now executes via `["sh", "-c", cmd]` instead of raw `exec_run(cmd)`. Enables shell builtins (`cd`), pipes, redirects, and aliases that previously failed with "executable file not found".
- **Status module (`co_cli/status.py`)**: Extracted environment/health checks into `StatusInfo` dataclass + `get_status()`. Banner and `co status` command consume pure data — no duplicated probe logic.
- **Chained approval loop**: `_handle_approvals()` now loops (`while`, not `if`) so resumed agent runs that trigger additional deferred tool calls get their own approval round.
- **`ToolCallPart.args` type handling**: Approval prompt formatter handles `str | dict | None` args (was crashing on JSON-string args from some model providers).
- **Deferred approval flow**: Migrated from inline `_confirm.py` prompts to pydantic-ai `DeferredToolRequests` + `requires_approval=True`. Side-effectful tools (`run_shell_command`, `draft_email`, `post_slack_message`) now go through centralized `_handle_approvals()` in the chat loop with `[y/n/a(yolo)]` prompt and `ToolDenied` on rejection.
- **Obsidian search: folder and tag filtering**: `search_notes` accepts `folder` (restrict to subfolder) and `tag` (match YAML frontmatter or inline `#tag`). New `_extract_frontmatter_tags()` parser handles `tags: [a, b]` and list formats.
- **Obsidian snippet improvements**: `_snippet_around()` helper breaks at word boundaries instead of fixed character offsets.
- **Shell error propagation**: `Sandbox.run_command()` raises `RuntimeError` on non-zero exit code (was silently returning error string). `run_shell_command` tool wraps errors in `ModelRetry` so the LLM can self-correct.
- **Config `max_request_limit`**: New setting (default 25) with `CO_CLI_MAX_REQUEST_LIMIT` env var, used as `UsageLimits(request_limit=...)` in the chat loop.
- **Google auth lazy caching**: `get_cached_google_creds()` resolves credentials once on first call (module-level cache). Replaced eager `build_google_service()` — Google API clients are now built per-call in each tool, avoiding stale service objects.
- **Agent unknown-provider error**: `get_agent()` raises `ValueError` for unrecognized `llm_provider` values instead of silently falling through to Ollama.
- **New tests**: `test_search_notes_folder_filter`, `test_search_notes_tag_filter`, `test_search_notes_snippet_word_boundaries`, `test_shell_nonzero_exit_raises_model_retry`.
- **New E2E script**: `scripts/e2e_ctrl_c.py` — PTY-based test that sends SIGINT during approval prompt and during `agent.run()`, asserts process survives and returns to `Co ❯` prompt.
- **New TODO docs**: `TODO-conversation-memory.md`, `TODO-cross-tool-rag.md`.

### Fixed
- **Banner version stale after bump**: `VERSION` now reads `pyproject.toml` directly via `tomllib` (was `importlib.metadata`, which required reinstall to reflect changes).
- **Ctrl-C exits process instead of returning to prompt**: `asyncio.run()` in Python 3.11+ delivers SIGINT as `asyncio.CancelledError`, not `KeyboardInterrupt`. Chat loop now catches both. Approval prompt (`Prompt.ask()`) temporarily restores the default SIGINT handler so Ctrl-C can interrupt synchronous `input()`. Safety-net `except KeyboardInterrupt` wraps `asyncio.run()` for edge cases. See `DESIGN-co-cli.md` §8.

### Changed
- **`DESIGN-co-cli.md`**: Added complete tool return type reference table (all 16 tools) to §5.1.1 with `_display_tool_outputs()` transport-layer separation explanation. Expanded tool architecture graph, cloud tool summary, and module summary to include all Slack, Gmail, and Calendar tools.
- **`DESIGN-tool-slack.md`**: Expanded from single-tool doc to full five-tool reference with shared helpers, setup guide, scope table, and test inventory.
- **`DESIGN-tool-shell-sandbox.md`**: Added container hardening documentation — non-root, network isolation, resource limits, privilege dropping, and configurable settings.
- **`TODO-tool-call-stability.md`**: Marked sandbox hardening phases 1–3 as done.
- **`TODO-slack-tooling.md`**: Marked Phase 1 (core reads) as done.
- **Obsidian tool return type**: `search_notes` and `list_notes` now return `dict[str, Any]` with `display`, `count`, `has_more` fields (was `list[dict]` / `list[str]`). `search_notes` returns empty dict on no results instead of raising `ModelRetry`.
- **Agent system prompt**: Added "Tool Output" section instructing the LLM to show `display` verbatim and respect `has_more`.
- **Config env override logic**: Fixed `Settings.from_file()` to check `field not in data or data[field] is None` (was `not data.get(field)`, which treated `0` and `""` as missing).
- **CoDeps**: Removed `google_drive`, `google_gmail`, `google_calendar` fields — replaced by `google_credentials_path`. Added comment clarifying `auto_confirm` purpose.
- **Tests**: Removed all `@pytest.mark.skipif` guards (Docker, GCP, Slack) per testing policy. Simplified test context setup — no more per-test credential/service building.
- **CLAUDE.md**: Streamlined — removed inline module/tool tables (covered by DESIGN docs), tightened coding standards.
- **Theming: Rich `Theme` migration**: Replaced manual `_c(role)` color resolver with idiomatic `Console(theme=Theme(...))`. Semantic style names (`"status"`, `"accent"`, `"shell"`, etc.) are now resolved natively by Rich. Added `"shell"` semantic style for shell output panel borders.
- **Design docs**: Updated `DESIGN-co-cli.md`, `DESIGN-tool-obsidian.md`, `DESIGN-tool-shell-sandbox.md` to reflect new approval flow, obsidian features, and shell error handling. Restructured `DESIGN-co-cli.md`: promoted §7.5+§7.6 (interrupt recovery + signal handling) into new **§8 Interrupt Handling**, renumbered §8–§12 → §9–§13.
- **TODO docs**: Trimmed `TODO-approval-flow.md` and `TODO-tool-call-stability.md` — removed completed items, kept only remaining work.

### Removed
- **`docs/TODO-structured-output.md`**: Problem already solved by `_display_tool_outputs()` transport-layer separation; proposed `CoResponse` union carried Gemini compatibility risk for no gain.
- **`co_cli/tools/_confirm.py`**: Inline approval prompt — superseded by `DeferredToolRequests` in chat loop.
- **`docs/TODO-obsidian-search.md`**: Merged into `TODO-cross-tool-rag.md`.
- **`docs/FIX-general-issues-team-work-codex-claude-code.md`**: All tracked issues resolved or moved to standalone docs.

---

## [0.2.7] - 2026-02-06

### Fixed
- **Telemetry SQLite lock contention**: `SQLiteSpanExporter` now uses WAL journal mode and `busy_timeout=5000ms` on every connection, preventing `database is locked` errors when `co tail`/Datasette read while the chat session writes spans. Export uses `executemany` (single batch write) with 3-attempt exponential backoff retry for transient lock failures.
- **Banner version stale**: `display_welcome_banner()` and `main.py` `service.version` now read from `importlib.metadata` instead of hardcoded strings, keeping the version in sync with `pyproject.toml` automatically.

### Changed
- **`docs/DESIGN-otel-logging.md`**: Added "Concurrent Access (WAL Mode)" section documenting WAL rationale, pragma settings, retry strategy, and batch insert design.

---

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
