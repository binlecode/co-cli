# Configuration

## 1. Functional Architecture

```mermaid
graph TD
    ENV[Environment variables\nos.environ + .env file]
    FILE[~/.co-cli/settings.json]
    DEFAULTS[Pydantic field defaults]

    LOAD[load_config\ncore.py]
    VAL[fill_from_env\nmodel_validator]
    SETTINGS[Settings\nnested sub-models]

    SINGLETON[get_settings\nlazy singleton]
    DEPS[CoDeps.config\nread-only after bootstrap]

    ENV --> LOAD
    FILE --> LOAD
    DEFAULTS --> LOAD
    LOAD --> VAL
    VAL --> SETTINGS
    SETTINGS --> SINGLETON
    SINGLETON --> DEPS
```

| Component | Module | Role |
|-----------|--------|------|
| `Settings` | `core.py` | Top-level Pydantic model; owns flat fields + all nested sub-models |
| `fill_from_env` | `core.py` | `model_validator(mode="before")` — maps env vars into nested dict before validation |
| `load_config()` | `core.py` | Loads `settings.json`, merges `.env`, applies env; returns `Settings` |
| `get_settings()` | `core.py` | Lazy module-level singleton — calls `load_config()` on first access |
| `LlmSettings` | `llm.py` | Provider, model, inference defaults; `reasoning_model_settings()`, `noreason_model_settings()`, `validate_config()` as instance methods |
| `_LLM_SETTINGS` | `llm.py` | Provider→model→mode canonical inference knobs used by `LlmSettings._inference()` |
| `DEFAULT_LLM_MODELS` | `llm.py` | Per-provider default model id (full id with variant tag) — used when `llm.model` is unset |
| `MemorySettings` | `memory.py` | Search backend, embedding, chunking, lifecycle, and recall settings |
| `CompactionSettings` | `compaction.py` | Context compaction trigger ratios and anti-thrash knobs |
| `WebSettings` | `web.py` | Domain allowlist/blocklist and HTTP retry policy |
| `ShellSettings` | `shell.py` | Shell timeout limit and auto-approval safe command list |
| `ObservabilitySettings` | `observability.py` | Log level, app-log + spans-log rotation, span-attribute redaction patterns |
| `SkillsSettings` | `skills.py` | Skill review automation knobs (enable + per-N-tool-call nudge interval) |
| `MCPServerSettings` | `mcp.py` | Per-server transport config (stdio or HTTP) |

`Settings` is constructed once per session by `create_deps()` via `get_settings()`, then
`copy.deepcopy`-ed to prevent cross-session mutation. After bootstrap it is read-only.


## 2. Core Logic

### Load pipeline

```
load_config(path?, env?)
  1. Load ~/.co-cli/.env via dotenv_values (no os.environ mutation)
  2. Load settings.json — json.load → raw dict (empty dict if absent)
  3. Pre-flight validate raw file data: Settings.model_validate(data, env={})
       → raises ValueError on invalid file content (masks env overrides cleanly)
  4. Resolve env context: _env (test) > os.environ (shell) > dot_env_vars (.env)
  5. Settings.model_validate(data, context={"env": env_context})
       → fill_from_env runs as model_validator(mode="before"):
           flat env vars injected directly into data dict
           nested env vars injected into data["llm"], data["web"], etc.
           each sub-model owns its ENV_MAP (LLM_ENV_MAP, MEMORY_ENV_MAP, etc.)
           provider-aware API key resolved via resolve_api_key_from_env()
           CO_MCP_SERVERS JSON decoded into data["mcp_servers"]
  6. _validate_personality(resolved.personality) — prints warnings, does not raise
```

Precedence: `_env` (explicit/test) > `os.environ` (shell) > `.env` file > `settings.json` > field defaults.

### Path constants

All user-global paths resolve from `USER_DIR`, which is read once at module import time:

```
USER_DIR          = CO_HOME env var | ~/.co-cli
SETTINGS_FILE     = USER_DIR / settings.json
SEARCH_DB         = USER_DIR / co-cli-search.db
LOGS_DIR          = USER_DIR / logs
MEMORY_DIR        = USER_DIR / memory
SESSIONS_DIR      = USER_DIR / sessions
TOOL_RESULTS_DIR  = USER_DIR / tool-results
GOOGLE_TOKEN_PATH = USER_DIR / google_token.json
```

`_ensure_dirs()` creates these on first call to `get_settings()`.

