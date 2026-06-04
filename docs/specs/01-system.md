# Co CLI System Design

This doc is the architectural map of `co-cli`: subsystems, core workflows, and the contracts between them. Component internals are owned by their per-subsystem specs listed in §2.

## 1. Functional Architecture

```
                    User
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│  co-cli                                                         │
│                                                                 │
│  ┌────────────┐   ┌─────────────────────────────────────────┐  │
│  │ Bootstrap  │──▶│  CoDeps                                 │  │
│  │ startup    │   │  config · model · shell · state         │  │
│  │ capability │   │  tool registry · skill registry         │  │
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
│  │  artifacts   │  │  JSONL       │  │    Structured JSONL   │ │
│  └──────────────┘  └──────────────┘  └───────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
          │                    │                    │
   LLM Provider         Workspace Files        MCP Servers
```

| Subsystem | Spec | Role |
|-----------|------|------|
| Bootstrap | [bootstrap.md](bootstrap.md) | Startup sequencing, degradation policy, `CoDeps` assembly |
| REPL / TUI | [tui.md](tui.md) | Prompt session, slash-command dispatch, completer |
| Agent loop | [core-loop.md](core-loop.md) | Turn orchestration, approval mechanics, retries |
| Prompt assembly | [prompt-assembly.md](prompt-assembly.md) | Instruction layers, history processors, recall injection |
| Compaction | [compaction.md](compaction.md) | Spill, proactive summarization, session JSONL rewrite |
| Memory | [memory.md](memory.md) | Memory tier: item storage, kind taxonomy, two-pass recall, `memory_create`/`append`/`replace`/`delete` |
| Sessions | [sessions.md](sessions.md) | Transcript storage, lexical (ripgrep) recall, `session_search` / `session_view` |
| Dream | [dream.md](dream.md) | Daemon reviewer + clock-driven housekeeping (memory + skill merge, decay, archive) |
| Tools | [tools.md](tools.md) | Tool registration, approval, `CoDeps` access patterns |
| Skills | [skills.md](skills.md) | Skill manifest, view/manage surface, dispatch |
| Personality | [personality.md](personality.md) | Soul files, canon injection, identity layer |
| Config | [config.md](config.md) | Settings model, env vars, load pipeline |
| Observability | [observability.md](observability.md) | Structured JSON-line spans log, `@trace` decorator, `co tail` / `co trace` viewers |
| Self-planning | [self-planning.md](self-planning.md) | `todo_write` / `todo_read` plan state, compaction snapshot, rehydration on `/resume` |
| Agents | [agents.md](agents.md) | Agent construction, orchestrator, daemon task agents |
| UAT Evals | [uat_evals.md](uat_evals.md) | End-to-end quality contract: eval registry, rubric discipline, gating policy |

### Package Dependency Direction (one-way rule)

```
main → bootstrap → agent → tools / context / config / knowledge / memory
```

- `main` owns CLI entrypoints and REPL lifecycle; it calls into `bootstrap` and `agent`.
- `bootstrap` assembles `CoDeps` at session start.
- `agent` builds the foreground agent and its toolset from `CoDeps`.
- `tools`, `context`, `config`, `knowledge`, `memory` are leaf packages — they do not import from each other or from `agent`, `bootstrap`, or `main`.
- All cross-package communication goes through `CoDeps` (passed via `RunContext[CoDeps]` in tool calls), never through direct imports.

Importing upward (e.g. a tool importing from `agent`) is a design error — fix the API.


## 2. Core Workflows

### Startup

`create_deps` is the one-shot assembly entrypoint — it validates config, probes the LLM, builds the model, wires MCP toolsets, loads skills, discovers the memory backend, and syncs both the knowledge index and canon store. On return, `CoDeps` is immutable except for mutable state buckets. The agent is built once and reused across turns. Degradation replaces hard failures: missing integrations narrow capability without aborting startup.

