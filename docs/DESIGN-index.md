# Co CLI — Documentation Index

> For system overview and architecture diagrams: [DESIGN-system.md](DESIGN-system.md).

## 1. Reading Guide

Start here when you need navigation, the consolidated config table, or the source module index. Use the question map below to jump to the owning doc.

```
DESIGN-index.md  ← start here (navigation, config reference, module index)
    │
    ▼
DESIGN-system.md ← system architecture diagrams, top-level system contracts
    │
    ▼
What is your question?
    │
    ├── tracing a bug or runtime workflow?
    │       └─▶  Layer 2 — Runtime docs
    │               core-loop
    │               system-bootstrap
    │               tools
    │
    └── need schema, lifecycle, or config detail?
            └─▶  Layer 3 — Component docs
                    DESIGN-system
                    DESIGN-tools
                    DESIGN-llm-models
                    DESIGN-observability
                            │
                            ▼
                follow 'See Also' links for depth
```

### Layer 2 — Runtime docs

Use a runtime doc for ordered execution, state transitions, and failure paths during a live run.

| If you're asking... | Read |
|---------------------|------|
| What happens from the moment a user types to the LLM responding? | [DESIGN-core-loop.md](DESIGN-core-loop.md) |
| Why did a tool not get approved / what's the approval decision chain? | [DESIGN-tools.md](DESIGN-tools.md) §Approval and [DESIGN-core-loop.md](DESIGN-core-loop.md) |
| What runs at startup before the first user message? | [DESIGN-system-bootstrap.md](DESIGN-system-bootstrap.md) |
| How is the system prompt assembled and how does history compaction work? | [DESIGN-system.md](DESIGN-system.md) §Agent Factory and [DESIGN-core-loop.md](DESIGN-core-loop.md) |

### Layer 3 — Component docs

Use a component doc for subsystem contracts, lifecycle, and implementation detail.

| If you're asking... | Read |
|---------------------|------|
| What is the top-level architecture, capability surface, or `CoDeps` contract? | [DESIGN-system.md](DESIGN-system.md) |
| How does a tool go from registration to the model calling it? | [DESIGN-tools.md](DESIGN-tools.md) |
| How does a memory get written, recalled, and eventually pruned? | [DESIGN-system.md](DESIGN-system.md) §Memory and [DESIGN-tools.md](DESIGN-tools.md) §Memory |
| How does knowledge get indexed and retrieved? | [DESIGN-system.md](DESIGN-system.md) §Knowledge and [DESIGN-tools.md](DESIGN-tools.md) §Knowledge |
| How does a skill go from `.md` file to running in the agent? | [DESIGN-system-bootstrap.md](DESIGN-system-bootstrap.md) §Skills Load and [DESIGN-core-loop.md](DESIGN-core-loop.md) |

### Layer 4 — Deep Component Docs

Use these when you already know the subsystem and need the owning deep-dive doc.

| If you're asking... | Read |
|---------------------|------|
| How does the agent factory, CoDeps, or approval flow work at the code level? | [DESIGN-system.md](DESIGN-system.md) and [DESIGN-core-loop.md](DESIGN-core-loop.md) |
| What models/providers are supported and how is the reasoning chain configured? | [DESIGN-llm-models.md](DESIGN-llm-models.md) |
| How does the prompt get structured / what gets injected at runtime? | [DESIGN-system.md](DESIGN-system.md) §Agent Factory |
| What are all the tools and their approval/return shape? | [DESIGN-tools.md](DESIGN-tools.md) |
| How does the MCP client connect and inherit approval? | [DESIGN-tools.md](DESIGN-tools.md) §MCP Tool Servers |
| What integrations are configured and healthy at startup? | [DESIGN-tools.md](DESIGN-tools.md) §Capabilities and [DESIGN-system-bootstrap.md](DESIGN-system-bootstrap.md) |

### Canonical Docs