### LLM inference model settings

`_LLM_SETTINGS` is the central table of per-provider, per-model inference knobs —
the canonical source of truth, not user-overridable. `model_key` is derived from
`llm.model` by splitting on `:` (`"qwen3.6:35b-a3b-agentic"` → `"qwen3.6"`).

```
LlmSettings._inference(mode):
  return _LLM_SETTINGS[provider][model_key][mode]   # or {} if absent
```

Per-provider default model id (used when `llm.model` is unset) lives separately in
`DEFAULT_LLM_MODELS[provider]` — full id including variant tag, since
`_LLM_SETTINGS` keys are variant-stripped base names.

Defined entries:

| Provider | Model key | Modes | Key noreason knob |
|----------|-----------|-------|-------------------|
| `ollama` | `qwen3.6` | reasoning, noreason | `extra_body: {think: false, reasoning_effort: "none"}` |
| `gemini` | `gemini-3-flash-preview` | reasoning, noreason | `thinking_config: {thinking_level: "MINIMAL"}` |
| `gemini` | `gemini-3.1-pro-preview` | reasoning, noreason | `thinking_config: {thinking_level: "LOW"}` (reasoning pins `HIGH`; pro rejects `MINIMAL`) |
| `gemini` | `gemini-2.5-flash` | noreason only | `thinking_config: {thinking_budget: 0}` |

`LlmSettings.validate_config()` (no IO) enforces: Gemini API key present when provider is gemini,
model key in `_LLM_SETTINGS`, and model key has a `reasoning` entry (noreason-only models cannot
be the main agent model). Empty `llm.model` is auto-resolved to `DEFAULT_LLM_MODELS[provider]`
by a pydantic `model_validator`, so the no-model case is handled before validation runs.

`LlmSettings.reasoning_model_settings()` → `ModelSettings` for the main agent.
`LlmSettings.noreason_model_settings()` → `ModelSettings` (Ollama) or `GoogleModelSettings` (Gemini)
for functional calls (compaction, memory extraction, dream merge) via `llm_call()`.

### Ollama context window probe

`probe_ollama_model(host, model)` posts to `/api/show`, parses `parameters.num_ctx`. Called
during `create_deps()` before `build_model()`. `max_context_tokens` is the contract pivot:

- **Floor check**: probed Modelfile `num_ctx` must be `>= max_context_tokens`; fails fast otherwise.
- **Ceiling check**: static `num_ctx` in `_LLM_SETTINGS` (injected per-request via
  `extra_body["options"]["num_ctx"]`) must be `<= max_context_tokens`; fails fast otherwise.

The two checks use `max_context_tokens` as the shared reference and do not compare against each other.
`deps.model_max_context_tokens` is always `config.llm.max_context_tokens` — the probed value is only used for the
floor validation, not as a dynamic injection value.

Gemini: no probe; `deps.model_max_context_tokens = config.llm.max_context_tokens` (ceiling used as-is).

### Profile-derived context budget

When `max_context_tokens` is not set explicitly, its default is resolved from the model
profile via `resolve_model_profile(config.llm)` (`co_cli/config/llm.py`): Ollama →
`ModelProfile.WEAK_LOCAL` → `65536` (the hard 64k baseline); any other provider (Gemini) →
`ModelProfile.FRONTIER` → `524288` (half the provider's 1M max window — `compaction_ratio`
clamps off it). An explicit `max_context_tokens` always overrides the profile default. The
Ollama floor/ceiling contract above is unchanged.


## 3. Config

### Top-level (flat)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `personality` | `CO_PERSONALITY` | `"tars"` | Active personality name; must match a bundled soul directory |
| `theme` | `CO_THEME` | `"light"` | TUI color theme |
| `reasoning_display` | `CO_REASONING_DISPLAY` | `"summary"` | Reasoning trace display: `off`, `summary`, `full` |
| `tool_retries` | `CO_TOOL_RETRIES` | `3` | Max retries on tool errors |
| `doom_loop_threshold` | `CO_DOOM_LOOP_THRESHOLD` | `3` | Consecutive identical tool calls before agent is halted (2–10) |
| `max_reflections` | `CO_MAX_REFLECTIONS` | `3` | Max agent self-reflection passes per turn (1–10) |
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | `None` | Brave Search API key |
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `None` | Path to an existing Google authorized-user token JSON (read-only; `co google auth` writes the default `GOOGLE_TOKEN_PATH`) |
| `google_client_secret_path` | `GOOGLE_CLIENT_SECRET_PATH` | `~/env-secrets/google_client_secret.json` | Path to the user's OAuth Desktop-app client JSON, consumed by `co google auth` |
| `memory_path` | `CO_MEMORY_PATH` | `~/.co-cli/memory` | Override for the memory items directory |
| `workspace_path` | `CO_WORKSPACE_PATH` | `None` | Write/cwd anchor: where `file_write`/`file_patch` land and the base for relative paths |
| `file_search_paths` | — | `[]` | Read-only reference roots for `file_read`/`file_search` (e.g. a notes vault). Empty → `[workspace_dir]`; non-empty is authoritative and total (no implicit workspace append). Writes never widen to these roots |

