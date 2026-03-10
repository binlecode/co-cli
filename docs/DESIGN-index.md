# Co CLI — Documentation Index

> For system overview, architecture diagrams, and core design: [DESIGN-core.md](DESIGN-core.md).

## 1. Reading Guide

The docs are organized in four layers. Use the question types below to find the right starting point.

```
DESIGN-index.md  ← start here (navigation, config reference, module index)
    │
    ▼
DESIGN-core.md   ← system architecture diagrams, agent loop internals
    │
    ▼
What is your question?
    │
    ├── tracing a bug or runtime workflow?
    │       └─▶  Layer 2 — Workflow docs
    │               flow-core-turn
    │               flow-approval
    │               flow-bootstrap  (canonical startup: model check + bootstrap)
    │               flow-context-governance
    │
    ├── changing a subsystem (memory, tools, knowledge, skills)?
    │       └─▶  Layer 3 — Lifecycle docs
    │               flow-tools-lifecycle
    │               flow-memory-lifecycle
    │               flow-knowledge-lifecycle
    │               flow-skills-lifecycle
    │
    └── need schema, algorithm, or config detail?
            └─▶  Layer 4 — Component docs
                    DESIGN-core
                    DESIGN-prompt-design
                    DESIGN-tools
                    DESIGN-memory
                    DESIGN-knowledge
                    DESIGN-skills
                    DESIGN-llm-models
                    DESIGN-personality
                    DESIGN-mcp-client
                    DESIGN-eval-llm-judge
                            │
                            ▼
                follow 'See Also' links for depth
```

### Layer 1 — Documentation Index (this doc)

Start here for navigation: reading guide, config reference, and module index.
System overview and architecture diagrams live in [DESIGN-core.md](DESIGN-core.md).

### Layer 2 — Workflow docs

Read a flow doc when you have a **runtime question about a specific workflow** — debugging a bug, tracing execution, or understanding how two modules interact during a user turn.

Each flow doc is self-contained: entry conditions → ordered steps → branching → state mutations → failure paths → owning source files.

| If you're asking... | Read |
|---------------------|------|
| What happens from the moment a user types to the LLM responding? | [DESIGN-core-loop.md](DESIGN-core-loop.md) |
| Why did a tool not get approved / what's the approval decision chain? | [DESIGN-flow-approval.md](DESIGN-flow-approval.md) |
| What runs at startup before the first user message? | [DESIGN-flow-bootstrap.md](DESIGN-flow-bootstrap.md) |
| How is the system prompt assembled and how does history compaction work? | [DESIGN-flow-context-governance.md](DESIGN-flow-context-governance.md) |

| Workflow | Doc | What it covers |
|----------|-----|----------------|
| One user turn (end-to-end) | [DESIGN-core-loop.md](DESIGN-core-loop.md) | chat loop → `run_turn` → streaming → tool calls → approval re-entry → post-turn hooks → retry/fallback |
| Startup (canonical) | [DESIGN-flow-bootstrap.md](DESIGN-flow-bootstrap.md) | canonical startup flow: model dependency check (provider + model availability), knowledge sync, session restore, skills load, MCP init fallback, integration health sweep, welcome banner |
| Tool approval | [DESIGN-flow-approval.md](DESIGN-flow-approval.md) | three-tier decision chain, shell policy path, skill grants, session auto-approve, `"a"` persistence |
| Context governance | [DESIGN-flow-context-governance.md](DESIGN-flow-context-governance.md) | prompt assembly, per-turn layers, memory injection, tool output trimming, history summarization, precomputed compaction |

### Layer 3 — Lifecycle docs

Read a lifecycle doc when you have a **subsystem question** — adding a new memory tool, changing how skills load, modifying retrieval behavior.

| If you're asking... | Read |
|---------------------|------|
| How does a tool go from registration to the model calling it? | [DESIGN-flow-tools-lifecycle.md](DESIGN-flow-tools-lifecycle.md) |
| How does a memory get written, recalled, and eventually pruned? | [DESIGN-flow-memory-lifecycle.md](DESIGN-flow-memory-lifecycle.md) |
| How does knowledge get indexed and retrieved? | [DESIGN-flow-knowledge-lifecycle.md](DESIGN-flow-knowledge-lifecycle.md) |
| How does a skill go from `.md` file to running in the agent? | [DESIGN-flow-skills-lifecycle.md](DESIGN-flow-skills-lifecycle.md) |

