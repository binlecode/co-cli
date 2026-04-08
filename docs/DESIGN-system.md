# Co CLI System Design

This doc covers the top-level runtime shape of `co-cli`. Startup checks and degradation rules live in [DESIGN-bootstrap.md](DESIGN-bootstrap.md), one-turn orchestration lives in [DESIGN-core-loop.md](DESIGN-core-loop.md), tool contracts live in [DESIGN-tools.md](DESIGN-tools.md), and skill behavior lives in [DESIGN-skills.md](DESIGN-skills.md).

## 1. What & How

`co-cli` is a local-first REPL built around one foreground `pydantic_ai.Agent` plus one optional lightweight resume agent. The system is assembled in two phases: synchronous bootstrap builds `CoDeps` and resolved model state, then async session activation enters the main agent context, discovers remote MCP tools, loads skills, syncs knowledge, and restores the session before the prompt loop starts.

```text
bootstrap
  main.py
    -> create_deps()
       -> settings singleton used directly (no CoConfig conversion)
       -> config.llm.validate_config()  # config shape only, no IO
       -> resolve_workspace_paths(settings, cwd)  # path fields onto CoDeps
       -> _discover_knowledge_backend()  # probe + construct store
       -> build CoDeps (config=settings, paths, services, runtime)
    -> build_agent()
       -> build_static_instructions()  # personality, rules, counter-steering

session activation
    -> restore_session()
    -> display banner

foreground turn
  PromptSession REPL
    -> slash dispatch or delegated skill input
    -> _run_foreground_turn()
    -> run_turn()
    -> tools / MCP / approvals
    -> history + session finalization
```

```mermaid
flowchart LR
    User[User] --> REPL[PromptSession REPL]
    REPL --> Main[co_cli/main.py]

    Main --> Bootstrap[create_deps()]
    Bootstrap --> Config[Settings]
    Bootstrap --> Deps[CoDeps]

    Main --> AgentFactory[build_agent()]
    Config --> AgentFactory
    Deps --> AgentFactory

    AgentFactory --> MainAgent[Main Agent]

    Main --> CreateDeps[create_deps]
    CreateDeps --> MCP[MCP discovery + skill loading + knowledge store]

    Main --> Turn[_run_foreground_turn()]
    Turn --> Orchestrate[run_turn()]
    Orchestrate --> MainAgent
    Orchestrate --> TaskAgent
    MainAgent --> Native[Native filtered toolset]
    MainAgent --> MCPTools[MCP toolsets]

    Native --> Workspace[Workspace + .co-cli]
    Native --> XDG[XDG config/data stores]
    MCPTools --> External[Remote servers / subprocess MCP]

    Main --> Telemetry[OTel + SQLiteSpanExporter]
```

## 2. Core Logic

### 2.1 Runtime Layers And Ownership

The running system is split into five owners:

| Owner | Responsibility |
| --- | --- |
| `co_cli/main.py` | CLI entrypoints, global telemetry wiring, REPL loop, startup sequencing, and teardown |
| `co_cli/bootstrap/_bootstrap.py` | bootstrap assembly, degradation decisions, knowledge sync, session restore, and session capability completion |
| `co_cli/agent.py` | main-agent construction, optional task-agent construction, native tool registration, MCP toolset construction, and MCP discovery |
| `co_cli/deps.py` | grouped runtime contract shared by tools, history processors, orchestration, and sub-agents |
| `co_cli/context/orchestrate.py` | one-turn execution, error handling, approval resumes, interrupt handling, and turn result assembly |

The design stays close to idiomatic Pydantic-AI:

- `CoDeps` is the single `deps_type`
- the main agent owns instructions, history processors, and toolsets
- tools consume `RunContext[CoDeps]`
- orchestration is outside tool bodies
- approval resume uses `DeferredToolRequests` / `DeferredToolResults`, not a custom loop protocol

### 2.2 Startup Sequencing

Startup is intentionally split between synchronous bootstrap and async activation:

1. `create_deps()` uses the `Settings` singleton directly and calls `resolve_workspace_paths(settings, cwd)` to resolve cwd-relative paths onto `CoDeps`.
2. `config.llm.validate_config()` performs the config-shape gate (no IO):
   - Missing reasoning role is a startup error.
   - Gemini without an API key is a startup error.
   - Ollama connectivity and model availability are deferred to runtime (`run_turn()` handles errors).
