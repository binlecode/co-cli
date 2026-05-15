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

| Subsystem | Spec | Role |
|-----------|------|------|
| Bootstrap | [bootstrap.md](bootstrap.md) | Startup sequencing, degradation policy, `CoDeps` assembly |
| REPL / TUI | [tui.md](tui.md) | Prompt session, slash-command dispatch, completer |
| Agent loop | [core-loop.md](core-loop.md) | Turn orchestration, approval mechanics, retries |
| Prompt assembly | [prompt-assembly.md](prompt-assembly.md) | Instruction layers, history processors, recall injection |
| Compaction | [compaction.md](compaction.md) | Spill, proactive summarization, session JSONL rewrite |
| Memory | [memory.md](memory.md) | Memory tier overview: knowledge + session channels, search surface |
| Knowledge | [knowledge.md](knowledge.md) | Artifact storage, kind taxonomy, `knowledge_manage` |
| Sessions | [sessions.md](sessions.md) | Transcript storage, chunking, `session_search` / `session_view` |
| Dream cycle | [dream.md](dream.md) | Session-end mining, knowledge merge, decay, archive |
| Tools | [tools.md](tools.md) | Tool registration, approval, `CoDeps` access patterns |
| Skills | [skill.md](skill.md) | Skill manifest, view/manage surface, dispatch |
| Personality | [personality.md](personality.md) | Soul files, canon injection, identity layer |
| Config | [config.md](config.md) | Settings model, env vars, load pipeline |
| Observability | [observability.md](observability.md) | OTel spans, JSONL logs, trace viewer |
| Self-planning | [self-planning.md](self-planning.md) | `todo_write` / `todo_read` plan state, compaction snapshot, rehydration on `/resume` |

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
  → build_tool_registry → MCP connect + discover
  → load_skills
  → memory backend discovery → knowledge sync → canon sync
  → CoDeps sealed
  → build_agent
  → restore_session → init_session_index
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
  → dream trigger    (session-end: mines + merges knowledge)