| Subsystem | Doc | What it covers |
|-----------|-----|----------------|
| Tools (all families) | [DESIGN-flow-tools-lifecycle.md](DESIGN-flow-tools-lifecycle.md) | registration, exposure to model, approval classification, execution paths, return shape, error classification |
| Memory | [DESIGN-flow-memory-lifecycle.md](DESIGN-flow-memory-lifecycle.md) | write path, edit, recall, runtime injection, signal detection, retention/decay |
| Knowledge | [DESIGN-flow-knowledge-lifecycle.md](DESIGN-flow-knowledge-lifecycle.md) | article save, source sync, retrieval, fallback, source namespace |
| Skills | [DESIGN-flow-skills-lifecycle.md](DESIGN-flow-skills-lifecycle.md) | startup load, precedence, dispatch, env injection, allowed-tools grant, install/upgrade |

### Layer 4 — Component docs

Read a component doc when you need **implementation detail for a specific module** — schema, algorithm specifics, config options for a single subsystem. These docs are usually pointed to by flow/lifecycle docs.

| If you're asking... | Read |
|---------------------|------|
| How does the agent factory, CoDeps, or approval flow work at the code level? | [DESIGN-core.md](DESIGN-core.md) |
| What models/providers are supported and how is the reasoning chain configured? | [DESIGN-llm-models.md](DESIGN-llm-models.md) |
| How does the prompt get structured / what are the safety policy rules? | [DESIGN-prompt-design.md](DESIGN-prompt-design.md) |
| What are all the tools and their approval/return shape? | [DESIGN-tools.md](DESIGN-tools.md) |
| How does memory dedup, consolidation, and certainty scoring work? | [DESIGN-memory.md](DESIGN-memory.md) |
| How does the FTS5/hybrid knowledge index work internally? | [DESIGN-knowledge.md](DESIGN-knowledge.md) |
| How does the MCP client connect and inherit approval? | [DESIGN-mcp-client.md](DESIGN-mcp-client.md) |
| What integrations are configured and healthy at startup? | [DESIGN-doctor.md](DESIGN-doctor.md) |

| Component | Doc | Summary |
|-----------|-----|---------|
| Core orchestration (agent factory, CoDeps, capability surface, session management, security) | [DESIGN-core.md](DESIGN-core.md) | Agent factory, CoDeps, capability surface (tools/skills/MCP/sub-agents/approval boundary), memory & knowledge systems, session management, security model |
| Agents + Prompt Architecture | [DESIGN-prompt-design.md](DESIGN-prompt-design.md) | Prompt composition: static assembly, per-turn layers, tool preamble, context governance coupling |
| Tools index | [DESIGN-tools.md](DESIGN-tools.md) — [execution](DESIGN-tools-execution.md), [integrations](DESIGN-tools-integrations.md), [delegation](DESIGN-tools-delegation.md) | Shell, files, background, todo, capabilities, Obsidian, Google, web, memory, sub-agent delegation |
| Knowledge internals | [DESIGN-knowledge.md](DESIGN-knowledge.md) | `KnowledgeIndex` FTS5/hybrid retrieval, article frontmatter schema, Obsidian/Drive indexing |
| Memory internals | [DESIGN-memory.md](DESIGN-memory.md) | Frontmatter contract, signal detection, dedup, consolidation, retention, certainty classification |
| Skills internals | [DESIGN-skills.md](DESIGN-skills.md) | `SkillCommand` schema, security scanner patterns |
| LLM Models | [DESIGN-llm-models.md](DESIGN-llm-models.md) | Gemini/Ollama model selection, inference parameters, Ollama local setup, sub-agent model roles |
| Personality System | [DESIGN-personality.md](DESIGN-personality.md) | File-driven roles, 5 traits, structural per-turn injection, reasoning depth override |
| MCP Client | [DESIGN-mcp-client.md](DESIGN-mcp-client.md) | External tool servers via Model Context Protocol (stdio and HTTP transports, auto-prefixing, approval inheritance) |
| Logging & Tracking | [DESIGN-logging-and-tracking.md](DESIGN-logging-and-tracking.md) | SQLite span exporter, WAL concurrency, trace viewers, real-time `co tail` |
| Doctor | [DESIGN-doctor.md](DESIGN-doctor.md) | System-wide integration health checks: check_* functions, DoctorResult, bootstrap sweep, capabilities tool delegation |
| Eval LLM-as-Judge | [DESIGN-eval-llm-judge.md](DESIGN-eval-llm-judge.md) | LLM-as-judge for personality evals: check types, judge file structure, prompt design, model settings |
| Config Reference | DESIGN-index.md §Config Reference (this doc) | Consolidated setting/env/default reference |
| Module Index | DESIGN-index.md §Modules (this doc) | All source files by layer with purpose |