3. (Step 2b) When provider is `ollama-openai`, `probe_ollama_context()` probes `/api/show` for the reasoning model's Modelfile `num_ctx`. Fail-fast if `num_ctx < MIN_AGENTIC_CONTEXT` (64K). Overrides `config.llm.num_ctx` with the runtime value via direct field assignment when they differ.
4. `_discover_knowledge_backend()` resolves reranker and embedder availability, updates config fields directly to reflect the runtime backend, and constructs the store:
   - on grep: reranker and discovery skipped entirely (no index)
   - on hybrid/fts5: rerankers degrade independently to `None`
   - knowledge degrades through `hybrid -> fts5 -> grep`
   - `deps.degradations["knowledge"]` records what changed and why when degradation occurs
5. `create_deps()` constructs the final `CoDeps` with `config=settings`, resolved workspace paths, service handles (shell, knowledge_store), registries (model_registry, tool_index, skill_commands), and runtime state.
6. `main.py` looks up the resolved reasoning model from `deps.model_registry`.
7. `build_agent()` assembles static instructions (`build_static_instructions()`) and constructs the main foreground agent.
8. `async with agent` activates the main agent context, which is required before MCP discovery.
10. `restore_session()` scans `.co-cli/sessions/` by mtime for the latest session, or creates a new one; copies `session_id` into `deps.session`. See [DESIGN-context.md](DESIGN-context.md) §2.3 for session and transcript persistence.
11. The REPL loop starts.

Teardown is owned by `main.py`:

1. Kill running background tasks
2. `deps.shell.cleanup()`
3. `AsyncExitStack.aclose()` for the main agent context

### 2.3 Agent Construction After The SDK Upgrade

The foreground runtime uses a single main agent for all turns, including approval-resume segments (the SDK skips `ModelRequestNode` on the `deferred_tool_results` path, so resume adds zero tokens).

`build_agent()` bakes these pieces into the main agent:

1. resolved model object plus `ModelSettings`
2. static `instructions` from `build_static_instructions()`
3. five history processors:
   - `truncate_tool_results`
   - `compact_assistant_responses`
   - `detect_safety_issues`
   - `inject_opening_context`
   - `summarize_history_window`
4. `CoToolLifecycle` capability — `before_tool_execute` (path normalization for file tools), `after_tool_execute` (OTel span enrichment + audit logging)
5. a native `FunctionToolset` wrapped by `filtered(...)`
6. optional MCP toolsets built from `config.mcp_servers`
7. per-turn dynamic instruction layers:
   - current date
   - shell guidance
   - project instructions from `.co-cli/instructions.md`
   - always-on memories
   - personality memories
   - deferred tool prompt (dynamic list of undiscovered deferred tools via `build_deferred_tool_prompt`; instructs model to call `search_tools`)

The filtered native toolset matters for approval resumes:

- native tools are always registered once at startup
- some domain tools are omitted entirely when the integration is absent
- `_filter` uses per-tool `LoadPolicy` (`ALWAYS`/`DEFERRED`) from `deps.tool_index`, plus `deps.session.discovered_tools` and `deps.runtime.resume_tool_names`, to decide visibility per API call
- MCP tools in `tool_index` follow the same visibility rule; unknown tools not in `tool_index` are hidden (default-deny)

Global instrumentation is enabled once in `main.py`:

- `TracerProvider` uses `SQLiteSpanExporter`
- `Agent.instrument_all(InstrumentationSettings(version=3, tracer_provider=...))` turns on Pydantic-AI OTel instrumentation for all agents

### 2.4 `CoDeps`: The Runtime Contract

`CoDeps` is the only dependency object passed into Pydantic-AI tools and history processors.

```text
CoDeps
├── config        Settings instance (read-only after bootstrap)
├── (top-level)   service handles, registries, workspace paths, degradations
├── session       mutable session state visible to tools
└── runtime       mutable orchestration / processor state
```

Practical ownership rules:

| Group | Holds | Mutation model |
| --- | --- | --- |
| `services` | shell backend, knowledge index, model registry, tool index, skill commands, resource lock store | built once; shared by reference |
| `config` | `Settings` instance (Pydantic BaseModel); read-only after bootstrap | read-only after bootstrap |
| `session` | creds cache, approval memory, todos, session-visible recall state | mutable across turns |
| `runtime` | per-turn usage/safety/progress/filter state plus cross-turn compaction/skill state | reset or managed by orchestration |

Sub-agent isolation is explicit in `make_subagent_deps(base)`:

- shared by reference: service handles, registries, `config`, `resource_locks`, workspace paths, `degradations`
- inherited as copied session state: `session_approval_rules`
- inherited as shared resolved creds: `google_creds`, `google_creds_resolved`
- reset for the child: `drive_page_tokens`, `session_todos`, `session_id`, `memory_recall_state`, all runtime fields

### 2.5 State Lifecycle Reference

#### Service handles and registries — top-level on `CoDeps`