### LLM (`llm.*`)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm.provider` | `CO_LLM_PROVIDER` | `"ollama"` | Provider: `ollama` or `gemini` |
| `llm.host` | `CO_LLM_HOST` | `"http://localhost:11433"` | Ollama server base URL (multi-instance router; `11434` bypasses to primary Ollama) |
| `llm.model` | `CO_LLM_MODEL` | `"qwen3.6:35b-a3b-agentic"` (Ollama default) | Single model name for all tasks; falls back to `DEFAULT_LLM_MODELS[provider]` when unset |
| `llm.judge_model` | — | `None` | Optional pinned-distinct judge model name. Used by phase-1 judge cases (W1.A coherence, W4.A skill body) AND all phase-2 behavioral evals. Inherits provider/host/api_key from `llm.*`; only the model name differs. When unset, the judge falls back to `llm.model` and `CaseResult.reason` carries `[judge_model_same_as_agent]` — a single-model regression can mask itself in the judge. Pick a model with comparable capability but a different family/training data than `model` when possible (e.g. `qwen` agent + `llama` judge) so single-family regressions don't mask. |
| `llm.max_context_tokens` | — | profile-derived | Context budget; default resolves from the model profile (`weak_local`/Ollama → `65536`; `frontier`/Gemini → `524288`). An explicit value overrides the profile default. Ollama caps it by the probed `num_ctx`. |
| `llm.max_model_requests_per_turn` | `CO_LLM_MAX_MODEL_REQUESTS_PER_TURN` | `40` | Max LLM calls (ModelResponses) per user turn; `0` disables the cap. Doom-loop circuit breaker, not a work limit — see [core-loop.md](core-loop.md) §1 for the sizing rationale |
| `llm.run_stall_timeout_secs` | `CO_LLM_RUN_STALL_TIMEOUT_SECS` | `120` | Model-generation stall window (seconds, > 0): max wall-time the run waits for model progress before `TurnResult(outcome='error')`. Tunable because local-model latency varies by model/hardware — see [core-loop.md](core-loop.md) §1.4 |
| `llm.api_key` | `GEMINI_API_KEY` (gemini), else `CO_LLM_API_KEY` | `None` | Provider API key |

Inference knobs (temperature, top_p, max_tokens, extra_body, thinking_config) are not
user-configurable — they live in `_LLM_SETTINGS` keyed by provider/model/mode.