---

## 2. Config Reference

Settings relevant to the agent loop. Full settings inventory in `co_cli/config.py`.

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `theme` | `CO_CLI_THEME` | `"light"` | CLI theme selection |
| `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | `null` | Obsidian vault root for notes tools |
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | `null` | Brave Search credential for `web_search` |
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `null` | Explicit OAuth credential path for Google tools |
| `personality` | `CO_CLI_PERSONALITY` | `"finch"` | Personality role name (per-turn injection) |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` | `3` | Agent-level retry budget for all tools |
| `model_http_retries` | `CO_CLI_MODEL_HTTP_RETRIES` | `2` | Max provider error retries per turn |
| `max_request_limit` | `CO_CLI_MAX_REQUEST_LIMIT` | `50` | Caps LLM round-trips per user turn |
| `role_models` | `CO_MODEL_ROLE_REASONING`, `CO_MODEL_ROLE_SUMMARIZATION`, `CO_MODEL_ROLE_CODING`, `CO_MODEL_ROLE_RESEARCH`, `CO_MODEL_ROLE_ANALYSIS` | provider defaults for all roles (ollama) or reasoning-only (gemini) | Ordered model chains per role (`reasoning` required; other roles optional) |
| `llm_provider` | `LLM_PROVIDER` | `"ollama"` | Provider selection (`gemini` or `ollama`) |
| `gemini_api_key` | `GEMINI_API_KEY` | `null` | Gemini credential (required when provider is `gemini`) |
| `ollama_host` | `OLLAMA_HOST` | `"http://localhost:11434"` | Ollama server base URL |
| `ollama_num_ctx` | `OLLAMA_NUM_CTX` | `262144` | Configured Ollama context window hint |
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
| `knowledge_search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `"fts5"` | Knowledge retrieval backend (`grep`, `fts5`, `hybrid`) |
| `knowledge_embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `"ollama"` | Embedding provider for hybrid retrieval |
| `knowledge_embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` | `"embeddinggemma"` | Embedding model name |
| `knowledge_embedding_dims` | `CO_KNOWLEDGE_EMBEDDING_DIMS` | `256` | Embedding vector dimensionality |
| `knowledge_hybrid_vector_weight` | `—` | `0.7` | Hybrid retrieval vector-score weight |
| `knowledge_hybrid_text_weight` | `—` | `0.3` | Hybrid retrieval BM25-score weight |
| `knowledge_reranker_provider` | `CO_KNOWLEDGE_RERANKER_PROVIDER` | `"local"` | Reranker provider (`none`, `local`, `ollama`, `gemini`) |
| `knowledge_reranker_model` | `CO_KNOWLEDGE_RERANKER_MODEL` | `""` | Optional reranker model override |
| `memory_max_count` | `CO_CLI_MEMORY_MAX_COUNT` | `200` | Max stored memories |
| `memory_dedup_window_days` | `CO_CLI_MEMORY_DEDUP_WINDOW_DAYS` | `7` | Duplicate-detection lookback window |
| `memory_dedup_threshold` | `CO_CLI_MEMORY_DEDUP_THRESHOLD` | `85` | Fuzzy similarity threshold for dedup |
| `memory_consolidation_top_k` | `CO_MEMORY_CONSOLIDATION_TOP_K` | `5` | Recent memories considered for LLM consolidation |
| `memory_consolidation_timeout_seconds` | `CO_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS` | `20` | Per-call timeout for consolidation LLM calls |
| `memory_auto_save_tags` | `CO_CLI_MEMORY_AUTO_SAVE_TAGS` | `["correction","preference"]` | Allowlist of signal tags eligible for auto-save; empty list disables all auto-signal saves |
| `knowledge_chunk_size` | `CO_CLI_KNOWLEDGE_CHUNK_SIZE` | `600` | Character window per chunk (0 = disable chunking) |
| `knowledge_chunk_overlap` | `CO_CLI_KNOWLEDGE_CHUNK_OVERLAP` | `80` | Character overlap between adjacent chunks |
| `memory_recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` | `30` | Half-life (days) for temporal decay scoring in FTS-backed recall (`fts5` and `hybrid`) |
| `session_ttl_minutes` | `CO_SESSION_TTL_MINUTES` | `60` | Session persistence TTL (minutes) |
| `mcp_servers` | `CO_CLI_MCP_SERVERS` | 3 defaults | MCP server configurations (JSON) |
| `background_max_concurrent` | `CO_BACKGROUND_MAX_CONCURRENT` | `5` | Max concurrent background tasks |
| `background_task_retention_days` | `CO_BACKGROUND_TASK_RETENTION_DAYS` | `7` | Days to keep completed/failed/cancelled task data |
| `background_auto_cleanup` | `CO_BACKGROUND_AUTO_CLEANUP` | `true` | Clean up old tasks on startup |
| `background_task_inactivity_timeout` | `CO_BACKGROUND_TASK_INACTIVITY_TIMEOUT` | `0` | Auto-cancel if no output for N seconds (0 = disabled) |