```
settings load → validate_config → [Ollama probe] → build_model
  → build_native_toolset → build_mcp_entries → MCP connect + discover
  → assemble_routing_toolset
  → load_skills
  → memory backend discovery → knowledge sync → canon sync
  → CoDeps sealed
  → build_orchestrator(ORCHESTRATOR_SPEC, deps)
  → restore_session
  → REPL ready
```

→ [bootstrap.md](bootstrap.md)

---

### Turn Execution

Each user turn runs `run_turn()` → pydantic-ai agent run → tool loop → post-turn writes. The key cross-subsystem steps within a turn:

```
run_turn(user_input)
  → prompt assembly  (personality + recall injection + history)
  → agent run        (LLM + tool loop with approval gates)
  → compaction check (spill → proactive → emit-time)
  → session persist  (append to JSONL)
  → review KICK      (session-end: REPL fires memory + skill review KICKs onto the daemon queue)
```

Approval gates run per-tool-call; retries wrap individual tool failures. The agent is not re-instantiated between turns.

→ [core-loop.md](core-loop.md) · [prompt-assembly.md](prompt-assembly.md) · [compaction.md](compaction.md)

---

### Prompt Assembly

Every turn's system prompt is assembled from three layers injected before the agent run:

1. **Static** — soul seed + mindsets + behavioral rules + toolset guidance + personality critique lens (injected at agent construction, byte-stable across turns)
2. **Per-turn instructions** — safety warnings, current time, deferred-tool awareness (per-tool stubs), and the `<available_skills>` skill manifest; emitted as `InstructionPart(dynamic=True)` so live `deps` state surfaces without churning the cached prefix
3. **Recall** — `memory_search` + `session_search` results injected into the turn context window

Recall is search-driven and on-demand — nothing is wholesale injected into every turn. History processors (compaction, spill placeholders) apply in the pre-run pass.

→ [prompt-assembly.md](prompt-assembly.md) · [personality.md](personality.md)

---

### Memory and Session Tiers

Memory and session are peer operational tiers with separate retrieval backends — memory is hybrid-indexed (`co-cli-search.db`, FTS5 + optional vec); sessions are searched lexically over the raw transcript files (no index):

- **Memory** (`~/.co-cli/memory/*.md`) — long-term declarative memory items: user preferences, rules, articles, notes. Model-writable via `memory_create`/`memory_append`/`memory_replace`/`memory_delete`. Extracted by the dream reviewer (in-session) and merged + decayed by the dream daemon's clock-driven housekeeping.
- **Session** (`~/.co-cli/sessions/*.jsonl`) — past conversation transcripts. Append-only. Recalled via file-based lexical (ripgrep) search returning line-cited snippets; full turns fetched via `session_view`.

Recall is always search-driven. Nothing is bulk-injected. Browse mode (empty query) returns recent-item metadata.

```
memory_search
  → IndexStore.search (FTS5 BM25 [+ vec cosine + cross-encoder rerank])
session_search
  → ripgrep over ~/.co-cli/sessions/*.jsonl (Python line-scan fallback)
  → snippet hits with line/path citations

memory_view / session_view
  → full memory item body / verbatim JSONL lines from disk
```

→ [memory.md](memory.md) · [sessions.md](sessions.md)

---

### Compaction

Compaction is context pressure management — keeps token use within the model's window across long sessions. Three mechanisms run in priority order:

1. **Spill** — oversized tool results written to disk; placeholder injected in place (`spill_ratio` threshold).
2. **Proactive** — mid-turn LLM summarization of the oldest history segment (`compaction_ratio` threshold); anti-thrash gate limits consecutive low-yield passes.
3. **Emit-time** — last-resort truncation at `tail_fraction` before a request would exceed the window.

Compaction rewrites the live session JSONL in place. The session UUID is unchanged.

→ [compaction.md](compaction.md)

---

### Dream Cycle

Triggered at session end. Runs offline (no user interaction):

1. **Mining** — extracts candidate knowledge from recent session transcripts via `llm_call`.
2. **Merge** — deduplicates against existing artifacts (Jaccard similarity); near-identical skipped, overlapping merged.
3. **Decay / archive** — scores artifacts by recency and recall frequency; eligible artifacts archived.