### Memory (`memory.*`)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `memory.search_backend` | `CO_MEMORY_SEARCH_BACKEND` | `"hybrid"` | Backend: `grep`, `fts5`, `hybrid` |
| `memory.embedding_provider` | `CO_MEMORY_EMBEDDING_PROVIDER` | `"tei"` | Embedding provider: `ollama`, `gemini`, `tei`, `none` |
| `memory.embedding_model` | `CO_MEMORY_EMBEDDING_MODEL` | `"embeddinggemma"` | Model name for embedding |
| `memory.embedding_dims` | `CO_MEMORY_EMBEDDING_DIMS` | `1024` | Embedding vector dimensions |
| `memory.embed_api_url` | `CO_MEMORY_EMBED_API_URL` | `"http://127.0.0.1:8283"` | TEI embedding server URL |
| `memory.cross_encoder_reranker_url` | `CO_MEMORY_CROSS_ENCODER_RERANKER_URL` | `"http://127.0.0.1:8282"` | TEI cross-encoder reranker URL; `null` to disable |
| `memory.tei_rerank_batch_size` | `CO_MEMORY_TEI_RERANK_BATCH_SIZE` | `50` | Reranker batch size (overridden by TEI `/info` response) |
| `memory.vector_similarity_floor` | `CO_MEMORY_VECTOR_SIMILARITY_FLOOR` | `0.02` | Min vector cosine for a vector-only candidate to enter RRF (lexical hits exempt) |
| `memory.rerank_score_floor` | `CO_MEMORY_RERANK_SCORE_FLOOR` | `0.2` | Min TEI reranker score to keep a reranked hit (unbounded range) |
| `memory.chunk_tokens` | `CO_MEMORY_CHUNK_TOKENS` | `600` | Token size per memory chunk |
| `memory.chunk_overlap_tokens` | `CO_MEMORY_CHUNK_OVERLAP_TOKENS` | `80` | Token overlap between chunks |
| `memory.consolidation_similarity_threshold` | `CO_MEMORY_CONSOLIDATION_SIMILARITY_THRESHOLD` | `0.75` | Token-Jaccard threshold for write-time dedup and daemon merge |
| `memory.review_enabled` | `CO_MEMORY_REVIEW_ENABLED` | `false` | Enable memory-domain reviewer KICKs (turn-boundary + session-end + compaction-snapshot) |
| `memory.profile_synthesis_enabled` | `CO_MEMORY_PROFILE_SYNTHESIS_ENABLED` | `false` | Enable the cross-session `USER.md` profile synthesis housekeeping sub-pass (see dream.md §2.6) |
| `memory.profile_synthesis_lookback_sessions` | `CO_MEMORY_PROFILE_SYNTHESIS_LOOKBACK_SESSIONS` | `10` | Session window width for profile synthesis (`ge=2`); trigger threshold and marker step are the derived `lookback // 2` |

### Compaction (`compaction.*`)

All ratios apply to the token budget returned by `resolve_compaction_budget()`.
Shape invariant: `tail_fraction < compaction_ratio` and `spill_ratio <= compaction_ratio` (both enforced by validator).

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `compaction.compaction_ratio` | `CO_COMPACTION_RATIO` | `0.50` | Proactive trigger fraction; fires when context ≥ this fraction of budget |
| `compaction.tail_fraction` | `CO_COMPACTION_TAIL_FRACTION` | `0.10` | Fraction of budget preserved as tail in compaction; must be < compaction_ratio |
| `compaction.spill_ratio` | `CO_COMPACTION_SPILL_RATIO` | `0.50` | Fraction above which tool returns spill to disk; must be ≤ compaction_ratio |
| `compaction.min_proactive_savings` | `CO_COMPACTION_MIN_PROACTIVE_SAVINGS` | `0.10` | Minimum savings fraction to count a proactive compaction as effective |
| `compaction.proactive_thrash_window` | `CO_COMPACTION_PROACTIVE_THRASH_WINDOW` | `2` | Consecutive low-yield compactions before anti-thrash gate activates |

### Web (`web.*`)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `web.fetch_allowed_domains` | `CO_WEB_FETCH_ALLOWED_DOMAINS` | `[]` | Allowlist of hostnames for web fetch; empty means allow all |
| `web.fetch_blocked_domains` | `CO_WEB_FETCH_BLOCKED_DOMAINS` | `[]` | Blocklist of hostnames; takes precedence over allowlist |
| `web.http_max_retries` | `CO_WEB_HTTP_MAX_RETRIES` | `2` | Max HTTP retries on transient failures |
| `web.http_backoff_base_seconds` | `CO_WEB_HTTP_BACKOFF_BASE_SECONDS` | `1.0` | Retry backoff base (seconds) |
| `web.http_backoff_max_seconds` | `CO_WEB_HTTP_BACKOFF_MAX_SECONDS` | `8.0` | Retry backoff ceiling (seconds) |
| `web.http_jitter_ratio` | `CO_WEB_HTTP_JITTER_RATIO` | `0.2` | Jitter fraction applied to backoff delay |

### Shell (`shell.*`)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `shell.max_timeout_seconds` | `CO_SHELL_MAX_TIMEOUT_SECONDS` | `300` | Max shell command timeout in seconds |
| `shell.safe_commands` | `CO_SHELL_SAFE_COMMANDS` | see below | Comma-separated prefix list for auto-approved commands |
| `shell.yield_window_seconds` | `CO_SHELL_YIELD_WINDOW_SECONDS` | `20` | Seconds a foreground `shell_exec` command may run before it is auto-yielded to a background task; `0` disables auto-yield. Validated below `max_timeout_seconds` |