```

Approval gates run per-tool-call; retries wrap individual tool failures. The agent is not re-instantiated between turns.

→ [core-loop.md](core-loop.md) · [prompt-assembly.md](prompt-assembly.md) · [compaction.md](compaction.md)

---

### Prompt Assembly

Every turn's system prompt is assembled from three layers injected before the agent run:

1. **Static** — soul seed + mindsets + bundled skill manifest (injected at agent construction, not per-turn)
2. **Dynamic** — personality-context artifacts from the knowledge index (canon, not user-queryable)
3. **Recall** — `knowledge_search` + `session_search` results injected into the turn context window

Recall is search-driven and on-demand — nothing is wholesale injected into every turn. History processors (compaction, spill placeholders) apply in the pre-run pass.

→ [prompt-assembly.md](prompt-assembly.md) · [personality.md](personality.md)

---

### Memory Channels

Two channels share one index (`co-cli-search.db`, FTS5 + optional vec):

- **Knowledge** (`~/.co-cli/knowledge/*.md`) — declarative facts, rules, articles, notes. Model-writable via `knowledge_manage`. Mined and decayed by the dream cycle.
- **Sessions** (`~/.co-cli/sessions/*.jsonl`) — past turn transcripts. Append-only; chunked at write time. Recalled via BM25 chunk snippets with line citations; full turns fetched via `session_view`.

Recall is always search-driven. No channel is bulk-injected. Browse mode (empty query) returns recent-item metadata.

```
knowledge_search / session_search
  → MemoryStore.search (FTS5 BM25 [+ vec cosine + cross-encoder rerank])
  → snippet hits with line/path citations

knowledge_view / session_view
  → full artifact body / verbatim JSONL lines from disk
```

→ [knowledge.md](knowledge.md) · [sessions.md](sessions.md)

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

Skills are procedural capability units — YAML-fronted markdown files in `co_cli/skills/` (bundled) and `~/.co-cli/skills/` (user-installed). They are discovered at startup and surfaced through two model-callable tools: `skill_view`, `skill_manage`. All discoverable skills (bundled and user-installed) are declared in the static system prompt via the `<available_skills>` manifest. Slash commands in the REPL dispatch to installed skills via the `skill_commands` registry on `CoDeps`.

→ [skill.md](skill.md) · [tui.md](tui.md)

---

### CoDeps Contract

`CoDeps` is the single shared runtime object. Everything that needs cross-subsystem access receives it by injection — there is no global mutable state.

| Group | Contents |
|-------|----------|
| Config & services | `config`, `model`, `shell`, `memory_store`, `resource_locks`, `file_tracker` |
| Registries | `tool_index`, `tool_registry`, `skill_commands` |
| Mutable state | `session` (`CoSessionState` — todos, background tasks, approval rules); `runtime` (`CoRuntimeState` — compaction counters, turn usage, persisted message count) |
| Paths | `workspace_dir`, `knowledge_dir`, `sessions_dir`, `tool_results_dir`, … |
| Degradations | `degradations` (`MappingProxyType`) — startup-detected capability drops; read-only after bootstrap |


## 3. Config

Settings most relevant to system assembly:

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm.provider` | `CO_LLM_PROVIDER` | `ollama` | Model provider for the session runtime |
| `llm.host` | `CO_LLM_HOST` | `http://localhost:11434` | Ollama-compatible host |
| `llm.model` | `CO_LLM_MODEL` | `qwen3.5:35b-a3b-q4_k_m-agentic` | Primary model for the foreground agent |
| `mcp_servers` | `CO_MCP_SERVERS` | bundled defaults | MCP server definitions attached during runtime assembly |
| `personality` | `CO_PERSONALITY` | `tars` | Personality assets injected during prompt assembly |
| `knowledge.search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `hybrid` | Preferred retrieval backend before runtime degradation |
| `knowledge_path` | `CO_KNOWLEDGE_PATH` | `~/.co-cli/knowledge/` | User-global knowledge artifact store |
| `reasoning_display` | `CO_REASONING_DISPLAY` | `summary` | Terminal reasoning display mode for interactive turns |

Full settings reference: [config.md](config.md).


## 4. Public Interface

System-level contracts crossing every subsystem. Per-subsystem APIs are documented in their own specs.

| Symbol | Source | Contract |
|--------|--------|----------|
| `CoDeps` | `co_cli/deps.py` | Frozen runtime context passed via `RunContext[CoDeps]` to all tools; bundles config, model, registries, paths, and mutable `session` / `runtime` state |
| `create_deps(frontend, stack) -> CoDeps` | `co_cli/bootstrap/core.py` | Async one-shot startup assembly: validates config, probes the model, wires MCP, loads skills, builds the memory store, and seals `CoDeps` |
| `build_agent(config, model, tool_registry, skill_manifest) -> Agent[CoDeps, Any]` | `co_cli/agents/core.py` | Constructs the foreground pydantic-ai agent with static instructions, history processors, and tool surface |
| `run_turn(deps, agent, user_input, message_history, frontend) -> TurnResult` | `co_cli/context/orchestrate.py` | Async single-turn entrypoint: assembly → segment stream → approval loop → output checks |
| `fork_deps(base) -> CoDeps` | `co_cli/deps.py` | Builds a delegated `CoDeps` for sub-agents; forwards `tool_index`, excludes `tool_registry`, increments `agent_depth` |
| `dispatch(raw_input, ctx) -> SlashOutcome` | `co_cli/commands/core.py` | Async slash-command router; returns `LocalOnly`, `ReplaceTranscript`, or `DelegateToAgent` |


## 5. Files

| File | Purpose |
|------|---------|
| `co_cli/main.py` | Top-level CLI lifecycle, REPL loop, and teardown |
| `co_cli/bootstrap/core.py` | Runtime assembly and startup flow |
| `co_cli/agents/core.py` | Foreground agent factory (`build_agent()`) |
| `co_cli/agents/_native_toolset.py` | Native toolset construction and tool registry |
| `co_cli/agents/mcp.py` | MCP toolset wiring and discovery |
| `co_cli/agents/_instructions.py` | Dynamic instruction callbacks and prompt assembly |
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
| Delegation agents share `model` handle | `tests/test_flow_delegation_discovery.py` |