Dream runs are idempotent. The trigger is `session_end` by default; `manual` trigger also available.

→ [dream.md](dream.md)

---

### Skills

Skills are procedural capability units — YAML-fronted markdown files in `co_cli/skills/` (bundled) and `~/.co-cli/skills/` (user-installed). They are discovered at startup and surfaced through model-callable tools: `skill_view` (read) and `skill_create`/`skill_edit`/`skill_patch`/`skill_delete` (write). All discoverable skills (bundled and user-installed) are declared via the `<available_skills>` manifest, emitted as a per-turn dynamic instruction so newly created skills become visible to the model on the next turn. Slash commands in the REPL dispatch to installed skills via the `skill_index` map on `CoDeps`.

The REPL writes KICK files to `$CO_HOME/daemons/dream/queue/` at threshold-based intervals (configurable per domain: memory and skill). The dream daemon dequeues these and runs the appropriate reviewer agent out-of-process. The same daemon also runs scheduled-tick housekeeping over both the memory store and the user skill library (`merge_skills` + `decay_skills`) — see [dream.md](dream.md) for the full mechanics. Usage counters, `pinned`, and `recall_days` are persisted in per-skill sidecars at `~/.co-cli/skills/<name>.usage.json`.

→ [skills.md](skills.md) · [tui.md](tui.md)

---

### CoDeps Contract

`CoDeps` is the single shared runtime object. Everything that needs cross-subsystem access receives it by injection — there is no global mutable state.

```
CoDeps
├── config & services
│   ├── config            Settings singleton
│   ├── model             LlmModel handle
│   ├── shell             shell backend
│   ├── memory_store      MemoryStore (None when degraded to grep)
│   ├── resource_locks    ResourceLockStore — shared across forks
│   └── file_tracker      FileReadTracker — shared across forks
├── registries
│   ├── tool_index        dict[name, ToolInfo] — approval + span attribute lookup; forwarded to forks
│   ├── toolset           AbstractToolset[CoDeps] — orchestrator routing surface; excluded from forks
│   └── skill_index       dict[name, SkillInfo] — slash-command dispatch; forwarded to forks
├── mutable state
│   ├── session (CoSessionState)
│   │   ├── todos                 runtime self-plan
│   │   ├── background_tasks      active background coroutines
│   │   └── approval_rules        remembered session approval decisions
│   └── runtime (CoRuntimeState)
│       ├── compaction counters   proactive pass tracking
│       ├── turn_usage            token usage for current turn
│       └── persisted_msg_count   JSONL write cursor
├── paths
│   ├── workspace_dir        write/cwd anchor (file_write/file_patch land here)
│   ├── file_search_roots    read scope for file_read/file_search; defaults to [workspace_dir]
│   ├── knowledge_dir
│   ├── sessions_dir
│   ├── tool_results_dir
│   └── …
└── degradations          MappingProxyType — startup capability drops; read-only after bootstrap
```


## 3. Config

Settings most relevant to system assembly:

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm.provider` | `CO_LLM_PROVIDER` | `ollama` | Model provider for the session runtime |
| `llm.host` | `CO_LLM_HOST` | `http://localhost:11434` | Ollama-compatible host |
| `llm.model` | `CO_LLM_MODEL` | `qwen3.5:35b-a3b-q4_k_m-agentic` | Primary model for the foreground agent |
| `mcp_servers` | `CO_MCP_SERVERS` | bundled defaults | MCP server definitions attached during runtime assembly |
| `personality` | `CO_PERSONALITY` | `tars` | Personality assets injected during prompt assembly |
| `memory.search_backend` | `CO_MEMORY_SEARCH_BACKEND` | `hybrid` | Preferred retrieval backend before runtime degradation |
| `memory_path` | `CO_MEMORY_PATH` | `~/.co-cli/memory/` | User-global memory item store |
| `reasoning_display` | `CO_REASONING_DISPLAY` | `summary` | Terminal reasoning display mode for interactive turns |