Default safe commands: `ls`, `tree`, `find`, `fd`, `cat`, `head`, `tail`, `grep`, `rg`, `ag`, `wc`, `sort`, `uniq`, `cut`, `tr`, `jq`, `echo`, `printf`, `pwd`, `whoami`, `hostname`, `uname`, `date`, `env`, `which`, `file`, `stat`, `id`, `du`, `df`, `git status`, `git diff`, `git log`, `git show`, `git branch`, `git tag`, `git blame`.

### Observability (`observability.*`)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `observability.log_level` | `CO_LOG_LEVEL` | `"INFO"` | Min log level for app-log JSONL output (`co-cli.jsonl`) |
| `observability.log_max_size_mb` | `CO_LOG_MAX_SIZE_MB` | `5` | Max app-log file size in MB before rotation |
| `observability.log_backup_count` | `CO_LOG_BACKUP_COUNT` | `3` | Rotated app-log backups to keep |
| `observability.spans_log_max_size_mb` | `CO_SPANS_LOG_MAX_SIZE_MB` | `50` | Max spans-log file size in MB before rotation (defaults higher than app log because span volume is higher) |
| `observability.spans_log_backup_count` | `CO_SPANS_LOG_BACKUP_COUNT` | `5` | Rotated spans-log backups to keep |
| `observability.redact_patterns` | — | see defaults | Regex list applied to span attribute string values (including string-encoded JSON) before write; `[REDACTED]` substitution |

Default redaction patterns: `sk-*` API keys, `Bearer` tokens, `ghp_` GitHub tokens, generic `API_KEY:` patterns, AWS `AKIA*` keys, PEM private key headers.

### Skills (`skills.*`)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `skills.review_enabled` | `CO_SKILLS_REVIEW_ENABLED` | `false` | Enable skill-domain reviewer KICKs at turn-boundary cadence |
| `skills.review_memory_nudge_interval` | `CO_SKILLS_REVIEW_MEMORY_NUDGE_INTERVAL` | `10` | User-turn count between memory-domain KICK triggers |
| `skills.review_skill_nudge_interval` | `CO_SKILLS_REVIEW_SKILL_NUDGE_INTERVAL` | `10` | LLM-iteration count between skill-domain KICK triggers |
| `skills.usage_tracking_enabled` | `CO_SKILLS_USAGE_TRACKING_ENABLED` | `true` | Persist per-skill usage counters, timestamps, and recall_days sidecars |
| `skills.recall_protection_days` | `CO_SKILLS_RECALL_PROTECTION_DAYS` | `30` | Recent-recall window that protects an aged skill from dream-daemon decay |
| `skills.decay_after_days` | `CO_SKILLS_DECAY_AFTER_DAYS` | `90` | Minimum sidecar age before a skill is eligible for dream-daemon decay |
| `skills.consolidation_similarity_threshold` | `CO_SKILLS_CONSOLIDATION_SIMILARITY_THRESHOLD` | `0.75` | Token-Jaccard threshold for dream-daemon skill merge clusters |

### Dream daemon (`dream.*`)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `dream.autostart` | `CO_DREAM_AUTOSTART` | `false` | Auto-spawn the dream daemon on REPL launch (the daemon is shared per CO_HOME and can also be started manually via `/dream start`) |
| `dream.review_timeout_seconds` | `CO_DREAM_REVIEW_TIMEOUT_SECONDS` | `120` | Per-review LLM call timeout; on expiry the worker retries with backoff |
| `dream.retry_backoff_seconds` | `CO_DREAM_RETRY_BACKOFF_SECONDS` | `30` | Sleep between retry attempts after a timeout |
| `dream.max_retry_attempts` | `CO_DREAM_MAX_RETRY_ATTEMPTS` | `3` | After this many timeouts, move the KICK file to `queue/failed/` |
| `dream.tick_interval_seconds` | `CO_DREAM_TICK_INTERVAL_SECONDS` | `5` | Idle-loop tick interval — drives the queue scan **and** the housekeeping-schedule check; only ticks when the queue is empty (range 1–60) |
| `dream.run_interval_hours` | `CO_DREAM_RUN_INTERVAL_HOURS` | `24` | Minimum hours between housekeeping passes (range 1–720; must align to the daily grid — below 24 a factor of 24 (1, 2, 3, 4, 6, 8, 12), above 24 a multiple of 24 (48, 72, …)) |
| `dream.run_start_at` | `CO_DREAM_RUN_START_AT` | `"03:00"` | Preferred local time-of-day boundary for the scheduled housekeeping tick |
| `dream.max_pass_seconds` | `CO_DREAM_MAX_PASS_SECONDS` | `600` | Wall-clock cap on the merge phase of a housekeeping pass (≥ 60); decay runs unconditionally after merge |
| `dream.done_retention_days` | `CO_DREAM_DONE_RETENTION_DAYS` | `7` | Age (days, ≥ 1) past which `queue/done/` and orphaned `snapshots/` files are pruned by housekeeping; `failed/` is never pruned |