| Doc | Role |
|-----|------|
| [DESIGN-system.md](DESIGN-system.md) | Top-level architecture, `CoDeps`, capability surface, security boundaries |
| [DESIGN-core-loop.md](DESIGN-core-loop.md) | Per-turn execution and approval flow interception |
| [DESIGN-system-bootstrap.md](DESIGN-system-bootstrap.md) | Startup and bootstrap sequence |
| [DESIGN-tools.md](DESIGN-tools.md) | Native tools, MCP tools, approval classes, return/error contracts |
| [DESIGN-llm-models.md](DESIGN-llm-models.md) | Provider/model selection and role-model chains |
| [DESIGN-observability.md](DESIGN-observability.md) | Tracing, SQLite exporter, viewers |
| `DESIGN-index.md` | Navigation, consolidated config reference, and module index |

---

## 2. Config Reference

Settings relevant to the agent loop. Full settings inventory in `co_cli/config.py`.

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `theme` | `CO_CLI_THEME` | `"light"` | CLI theme selection |
| `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | `null` | Obsidian vault root for notes tools |
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | `null` | Brave Search credential for `web_search` |
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `null` | Explicit OAuth credential path for Google tools |
| `library_path` | `CO_LIBRARY_PATH` | `null` | User-global article library directory override (`~/.local/share/co-cli/library/` by default) |
| `personality` | `CO_CLI_PERSONALITY` | `"finch"` | Personality role name (per-turn injection) |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` | `3` | Agent-level retry budget for all tools |
| `model_http_retries` | `CO_CLI_MODEL_HTTP_RETRIES` | `2` | Max provider error retries per turn |
| `max_request_limit` | `CO_CLI_MAX_REQUEST_LIMIT` | `50` | Caps LLM round-trips per user turn |
| `role_models` | `CO_MODEL_ROLE_REASONING`, `CO_MODEL_ROLE_SUMMARIZATION`, `CO_MODEL_ROLE_CODING`, `CO_MODEL_ROLE_RESEARCH`, `CO_MODEL_ROLE_ANALYSIS` | provider defaults for all roles (`ollama-openai`) or reasoning-only (`gemini`) | Ordered model chains per role (`reasoning` required; other roles optional) |
| `llm_provider` | `LLM_PROVIDER` | `"ollama-openai"` | Provider selection (`ollama-openai` or `gemini`) |
| `llm_api_key` | `LLM_API_KEY` | `null` | LLM API key (required when `llm_provider=gemini`) |
| `llm_host` | `LLM_HOST` | `"http://localhost:11434"` | LLM server base URL (Ollama or compatible) |
| `llm_num_ctx` | `LLM_NUM_CTX` | `262144` | Configured context window hint (passed to Ollama; ignored by Ollama API — set in Modelfile) |
| `ctx_warn_threshold` | `CO_CTX_WARN_THRESHOLD` | `0.85` | Warn ratio for context saturation |
| `ctx_overflow_threshold` | `CO_CTX_OVERFLOW_THRESHOLD` | `1.0` | Overflow ratio for context saturation |
| `doom_loop_threshold` | `CO_CLI_DOOM_LOOP_THRESHOLD` | `3` | Consecutive identical tool-call threshold for doom-loop warning |
| `max_reflections` | `CO_CLI_MAX_REFLECTIONS` | `3` | Consecutive shell-error threshold for reflection-cap warning |
| `max_history_messages` | `CO_CLI_MAX_HISTORY_MESSAGES` | `40` | Sliding window threshold |
| `tool_output_trim_chars` | `CO_CLI_TOOL_OUTPUT_TRIM_CHARS` | `2000` | Truncate old tool outputs |
| `role_models["summarization"]` | `CO_MODEL_ROLE_SUMMARIZATION` | provider default when absent | Optional summarization model chain; head used for `/compact` and history compaction (absent = falls back to primary model) |
| `shell_max_timeout` | `CO_CLI_SHELL_MAX_TIMEOUT` | `600` | Hard cap for `run_shell_command` timeout seconds |
| `shell_safe_commands` | `CO_CLI_SHELL_SAFE_COMMANDS` | built-in list | Safe-prefix auto-approval allowlist |
| `web_policy.search` | `CO_CLI_WEB_POLICY_SEARCH` | `"allow"` | `web_search` policy (`allow`, `ask`, `deny`) |
| `web_policy.fetch` | `CO_CLI_WEB_POLICY_FETCH` | `"allow"` | `web_fetch` policy (`allow`, `ask`, `deny`) |
| `web_fetch_allowed_domains` | `CO_CLI_WEB_FETCH_ALLOWED_DOMAINS` | `[]` | Optional domain allowlist for `web_fetch` |
| `web_fetch_blocked_domains` | `CO_CLI_WEB_FETCH_BLOCKED_DOMAINS` | `[]` | Domain blocklist for `web_fetch` |
| `web_http_max_retries` | `CO_CLI_WEB_HTTP_MAX_RETRIES` | `2` | Max HTTP retries for `web_fetch` (exponential backoff) |
| `web_http_backoff_base_seconds` | `CO_CLI_WEB_HTTP_BACKOFF_BASE_SECONDS` | `1.0` | Base backoff interval (seconds) for `web_fetch` retries |
| `web_http_backoff_max_seconds` | `CO_CLI_WEB_HTTP_BACKOFF_MAX_SECONDS` | `8.0` | Max backoff cap (seconds) for `web_fetch` retries |
| `web_http_jitter_ratio` | `CO_CLI_WEB_HTTP_JITTER_RATIO` | `0.2` | Jitter fraction applied to backoff interval (0–1) |
| `knowledge_search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `"hybrid"` | Knowledge retrieval backend (`grep`, `fts5`, `hybrid`) |
| `knowledge_embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `"tei"` | Embedding provider for hybrid retrieval |
| `knowledge_embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` | `"embeddinggemma"` | Embedding model name |
| `knowledge_embedding_dims` | `CO_KNOWLEDGE_EMBEDDING_DIMS` | `1024` | Embedding vector dimensionality (1024 = bge-m3; legacy Ollama embeddinggemma used 256) |
| `knowledge_hybrid_vector_weight` | `—` | `0.7` | Retained for backward compatibility; ignored — hybrid merge uses RRF (rank-based, not score-weighted) |
| `knowledge_hybrid_text_weight` | `—` | `0.3` | Retained for backward compatibility; ignored — hybrid merge uses RRF (rank-based, not score-weighted) |
| `knowledge_cross_encoder_reranker_url` | `CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL` | `"http://127.0.0.1:8282"` | TEI cross-encoder rerank URL (`POST /rerank`); `null` disables cross-encoder reranking |
| `knowledge_llm_reranker` | `—` | `null` | LLM listwise reranker (`ModelEntry`: `provider:model`); used when cross-encoder is absent |
| `knowledge_embed_api_url` | `CO_KNOWLEDGE_EMBED_API_URL` | `"http://127.0.0.1:8283"` | Base URL for TEI embed service (`POST /embed`) |
| `memory_max_count` | `CO_CLI_MEMORY_MAX_COUNT` | `200` | Max stored memories |
| `memory_dedup_window_days` | `CO_CLI_MEMORY_DEDUP_WINDOW_DAYS` | `7` | Duplicate-detection lookback window |
| `memory_dedup_threshold` | `CO_CLI_MEMORY_DEDUP_THRESHOLD` | `85` | Fuzzy similarity threshold for dedup |
| `memory_consolidation_top_k` | `CO_MEMORY_CONSOLIDATION_TOP_K` | `5` | Recent memories considered for LLM consolidation |
| `memory_consolidation_timeout_seconds` | `CO_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS` | `20` | Per-call timeout for consolidation LLM calls |
| `memory_auto_save_tags` | `CO_CLI_MEMORY_AUTO_SAVE_TAGS` | `["correction","preference"]` | Allowlist of signal tags eligible for auto-save; empty list disables all auto-signal saves |
| `memory_injection_max_chars` | `CO_CLI_MEMORY_INJECTION_MAX_CHARS` | `2000` | Cap on injected recall content in `inject_opening_context` (chars) |
| `knowledge_chunk_size` | `CO_CLI_KNOWLEDGE_CHUNK_SIZE` | `600` | Character window per chunk (0 = disable chunking) |
| `knowledge_chunk_overlap` | `CO_CLI_KNOWLEDGE_CHUNK_OVERLAP` | `80` | Character overlap between adjacent chunks |
| `memory_recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` | `30` | Half-life (days) for temporal decay scoring in FTS-backed recall (`fts5` and `hybrid`) |
| `session_ttl_minutes` | `CO_SESSION_TTL_MINUTES` | `60` | Session persistence TTL (minutes) |
| `mcp_servers` | `CO_CLI_MCP_SERVERS` | 2 defaults | MCP server configurations (JSON) |
| `background_max_concurrent` | `CO_BACKGROUND_MAX_CONCURRENT` | `5` | Max concurrent background tasks |
| `background_task_retention_days` | `CO_BACKGROUND_TASK_RETENTION_DAYS` | `7` | Days to keep completed/failed/cancelled task data |
| `background_auto_cleanup` | `CO_BACKGROUND_AUTO_CLEANUP` | `true` | Clean up old tasks on startup |
| `background_task_inactivity_timeout` | `CO_BACKGROUND_TASK_INACTIVITY_TIMEOUT` | `0` | Auto-cancel if no output for N seconds (0 = disabled) |
| `subagent_scope_chars` | `CO_CLI_SUBAGENT_SCOPE_CHARS` | `120` | Max chars of primary input captured as `scope` in sub-agent tool results |