---

## 3. XDG Directory Structure

```
~/.config/co-cli/
└── settings.json          # User configuration

~/.local/share/co-cli/
├── co-cli.db              # OpenTelemetry traces (SQLite)
├── search.db              # FTS5 / hybrid knowledge search index (rebuildable)
└── history.txt            # REPL command history

<project-root>/
└── .co-cli/
    ├── memory/            # Memory files (kind: memory); articles stored in ~/.local/share/co-cli/library/
    ├── skills/            # Project-local skill .md files (override package-default on name collision)
    ├── session.json       # Session persistence (mode 0o600): session_id, created_at, last_used_at, compaction_count
    ├── exec-approvals.json # Persistent exec approval patterns (mode 0o600)
    └── settings.json      # Project configuration (overrides user)
```

---

## 4. Modules

| Layer | Module | Purpose |
|-------|--------|---------|
| 1. Agents + Orchestration | `main.py` | CLI entry point, chat loop, OTel setup, `create_deps()` |
| 1. Agents + Orchestration | `agent.py` | `get_agent()` factory — model selection, tool registration, system prompt |
| 1. Agents + Orchestration | `_orchestrate.py` | `FrontendProtocol`, `TurnResult`, `run_turn()`, `_stream_events()`, `_collect_deferred_tool_approvals()` |
| 1. Agents + Orchestration | `_tool_approvals.py` | Deferred approval helpers: `is_shell_command_persistently_approved()`, `remember_tool_approval()`, `record_approval_choice()`, `format_tool_call_description()`, `decode_tool_args()` |
| 1. Agents + Orchestration | `_provider_errors.py` | `ProviderErrorAction`, `classify_provider_error()` — chat-loop error classification |
| 2. Runtime Deps + Session State | `deps.py` | `CoDeps` dataclass — runtime dependencies injected via `RunContext` |
| 2. Runtime Deps + Session State | `_model_check.py` | `run_model_check()`, `PreflightResult`, `_check_llm_provider()`, `_check_model_availability()` — pre-agent resource gate |
| 2. Runtime Deps + Session State | `_bootstrap.py` | `run_bootstrap()` — four startup steps: knowledge sync, session restore/create, skills count report, integration health sweep |
| 2. Runtime Deps + Session State | `_session.py` | Session persistence: `new_session()`, `load_session()`, `save_session()`, `is_fresh()`, `touch_session()`, `increment_compaction()` |
| 2. Runtime Deps + Session State | `_history.py` | History processors and `summarize_messages()` |
| 2. Runtime Deps + Session State | `_exec_approvals.py` | Persistent exec approvals: `derive_pattern()`, `find_approved()`, `add_approval()`, `update_last_used()`, `prune_stale()` |
| 3. Tool Layer | `tools/shell.py` | `run_shell_command` — approval-gated shell execution |
| 3. Tool Layer | `tools/files.py` | Native file tools: `list_directory`, `read_file`, `find_in_files`, `write_file`, `edit_file` |
| 3. Tool Layer | `tools/delegation.py` | `delegate_coder`, `delegate_research`, `delegate_analysis` — read-only sub-agent delegation tools |
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
| 3. Tool Layer | `_shell_backend.py` | `ShellBackend` — approval-gated subprocess execution |
| 3. Tool Layer | `_shell_policy.py` | Shell policy engine: `evaluate_shell_command()` — DENY / ALLOW / REQUIRE_APPROVAL classification |
| 3. Tool Layer | `_approval.py` | Shell safe-command classification (`_is_safe_command`) |
| 3. Tool Layer | `_shell_env.py` | Shell env sanitizer + process-group kill helpers (`restricted_env`, `kill_process_tree`) |
| 3. Tool Layer | `_background.py` | `TaskStatus` enum, `TaskStorage` (filesystem), `TaskRunner` (asyncio process manager) — background task execution |
| 3. Tool Layer | `_workspace_checkpoint.py` | Workspace checkpoint + rewind: `create_checkpoint()`, `restore_checkpoint()` |
| 3. Tool Layer | `agents/__init__.py` | Sub-agent package init |
| 3. Tool Layer | `agents/_factory.py` | `ResolvedModel` (model + settings pair), `ModelRegistry` (session-scoped role registry built via `ModelRegistry.from_config(config)`), `build_model(model_entry, provider, ollama_host, ollama_num_ctx)` — provider-aware model factory |
| 3. Tool Layer | `agents/coder.py` | Read-only coder sub-agent: `CoderResult`, `make_coder_agent(resolved_model: ResolvedModel)` |
| 3. Tool Layer | `agents/research.py` | Read-only research sub-agent: `ResearchResult`, `make_research_agent(resolved_model: ResolvedModel)` |
| 3. Tool Layer | `agents/analysis.py` | Read-only analysis sub-agent: `AnalysisResult`, `make_analysis_agent(resolved_model: ResolvedModel)` |
| 4. Knowledge + Memory | `_knowledge_index.py` | FTS5/hybrid index for memory/article/obsidian/drive search |
| 4. Knowledge + Memory | `tools/articles.py` | Article/knowledge tools: `save_article`, `search_knowledge`, `read_article_detail`, `recall_article` |
| 4. Knowledge + Memory | `_memory_lifecycle.py` | Write entrypoint for all memory saves: dedup → consolidation → write → retention |
| 4. Knowledge + Memory | `_memory_consolidator.py` | LLM-driven fact extraction and contradiction resolution |
| 4. Knowledge + Memory | `_memory_retention.py` | Cut-only retention enforcement |
| 4. Knowledge + Memory | `tools/memory.py` | Memory recall/edit tools: `save_memory`, `recall_memory`, `search_memories`, `list_memories`, `update_memory`, `append_memory` |
| 4. Knowledge + Memory | `tools/personality.py` | Per-turn personality-context memory injector helper |
| 4. Knowledge + Memory | `_frontmatter.py` | YAML frontmatter parser for skills/knowledge markdown files |
| 4. Knowledge + Memory | `_signal_analyzer.py` | Post-turn signal detector for auto-memory capture |
| 5. Skills | `_commands.py` | Slash command registry, handlers, `dispatch()`, `_load_skills()`, `SkillCommand` |
| 5. Skills | `skills/` | Package-default skill `.md` files — always available; currently ships `doctor.md` |
| 6. Config + Infra Reference | `config.py` | `Settings` + `MCPServerConfig` (Pydantic BaseModel) from `settings.json` + env vars |
| 6. Config + Infra Reference | `display.py` | Themed Rich Console, semantic styles, display helpers, `TerminalFrontend` |
| 6. Config + Infra Reference | `_status.py` | `StatusInfo` dataclass + `get_status()` + `render_status_table()` + `SecurityFinding` dataclass + `check_security()` + `render_security_findings()` |
| 6. Config + Infra Reference | `_telemetry.py` | `SQLiteSpanExporter` — OTel spans to SQLite with WAL mode |
| 6. Config + Infra Reference | `_tail.py` | Real-time span viewer (`co tail`) |
| 6. Config + Infra Reference | `_trace_viewer.py` | Static HTML trace viewer (`co traces`) |
| 6. Config + Infra Reference | `_banner.py` | ASCII art welcome banner |
| 6. Config + Infra Reference | `tests/test_tool_calling_functional.py` | Functional tool-calling gate — selection, args, refusal, intent routing, recovery |

---

## 5. Dependencies

### Runtime

| Package | Version | Purpose |
|---------|---------|---------|
| `pydantic-ai` | `==1.59.0` | LLM orchestration |
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

### Development

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | `>=9.0.2` | Testing framework |
| `pytest-asyncio` | `>=1.3.0` | Async test support |
| `pytest-cov` | `>=7.0.0` | Coverage reporting |