### REPL input queue (`repl.*`)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `repl.queue_cap` | `CO_REPL_QUEUE_CAP` | `0` | Max pending mid-turn input-queue items (≥ 0); `0` = unbounded (Phase 1/2 behavior) |
| `repl.drop_policy` | `CO_REPL_DROP_POLICY` | `"oldest"` | When an enqueue would exceed `queue_cap`: `"oldest"` drops the head then appends; `"newest"` rejects the incoming item. Inert at cap `0` |

### MCP servers (`mcp_servers.*`)

| Field | Default | Description |
|-------|---------|-------------|
| `command` | `None` | Executable for stdio transport (e.g. `npx`) |
| `url` | `None` | Remote URL for HTTP transport; mutually exclusive with `command` |
| `args` | `[]` | CLI arguments (stdio only) |
| `connect_timeout_seconds` | `5` | Connection/tool-discovery timeout in seconds (1–60); does not bound tool-call execution |
| `call_timeout_seconds` | `120` | Per-tool-call response timeout in seconds (1–600); mirrors the model-progress stall window (`llm.run_stall_timeout_secs`) |
| `env` | `{}` | Extra env vars for subprocess (stdio only) |
| `approval` | `"ask"` | Tool approval policy: `ask` or `auto` |
| `prefix` | `None` | Optional tool name prefix for this server |

Default shipped server: `context7` (npx stdio, approval `auto`).
`CO_MCP_SERVERS` env var accepts a JSON blob that replaces the entire `mcp_servers` dict.


## 4. Public Interface

### Config loading

| Symbol | Source | Contract |
|--------|--------|----------|
| `Settings` | `co_cli/config/core.py` | Top-level Pydantic model; immutable session config |
| `load_config(path=None, env=None) -> Settings` | `co_cli/config/core.py` | Loads `settings.json` + `.env` + env vars, validates, returns `Settings` |
| `get_settings() -> Settings` | `co_cli/config/core.py` | Lazy module-level singleton; calls `load_config()` on first access; `deepcopy`-ed by `create_deps()` |

### Sub-model settings

| Symbol | Source | Contract |
|--------|--------|----------|
| `LlmSettings` | `co_cli/config/llm.py` | LLM provider + model + inference knobs; exposes `validate_config()`, `reasoning_model_settings()`, `noreason_model_settings()` |
| `MemorySettings` | `co_cli/config/memory.py` | Search backend, embedding, chunking, lifecycle, and recall settings |
| `CompactionSettings` | `co_cli/config/compaction.py` | Compaction trigger ratios; `_validate_shape` enforces `tail_fraction < compaction_ratio` and `spill_ratio ≤ compaction_ratio` |
| `WebSettings` | `co_cli/config/web.py` | Web fetch domain policy and HTTP retry knobs |
| `ShellSettings` | `co_cli/config/shell.py` | Shell timeout and safe-command allowlist |
| `ObservabilitySettings` | `co_cli/config/observability.py` | Log level, rotation, span redaction patterns |
| `SkillsSettings` | `co_cli/config/skills.py` | Skill review automation knobs |
| `DreamSettings` | `co_cli/config/dream.py` | Dream-daemon enable, timeout, retry, and housekeeping cadence knobs |
| `ReplSettings` | `co_cli/config/repl.py` | REPL input-queue bound (`queue_cap`) and drop policy (`drop_policy`) |
| `MCPServerSettings` | `co_cli/config/mcp.py` | Per-server transport config |

### Path constants

