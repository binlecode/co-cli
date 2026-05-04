# Co CLI System Design


This doc defines the runtime architecture of `co-cli`: subsystems, boundaries, and cross-subsystem contracts. Component internals are owned by their component specs; see section 2.4 for the full cross-reference map.

## 1. What & How

`co-cli` is a local-first, approval-first terminal agent. A Typer CLI starts a REPL, startup assembles a `CoDeps` runtime and a single foreground `Agent`, and each user turn runs through one orchestration entrypoint that can call native tools, MCP tools, and persistent context stores. Durable state lives outside the model: session transcripts, knowledge artifacts, the knowledge index, tool-result spill files, and telemetry are all stored on disk and reloaded or queried when needed.

```
                    User
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│  co-cli                                                         │
│                                                                 │
│  ┌────────────┐   ┌─────────────────────────────────────────┐  │
│  │ Bootstrap  │──▶│  CoDeps                                 │  │
│  │ startup    │   │  config · model · shell · state         │  │
│  │ capability │   │  tool registry · skill commands         │  │
│  │ discovery  │   │  paths · degradations                   │  │
│  └────────────┘   └─────────────────┬───────────────────────┘  │
│                                     │ injected into all         │
│  ┌──────────────────────────────────▼──────────────────────┐   │
│  │  REPL Loop                                              │   │
│  │  ┌───────────────────────┐  ┌──────────────────────┐   │   │
│  │  │  TUI / Commands       │  │  Foreground Turn     │   │   │
│  │  │  prompt · completer   │  │  orchestrate         │   │   │
│  │  │  slash dispatch       │  │  approval · retries  │   │   │
│  │  └───────────────────────┘  └──────────┬───────────┘   │   │
│  └─────────────────────────────────────────┼───────────────┘   │
│                                            │                    │
│  ┌─────────────────────────────────────────▼────────────────┐  │
│  │  Foreground Agent                                        │  │
│  │  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  │  │
│  │  │ Native Tools │  │  MCP Toolsets │  │  Personality │  │  │
│  │  └──────────────┘  └───────────────┘  └──────────────┘  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │  Knowledge   │  │  Sessions    │  │     Observability     │ │
│  │  artifacts   │  │  JSONL       │  │     OTel → SQLite     │ │
│  └──────────────┘  └──────────────┘  └───────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
          │                    │                    │
   LLM Provider         Workspace Files        MCP Servers
```

## 2. Core Logic

### 2.1 Runtime Shape

The runtime is split into a small set of top-level owners:

| Owner | Responsibility |
| --- | --- |
| `co_cli/main.py` | CLI entrypoints, REPL loop, top-level lifecycle, and teardown |
| `co_cli/bootstrap/` | session startup, runtime assembly, and capability discovery |
| `co_cli/agent/` | agent construction, instruction layers, and toolset assembly |
| `co_cli/commands/` | slash-command dispatch and skill delegation entrypoints |
| `co_cli/context/` | foreground-turn orchestration, history management, sessions, and transcripts |
| `co_cli/display/` | terminal rendering, prompt UX, and approval interaction |
| `co_cli/observability/` | telemetry export and trace storage plumbing |

This keeps the architecture intentionally simple:

- startup prepares the runtime once per session
- the agent is built once and reused across turns
- orchestration owns one-turn execution and approval resumes
- tools and storage are accessed through `CoDeps`, not through global mutable state

### 2.2 Session Lifecycle

The system has three phases:

1. **Startup**: load settings, resolve workspace paths, construct `CoDeps`, connect optional MCP servers, load skills, resolve the knowledge backend, build the agent, and restore or create the current session.
2. **Interactive session**: run the REPL, dispatch slash commands locally when possible, and send agent turns through `run_turn()` when model work is needed.
3. **Teardown**: drain background work, clean up the shell backend, and close async resources such as MCP connections.

Startup sequencing detail is in [bootstrap.md](bootstrap.md); turn execution in [core-loop.md](core-loop.md).

### 2.3 Runtime Contract

`CoDeps` is the shared runtime contract passed into tools and agent-side helpers. It carries:

- **Config & Services** (read-only or long-lived after bootstrap)
  - `config`: `Settings` object
  - `shell`: `ShellBackend` handle
  - `model`: `LlmModel` handle
  - `memory_store`: Optional `MemoryStore` integration
  - `resource_locks`: Shared `ResourceLockStore`
  - `file_read_mtimes`: Staleness detection registry