Shared by reference with sub-agents. Service handles (shell, knowledge_store) and bootstrap-set registries (tool_index, skill_commands, model_registry) are top-level fields on `CoDeps`.

| Field | Set by | Reset by | Sub-agent |
| --- | --- | --- | --- |
| `tool_index` | `create_deps()` builds native `ToolInfo` map via `build_tool_registry()` and merges MCP entries via `discover_mcp_tools()` | never | shared ref |
| `skill_commands` | `create_deps()` via `_load_skills()` | replaced on skill reload | shared ref |
| `model_registry` | `_chat_loop()` via `ModelRegistry.from_config()` | never | shared ref |

#### `CoSessionState` — `deps.session`

Visible to tools and slash commands.

| Field | Set by | Reset by | Sub-agent |
| --- | --- | --- | --- |
| `google_creds` | first Google tool call via `get_cached_google_creds()` | never | inherited ref |
| `google_creds_resolved` | first Google tool call via `get_cached_google_creds()` | never | inherited |
| `session_approval_rules` | `_collect_deferred_tool_approvals()` via `record_approval_choice()` | `/approvals clear` or session end | inherited as copy |
| `drive_page_tokens` | Drive pagination tools | never | fresh empty |
| `session_todos` | todo tools | never | fresh empty |
| `session_id` | `restore_session()` | session restart | fresh empty |
| `memory_recall_state` | `inject_opening_context()` | new REPL session | fresh default |
| `background_tasks` | `start_background_task` tool | never (in-memory only) | fresh empty dict |
| `discovered_tools` | `search_tools()` discovers deferred tools | `/new` (session reset) | fresh empty set |

#### `CoRuntimeState` — `deps.runtime`

Owned by orchestration and history processors.

| Field | Set by | Reset by | Sub-agent |
| --- | --- | --- | --- |
| `compaction_failure_count` | `summarize_history_window()` on inline summarization failure | reset to 0 on success; NOT reset by `reset_for_turn()` | fresh `0` |
| `turn_usage` | `run_turn()` init + `_merge_turn_usage()` | `reset_for_turn()` | fresh `None` |
| `tool_progress_callback` | `StreamRenderer.install_progress()` | `StreamRenderer.clear_progress()` and `run_turn()` finally | fresh `None` |
| `safety_state` | `create_deps()` init and `reset_for_turn()` | `reset_for_turn()` | fresh default |
| `active_skill_name` | `_chat_loop()` when a skill delegates to the agent | `_cleanup_skill_run_state()` | fresh `None` |
| `resume_tool_names` | `_run_approval_loop()` sets `frozenset` of approved deferred tool names before each resume hop | `_run_approval_loop()` exit and `reset_for_turn()` | fresh `None` |

One important split is intentional:

- persisted session JSON lives in `session_data` local variables in `main.py`
- only the `session_id` copy is exposed to tools through `deps.session.session_id`

### 2.6 Capability Surface

There are three related capability surfaces:

| Surface | Completed by | Includes |
| --- | --- | --- |
| static tool definitions | `build_agent()` | native filtered toolset plus MCP toolset definitions |
| connected session capabilities | `create_deps()` | discovered MCP tool names plus loaded skills |
| runtime-visible native schema subset | `_filter` in `_build_filtered_toolset()` (main turns: `ALWAYS` tools + `discovered_tools`; resume turns: `resume_tool_names` + `ALWAYS` tools) | owned by per-tool `LoadPolicy` in `agent.py` |

Key distinctions:

- skills are not tools; they rewrite user input into delegated agent input
- MCP toolsets are attached during agent construction but only discovered after entering the main agent context
- `deps.tool_index` is the single source of truth for tool capability state; tool names and approvals are derived from it
- the `_filter` function in `_build_filtered_toolset()` uses per-tool loading policy to narrow the native `FunctionToolset`

### 2.7 Persistent Stores And External State

The runtime writes to a small, explicit set of stores:

| Store | Purpose | Writer |
| --- | --- | --- |
| `~/.local/share/co-cli/co-cli-logs.db` | OpenTelemetry span storage | `SQLiteSpanExporter` |
| `~/.local/share/co-cli/co-cli-search.db` | knowledge index storage | `KnowledgeStore` |
| `<cwd>/.co-cli/memory/` | project-local memory markdown | memory lifecycle and memory tools |
| configured library dir, default `~/.local/share/co-cli/library/` | article markdown store | article tools |
| `<cwd>/.co-cli/sessions/{id}.json` | session id, timestamps, compaction count | session helpers in `context/session.py` |
| `<cwd>/.co-cli/sessions/{id}.jsonl` | JSONL conversation transcript (append-only) | `context/transcript.py` |
| `~/.config/co-cli/google_token.json` | cached Google authorized-user credentials | Google auth helper |