---

## 3. XDG Directory Structure

```
~/.config/co-cli/
└── settings.json          # User configuration

~/.local/share/co-cli/
├── co-cli-logs.db         # OpenTelemetry traces (SQLite)
├── co-cli-search.db       # FTS5 / hybrid knowledge search index (rebuildable)
└── history.txt            # REPL command history

<project-root>/
└── .co-cli/
    ├── memory/            # Memory files (kind: memory); articles stored in ~/.local/share/co-cli/library/
    ├── skills/            # Project-local skill .md files (override package-default on name collision)
    ├── session.json       # Session persistence (mode 0o600): session_id, created_at, last_used_at, compaction_count
    └── settings.json      # Project configuration (overrides user)
```

---

## 4. Modules

| Layer | Module | Purpose |
|-------|--------|---------|
| 1. Agents + Orchestration | `main.py` | CLI entry point, chat loop, OTel setup |
| 1. Agents + Orchestration | `bootstrap/_bootstrap.py` | `create_deps()`, `sync_knowledge()`, `restore_session()` — startup assembly and inline wakeup steps |
| 1. Agents + Orchestration | `agent.py` | `build_agent()` factory — model selection, tool registration, system prompt |
| 1. Agents + Orchestration | `context/_orchestrate.py` | `TurnResult`, `run_turn()`, `_run_stream_turn()`, `_collect_deferred_tool_approvals()` |
| 1. Agents + Orchestration | `tools/_tool_approvals.py` | Deferred approval helpers: `ApprovalSubject`, `resolve_approval_subject()`, `is_auto_approved()`, `remember_tool_approval()`, `record_approval_choice()`, `decode_tool_args()` |
| 2. Runtime Deps + Session State | `deps.py` | `CoDeps` dataclass — runtime dependencies injected via `RunContext`; `SessionApprovalRule` frozen dataclass |
| 2. Runtime Deps + Session State | `bootstrap/_check.py` | `RuntimeCheck` dataclass + `check_runtime(deps) → RuntimeCheck` — primary runtime diagnostic aggregator (capabilities, status, findings, fallbacks, mcp_probes, summary_lines); `check_agent_llm`, `check_settings` |
| 2. Runtime Deps + Session State | `context/_session.py` | Session persistence: `new_session()`, `load_session()`, `save_session()`, `is_fresh()`, `touch_session()`, `increment_compaction()` |
| 2. Runtime Deps + Session State | `context/_history.py` | History processors and `summarize_messages()` |
| 3. Tool Layer | `tools/shell.py` | `run_shell_command` — conditionally approved shell execution (command-scoped policy) |
| 3. Tool Layer | `tools/files.py` | Native file tools: `list_directory`, `read_file`, `find_in_files`, `write_file`, `edit_file` |
| 3. Tool Layer | `tools/subagent.py` | `run_coder_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_thinking_subagent` — sub-agent tools |
| 3. Tool Layer | `tools/todo.py` | Session todo tools: `todo_write`, `todo_read` |
| 3. Tool Layer | `tools/obsidian.py` | `search_notes`, `list_notes`, `read_note` |
| 3. Tool Layer | `tools/google_drive.py` | `search_drive_files`, `read_drive_file` |
| 3. Tool Layer | `tools/google_gmail.py` | `list_emails`, `search_emails`, `create_email_draft` |
| 3. Tool Layer | `tools/google_calendar.py` | `list_calendar_events`, `search_calendar_events` |
| 3. Tool Layer | `tools/web.py` | `web_search`, `web_fetch` — Brave Search API + URL fetch |
| 3. Tool Layer | `tools/capabilities.py` | `check_capabilities` — capability introspection tool (read-only, no approval) |
| 3. Tool Layer | `tools/task_control.py` | Background task tools: `start_background_task` (approval), `check_task_status`, `cancel_background_task`, `list_background_tasks` |
| 3. Tool Layer | `tools/_google_auth.py` | Google credential resolution (ensure/get/cached) |
| 3. Tool Layer | `tools/_errors.py` | Shared error helpers: `terminal_error()`, `http_status_code()` |
| 3. Tool Layer | `tools/_shell_backend.py` | `ShellBackend` — subprocess execution backend used by command-scoped shell approval policy |
| 3. Tool Layer | `tools/_shell_policy.py` | Shell policy engine: `evaluate_shell_command()` — DENY / ALLOW / REQUIRE_APPROVAL classification |
| 3. Tool Layer | `tools/_approval.py` | Shell safe-command classification (`_is_safe_command`) |
| 3. Tool Layer | `tools/_shell_env.py` | Shell env sanitizer + process-group kill helpers (`restricted_env`, `kill_process_tree`) |
| 3. Tool Layer | `tools/_background.py` | `TaskStatus` enum, `TaskStorage` (filesystem), `TaskRunner` (asyncio process manager) — background task execution |
| 2. Runtime Deps + Session State | `_model_factory.py` | `ResolvedModel` (model + settings pair), `ModelRegistry` (session-scoped role registry built via `ModelRegistry.from_config(config)`), `build_model(model_entry, provider, llm_host, api_key)` — provider-aware model factory |
| 3. Tool Layer | `tools/_subagent_agents.py` | `CoderResult`, `make_coder_agent()`, `ResearchResult`, `make_research_agent()`, `AnalysisResult`, `make_analysis_agent()`, `ThinkingResult`, `make_thinking_agent()` — sub-agent helpers |
| 4. Knowledge + Memory | `knowledge/_index_store.py` | FTS5/hybrid index for memory/article/obsidian/drive search; `index_chunks`, `remove_chunks` |
| 4. Knowledge + Memory | `knowledge/_chunker.py` | `chunk_text()` — paragraph-boundary chunker with token estimation and overlap; used by all non-memory index paths |
| 4. Knowledge + Memory | `tools/articles.py` | Article/knowledge tools: `save_article`, `search_knowledge`, `read_article_detail`, `recall_article` |
| 4. Knowledge + Memory | `memory/_lifecycle.py` | Write entrypoint for all memory saves: dedup → consolidation → write → retention |
| 4. Knowledge + Memory | `memory/_consolidator.py` | LLM-driven fact extraction and contradiction resolution |
| 4. Knowledge + Memory | `memory/_retention.py` | Cut-only retention enforcement |
| 4. Knowledge + Memory | `tools/memory.py` | Memory recall/edit tools: `save_memory`, `recall_memory`, `search_memories`, `list_memories`, `update_memory`, `append_memory` |
| 4. Knowledge + Memory | `tools/personality.py` | Per-turn personality-context memory injector helper |
| 4. Knowledge + Memory | `knowledge/_frontmatter.py` | YAML frontmatter parser for skills/knowledge markdown files |
| 4. Knowledge + Memory | `memory/_signal_detector.py` | Post-turn signal detector for auto-memory capture |
| 5. Skills | `commands/_commands.py` | Slash command registry, handlers, `dispatch()`, `_load_skills()`, `SkillCommand` |
| 5. Skills | `skills/` | Package-default skill `.md` files — always available; currently ships `doctor.md` |
| 6. Config + Infra Reference | `config.py` | `Settings` + `MCPServerConfig` (Pydantic BaseModel) from `settings.json` + env vars |
| 6. Config + Infra Reference | `display.py` | Themed Rich Console, semantic styles, display helpers, `FrontendProtocol` (display + interaction contract), `TerminalFrontend` |
| 6. Config + Infra Reference | `bootstrap/_render_status.py` | `StatusInfo` dataclass + `get_status()` + `render_status_table()` + `SecurityFinding` dataclass + `check_security()` + `render_security_findings()` |
| 6. Config + Infra Reference | `observability/_telemetry.py` | `SQLiteSpanExporter` — OTel spans to SQLite with WAL mode |
| 6. Config + Infra Reference | `observability/_tail.py` | Real-time span viewer (`co tail`) |
| 6. Config + Infra Reference | `observability/_viewer.py` | Static HTML trace viewer (`co traces`) |
| 6. Config + Infra Reference | `tests/test_tool_calling_functional.py` | Functional tool-calling gate — selection, args, refusal, intent routing, recovery |