- **Registries** (bootstrap-built tool definitions)
  - `tool_index`: Flat dict of all `ToolInfo` metadata
  - `tool_registry`: Live `pydantic-ai` ToolRegistry
  - `skill_commands`: Discovered `SkillConfig` instances
- **Mutable State** (split by lifecycle)
  - `session`: `CoSessionState` (persists across turns: `session_todos`, `background_tasks`, `session_approval_rules`, etc.)
  - `runtime`: `CoRuntimeState` (managed by orchestration: `turn_usage`, `compaction_skip_count`, `compaction_applied_this_turn`, `consecutive_low_yield_proactive_compactions`, `previous_compaction_summary`, `post_compaction_token_estimate`, `message_count_at_last_compaction`)
- **Paths** (resolved workspace and user-global paths)
  - `workspace_root`, `knowledge_dir`, `sessions_dir`, etc.
- **Degradations**
  - `degradations`: Dict of startup-detected capability drops

The important architectural rule is that `co-cli` does not hide these concerns behind multiple config or service facades. Bootstrap assembles one runtime object, and the rest of the system consumes that object directly.

### 2.4 System Boundaries

The system is deliberately local-first:

- the primary control loop, persistent stores, and telemetry are local
- external systems are reached either during startup capability setup or through explicit tool boundaries at turn time
- write-capable agent actions go through the approval model
- missing or unhealthy optional integrations degrade capability rather than redefining the core loop

Persistent state is also intentionally small in surface area:

- all user/session state lives under `~/.co-cli/` (knowledge, sessions, skills, settings)
- model context is rebuilt from files, settings, and history instead of being treated as hidden process state

The specialized DESIGN docs own the detailed behavior inside each boundary:

- bootstrap order and degradation policy: [bootstrap.md](bootstrap.md)
- turn execution, approvals, and retries: [core-loop.md](core-loop.md)
- prompt assembly, instruction layers, and history processors: [prompt-assembly.md](prompt-assembly.md)
- compaction mechanisms (emit-time, prepass, window, overflow): [compaction.md](compaction.md)
- session transcripts, knowledge artifacts (including canon as `kind='canon'`), and retrieval: [memory.md](memory.md)
- dream-cycle mining, merge, decay, archive, and state: [dream.md](dream.md)
- REPL loop, completer, and slash commands: [tui.md](tui.md)
- tool registration and approval behavior: [tools.md](tools.md)
- skill loading and dispatch: [skills.md](skills.md)
- personality configuration, character assets, and soul files: [personality.md](personality.md)
- provider, model, and configuration: [config.md](config.md)
- tracing and log viewers: [observability.md](observability.md)

## 3. Config

These settings most directly affect top-level system assembly.

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `llm.provider` | `CO_LLM_PROVIDER` | `ollama` | Default model provider used for the session runtime |
| `llm.host` | `CO_LLM_HOST` | `http://localhost:11434` | Ollama-compatible host used during model setup and runtime calls |
| `llm.model` | `CO_LLM_MODEL` | `qwen3.5:35b-a3b-think` | Primary model name used when building the foreground agent |
| `mcp_servers` | `CO_MCP_SERVERS` | bundled defaults | MCP server definitions attached during runtime assembly |
| `personality` | `CO_PERSONALITY` | `tars` | Personality assets injected during prompt assembly |
| `knowledge.search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `hybrid` | Preferred retrieval backend before runtime degradation |
| `knowledge_path` | `CO_KNOWLEDGE_PATH` | `~/.co-cli/knowledge/` | User-global knowledge artifact store |
| `reasoning_display` | `CO_REASONING_DISPLAY` | `summary` | Terminal reasoning display mode for interactive turns |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/main.py` | Top-level CLI lifecycle, REPL loop, and teardown |
| `co_cli/bootstrap/core.py` | Runtime assembly and startup flow |
| `co_cli/agent/core.py` | Foreground agent factory (`build_agent()`) |
| `co_cli/agent/_native_toolset.py` | Native toolset construction and tool registry |
| `co_cli/agent/mcp.py` | MCP toolset wiring and discovery |
| `co_cli/agent/_instructions.py` | Dynamic instruction callbacks and prompt assembly |
| `co_cli/commands/core.py` | Slash-command dispatch and skill handoff into the REPL loop |
| `co_cli/deps.py` | Shared runtime contract and workspace path resolution |
| `co_cli/context/orchestrate.py` | One-turn execution entrypoint |