Full settings reference: [config.md](config.md).


## 4. Public Interface

System-level contracts crossing every subsystem. Per-subsystem APIs are documented in their own specs.

| Symbol | Source | Contract |
|--------|--------|----------|
| `CoDeps` | `co_cli/deps.py` | Frozen runtime context passed via `RunContext[CoDeps]` to all tools; bundles config, model, registries, paths, and mutable `session` / `runtime` state |
| `create_deps(frontend, stack) -> CoDeps` | `co_cli/bootstrap/core.py` | Async one-shot startup assembly: validates config, probes the model, wires MCP, loads skills, builds the memory store, and seals `CoDeps` |
| `build_orchestrator(spec, deps) -> Agent[CoDeps, Any]` | `co_cli/agent/build.py` | Constructs the foreground orchestrator agent from `ORCHESTRATOR_SPEC` — composes static-instruction builders, registers per-turn instructions, attaches history processors, and reads toolset from `deps.toolset` |
| `build_task_agent(spec, deps, model) -> Agent[CoDeps, Any]` | `co_cli/agent/build.py` | Constructs a focused task agent from a `TaskAgentSpec`; resolves `spec.tool_names` against `TOOL_REGISTRY_BY_NAME`, filtered by `_config_requirement_met`; fails loud on unknown names |
| `run_turn(deps, agent, user_input, message_history, frontend) -> TurnResult` | `co_cli/context/orchestrate.py` | Async single-turn entrypoint: assembly → segment stream → approval loop → output checks |
| `fork_deps(base) -> CoDeps` | `co_cli/deps.py` | Builds a delegated `CoDeps` for sub-agents; forwards `tool_index`, excludes `toolset`, increments `agent_depth` |
| `fork_deps_for_reviewer(parent) -> CoDeps` | `co_cli/deps.py` | Fork for the dream daemon reviewer agent; delegates to `fork_deps` |
| `dispatch(raw_input, ctx) -> SlashOutcome` | `co_cli/commands/core.py` | Async slash-command router; returns `LocalOnly`, `ReplaceTranscript`, or `DelegateToAgent` |


## 5. Files

| File | Purpose |
|------|---------|
| `co_cli/main.py` | Top-level CLI lifecycle, REPL loop, and teardown |
| `co_cli/bootstrap/core.py` | Runtime assembly and startup flow |
| `co_cli/agent/build.py` | Agent builders (`build_orchestrator()`, `build_task_agent()`) |
| `co_cli/agent/spec.py` | `OrchestratorSpec` and `TaskAgentSpec` declarative records |
| `co_cli/agent/orchestrator.py` | `ORCHESTRATOR_SPEC` — always-present primary agent record |
| `co_cli/agent/run.py` | Task-agent runner: `run_standalone` (daemon) |
| `co_cli/agent/core.py` | Toolset composition helpers (`build_native_toolset`, `build_mcp_entries`, `assemble_routing_toolset`) |
| `co_cli/agent/toolset.py` | Native toolset construction and tool registry |
| `co_cli/agent/mcp.py` | MCP toolset wiring and discovery |
| `co_cli/agent/_instructions.py` | Dynamic instruction callbacks and prompt assembly |
| `co_cli/commands/core.py` | Slash-command dispatch and skill handoff into the REPL loop |
| `co_cli/deps.py` | `CoDeps` runtime contract and workspace path resolution |
| `co_cli/context/orchestrate.py` | One-turn execution entrypoint |


## 6. Test Gates

| Property | Test file |
|----------|-----------|
| Bootstrap assembles `CoDeps` and agent without errors | `tests/test_flow_chat_loop.py` |
| Degraded startup (missing memory backend) does not abort | `tests/test_flow_capability_checks.py` |
| Turn produces a non-empty model response | `tests/test_flow_chat_loop.py` |
| Tool call executes within a turn | `tests/test_flow_chat_loop.py` |