---

## 5. Dependencies

### Runtime

| Package | Version | Purpose |
|---------|---------|---------|
| `pydantic-ai` | `==1.70.0` | LLM orchestration |
| `typer` | `>=0.21.1` | CLI framework |
| `rich` | `>=14.3.2` | Terminal UI |
| `prompt-toolkit` | `>=3.0.52` | Interactive REPL |
| `google-genai` | `>=1.61.0` | Gemini API |
| `google-api-python-client` | `>=2.189.0` | Drive/Gmail/Calendar |
| `google-auth-httplib2` | `>=0.3.0` | Google auth transport adapter |
| `google-auth-oauthlib` | `>=1.2.4` | OAuth2 |
| `opentelemetry-sdk` | `>=1.39.1` | Tracing |
| `httpx` | `>=0.28.1` | HTTP client (web tools) |
| `html2text` | `>=2025.4.15` | HTML→markdown conversion (web_fetch) |
| `datasette` | `>=0.65.2` | Telemetry dashboard |
| `datasette-pretty-json` | `>=0.3` | Datasette JSON rendering plugin |
| `rapidfuzz` | `>=3.14.3` | Fuzzy similarity for memory deduplication |
| `pysqlite3` | `>=0.6.0` | SQLite compatibility/runtime support |
| `sqlite-vec` | `>=0.1.6` | Vector search extension for hybrid knowledge backend |
| `ollama` | `>=0.6.1` | Ollama Python client (model availability checks) |

### Development

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | `>=9.0.2` | Testing framework |
| `pytest-asyncio` | `>=1.3.0` | Async test support |
| `pytest-cov` | `>=7.0.0` | Coverage reporting |
