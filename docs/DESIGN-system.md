# Co CLI System Design

This doc covers the top-level runtime shape of `co-cli`. Startup checks and degradation rules live in [DESIGN-bootstrap.md](DESIGN-bootstrap.md), one-turn orchestration lives in [DESIGN-core-loop.md](DESIGN-core-loop.md), tool contracts live in [DESIGN-tools.md](DESIGN-tools.md), and skill behavior lives in [DESIGN-skills.md](DESIGN-skills.md).

## 1. What & How

`co-cli` is a local-first REPL built around one foreground `pydantic_ai.Agent` plus one optional lightweight resume agent. The system is assembled in two phases: synchronous bootstrap builds `CoDeps` and resolved model state, then async session activation enters the main agent context, discovers remote MCP tools, loads skills, syncs knowledge, and restores the session before the prompt loop starts.

```text
bootstrap
  main.py
    -> create_deps()
       -> CoConfig.from_settings()
       -> config.validate()  # config shape only, no IO
       -> resolve_knowledge_backend()  # includes reranker resolution
       -> build CoServices / CoRuntimeState / CoDeps
    -> build_agent()
       -> build_static_instructions()  # personality, rules, counter-steering
    -> build_task_agent() when ROLE_TASK is configured

session activation
  async with main agent
    -> initialize_session_capabilities()
    -> sync_knowledge()
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
    Bootstrap --> Config[CoConfig]
    Bootstrap --> Services[CoServices]
    Bootstrap --> Deps[CoDeps]

    Main --> AgentFactory[build_agent()]
    Main --> TaskAgentFactory[build_task_agent()]
    Config --> AgentFactory
    Config --> TaskAgentFactory
    Deps --> AgentFactory
    Deps --> TaskAgentFactory

    AgentFactory --> MainAgent[Main Agent]
    TaskAgentFactory --> TaskAgent[Task Agent]

    Main --> Activate[initialize_session_capabilities()]
    MainAgent --> Activate
    Activate --> MCP[MCP discovery + skill loading]

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
| `co_cli/context/_orchestrate.py` | one-turn execution, provider retries, approval resumes, interrupt handling, and turn result assembly |

The design stays close to idiomatic Pydantic-AI:

- `CoDeps` is the single `deps_type`
- the main agent owns instructions, history processors, and toolsets
- tools consume `RunContext[CoDeps]`
- orchestration is outside tool bodies
- approval resume uses `DeferredToolRequests` / `DeferredToolResults`, not a custom loop protocol

### 2.2 Startup Sequencing

Startup is intentionally split between synchronous bootstrap and async activation:

1. `create_deps()` resolves cwd-aware config with `CoConfig.from_settings(settings, cwd=Path.cwd())`.
2. `config.validate()` performs the config-shape gate (no IO):
   - Missing reasoning role is a startup error.
   - Gemini without an API key is a startup error.
   - Ollama connectivity and model availability are deferred to runtime (`run_turn()` handles errors).
3. `resolve_knowledge_backend()` degrades capabilities in place:
   - on grep: reranker skipped entirely (no index to rerank against)
   - on hybrid/fts5: rerankers degrade independently to `None`
   - knowledge degrades through `hybrid -> fts5 -> grep`
4. `create_deps()` constructs:
   - `CoServices(shell, knowledge_index, model_registry)`
   - `CoRuntimeState(safety_state=SafetyState())`
   - the final `CoDeps`
5. `main.py` looks up the resolved reasoning model from `deps.services.model_registry`.
6. `build_agent()` assembles static instructions (`build_static_instructions()`) and constructs the main foreground agent.
7. `build_task_agent()` constructs the lightweight resume agent only when `ROLE_TASK` is configured.
8. `async with agent` activates the main agent context, which is required before MCP discovery.
9. `initialize_session_capabilities()` completes the session capability surface:
    - discovers MCP tool names from connected servers
    - logs discovery errors via `frontend.on_status()`
    - loads skills into `deps.services.skill_commands`
11. `sync_knowledge()` syncs memory and article directories into the active `KnowledgeIndex` when available.
12. `restore_session()` restores or creates `.co-cli/session.json`, copying only the `session_id` into `deps.session`.
13. The REPL loop starts.

Teardown is owned by `main.py`:

1. `HistoryCompactionState.shutdown()`
2. `AsyncExitStack.aclose()` for the main agent context
3. `deps.services.shell.cleanup()`

### 2.3 Agent Construction After The SDK Upgrade

The foreground runtime now has two distinct agent surfaces:

| Agent | Built by | Purpose |
| --- | --- | --- |
| main agent | `build_agent()` | normal foreground turns |
| task agent | `build_task_agent()` | approval-resume hops inside an existing turn |

`build_agent()` bakes these pieces into the main agent:

1. resolved model object plus `ModelSettings`
2. static `instructions=config.static_instructions`
3. four history processors:
   - `truncate_tool_returns`
   - `detect_safety_issues`
   - `inject_opening_context`
   - `truncate_history_window`
4. a native `FunctionToolset` wrapped by `filtered(...)`
5. optional MCP toolsets built from `config.mcp_servers`
6. per-turn dynamic instruction layers:
   - current date
   - shell guidance
   - project instructions from `.co-cli/instructions.md`
   - always-on memories
   - personality memories
   - deferred tool prompt (dynamic list of undiscovered deferred tools via `build_deferred_tool_prompt`; instructs model to call `search_tools`)

The filtered native toolset matters for approval resumes:

- native tools are always registered once at startup
- some domain tools are omitted entirely when the integration is absent
- `_filter` uses per-tool `always_load`/`should_defer` flags from `deps.services.tool_index`, plus `deps.session.discovered_tools` and `deps.runtime.resume_tool_names`, to decide visibility per API call
- MCP tools in `tool_index` follow the same visibility rule; MCP tools not yet in `tool_index` pass through the filter

`build_task_agent()` intentionally removes the heavy prompt/context layers:

- short fixed system prompt
- same native filtered toolset construction path
- same MCP toolset definitions
- no history processors
- no dynamic date/project/personality/memory instruction layers

Global instrumentation is enabled once in `main.py`:

- `TracerProvider` uses `SQLiteSpanExporter`
- `Agent.instrument_all(InstrumentationSettings(version=3, tracer_provider=...))` turns on Pydantic-AI OTel instrumentation for all agents

### 2.4 `CoDeps`: The Runtime Contract

`CoDeps` is the only dependency object passed into Pydantic-AI tools and history processors.

```text
CoDeps
├── services      service handles + bootstrap-set registries
├── config        resolved read-only session config
├── session       mutable session state visible to tools
└── runtime       mutable orchestration / processor state
```

Practical ownership rules:

| Group | Holds | Mutation model |
| --- | --- | --- |
| `services` | shell backend, knowledge index, model registry, task agent, tool index, skill commands | built once; shared by reference |
| `config` | resolved scalar settings and paths | read-only after bootstrap |
| `session` | creds cache, approval memory, todos, session-visible recall state | mutable across turns |
| `runtime` | per-turn usage/safety/progress/filter state plus cross-turn compaction/skill state | reset or managed by orchestration |

Sub-agent isolation is explicit in `make_subagent_deps(base)`:

- shared by reference: `services`, `config`
- inherited as copied session state: `session_approval_rules`
- inherited as shared resolved creds: `google_creds`, `google_creds_resolved`
- reset for the child: `drive_page_tokens`, `session_todos`, `session_id`, `memory_recall_state`, all runtime fields

### 2.5 State Lifecycle Reference

#### `CoServices` registries — `deps.services`

Shared by reference with sub-agents. Service handles (shell, knowledge_index) and bootstrap-set registries (tool_index, skill_commands, model_registry) live together.

| Field | Set by | Reset by | Sub-agent |
| --- | --- | --- | --- |
| `tool_index` | `build_agent()` seeds native `ToolConfig` map (with `always_load`/`should_defer` flags); `initialize_session_capabilities()` merges MCP `ToolConfig` entries after discovery | never | shared ref |
| `skill_commands` | `initialize_session_capabilities()` via `set_skill_commands()` | replaced on skill reload | shared ref |
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
| `precomputed_compaction` | `HistoryCompactionState.on_turn_start()` harvest | `HistoryCompactionState.on_turn_end()` | fresh `None` |
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
| static tool definitions | `build_agent()` / `build_task_agent()` | native filtered toolset plus MCP toolset definitions |
| connected session capabilities | `initialize_session_capabilities()` | discovered MCP tool names plus loaded skills |
| runtime-visible native schema subset | `_filter` in `_build_filtered_toolset()` (main turns: `always_load` tools + `discovered_tools`; resume turns: `resume_tool_names` + `always_load` tools) | owned by per-tool loading policy in `agent.py` |

Key distinctions:

- skills are not tools; they rewrite user input into delegated agent input
- MCP toolsets are attached during agent construction but only discovered after entering the main agent context
- `deps.services.tool_index` is the single source of truth for tool capability state; tool names and approvals are derived from it
- the `_filter` function in `_build_filtered_toolset()` uses per-tool loading policy to narrow the native `FunctionToolset`

### 2.7 Persistent Stores And External State

The runtime writes to a small, explicit set of stores:

| Store | Purpose | Writer |
| --- | --- | --- |
| `~/.local/share/co-cli/co-cli-logs.db` | OpenTelemetry span storage | `SQLiteSpanExporter` |
| `~/.local/share/co-cli/co-cli-search.db` | knowledge index storage | `KnowledgeIndex` |
| `<cwd>/.co-cli/memory/` | project-local memory markdown | memory lifecycle and memory tools |
| configured library dir, default `~/.local/share/co-cli/library/` | article markdown store | article tools |
| `<cwd>/.co-cli/session.json` | session id, timestamps, compaction count | session helpers in `context/_session.py` |
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
| `llm_provider` | `LLM_PROVIDER` | `ollama-openai` | Session-wide default provider unless a role overrides it |
| `llm_host` | `LLM_HOST` | `http://localhost:11434` | Ollama OpenAI-compatible base host |
| `llm_api_key` | `LLM_API_KEY` | unset | Required for Gemini and provider-specific auth flows |
| `role_models` | `CO_MODEL_ROLE_<ROLE>` | built-in role map | Per-role model selection for reasoning, summarization, coding, research, analysis, and task |
| `mcp_servers` | `CO_CLI_MCP_SERVERS` | bundled GitHub + Context7 defaults | MCP transport definitions and approval mode |
| `personality` | `CO_CLI_PERSONALITY` | `finch` | Personality assets used during prompt assembly and instruction injection |
| `knowledge_search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `hybrid` | Preferred knowledge backend before degradation |
| `knowledge_embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `tei` | Embedding provider used for hybrid search |
| `knowledge_cross_encoder_reranker_url` | `CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL` | `http://127.0.0.1:8282` | Optional TEI reranker endpoint |
| `memory_dir` | n/a | `<cwd>/.co-cli/memory` | Project-local memory root after cwd resolution |
| `library_path` | `CO_LIBRARY_PATH` | `~/.local/share/co-cli/library` | Global article store root |
| `session_ttl_minutes` | `CO_SESSION_TTL_MINUTES` | `60` | Session freshness window for restore |
| `reasoning_display` | `CO_CLI_REASONING_DISPLAY` | `summary` | Terminal reasoning display mode (`off`, `summary`, `full`) |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/main.py` | CLI entrypoints, global telemetry wiring, REPL startup, foreground-turn wrapper, and teardown |
| `co_cli/bootstrap/_bootstrap.py` | bootstrap assembly, degradation, knowledge sync, session restore, and capability completion |
| `co_cli/agent.py` | main/task agent factories, native filtered toolset construction, MCP toolset construction, and MCP discovery |
| `co_cli/deps.py` | grouped dependency dataclasses, runtime/session/capability state, and sub-agent isolation |
| `co_cli/_model_factory.py` | session-scoped resolved model registry by role |
| `co_cli/prompts/_assembly.py` | static system prompt assembly |
| `co_cli/context/_orchestrate.py` | one-turn orchestration, retries, approvals, output-limit checks, and interrupts |
| `co_cli/context/_history.py` | history processors and background compaction lifecycle |
| `co_cli/context/_session.py` | session JSON persistence helpers |
| `co_cli/context/_types.py` | shared compaction and safety dataclasses |
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