### 2.8 Failure And Degradation Boundaries

Bootstrap prefers graceful degradation over early failure, except where the foreground agent cannot function at all.

Hard startup failures:

- Gemini without API credentials
- missing reasoning model
- invalid MCP server configuration during settings validation
- empty or invalid prompt assembly inputs

Graceful degradation:

- Ollama host unreachable
- missing optional role models
- unavailable cross-encoder reranker
- unavailable LLM reranker
- hybrid knowledge unavailable, falling back to `fts5` or `grep`
- knowledge sync failure, which closes and disables the live knowledge index for the session
- MCP connection or tool-list failure, which logs errors via `frontend.on_status()`

## 3. Config

These are the system-level settings that most directly shape runtime assembly.

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `llm.provider` | `LLM_PROVIDER` | `ollama-openai` | Session-wide default provider unless a role overrides it |
| `llm.host` | `LLM_HOST` | `http://localhost:11434` | Ollama OpenAI-compatible base host |
| `llm.api_key` | `LLM_API_KEY` | unset | Required for Gemini and provider-specific auth flows |
| `llm.role_models` | `CO_MODEL_ROLE_<ROLE>` | built-in role map | Per-role model selection for reasoning, summarization, coding, research, analysis, and task |
| `mcp_servers` | `CO_CLI_MCP_SERVERS` | bundled GitHub + Context7 defaults | MCP transport definitions and approval mode |
| `personality` | `CO_CLI_PERSONALITY` | `tars` | Personality assets used during prompt assembly and instruction injection |
| `knowledge.search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `hybrid` | Preferred knowledge backend before degradation |
| `knowledge.embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `tei` | Embedding provider used for hybrid search |
| `knowledge.cross_encoder_reranker_url` | `CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL` | `http://127.0.0.1:8282` | Optional TEI reranker endpoint |
| `library_path` | `CO_LIBRARY_PATH` | `~/.local/share/co-cli/library` | Global article store root; resolved to `deps.library_dir` |
| _(removed)_ | | | Session TTL removed — sessions persist indefinitely; new session via `/new` |
| `reasoning_display` | `CO_CLI_REASONING_DISPLAY` | `summary` | Terminal reasoning display mode (`off`, `summary`, `full`) |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/main.py` | CLI entrypoints, global telemetry wiring, REPL startup, foreground-turn wrapper, and teardown |
| `co_cli/bootstrap/_bootstrap.py` | bootstrap assembly, degradation, knowledge sync, session restore, and capability completion |
| `co_cli/agent.py` | main agent factory, native filtered toolset construction, MCP toolset construction, and MCP discovery |
| `co_cli/deps.py` | grouped dependency dataclasses, runtime/session/capability state, and sub-agent isolation |
| `co_cli/_model_factory.py` | session-scoped resolved model registry by role |
| `co_cli/prompts/_assembly.py` | static system prompt assembly |
| `co_cli/context/orchestrate.py` | one-turn orchestration, error handling, approvals, output-limit checks, and interrupts |
| `co_cli/context/_history.py` | history processors: tool-output trim, safety detection, memory injection, and sliding-window compaction trigger |
| `co_cli/context/summarization.py` | summarization, budget resolution, and token estimation — shared by history processor and `/compact` |
| `co_cli/context/session.py` | session JSON persistence helpers |
| `co_cli/context/transcript.py` | JSONL transcript append, compact-boundary write, and load-with-boundary-skip |
| `co_cli/context/session_browser.py` | session listing, `SessionSummary`, and session summary generation for UI |
| `co_cli/context/types.py` | shared safety and memory-recall dataclasses |
| `co_cli/commands/_commands.py` | built-in slash commands, skill loading, and slash dispatch |
| `co_cli/display/_core.py` | terminal frontend surfaces and approval prompting |
| `co_cli/display/_stream_renderer.py` | event-to-frontend stream buffering and progress callback wiring |
| `co_cli/bootstrap/_render_status.py` | `StatusResult`, rendered status table, and security checks |
| `co_cli/bootstrap/_check.py` | provider, model, embedder, reranker, and MCP health checks |
| `docs/DESIGN-bootstrap.md` | bootstrap-specific details |
| `docs/DESIGN-core-loop.md` | foreground turn execution details |
| `docs/DESIGN-context.md` | context layers, history processors, memory, and knowledge index |
| `docs/DESIGN-observability.md` | tracing, span schema, and trace viewer |
| `docs/DESIGN-tools.md` | native tool and approval behavior |
| `docs/DESIGN-skills.md` | skill loading and dispatch |
| `docs/DESIGN-llm-models.md` | provider and role-model rules |