| Symbol | Source | Value |
|--------|--------|-------|
| `USER_DIR` | `co_cli/config/core.py` | `CO_HOME` env var or `~/.co-cli/` |
| `SETTINGS_FILE` | `co_cli/config/core.py` | `USER_DIR / settings.json` |
| `SEARCH_DB` | `co_cli/config/core.py` | `USER_DIR / co-cli-search.db` |
| `LOGS_DIR` | `co_cli/config/core.py` | `USER_DIR / logs` (holds `co-cli.jsonl`, `co-cli-spans.jsonl`, and the dream daemon's `co-dream.jsonl` / `co-dream-spans.jsonl`) |
| `MEMORY_DIR` | `co_cli/config/core.py` | `USER_DIR / memory` |
| `SESSIONS_DIR` | `co_cli/config/core.py` | `USER_DIR / sessions` |
| `TOOL_RESULTS_DIR` | `co_cli/config/core.py` | `USER_DIR / tool-results` |
| `GOOGLE_TOKEN_PATH` | `co_cli/config/core.py` | `USER_DIR / google_token.json` |

### LLM constants and model factory

| Symbol | Source | Contract |
|--------|--------|----------|
| `DEFAULT_LLM_MODELS` | `co_cli/config/llm.py` | Per-provider default full model id (used when `llm.model` is unset) |
| `_LLM_SETTINGS` | `co_cli/config/llm.py` | Provider→model→mode canonical inference knobs (module-private, not user-overridable) |
| `LlmModel` | `co_cli/llm/factory.py` | Frozen dataclass holding pydantic-ai model + both `ModelSettings` |
| `build_model(llm) -> LlmModel` | `co_cli/llm/factory.py` | Constructs the pydantic-ai model from `LlmSettings` |
| `llm_call(deps, prompt, ...) -> str` | `co_cli/llm/call.py` | Async — single-prompt functional LLM primitive; defaults to `deps.model.settings_noreason` |


## 5. Files

| File | Purpose |
|------|---------|
| `co_cli/config/core.py` | `Settings`, `load_config()`, `get_settings()`, `fill_from_env`; path constants (`USER_DIR`, `SETTINGS_FILE`, `SEARCH_DB`, etc.) |
| `co_cli/config/llm.py` | `LlmSettings` with `reasoning_model_settings()`, `noreason_model_settings()`, `validate_config()` methods; `_LLM_SETTINGS`, `DEFAULT_LLM_MODELS` |
| `co_cli/config/memory.py` | `MemorySettings` — search backend, embedding, chunking, lifecycle, recall |
| `co_cli/config/compaction.py` | `CompactionSettings` — trigger ratio, spill ratio, tail fraction, anti-thrash window |
| `co_cli/config/web.py` | `WebSettings` — domain policy, HTTP retry and backoff |
| `co_cli/config/shell.py` | `ShellSettings` — timeout, safe command list |
| `co_cli/config/observability.py` | `ObservabilitySettings` — log level, rotation, redaction patterns |
| `co_cli/config/skills.py` | `SkillsSettings` — background review enable flag and turn-boundary nudge interval |
| `co_cli/config/mcp.py` | `MCPServerSettings`, `DEFAULT_MCP_SERVERS`, `parse_mcp_servers_from_env()` |
| `co_cli/llm/factory.py` | `LlmModel` dataclass; `build_model()` — constructs pydantic-ai model + both `ModelSettings` from `LlmSettings` |
| `co_cli/llm/call.py` | `llm_call()` — single-prompt functional LLM primitive; defaults to `deps.model.settings_noreason` |
| `co_cli/check.py` | `probe_ollama_model()` — `/api/show` probe for num_ctx + capabilities |
| `co_cli/bootstrap/core.py` | `create_deps()` — calls `validate_config()`, `probe_ollama_model()`, `build_model()` at startup |
| `co_cli/context/summarization.py` | `resolve_compaction_budget(deps)` — returns `deps.model_max_context_tokens` directly |


## 6. Test Gates

| Property | Test file |
|----------|-----------|
| Config loads from settings.json with env overrides | `tests/_settings.py` |
| `llm_call()` returns non-empty text | `tests/test_flow_llm_call.py` |
| `llm_call()` applies system instructions | `tests/test_flow_llm_call.py` |
| `llm_call()` threads message history | `tests/test_flow_llm_call.py` |
| Provider/model availability reflected in capabilities surface | `tests/test_flow_capability_checks.py` |
| Degradation state surfaces in capability checks | `tests/test_flow_capability_checks.py` |
| Compaction budget resolves from `model_max_context_tokens` | `tests/test_flow_compaction_summarization.py` |
