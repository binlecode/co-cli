# UAT Readiness Assessment — Full System Scan

**Date:** 2026-04-15
**Scope:** Config, Bootstrap, Context/Compaction, Tools/Toolcall, Memory, Knowledge
**Method:** Deep code scan of every source file, spec, and test in each subsystem. Every claim below cites `file:line`.
**Version:** v0.7.160 (commit fc0967b)

---

## Table of Contents

1. [Verdict Summary](#1-verdict-summary)
2. [Config System](#2-config-system)
3. [Bootstrap Flow](#3-bootstrap-flow)
4. [Context Management and Compaction](#4-context-management-and-compaction)
5. [Tools and Toolcall](#5-tools-and-toolcall)
6. [Memory System](#6-memory-system)
7. [Knowledge System](#7-knowledge-system)
8. [Consolidated Gap Table](#8-consolidated-gap-table)
9. [Recommended Fix Order](#9-recommended-fix-order)

---

## 1. Verdict Summary

### Blocking (must fix before UAT)

| ID | Area | Gap | Location |
|----|------|-----|----------|
| B1 | Config | `GITHUB_TOKEN_BINLECODE` hardcoded env var name | `bootstrap/core.py:38` |
| B2 | Memory | `/memory forget` does not clean `search.db` | `_commands.py:1169` |
| B3 | Knowledge | `SearchResult.tags` is `str` vs `MemoryEntry.tags` is `list[str]` | `_store.py` vs `recall.py:30` |
| B4 | Context | Compaction circuit breaker never resets | `_history.py:469-493` |

### High (degrades experience, won't crash)

| ID | Area | Gap | Location |
|----|------|-----|----------|
| H1 | Memory | Extractor produces duplicate memories | `_extractor.py`, spec line 20 |
| H2 | Memory | `always_on` cap enforced only at read (5), not at write | `recall.py:131` |
| H3 | Config | No env var for `memory.extract_every_n_turns` | `_memory.py` |
| H4 | Config | No env var for `llm.reasoning` / `llm.noreason` | `_llm.py` |
| H5 | Context | Token estimation uses hardcoded 4 chars/token | `summarization.py:36` |
| H6 | Context | 5MB+ transcript resume silently skips pre-boundary messages | `transcript.py:173-178` |
| H7 | Knowledge | Stopword-only queries return empty `[]` | `_store.py:1318` |
| H8 | Bootstrap | All MCP servers fail with no aggregate warning | `bootstrap/core.py:254-269` |

### What's Solid (no gaps found)

- Tool pattern compliance across all 36 tools.
- Approval flow end-to-end.
- Bootstrap degradation chain (hybrid -> fts5 -> grep).
- Session persistence with OOM guards.
- Observability (OTel spans, SQLite exporter).
- Config validation (Pydantic, env override, graceful malformed JSON handling).

---

## 2. Config System

### 2.1 Settings Model Structure

All Pydantic Settings reside in `co_cli/config/`. Root model is `Settings` in `co_cli/config/_core.py`.

**Nested sub-models (via `Field` defaults):**

| Sub-model | File | Key Fields |
|-----------|------|------------|
| `LlmSettings` | `_llm.py` | `provider`, `host`, `model`, `num_ctx`, `ctx_warn_threshold`, `ctx_overflow_threshold`, `reasoning` (sub-model), `noreason` (sub-model) |
| `KnowledgeSettings` | `_knowledge.py` | `search_backend`, `embedding_provider`, `embedding_model`, `embedding_dims`, `cross_encoder_reranker_url`, `llm_reranker`, `embed_api_url`, `chunk_size`, `chunk_overlap`, `tei_rerank_batch_size` |
| `MemorySettings` | `_memory.py` | `recall_half_life_days` (default 30), `injection_max_chars` (default 2000), `extract_every_n_turns` (default 3) |
| `ShellSettings` | `_shell.py` | `max_timeout` (default 600), `safe_commands` (DENY-safe list) |
| `WebSettings` | `_web.py` | `fetch_allowed_domains`, `fetch_blocked_domains`, `http_max_retries`, `http_backoff_base_seconds`, `http_backoff_max_seconds`, `http_jitter_ratio` |
| `SubagentSettings` | `_subagent.py` | `scope_chars`, `max_requests_research`, `max_requests_analysis`, `max_requests_thinking` |
| `ObservabilitySettings` | `_observability.py` | `log_level`, `log_max_size_mb`, `log_backup_count`, `redact_patterns` |

**Flat fields on root `Settings`:**

| Field | Type | Default | Used By |
|-------|------|---------|---------|
| `obsidian_vault_path` | `str \| None` | `None` | `deps.resolve_workspace_paths()` |
| `brave_search_api_key` | `str \| None` | `None` | `tools/web.py` |
| `google_credentials_path` | `str \| None` | `None` | `tools/google/_auth.py` |
| `library_path` | `str \| None` | `None` | `deps.resolve_workspace_paths()` |
| `theme` | `str` | `"light"` | `display/_core.py` |
| `reasoning_display` | `Literal["off","summary","full"]` | `"summary"` | `commands/_commands.py` |
| `personality` | `str` | `"tars"` | Everywhere, validated against `VALID_PERSONALITIES` |
| `tool_retries` | `int` | `3` | `agent/_core.py` |
| `doom_loop_threshold` | `int` | `3` (range 2-10) | `context/orchestrate.py` |
| `max_reflections` | `int` | `3` (range 1-10) | `context/orchestrate.py` |
| `mcp_servers` | `dict[str, MCPServerSettings]` | `{}` | `bootstrap/core.py`, `agent/_mcp.py` |

### 2.2 Config Loading Precedence

Load order (lowest to highest precedence):

1. **Defaults** — hardcoded in model `Field` definitions.
2. **User `settings.json`** — `~/.co-cli/settings.json` (path via `CO_CLI_HOME` env var or `~/.co-cli`).
3. **Environment variables** — override via `fill_from_env()` `@model_validator`.
4. **Test overrides** — via `_user_config_path` and `_env` parameters (test only).

**Load function:** `load_config(_user_config_path=None, _env=None)` at `config/_core.py:263-288`.

**Path constants (module-level):**
- `USER_DIR = Path(os.getenv("CO_CLI_HOME", Path.home() / ".co-cli"))` — line 30.
- `SETTINGS_FILE = USER_DIR / "settings.json"` — line 33.

**Singleton pattern:** `get_settings()` caches result in module-level `_settings`. Invoked once per session via `config/_core.py:__getattr__` lazy loader.

### 2.3 Environment Variable Mapping

**Flat Settings:**

| Field | Env Var |
|-------|---------|
| `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` |
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` |
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` |
| `library_path` | `CO_LIBRARY_PATH` |
| `theme` | `CO_CLI_THEME` |
| `reasoning_display` | `CO_CLI_REASONING_DISPLAY` |
| `personality` | `CO_CLI_PERSONALITY` |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` |
| `doom_loop_threshold` | `CO_CLI_DOOM_LOOP_THRESHOLD` |
| `max_reflections` | `CO_CLI_MAX_REFLECTIONS` |

**LLM Settings:**

| Field | Env Var |
|-------|---------|
| `llm.api_key` | `LLM_API_KEY` |
| `llm.provider` | `LLM_PROVIDER` |
| `llm.host` | `LLM_HOST` |
| `llm.model` | `CO_LLM_MODEL` |
| `llm.num_ctx` | `LLM_NUM_CTX` |
| `llm.ctx_warn_threshold` | `CO_CTX_WARN_THRESHOLD` |
| `llm.ctx_overflow_threshold` | `CO_CTX_OVERFLOW_THRESHOLD` |

**Knowledge Settings:**

| Field | Env Var |
|-------|---------|
| `knowledge.search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` |
| `knowledge.embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` |
| `knowledge.embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` |
| `knowledge.embedding_dims` | `CO_KNOWLEDGE_EMBEDDING_DIMS` |
| `knowledge.cross_encoder_reranker_url` | `CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL` |
| `knowledge.embed_api_url` | `CO_KNOWLEDGE_EMBED_API_URL` |
| `knowledge.chunk_size` | `CO_CLI_KNOWLEDGE_CHUNK_SIZE` |
| `knowledge.chunk_overlap` | `CO_CLI_KNOWLEDGE_CHUNK_OVERLAP` |

**Memory Settings:**

| Field | Env Var |
|-------|---------|
| `memory.recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` |
| `memory.injection_max_chars` | `CO_CLI_MEMORY_INJECTION_MAX_CHARS` |
| `memory.extract_every_n_turns` | **(NONE — gap H3)** |

**Subagent Settings:**

| Field | Env Var |
|-------|---------|
| `subagent.scope_chars` | `CO_CLI_SUBAGENT_SCOPE_CHARS` |
| `subagent.max_requests_research` | `CO_CLI_SUBAGENT_MAX_REQUESTS_RESEARCH` |
| `subagent.max_requests_analysis` | `CO_CLI_SUBAGENT_MAX_REQUESTS_ANALYSIS` |
| `subagent.max_requests_thinking` | `CO_CLI_SUBAGENT_MAX_REQUESTS_THINKING` |

**Shell Settings:**

| Field | Env Var |
|-------|---------|
| `shell.max_timeout` | `CO_CLI_SHELL_MAX_TIMEOUT` |
| `shell.safe_commands` | `CO_CLI_SHELL_SAFE_COMMANDS` |

**Web Settings:**

| Field | Env Var |
|-------|---------|
| `web.fetch_allowed_domains` | `CO_CLI_WEB_FETCH_ALLOWED_DOMAINS` |
| `web.fetch_blocked_domains` | `CO_CLI_WEB_FETCH_BLOCKED_DOMAINS` |
| `web.http_max_retries` | `CO_CLI_WEB_HTTP_MAX_RETRIES` |
| `web.http_backoff_base_seconds` | `CO_CLI_WEB_HTTP_BACKOFF_BASE_SECONDS` |
| `web.http_backoff_max_seconds` | `CO_CLI_WEB_HTTP_BACKOFF_MAX_SECONDS` |
| `web.http_jitter_ratio` | `CO_CLI_WEB_HTTP_JITTER_RATIO` |

**Observability Settings:**

| Field | Env Var |
|-------|---------|
| `observability.log_level` | `CO_CLI_LOG_LEVEL` |
| `observability.log_max_size_mb` | `CO_CLI_LOG_MAX_SIZE_MB` |
| `observability.log_backup_count` | `CO_CLI_LOG_BACKUP_COUNT` |

**Other:**

| Field | Env Var |
|-------|---------|
| `mcp_servers` | `CO_CLI_MCP_SERVERS` (JSON) |
| `llm.reasoning` | **(NONE — gap H4)** |
| `llm.noreason` | **(NONE — gap H4)** |
| `observability.redact_patterns` | **(NONE)** |

### 2.4 Validation and Error Handling

**Pydantic validators:**

| Validator | Location | Behavior |
|-----------|----------|----------|
| `Settings.fill_from_env()` | `@model_validator(mode="before")` | Injects env var overrides before Pydantic parse |
| `Settings._validate_personality_name()` | `@field_validator` | Ensures personality in `VALID_PERSONALITIES` |
| `LlmSettings.validate_config()` | Manual method | Checks provider/model shape (no exception, returns bool) |
| `WebSettings._validate_web_retry_bounds()` | `@model_validator` | Ensures `base_seconds <= max_seconds` |
| `WebSettings._parse_web_domains()` | `@field_validator` | Converts CSV strings to `list[str]` |
| `MCPServerSettings._require_command_or_url()` | `@model_validator` | Mutually exclusive command/url check |

**Error behavior:**

| Condition | Behavior | Location |
|-----------|----------|----------|
| Missing `settings.json` | Uses defaults silently | `_core.py:268` (`data = {}`) |
| Invalid JSON | Prints to stderr, uses defaults | `_core.py:274` |
| Schema mismatch (Pydantic `ValidationError`) | Wraps in `ValueError` with file path hint, raises to caller | `_core.py:280-283` |
| Invalid personality | Raises `ValueError` with file hint | `_core.py:285-286` |
| Gemini without `api_key` | Caught by `llm.validate_config()` in bootstrap | `bootstrap/core.py:226-228` |
| Invalid retry bounds | Raises `ValueError` with file hint | `_web.py` |

**Test coverage:** 17 tests in `tests/test_config.py` covering precedence, validation, malformed JSON, schema errors.

### 2.5 Settings Reference File

`settings.reference.json` exists at repo root. All fields correspond to the Settings model. Default values match code constants. JSON structure mirrors nested Pydantic models.

### 2.6 Config Gaps

**B1 — Hardcoded env var name (BLOCKING):**
`bootstrap/core.py:38` reads `os.getenv("GITHUB_TOKEN_BINLECODE", "")` to populate MCP server env vars. This is a personal env var name, not configurable via Settings. Any other user's GitHub MCP integration silently gets no token. Should be `CO_CLI_GITHUB_TOKEN` or `settings.integrations.github_token`.

**H3 — No env var for `memory.extract_every_n_turns` (HIGH):**
Defined in the model with default 3 but has no env var mapping. Cannot tune extraction cadence without editing `settings.json`.

**H4 — No env var for `llm.reasoning` / `llm.noreason` (HIGH):**
These sub-objects control model inference parameters (temperature, max_tokens for reasoning/non-reasoning modes). Only settable via JSON file edit.

**Config mutations not persisted (ACCEPTED):**
During bootstrap, `config.llm.num_ctx` is overwritten by Ollama probe result (`core.py:245`), `config.knowledge` fields are mutated for degradation fallback, and `config.mcp_servers` is modified with token injection. None of these mutations persist to disk. This is by design — runtime-only overrides.

**Other minor gaps:**
- `observability.redact_patterns` has no env var mapping.
- `tool_retries` is a flat field on root Settings instead of nested under an `agent` sub-model (organizational inconsistency, no runtime impact).
- No project-level override path (`.co-cli/settings.json` in working directory). All users share the global settings file.

---

## 3. Bootstrap Flow

### 3.1 Entry Point

- `pyproject.toml:32` — `co = "co_cli.main:app"` (Typer CLI).
- `main.py:85-96` — Typer app with `/chat` as default command.
- `main.py:324-353` — `chat()` command entry point; resolves theme/reasoning-display, then `asyncio.run(_chat_loop())`.

### 3.2 Complete Bootstrap Sequence

**Step 1: Settings Load**
- `main.py:44` — `from co_cli.config._core import settings` triggers lazy singleton.
- `config/_core.py:310-314` — `__getattr__` lazy loader calls `get_settings()`.
- `config/_core.py:295-307` — `get_settings()`: calls `_ensure_dirs()`, then `load_config()`.
- `config/_core.py:61-68` — `_ensure_dirs()` creates `~/.co-cli/` and subdirs (idempotent).
- `config/_core.py:263-288` — `load_config()` loads from `~/.co-cli/settings.json`, applies env overrides, validates with Pydantic.

**Step 2: Observability Setup (before bootstrap)**
- `main.py:59-64` — `setup_file_logging()` creates rotating handlers for `co-cli.log` and `errors.log`.
  - `observability/_file_logging.py:62-99` — Idempotent; creates parent dirs.
- `main.py:67-74` — `setup_tracer_provider()` creates SQLite exporter.
  - `observability/_telemetry.py:43-59` — Creates parent dirs and initializes DB schema.
- `main.py:78` — `Agent.instrument_all()` enables pydantic-ai instrumentation globally.

**Step 3: Chat Loop Entry and UI Setup**
- `main.py:350` — `asyncio.run(_chat_loop(reasoning_display=effective_mode))`.
- `main.py:234-243` — Constructs:
  - `TerminalFrontend()` — Display backend.
  - `WordCompleter(["/cmd", ...])` — Tab completion for built-in commands.
  - `PromptSession()` — History file at `~/.co-cli/history.txt`.
  - `AsyncExitStack()` — Cleanup guards for all async resources.

**Step 4: `create_deps()` — Core Bootstrap**
- `main.py:247` — `deps = await create_deps(frontend, stack)`.
- `bootstrap/core.py:208-309` — Orchestration function.

  **4a: Config and path resolution**
  - `core.py:221` — Read settings singleton.
  - `core.py:222-223` — `resolve_workspace_paths()` at `deps.py:213-226`.

  **4b: LLM validation (fail-fast)**
  - `core.py:226-228` — `config.llm.validate_config()` checks provider/model shape.
  - Raises `ValueError` if no provider configured or Gemini without API key.

  **4c: Ollama context probe (optional)**
  - `core.py:232-245` — If using Ollama:
    - `bootstrap/check.py:132-194` — `probe_ollama_context()` calls `/api/show` to get runtime `num_ctx`.
    - If result < `MIN_AGENTIC_CONTEXT` (65,536) — raises `ValueError` and aborts.
    - If probe fails (network) — warns but continues.
    - Overwrites `config.llm.num_ctx` with runtime value if different.

  **4d: MCP env resolution**
  - `core.py:248` — `_resolve_mcp_env_tokens(config)` at `core.py:31-43`.
  - Resolves env-based MCP credentials (including the hardcoded `GITHUB_TOKEN_BINLECODE`).

  **4e: Model and tool registry build**
  - `core.py:249` — `build_model(config.llm)` at `llm/_factory.py:38-88`.
    - Ollama-OpenAI: Creates `AsyncOpenAI` client with custom HTTP timeouts.
    - Gemini: Creates `GoogleModel` with API key.
    - Returns `LlmModel(model, settings, context_window)`.
  - `core.py:250` — `build_tool_registry(config)` at `agent/_core.py:42-64`.
    - Builds native toolset + MCP toolsets (pure config, no IO).
    - Returns `ToolRegistry(toolset, mcp_toolsets, tool_index)`.

  **4f: MCP connection and discovery**
  - `core.py:254-269` — For each MCP toolset:
    - `await stack.enter_async_context(ts)` connects server (subprocess or HTTP).
    - Per-MCP-server failure isolated: fails -> status warning, recorded in `degradations["mcp.<prefix>"]`.
    - Native tools remain available even if all MCP servers fail.

  **4g: Skills loading (two-pass)**
  - `core.py:275-280` — `_load_skills()` at `commands/_commands.py:962-994`.
    - Pass 1: Bundled skills from `co_cli/skills/` (no security scan).
    - Pass 2: User skills from `~/.co-cli/skills/` (override bundled, security scanned).
    - Per-skill-file failure isolated: malformed frontmatter or env validation failure logged, skill skipped.

  **4h: Knowledge backend resolution**
  - `core.py:285` — `_discover_knowledge_backend()` at `core.py:78-163`.
  - Config preferred backend: `hybrid`, `fts5`, or `grep`.
  - Three-tier fallback with graceful degradation:
    1. **Hybrid** (sqlite-vec + embedding): Probes cross-encoder and LLM reranker, then embedder. Reranker unavailable -> recorded in degradations. Embedder unavailable -> falls back to FTS5.
    2. **FTS5** (keyword-only): If hybrid fails.
    3. **Grep** (fallback): If FTS5 DB init fails, returns `None`.
  - Each degradation recorded in `degradations` dict with reason.

  **4i: Knowledge store sync**
  - `core.py:288-294` — `_sync_knowledge_store()` at `core.py:166-205`.
  - Hash-based: only re-indexes changed files.
  - Sync failure: store closed and returned as `None`, session continues without indexed search (grep fallback).

  **4j: Assemble CoDeps**
  - `core.py:297-309` — Creates `CoDeps` dataclass at `deps.py:161-211`.
  - Services: `ShellBackend()`, `config`, `model`, `knowledge_store`, `tool_index`, `tool_registry`, `skill_commands`, `runtime` (fresh per turn), `session` (mutable session-level state), `degradations`.

**Step 5: Agent Construction**
- `main.py:254` — `build_agent(config=deps.config, model=deps.model, tool_registry=deps.tool_registry)`.
- `agent/_core.py:67-176`:
  - Normalizes model to raw pydantic-ai model object.
  - Calls `build_static_instructions(config)` from `prompts/_assembly.py`.
  - Creates `Agent[CoDeps, str | DeferredToolRequests]` with combined toolset (native + MCP), history processors, and tool lifecycle.
  - Registers 5 conditional instruction layers (date, shell guidance, memories, personality, category awareness).

**Step 6: Session Restore and Index**
- `main.py:256` — `restore_session(deps, frontend)` at `core.py:337-358`.
  - `find_latest_session()` scans `~/.co-cli/sessions/` for latest `.jsonl` by lexicographic sort.
  - Returns existing path or creates new path via `new_session_path()`.
- `main.py:257` — `_init_session_index()` at `core.py:312-334`.
  - Opens or creates `~/.co-cli/session-index.db` (FTS5).
  - Syncs past sessions (excluding current) into index.
  - Failure: logged warning, `deps.session_index = None`, graceful degradation.

**Step 7: Welcome Banner and REPL Ready**
- `main.py:260` — Status: skill count.
- `main.py:262-263` — If previous session exists: resume hint.
- `main.py:265` — `display_welcome_banner(deps)` at `bootstrap/banner.py`.
- `main.py:268` — Initialize message history.
- `main.py:271-316` — REPL loop begins.

### 3.3 Signal Handling and Cleanup

- `main.py:307-316` — First Ctrl+C: "Press again to exit" (within 2s window). Second: breaks loop.
- `main.py:319-320` — Finally block: `_drain_and_cleanup(deps, stack)`.
  - `main.py:184-199` — Drains pending memory extraction, kills background tasks, calls `deps.shell.cleanup()`, closes `AsyncExitStack` (closes all MCP servers).

### 3.4 Bootstrap Failure Modes

| Failure | Handling | Location |
|---------|----------|----------|
| `settings.json` invalid | Raises `ValueError`, exits | `config/_core.py:280-283` |
| `settings.json` missing | Uses defaults (no error) | `config/_core.py:268` |
| LLM provider/model invalid | Raises `ValueError`, caught at `main.py:248-250` | `core.py:226-228` |
| Ollama context too small (<65K) | Raises `ValueError`, exits | `core.py:236-237` |
| LLM provider unreachable | Deferred to runtime (first model call) | `llm/_factory.py:54-86` |
| MCP server fails | Per-server warning + degradation recorded | `core.py:260-268` |
| Knowledge backend fails | Degrades hybrid -> fts5 -> grep | `core.py:136-163` |
| Knowledge sync fails | Store closed, session continues | `core.py:195-203` |
| Session index fails | Logged warning, `session_index=None` | `core.py:331-334` |
| Skill file fails | Logged per-file, continues | `commands/_commands.py:955-959` |
| Logging setup fails | Silent (no error surfaced) | `main.py:59-64, 67-74` |

### 3.5 Bootstrap Gaps

**H8 — All MCP servers fail with no aggregate warning (HIGH):**
If every configured MCP server errors during step 4f, individual per-server warnings appear but no top-level "all MCP integrations unavailable" banner. User may not notice scattered messages among other bootstrap output. Location: `bootstrap/core.py:254-269`.

**Minor:**
- Personality files missing -> validation warnings printed to stdout before logging is configured (could be missed). Location: `config/_core.py:285-286`.
- Logging setup failure is completely silent. Location: `main.py:59-64, 67-74`.

**Spec vs code alignment:** Spec (`docs/specs/flow-bootstrap.md`) accurately describes config precedence, degradation behavior, failure modes, and recovery. No significant divergences found.

---

## 4. Context Management and Compaction

### 4.1 Prompt Assembly

**Static instructions** — assembled at agent construction via `build_static_instructions(config)` at `prompts/_assembly.py:68-135`:

1. Soul seed — identity anchor from `souls/{role}/seed.md`.
2. Character memories — from `souls/{role}/memories/*.md`.
3. Mindsets — task-type prompts from `souls/{role}/mindsets/{task_type}.md`.
4. Behavioral rules — numbered `NN_rule_id.md` files (01 onward, contiguous, strict order validation at lines 24-65).
5. Soul examples — from `souls/{role}/examples.md`.
6. Critique — from soul review lens, appended as `## Review lens` section.

Personality is optional; if absent, only rules are assembled.

**Dynamic instructions** — five layers registered via `@agent.instructions()` at `agent/_core.py:156-160`, evaluated fresh each turn:

| Layer | Trigger | Source | Budget |
|-------|---------|--------|--------|
| `add_current_date` | Always | `Date: YYYY-MM-DD` | ~10 tokens |
| `add_shell_guidance` | Always | Shell approval/reminder text | ~100 tokens |
| `add_always_on_memories` | `always_on=True` entries exist | `agent/_instructions.py:24-31`; caps at 5 memories, `memory_injection_max_chars` (default 2K) | ~500 tokens |
| `add_personality_memories` | `config.personality` set | `prompts/personalities/_injector.py`; top-5 personality-context tagged memories sorted by recency | ~500 tokens |
| `add_category_awareness_prompt` | Deferred tools registered | `tools/_deferred_prompt.py`; category-level prompt for `search_tools` discovery | ~100 tokens |

### 4.2 History Processing Chain

Five history processors registered at `agent/_core.py:144-150`, run before every model request in order:

**Processor 1: `truncate_tool_results()`** (sync, `_history.py:265-310`)
- Clears content of older `ToolReturnPart` entries for compactable tools.
- Preserves last user turn (via `_find_last_turn_start()`, line 214-224).
- Keeps 5 most recent per tool type (`COMPACTABLE_KEEP_RECENT = 5`).
- Replaced content: `"[tool result cleared -- older than 5 most recent calls]"`.

**Processor 2: `compact_assistant_responses()`** (sync, `_history.py:318-342`)
- Caps large `TextPart`/`ThinkingPart` in older `ModelResponse` messages.
- Max chars per part: `OLDER_MSG_MAX_CHARS = 2,500`.
- Retention: 20% head / 80% tail with `[...truncated...]` marker (lines 227-238).
- Never touches `ToolCallPart`, `ToolReturnPart`, or `UserPromptPart`.
- Last turn always protected.

**Processor 3: `detect_safety_issues()`** (sync, stateful, `_history.py:764-833`)
- **Doom loop detection:** Counts identical consecutive tool calls; injects warning at `doom_loop_threshold` (default 3). Scan window: 10 calls max (`_count_consecutive_same_calls()`, lines 695-722).
- **Shell reflection cap:** Counts consecutive shell errors; injects warning at `max_reflections` (default 3). Detection via `_count_consecutive_shell_errors()` (lines 745-761). Error patterns: `"error"`, `"shell: command failed"`, `"shell: unexpected error"` (lines 733-738).
- State stored on `ctx.deps.runtime.safety_state` (reset per turn).
- Injections appended as `SystemPromptPart`.

**Processor 4: `inject_opening_context()`** (async, stateful, `_history.py:627-687`)
- Recalls top-3 memories matching the last user message.
- Trigger: each new user turn (via `_count_user_turns()`, line 618-624).
- Source: `_recall_for_context()` in `tools/memory.py` — FTS5 BM25 search, no LLM cost. Falls back to `grep_recall()` if `knowledge_store is None`.
- Capped to `memory_injection_max_chars` (default 2K).
- State on `ctx.deps.session.memory_recall_state`.
- Injected as `SystemPromptPart` with "Relevant memories:" header.

**Processor 5: `summarize_history_window()`** (async, LLM-powered, `_history.py:547-600`)
- **Trigger:** Estimated tokens exceed 85% of compaction budget.
- **Token count sources (priority):**
  1. `latest_response_input_tokens()` from last `ModelResponse.usage.input_tokens` (lines 52-61).
  2. Fallback: `estimate_message_tokens()` — rough char-based estimate (~4 chars per token, line 36).
- **Budget resolution** (`resolve_compaction_budget()`, lines 72-98):
  1. Model's `context_window` minus 16K output reserve.
  2. Ollama override: `config.llm.num_ctx`.
  3. Fallback: `_DEFAULT_TOKEN_BUDGET = 100,000`.
- **Compaction boundary** (`_compute_compaction_boundaries()`, lines 180-201):
  - Head: first turn with `TextPart`/`ThinkingPart`.
  - Tail: last 50% of messages, snapped to nearest turn boundary.
  - Returns `None` (no-op) if <=2 groups or nothing to drop.
- **Summarization path:**
  1. `_summarize_dropped_messages()` (lines 458-494).
  2. Context enrichment via `_gather_compaction_context()` (lines 405-433): file working set, pending todos, always-on memories, prior summary text. All capped at `_CONTEXT_MAX_CHARS = 4,000`.
  3. Summarizer agent (module-level, lines 162-165) with structured template (Goal, Key Decisions, Working Set, Progress, Next Steps).
- **Circuit breaker:** `compaction_failure_count >= 3` -> static marker fallback (lines 469-472). Resets on success (line 489). Increments on failure (line 493).
- **Preservation:** `_preserve_search_tool_breadcrumbs()` (lines 497-504) keeps `search_tools` discovery state across compaction boundary.

### 4.3 Compaction Mechanisms

**Inline compaction (automatic, per-turn):**
Triggered by processor 5 when tokens exceed 85% of budget. Replaces middle messages with summary marker. Sets `deps.runtime.history_compaction_applied = True` (line 598). After turn, `_finalize_turn()` (main.py:99-154) branches to new child session via `persist_session_history(..., history_compacted=True)`.

**Emergency overflow recovery (on provider error):**
- Entry: `context/orchestrate.py:run_turn()` (lines 517-671).
- Detection via `_is_context_overflow()` (lines 489-499): status 400 or 413 AND body patterns like `"prompt is too long"`, `"context_length_exceeded"`, `"maximum context length"`.
- Recovery: materializes pending user input, calls `recover_overflow_history()` (lines 507-539). Keeps first + last groups, summarizes middle. Falls back to static marker if summarizer unavailable.
- One retry per turn (`overflow_recovery_attempted` flag). Second overflow -> terminal error (lines 597-601).

**Manual compaction (`/compact` command):**
- Entry: `commands/_commands.py:_cmd_compact()` (lines 330-381).
- Summarizes entire history, builds minimal 2-message replacement, returns `ReplaceTranscript(history=new_history, compaction_applied=True)`. Caller branches to new child session.

### 4.4 Session Persistence

**Transcript format:** Append-only JSONL at `~/.co-cli/sessions/YYYY-MM-DD-THHMMSSz-{uuid8}.jsonl`. Each line is either a message row (pydantic-ai `ModelMessagesTypeAdapter`) or a control line (`session_meta`, `compact_boundary`).

**File operations:**
- `transcript.py:append_messages()` (lines 40-53): append message rows, creates file on first call.
- `transcript.py:persist_session_history()` (lines 87-111): append-only for normal turns; branch to new file for compacted history.
- `transcript.py:write_session_meta()` (lines 69-84): records parent session link on new file.
- `transcript.py:write_compact_boundary()` (lines 56-66): legacy marker.

**Transcript loading:**
- `transcript.py:load_transcript()` (lines 114-180).
- OOM guard: files > `MAX_TRANSCRIPT_READ_BYTES = 50 MB` rejected entirely (lines 134-141).
- Skip precompact threshold: files > `SKIP_PRECOMPACT_THRESHOLD = 5 MB` skip pre-boundary messages (lines 143-178).
- Skips malformed lines with warning. Skips control lines. Graceful read failures.

**Session discovery:**
- `session.py:find_latest_session()` (lines 50-59): lexicographic sort (= chronological).
- `session.py:new_session_path()` (lines 62-70): `sessions_dir/YYYY-MM-DD-THHMMSSz-{uuid8}.jsonl`.

**Durability:** `deps.session.persisted_message_count` tracks messages written. On turn completion, only new messages appended. History replacement resets count.

### 4.5 Token Counting

**Estimation:** `summarization.py:estimate_message_tokens()` (lines 35-49). Iterates all message parts, extracts content, sums character count, divides by 4. Hardcoded assumption: ~4 chars per token for English.

**Provider-reported:** `latest_response_input_tokens()` (lines 52-61). Scans history in reverse for first `ModelResponse.usage.input_tokens > 0`. Fallback: 0.

**No explicit tokenizer implementation.** Token counting is char-based estimation for triggering compaction and provider-reported when available. Neither is calibrated to a specific model tokenizer.

### 4.6 Context Gaps

**B4 — Compaction circuit breaker never resets (BLOCKING):**
`compaction_failure_count` is cross-turn state on `deps` (not reset per turn). After 3 consecutive summarizer failures, all future compactions for the entire session fall back to static markers (no summary text, just "X earlier messages were removed"). No recovery path — user must `/new` to escape. Circuit breaker should reset on successful compaction or on new user turn. Location: `deps.py:132`, `_history.py:469-493`.

**H5 — Token estimation accuracy (HIGH):**
The 4 chars/token rule is rough. Risk: compaction triggers too early (wasting an LLM call) or too late (requiring emergency overflow recovery). Mitigated by provider-reported tokens when available, but first turn in session has no prior usage data. Particularly inaccurate for multilingual content, code-heavy sessions, and JSON-heavy tool arguments.

**H6 — Silent 5MB+ transcript truncation (HIGH):**
Files > 5 MB during session resume silently skip all pre-boundary messages. Logged at `transcript.py:173-178` but not surfaced to the user via the frontend. User doesn't know they've lost earlier context.

**Design tradeoffs (accepted):**
- Compaction per-turn (not proactive) to avoid unnecessary LLM calls.
- No concurrent-instance session safety — documented non-goal.
- Fire-and-forget memory extraction — loss doesn't block turn completion.
- `search_tools` breadcrumb preservation across compaction — intentional to avoid rediscovery cost.

---

## 5. Tools and Toolcall

### 5.1 Tool Registry

Registry built by `_build_native_toolset()` at `agent/_native_toolset.py:54-301`. Returns `FunctionToolset[CoDeps]` + `dict[str, ToolInfo]`.

Registration helper `_register_tool()` (lines 71-107) accepts: `fn`, `approval` (bool), `is_read_only` (bool), `is_concurrent_safe` (bool), `visibility` (ALWAYS/DEFERRED), `integration` (optional domain string), `retries` (optional), `max_result_size` (default 50,000).

### 5.2 Complete Native Tool Inventory

**ALWAYS-visible tools (14)** — immediately available every turn:

| Tool | Approval | Read-Only | File | Concurrent | Retries | Max Size |
|------|----------|-----------|------|------------|---------|----------|
| `check_capabilities` | No | Yes | `capabilities.py` | Yes | - | 50K |
| `read_todos` | No | Yes | `todo.py` | Yes | - | 50K |
| `write_todos` | No | No | `todo.py` | Yes | - | 50K |
| `search_memories` | No | Yes | `memory.py` | Yes | - | 50K |
| `search_knowledge` | No | Yes | `articles.py` | Yes | - | 50K |
| `search_articles` | No | Yes | `articles.py` | Yes | - | 50K |
| `read_article` | No | Yes | `articles.py` | Yes | - | 50K |
| `list_memories` | No | Yes | `memory.py` | Yes | - | 50K |
| `glob` | No | Yes | `files.py` | Yes | - | 50K |
| `read_file` | No | Yes | `files.py` | Yes | - | 80K |
| `grep` | No | Yes | `files.py` | Yes | - | 50K |
| `web_search` | No | Yes | `web.py` | Yes | 3 | 50K |
| `web_fetch` | No | Yes | `web.py` | Yes | 3 | 50K |
| `run_shell_command` | No | No | `shell.py` | Yes | - | 30K |

**DEFERRED tools (12)** — discovered via `search_tools`:

| Tool | Approval | Read-Only | File | Concurrent | Retries | Max Size |
|------|----------|-----------|------|------------|---------|----------|
| `write_file` | Yes | No | `files.py` | No | 1 | 50K |
| `patch` | Yes | No | `files.py` | No | 1 | 50K |
| `save_article` | Yes | No | `articles.py` | Yes | 1 | 50K |
| `start_background_task` | Yes | No | `task_control.py` | Yes | - | 50K |
| `check_task_status` | No | Yes | `task_control.py` | Yes | - | 50K |
| `cancel_background_task` | No | No | `task_control.py` | Yes | - | 50K |
| `list_background_tasks` | No | Yes | `task_control.py` | Yes | - | 50K |
| `execute_code` | No | No | `execute_code.py` | No | - | 50K |
| `research_web` | No | No | `agents.py` | Yes | - | 50K |
| `analyze_knowledge` | No | No | `agents.py` | Yes | - | 50K |
| `reason_about` | No | No | `agents.py` | Yes | - | 50K |
| `session_search` | No | Yes | `session_search.py` | Yes | - | 50K |

**Integration tools (conditional, all DEFERRED):**

| Integration | Tools | Condition |
|-------------|-------|-----------|
| Obsidian | `list_notes`, `search_notes`, `read_note` | `obsidian_vault_path` set |
| Gmail | `list_gmail_emails`, `search_gmail_emails`, `create_gmail_draft` (approval) | `google_credentials_path` set |
| Calendar | `list_calendar_events`, `search_calendar_events` | `google_credentials_path` set |
| Drive | `search_drive_files`, `read_drive_file` | `google_credentials_path` set |

**Total:** 26 native + 10 integration = 36 tools when fully configured.

### 5.3 Tool Discovery (Deferred Tools)

Mechanism: pydantic-ai SDK's `ToolSearchToolset` (auto-added by Agent). Per-tool `defer_loading` set at `_native_toolset.py:102` based on visibility enum.

**Category awareness prompt** injected per-turn via `add_category_awareness_prompt()` at `tools/_deferred_prompt.py`. Groups deferred tools into categories with representative names:
- "file editing (write_file, patch)"
- "memory management (save_article)"
- "background tasks (start_background_task)"
- "code execution (execute_code)"
- "sub-agents (research_web, analyze_knowledge, reason_about)"
- Integration categories: Gmail, Calendar, Drive, Obsidian.

Discovery path: user prompt -> model calls `search_tools` -> SDK lists deferred tools + categories -> model discovers and calls desired tool.

### 5.4 Approval Flow

**Request phase:** Tool marked `requires_approval=True` + `defer_loading=True`. Agent calls tool, SDK produces `DeferredToolRequests` output.

**Collection phase** (`_collect_deferred_tool_approvals()`, `orchestrate.py:171-225`):
1. Decode args via `decode_tool_args(call.args)`.
2. Resolve subject via `resolve_approval_subject(tool_name, args)`.
3. Check auto-approval via `is_auto_approved(subject, deps)`.
4. If not auto-approved: prompt user via `frontend.prompt_approval(subject)`.

**User prompt UX** (`display/_core.py:462-482`):
```
[Subject display panel]
Allow? [y=once  a=session  n=deny]
```
- `y`: Execute once.
- `a`: Remember rule in `ctx.deps.session.session_approval_rules`.
- `n`: Deny and continue without tool.

**Resume phase:** Approved tools execute via `_execute_stream_segment()` with `deferred_tool_results`. Filter narrows to `resume_tool_names + ALWAYS-visible` tools. Loop until output is `str` (no more deferred requests).

**Approval subjects (4 kinds):**

| Kind | Value | Scoping | Tools |
|------|-------|---------|-------|
| SHELL | Utility name | First token of command | `run_shell_command` |
| PATH | Parent directory | Bare parent of path | `write_file`, `patch` |
| DOMAIN | Hostname | Parsed URL hostname | `web_fetch` |
| TOOL | Tool name | Exact tool name | All others + MCP |

### 5.5 Tool Output / Error Structure

All tools return `ToolReturn` (pydantic-ai type) via helpers in `tools/tool_io.py`:

| Function | Purpose | Behavior |
|----------|---------|----------|
| `tool_output(display, ctx=ctx, **metadata)` | Standard result | Auto-persists if > `max_result_size`; returns preview placeholder |
| `tool_error(message, ctx=ctx)` | Terminal error (no retry) | Sets `metadata.error=True`; model sees error |
| `tool_output_raw(display, **metadata)` | Helper without RunContext | No size check/persist |
| `handle_google_api_error(label, e, ctx=ctx)` | Google API errors | 401 -> terminal; 403/404/429/5xx -> `ModelRetry` |

Error routing patterns:
- `raise ModelRetry(msg)` — transient errors (timeouts, 429, 500s, network).
- `raise ApprovalRequired(metadata)` — deferred approval.
- `return tool_error(msg, ctx=ctx)` — terminal errors (validation, 401, path escape).

### 5.6 Tool Pattern Compliance

Verified across all tool files:

- **RunContext injection:** All public tool functions accept `ctx: RunContext[CoDeps]`.
- **Return type:** All return `ToolReturn` via `tool_output()`/`tool_error()`. No raw `str`, `dict`, or `list` returns.
- **No module-level mutable state:** All module-level constants are immutable (`_TRACER`, `_MAX_EDIT_BYTES`, regex patterns).
- **No direct settings imports:** Only 2 constant imports (`ADC_PATH`, `NOREASON_SETTINGS`) — not settings access.
- **Concurrent safety flags:** Read-only always concurrent-safe. Write operations (`write_file`, `patch`, `execute_code`) sequential.

### 5.7 Tool Gaps

No critical issues found. Minor notes:

- Session approval rules not persisted cross-session (by design — security boundary).
- Deferred tools not visible without search (mitigated by category awareness prompt; tested in `test_tool_registry.py`).
- No per-tool timeout enforcement except shell/execute_code (HTTP clients have built-in timeouts).
- Agent depth limit `MAX_AGENT_DEPTH=2` prevents 3-level delegation chains (documented edge case).

---

## 6. Memory System

### 6.1 Storage Format

Memory files live at `~/.co-cli/memory/*.md`. YAML frontmatter parsed by `knowledge/_frontmatter.py:parse_frontmatter()`. File layout rendered by `render_memory_file()` at line 175.

```yaml
---
id: <UUID4 or int>
created: <ISO8601>
kind: "memory" | "article"
type: "user" | "feedback" | "project" | "reference" | null
tags: [list of strings]
description: <string, max 200 chars, no newlines>
updated: <ISO8601, added by update/append>
related: [slug list]
always_on: bool
name: <string, write-only metadata for slug generation>
origin_url: <string, articles only, dedup key>
---

<Markdown body>
```

Filenames: `{slug}-{uuid[:8]}.md` (`tools/memory.py:400-403`). Slug: max 50 chars, `[^a-z0-9]+` -> `-`.

**MemoryEntry dataclass** (`recall.py:24-39`): `id`, `path`, `content`, `tags`, `created`, `updated`, `related`, `kind`, `artifact_type`, `always_on`, `description`, `type`.

### 6.2 CRUD Operations

**Create — `save_memory()`** (`tools/memory.py:376-450`):
- Always creates NEW file (UUID suffix, no dedup check).
- Tags lowercased. Immediately re-indexes in `knowledge_store` if available.
- OTel span `"co.memory.save"` with `"memory.type"` attribute.
- Registered only on the `_memory_extractor_agent` (not on main agent).

**Read — `load_memories()`** (`recall.py:47-111`):
- Glob `*.md`, parse YAML frontmatter, validate.
- Early-exit per file on kind or tag mismatch.
- Skips malformed files with `logger.warning`.
- Returns empty list if directory missing.

**Search — `grep_recall()`** (`tools/memory.py:50-66`):
- Case-insensitive substring match on content and tags.
- Sorts by recency (updated or created, newest first).

**Always-on — `load_always_on_memories()`** (`recall.py:122-131`):
- Filters to `always_on=True`, hard cap of 5 entries.

**FTS/BM25 — `KnowledgeStore.search(source="memory")`** (`_store.py:582-634`):
- Uses `docs_fts` for memory source (no chunks).
- Returns `SearchResult` with BM25 ranking.

**Update — `update_memory()`** (`tools/memory.py:453-562`):
- Find file by stem == slug. Resource lock via `ctx.deps.resource_locks.try_acquire(slug)`.
- Requires exact match (once), expandtabs-normalized.
- Atomic write via tempfile + `os.replace`. Refreshes `updated` timestamp.
- Re-indexes in `knowledge_store`.
- Not registered on main agent (per spec line 197).

**Delete — `/memory forget`** (`_commands.py:1125-1169`):
- Uses `grep_recall` filtering. Preview required, explicit `y` confirmation.
- Calls `m.path.unlink()` ONLY. Does NOT clean `search.db`.

**Append — `append_memory()`** (`tools/memory.py:565-633`):
- Append with `\n` prefix. Resource lock. Atomic write via tempfile.
- Refreshes `updated` timestamp. Re-indexes.
- Not registered on main agent.

### 6.3 Memory Extraction Pipeline

**Signal detection:** Extractor prompt at `memory/prompts/memory_extractor.md`. Four signal types: `user`, `feedback`, `project`, `reference`. Max 3 `save_memory` calls per window.

**Pipeline:**

1. **Trigger:** `_finalize_turn()` (`main.py:99-180`) after each clean turn.
   - Skipped if `history_compaction_applied=True` (`main.py:119-120`).
   - Cadence gate: `extract_every_n_turns` (default 3).

2. **Delta window:** `_build_window(messages)` (`_extractor.py:39-94`).
   - Formats last 10 user text messages + last 10 tool calls/results.
   - Skips Read-tool output and long content >1000 chars.

3. **Extraction:** `fire_and_forget_extraction(delta, deps, frontend, cursor_start)` (`_extractor.py:157-174`).
   - Guard: skip if task already running.
   - Spawns asyncio task `"memory_extraction"`.

4. **Async worker:** `_run_extraction_async()` (`_extractor.py:114-145`).
   - Runs `_memory_extractor_agent` with `save_memory` tool.
   - OTel span `"co.memory.extraction"`.
   - Cursor advance ONLY on success (line 140). Failed extractions retry next turn.

5. **Shutdown:** `drain_pending_extraction(timeout_ms=10_000)` (`_extractor.py:177-195`).
   - Called at REPL exit. Cancels on timeout.

### 6.4 Memory Recall and Injection

**Per-turn recall** — history processor 4 (`inject_opening_context`, `_history.py:627-687`):
- Every new user turn, calls `_recall_for_context(ctx, user_msg, max_results=3)`.
- Uses `knowledge_store.search(source="memory", kind="memory")` (FTS5 BM25).
- Falls back to `grep_recall()` if `knowledge_store is None`.
- One-hop `related` expansion: resolves `related` field slugs, capped at 5 total.
- Injected as `SystemPromptPart` with "Relevant memories:" header, capped to `memory_injection_max_chars` (default 2000).

**Standing context (always-on)** — dynamic instruction layer (`agent/_instructions.py:24-31`):
- `load_always_on_memories(memory_dir)` loads max 5 entries.
- Injected as `"Standing context:\n{text}"`.

### 6.5 Tool Registration

| Tool | Registered On | Visibility |
|------|---------------|------------|
| `search_memories` | Main agent | ALWAYS |
| `list_memories` | Main agent | ALWAYS |
| `save_memory` | Extractor agent only | N/A |
| `update_memory` | Not registered | N/A |
| `append_memory` | Not registered | N/A |

REPL commands: `/memory list`, `/memory count`, `/memory forget`. Shared filters: `--older-than N`, `--type X`, `--kind X`.

### 6.6 Memory Gaps

**B2 — `/memory forget` does not clean `search.db` (BLOCKING):**
`_commands.py:1169` calls `m.path.unlink()` only. Stale FTS entries persist in `search.db` until next `sync_dir` run. User forgets a memory, searches again, and the ghost entry still appears. Spec line 20 acknowledges this. Fix: add `knowledge_store.remove_entry(path)` after `unlink()`.

**H1 — Extractor produces duplicate memories (HIGH):**
Same signal in overlapping turn windows creates multiple near-identical files. `save_memory` always creates a new file (UUID suffix) with no dedup check. Spec line 20 acknowledges this. Fix: add content-hash dedup check before writing.

**H2 — `always_on` cap enforced only at read (HIGH):**
`load_always_on_memories()` at `recall.py:131` caps to 5 entries. But `save_memory` has no check — user can set 100 memories as `always_on: true`. Only 5 injected silently, rest ignored without feedback. Fix: warn at write time if `always_on` count exceeds 5.

**Minor gaps:**
- `artifact_type` field in MemoryEntry parsed from frontmatter but not documented in spec.
- `related` one-hop expansion depth (5 entries) not in spec field table.
- Storage full: no explicit handling — relies on OS filesystem errors. `file_path.write_text()` raises; caught in extractor, cursor not advanced (retried next turn).
- Corrupt YAML frontmatter: `parse_frontmatter` returns `{}, content` on error. Load behavior: logged warning, file skipped, never crashes.

---

## 7. Knowledge System

### 7.1 Architecture

Single SQLite DB at `~/.co-cli/co-cli-search.db` (`SEARCH_DB`).

**Schema** (`_store.py:117-205`):
- `docs` + `docs_fts` (document-level, retained for schema compat; memory queries use this).
- `chunks` + `chunks_fts` (chunk-level BM25, active search target for articles/library).
- `embedding_cache` (keyed by provider+model+content_hash).
- `chunks_vec_{dims}` / `docs_vec_{dims}` (sqlite-vec virtual tables, hybrid mode only).
- FTS5 with porter tokenizer + unicode61.
- AI/AD/AU triggers keep FTS in sync.

### 7.2 KnowledgeStore Key Methods

| Method | Role | Location |
|--------|------|----------|
| `index()` | Upsert doc + embeddings | `_store.py:420-496` |
| `index_chunks()` | Write chunks atomically, embed each in hybrid | `_store.py:497-558` |
| `search()` | Route to FTS or hybrid backend | `_store.py:582-634` |
| `_fts_search()` | BM25 on `chunks_fts`, doc-level dedup | `_store.py:763-855` |
| `_hybrid_search()` | FTS + vec, RRF merge (k=60) | `_store.py:636-696` |
| `_build_fts_query()` | Tokenize, filter stopwords, quote, AND-join | `_store.py:1303-1320` |
| `_hybrid_merge()` | RRF keyed by (path, chunk_index), accumulate doc scores | `_store.py:1088-1139` |
| `sync_dir()` | Incremental scan via SHA256 hash, skip unchanged | `_store.py:1332-1411` |
| `remove_stale()` | Delete entries for files no longer on disk | `_store.py:1436-1488` |
| `rebuild()` | Wipe source + re-sync | `_store.py:1490-1523` |
| `_embed_cached()` | Cache lookups + generate on miss | `_store.py:1141-1172` |
| `_rerank_results()` | Dispatch to TEI/LLM reranker | `_store.py:1178-1197` |

### 7.3 Articles vs Memories

| Aspect | Articles | Memories |
|--------|----------|----------|
| Path | `library_dir/*.md` | `memory_dir/*.md` |
| Dedup | `origin_url` exact match | File-level via UUID, no dedup |
| Consolidation | Updates existing on URL match (tags merged, content replaced) | Never (extractor fire-and-forget) |
| Decay | `decay_protected: true` | Subject to `recall_half_life_days` |
| Indexing | `chunks_fts` (BM25 on chunks) + optional `chunks_vec` | `docs_fts` only (BM25 on whole doc) |
| Search entry | `search_knowledge(source="library")` or `search_articles()` | `search_memories(source="memory")` |

### 7.4 Indexing Pipeline

**Write-time indexing (`save_article`)** at `articles.py`:
1. Dedup by URL glob + exact match.
2. Duplicate found -> consolidate (tags merged, content replaced).
3. New -> write `.md` file.
4. Always trigger: `knowledge_store.index()` + `index_chunks()`.

**Chunking** (`knowledge/_chunker.py:105-156`):
- Estimates tokens as `len(text) / 4`.
- Splits at paragraph > line > character boundaries.
- Prepends `overlap * 4` characters of prior chunk.
- Returns `Chunk(index, content, start_line, end_line)`.
- Defaults: `chunk_size=600` tokens, `chunk_overlap=80` tokens.

**Bootstrap sync:** `sync_dir("library", library_dir)` at startup. Incremental via SHA256 hash — only re-indexes changed files. `remove_stale()` called after indexing.

**Obsidian lazy sync:** Triggered on first `search_knowledge()` call. `sync_dir("obsidian", vault_path)`.

### 7.5 Search Backend Selection

```
Requested = "hybrid" + embedding_provider != "none"  ->  "hybrid"
Otherwise  ->  "fts5" or "grep" as configured
Degrades gracefully at bootstrap if hybrid/fts5 unavailable
```

### 7.6 FTS Query Building

`_build_fts_query()` at `_store.py:1303-1320`:
- Lowercases, strips non-word/non-hyphen chars per token.
- Filters 50+ hardcoded stopwords (`STOPWORDS` set, line 47-115).
- Filters single-char tokens.
- Returns `None` if no tokens survive (immediate empty result).
- Quotes terms, AND-joins: `"term1" AND "term2" AND ...`.

### 7.7 Hybrid Merge (RRF)

`_hybrid_merge()` at `_store.py:1088-1139`:
- Chunk-level RRF: `score = sum(1/(k+i+1))` for each occurrence.
- Doc-level accumulation: sum chunk RRF scores per path.
- Winning chunk (highest chunk score) carries snippet/start_line/end_line to doc result.
- Returns doc-level `SearchResult` list sorted by total score.

### 7.8 Reranking

Priority: TEI `/rerank` endpoint -> LLM listwise reranker -> none.

**TEI cross-encoder** (`_tei_rerank`, lines 1274-1301):
- POST to `{cross_encoder_url}/rerank` with `{"query": query, "texts": texts}`.
- Batches by `tei_rerank_batch_size` (default 50).
- Re-scores candidates, sorts descending.

**LLM reranker** (`_llm_rerank`, lines 1250-1268):
- Builds prompt with numbered docs.
- Calls `_call_reranker_llm()` expecting 1-based ranked indices.
- Maps indices to scores: `scores[idx] = 1.0 - rank / n`.

**Fetch texts** (`_fetch_reranker_texts`, lines 1199-1239):
- Chunk-level: fetches `chunk.content`.
- Doc-level (memory): fetches `docs.content` preamble (first 200 chars).

**Error handling:** Silently falls back to unranked on any exception (line 1195-1197). Logs warning.

### 7.9 Search Tools and Paths

| Tool | Backend | Source | Kind | Chunk-Level |
|------|---------|--------|------|-------------|
| `search_knowledge()` | FTS or hybrid | library/obsidian/drive | any | Yes (`chunks_fts`) |
| `search_articles()` | FTS or grep | library only | article | Yes (`chunks_fts`) |
| `search_memories()` | FTS only | memory only | memory | No (`docs_fts`) |
| `session_search()` | SessionIndex (separate DB) | sessions | N/A | N/A |

**Fallback chains:**
- `search_articles()`: FTS -> grep.
- `search_knowledge()`: grep fallback when store is None; obsidian/drive require FTS (no grep fallback).
- `search_memories()`: error if store is None (no fallback in the search tool path; `_recall_for_context` has its own grep fallback).

### 7.10 Knowledge Gaps

**B3 — SearchResult.tags type mismatch (BLOCKING):**
`SearchResult.tags` is a space-separated `str` (from SQL). `MemoryEntry.tags` is `list[str]` (from YAML frontmatter). This creates serialization friction at the boundary between search results and memory entries. Could cause tag-filter mismatches or display inconsistencies depending on which path the caller uses.

**H7 — Stopword-only queries return empty (HIGH):**
`_build_fts_query()` returns `None` when all tokens are stopwords. `search()` returns `[]`. No "try a different query" hint to the user. Queries like "the" or "is it" produce hard-empty results.

**Minor gaps:**
- Stale index after article forget: `/memory forget --kind article` unlinks file but leaves stale `search.db` entries until next `sync_dir` (same root cause as B2).
- No concurrent write safety on `search.db`: multi-agent scenarios could corrupt index. Explicitly deferred in spec as non-goal.
- Chunk crowding: long docs with N chunks could suppress other results. Mitigated by 20x overfetch + doc-level dedup.
- RRF merges FTS and vector scores without re-weighting (standard approach, accepted).
- `sync_dir()` not atomic across whole directory; per-file commits are atomic.
- Vector score normalization: distance [0,2] -> score [0,1]; BM25 rank [-inf,0] -> score (0,1]. RRF absorbs this at chunk level.

---

## 8. Consolidated Gap Table

### Blocking

| ID | Area | Gap | Impact | Fix Complexity | Location |
|----|------|-----|--------|----------------|----------|
| B1 | Config | `GITHUB_TOKEN_BINLECODE` hardcoded env var | MCP GitHub integration locked to personal env var name; other users get no token silently | Low — rename env var or move to Settings | `bootstrap/core.py:38` |
| B2 | Memory | `/memory forget` does not clean `search.db` | Forgotten memories still appear in search results until `sync_dir` | Low — add `knowledge_store.remove_entry(path)` after `unlink()` | `_commands.py:1169` |
| B3 | Knowledge | `SearchResult.tags` is `str` vs `MemoryEntry.tags` is `list[str]` | Tag-filter mismatches, display inconsistencies at search/memory boundary | Medium — normalize `SearchResult.tags` to `list[str]` and update all consumers | `_store.py` vs `recall.py:30` |
| B4 | Context | Compaction circuit breaker never resets | After 3 summarizer failures, all compaction degrades to static markers for entire session; no recovery | Low — reset counter on success or on new user turn | `_history.py:469-493`, `deps.py:132` |

### High

| ID | Area | Gap | Impact | Fix Complexity | Location |
|----|------|-----|--------|----------------|----------|
| H1 | Memory | Duplicate memory extraction | Same signal in consecutive windows produces multiple files; memory bloat over time | Medium — content-hash dedup check in `save_memory` | `_extractor.py`, spec line 20 |
| H2 | Memory | `always_on` cap enforced only at read | Users can write unlimited `always_on` memories; only 5 injected silently | Low — warn at write time | `recall.py:131` |
| H3 | Config | No env var for `memory.extract_every_n_turns` | Cannot tune extraction cadence via env | Low — add env var mapping | `_memory.py` |
| H4 | Config | No env var for `llm.reasoning` / `llm.noreason` | Model inference tuning requires JSON file edit | Medium — design env var schema for nested model objects | `_llm.py` |
| H5 | Context | 4 chars/token estimation | Compaction threshold inaccuracy; mitigated by provider-reported tokens | Medium — integrate model-specific tokenizer or better heuristic | `summarization.py:36` |
| H6 | Context | 5MB+ transcript resume silent truncation | User loses earlier context without UI notification | Low — surface message via frontend on resume | `transcript.py:173-178` |
| H7 | Knowledge | Stopword-only queries return empty | No hint to user; search appears broken | Low — return user-friendly message | `_store.py:1318` |
| H8 | Bootstrap | No aggregate MCP failure warning | All servers fail but no top-level banner | Low — check `if all_failed` after loop | `bootstrap/core.py:254-269` |

### Low / Accepted

| ID | Area | Gap | Status |
|----|------|-----|--------|
| L1 | Config | Config mutations not persisted | By design |
| L2 | Config | No project-level settings override | Single-user tool |
| L3 | Tools | Session approval rules not cross-session | Security boundary |
| L4 | Tools | Deferred tools need `search_tools` | Mitigated by category prompt |
| L5 | Tools | No per-tool timeout (except shell) | HTTP clients have built-in timeouts |
| L6 | Knowledge | No concurrent write safety | Explicitly deferred |
| L7 | Knowledge | Chunk crowding from long docs | Mitigated by overfetch + dedup |
| L8 | Knowledge | RRF score scale differences | Standard approach |
| L9 | Context | No concurrent-instance safety | Documented non-goal |
| L10 | Bootstrap | Missing settings.json -> silent defaults | Expected for first-run |
| L11 | Config | `observability.redact_patterns` no env var | Rare override need |
| L12 | Memory | `artifact_type` field undocumented in spec | Parsed but no consumer issues |
| L13 | Knowledge | `sync_dir()` not atomic across directory | Per-file commits atomic |

---

## 9. Recommended Fix Order

Priority ordered by UAT impact and fix complexity:

1. **B2** — Add `knowledge_store.remove_entry(path)` to `/memory forget` after `unlink()`. Small change, high user-trust impact. Eliminates ghost search results.

2. **B4** — Reset `compaction_failure_count` on successful compaction or on new user turn. One-line fix in `_history.py`. Eliminates permanent degradation trap.

3. **B1** — Replace `GITHUB_TOKEN_BINLECODE` with `CO_CLI_GITHUB_TOKEN` or `settings.integrations.github_token`. Config-level change. Unblocks GitHub MCP for any user.

4. **B3** — Normalize `SearchResult.tags` to `list[str]` (or ensure all callers handle both). Requires tracing consumers of `SearchResult` across `_store.py`, `articles.py`, `memory.py`, and display code.

5. **H1** — Add content-hash dedup check in `save_memory` before writing. Prevents exact duplicate accumulation.

6. **H2** — Warn at write time if `always_on` count exceeds 5. User feedback improvement.

7. **H7** — Return "no searchable terms in query" message instead of empty `[]` for stopword-only queries.

8. **H6** — Surface "resumed from compacted checkpoint — earlier context omitted" via frontend on 5MB+ transcript load.

9. **H8** — Add aggregate MCP failure check after server connection loop.

10. **H3** — Add env var mapping for `memory.extract_every_n_turns`.

11. **H5** — Improve token estimation (lower priority — provider-reported tokens mitigate this in practice).

12. **H4** — Design env var schema for `llm.reasoning` / `llm.noreason` sub-objects.
