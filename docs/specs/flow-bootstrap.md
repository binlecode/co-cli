# Co CLI — System Bootstrap Design

## Product Intent

**Goal:** Document the canonical startup sequence from settings load to REPL entry, including degradation.
**Functional areas:**
- Settings loading and config precedence
- CoDeps assembly (`create_deps()`)
- MCP connection and skill loading
- Knowledge backend resolution and sync
- Session restore and welcome banner

**Non-goals:**
- Runtime health checks (owned by `/status` tool)
- Per-component initialization internals

**Success criteria:** Bootstrap completes with degradations recorded; optional failures don't abort startup; welcome banner printed.
**Status:** Stable

---

## 1. What & How

Canonical startup flow for co-cli. This doc is the sole owner for the sequence from settings loading through `display_welcome_banner()`: layered config load, deps initialization (`create_deps()`), model and tool registry construction, MCP connection, skill loading, knowledge backend resolution and sync, session restore, startup status reporting, and the final boundary into the REPL. Skill file format, load gates, and dispatch semantics live in [skills.md](skills.md).

Bootstrap owns sequencing. Integration health checks (`check_runtime()` in `co_cli/bootstrap/check.py`) are not called during bootstrap; they are invoked on-demand by the `/status` tool in `co_cli/tools/capabilities.py`.

```
co_cli.main  (module load)
│
├─ co_cli.display._core → co_cli.config.settings (lazy init)
│      _ensure_dirs() → load_config()
│          Layer 1: ~/.co-cli/settings.json
│          Layer 2: <cwd>/.co-cli/settings.json → _deep_merge_settings()
│          Layer 3: fill_from_env (CO_CLI_* env vars)
│          → Settings singleton cached (nested sub-models: llm, knowledge, web, memory, subagent, shell)
│
├─ SQLiteSpanExporter() → TracerProvider → Agent.instrument_all()
│
co_cli.main.chat() → asyncio.run(_chat_loop())
│
├─ TerminalFrontend()
├─ WordCompleter(["/cmd", ...])
├─ PromptSession(history=..., completer=...)
├─ AsyncExitStack()
│
├─ create_deps(frontend, stack)
│  ├─ settings singleton used directly; resolve_workspace_paths(settings, cwd)
│  ├─ config.llm.validate_config() → error: raise ValueError (config shape only)
│  ├─ build_model(config.llm) → LlmModel
│  ├─ build_tool_registry(config) → ToolRegistry(toolset, mcp_toolsets, tool_index)
│  ├─ [if mcp_toolsets]
│  │      enter each MCP server on stack (stays alive for session)
│  │      on fail → warning per server
│  │      discover_mcp_tools(mcp_toolsets, exclude=native_tools) → mcp_index
│  │      tool_index.update(mcp_index)
│  ├─ _load_skills(skills_dir, settings, user_skills_dir) → skill_commands
│  ├─ _discover_knowledge_backend(config, frontend, degradations) → KnowledgeStore | None
│  │      grep → return None
│  │      _resolve_reranker() → probe embedder → resolve backend
│  │      config fields mutated directly to reflect runtime backend
│  │      degradation recorded in degradations dict
│  │      construct KnowledgeStore with resolved config
│  │      on fail → hybrid falls back to fts5, then grep (returns None)
│  ├─ _sync_knowledge_store(store, config, frontend, memory_dir, library_dir)
│  │      reconcile store with memory + library files on disk
│  │      hash-based — skips unchanged files
│  │      on fail → store closed, returns None
│  └─ → CoDeps(shell, config=settings, paths, model, knowledge_store, tool_index, skill_commands, runtime, degradations)
│
├─ completer.words updated with skills
├─ build_agent(config=deps.config, model=deps.model) → Agent
│
├─ restore_session(deps, frontend)
│      found → deps.session.session_path = existing path; none found → new_session_path()
│
├─ frontend.on_status("  {skill_count} skill(s) loaded")
│
├─ [if transcript exists] console.print("Previous session available — /resume to continue")
│
├─ display_welcome_banner(deps)
▼
REPL loop begins
```

## 2. Core Logic

Bootstrap is easiest to understand as one ordered startup path. The sequence diagram above is canonical; the sections below follow that same order.

### Step 1. Load `Settings`

First access to `co_cli.config.settings` creates `~/.co-cli/` if needed, loads config files, applies env overrides, validates the merged result, and caches a singleton `Settings` instance for the rest of the session.

```text
load_config():
    read ~/.co-cli/settings.json
    deep-merge <cwd>/.co-cli/settings.json
    apply env vars before validation
    validate as Settings
```

The three config layers are:

```text
1. ~/.co-cli/settings.json
2. <cwd>/.co-cli/settings.json
3. env vars via fill_from_env()
```

This is the only startup config object. Bootstrap passes `Settings` directly into later steps; there is no separate bootstrap-only config wrapper.

### Step 2. Enter `chat_loop()` and construct shell-local UI objects

`chat_loop()` creates the terminal frontend, a `PromptSession`, and an `AsyncExitStack`. The completer starts with built-in slash commands only. Skill names are not added until after `create_deps()` loads them.

### Step 3. Start `create_deps()` and resolve paths

`create_deps()` assembles the runtime contract used by the agent and tools. This function owns the entire sequence from Step 3 through Step 10. It resolves workspace-relative paths first, then performs the rest of bootstrap against the shared `Settings` instance.

```text
config = settings
paths = resolve_workspace_paths(config, cwd)
```

Those resolved paths become `CoDeps` fields such as `memory_dir`, `library_dir`, `skills_dir`, `sessions_dir`, and `tool_results_dir`. Config stays on `deps.config`; paths and degradation state do not live on `Settings`.

### Step 4. Fail fast on invalid model configuration

Bootstrap calls `config.llm.validate_config()` as a shape check only. It rejects missing required configuration before any long-lived runtime is built.

| Condition | Behavior |
| --- | --- |
| No model configured | raise `ValueError`; session never starts |
| Gemini provider with missing API key | raise `ValueError`; session never starts |
| Provider connectivity problem | startup continues; first runtime model call surfaces the error |

If the provider is `ollama-openai`, bootstrap also probes the model's runtime `num_ctx` from `/api/show`. When the probe returns a positive value different from config, bootstrap overwrites `config.llm.num_ctx` so runtime state reflects the actual Modelfile allocation. If the probed value is below `MIN_AGENTIC_CONTEXT`, startup fails immediately.

### Step 5. Resolve MCP env tokens and build the local registry

Bootstrap resolves env-derived MCP credentials, builds the foreground `LlmModel`, and constructs the native tool registry. At this point there is still no `CoDeps`; bootstrap is still gathering the pieces that will go into it.

### Step 6. Connect MCP servers and discover their tools

Each configured MCP toolset is entered on the caller's `AsyncExitStack` so it stays alive for the session. Failures are isolated per server: a bad MCP server produces a status warning and is skipped, while successful servers still contribute tools.

```text
for each mcp toolset:
    enter async context
    on failure: warn and skip

discover tools on connected servers
merge discovered tools into tool_index
```

### Step 7. Load skills with three-pass precedence

Bootstrap loads skills before `CoDeps` assembly so the resulting `skill_commands` map can be stored directly on `deps`.

```text
pass 1: built-in skills
pass 2: user-global skills
pass 3: project-local skills
```

Later passes override earlier ones. After `create_deps()` returns, `chat_loop()` updates `completer.words` in place so prompt completion expands from built-in commands to built-in commands plus loaded skills.

### Step 8. Resolve the knowledge backend

Bootstrap then decides whether the session can use `hybrid`, `fts5`, or only `grep`. This step is intentionally mutating: it updates `config.knowledge.search_backend` to the backend that is actually available, not the backend the user originally requested.

```text
if configured backend is grep:
    no store
else:
    resolve reranker availability
    probe embedder
    choose hybrid or fts5
    build KnowledgeStore
    on failure: degrade to fts5, then grep
```

When degradation happens, bootstrap records the reason in `deps.degradations`. The important invariant is that downstream code reads runtime truth from `deps.config` and explanation text from `deps.degradations`.

### Step 9. Sync the knowledge store

If a `KnowledgeStore` exists, bootstrap reconciles it with the current memory and library trees on disk. Sync is hash-based, so unchanged files are skipped. A sync failure closes the store and disables indexed retrieval for the session; the CLI continues with grep fallback instead of aborting startup.

### Step 10. Assemble `CoDeps`

After model setup, MCP discovery, skill loading, backend resolution, and sync, bootstrap creates `CoDeps`.

`CoDeps` is the runtime object shared by tools and sub-agents. It holds:

- `config`: the session `Settings` instance
- `model`, `knowledge_store`, and `shell`: service handles
- `tool_index` and `skill_commands`: bootstrap-built registries
- resolved workspace paths
- `degradations`: runtime downgrade reasons
- mutable `session` and `runtime` state groups

After bootstrap completes, `deps.config` is treated as read-only by convention even though some fields were deliberately mutated during startup to reflect runtime reality.

### Step 11. Build the foreground agent

Once `create_deps()` returns, `chat_loop()` updates completion words with loaded skill names and calls `build_agent(config=deps.config, model=deps.model)`. Prompt instruction assembly belongs to agent construction, not to bootstrap.

### Step 12. Restore or create the session

Bootstrap runs `migrate_session_files()` to convert any legacy `{uuid}.jsonl` files to the new `YYYY-MM-DD-THHMMSSz-{uuid8}.jsonl` format and removes stale `.json` sidecars. It then scans `*.jsonl` by filename (lexicographic sort = chronological sort) and sets `deps.session.session_path` to the most recent file.

```text
migrate_session_files(deps.sessions_dir)   # rename legacy files, drop .json sidecars
path = find_latest_session(deps.sessions_dir)
if found:
    deps.session.session_path = path
else:
    deps.session.session_path = new_session_path(deps.sessions_dir)  # path only, no file write
```

No session file is written at startup — the file is created on the first `append_messages` call after the first turn. Session filename format and ongoing lifecycle are owned by [context.md](context.md).

### Step 13. Print startup status and enter the REPL boundary

After session restore, `chat_loop()` reports the loaded skill count, optionally shows the resume hint when a transcript exists, then calls `display_welcome_banner(deps)`. The banner is the boundary between bootstrap and normal interactive operation.

Everything from `create_deps()` through banner display runs inside `chat_loop()` cleanup guards. If startup exits early, the shell backend and MCP stack still unwind.

### Failure And Degradation Behavior

| Condition | Outcome |
| --- | --- |
| Config validation fails in `load_config()` | startup stops before `chat_loop()` begins |
| `create_deps()` raises `ValueError` | `_chat_loop()` prints a startup error and exits |
| MCP server fails to connect | warning only; native tools and other MCP servers still work |
| Knowledge backend construction fails | degrade `hybrid → fts5 → grep` |
| Knowledge sync fails | close the store and continue without indexed retrieval |
| Session restore fails to find usable state | create a new session |
| One skill file fails to load | skip that file and continue loading others |

## 3. Config

These settings most directly affect bootstrap behavior.

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `llm.provider` | `LLM_PROVIDER` | `ollama-openai` | Selects provider-specific bootstrap checks and model wiring |
| `llm.host` | `LLM_HOST` | `http://localhost:11434` | Host used by Ollama checks and runtime model calls |
| `llm.model` | `CO_LLM_MODEL` | provider default | Primary foreground model built during startup |
| `llm.num_ctx` | `LLM_NUM_CTX` | provider default | Context window target; may be overwritten by the Ollama runtime probe |
| `knowledge.search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `hybrid` | Preferred retrieval backend before degradation |
| `knowledge.embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `tei` | Determines whether hybrid search can stay enabled |
| `library_path` | `CO_LIBRARY_PATH` | `~/.co-cli/library` | User-global article directory used during knowledge sync |
| `mcp_servers` | `CO_CLI_MCP_SERVERS` | bundled defaults | MCP server definitions connected during startup |
| `personality` | `CO_CLI_PERSONALITY` | `tars` | Personality selected before agent instruction assembly |
| `reasoning_display` | `CO_CLI_REASONING_DISPLAY` | `summary` | Interactive thinking display mode used by the frontend |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/main.py` | Owns `_chat_loop()` startup orchestration and the REPL boundary |
| `co_cli/bootstrap/core.py` | Owns `create_deps()` and `restore_session()` |
| `co_cli/bootstrap/check.py` | Provider, embedder, reranker, and Ollama `num_ctx` checks |
| `co_cli/bootstrap/banner.py` | Renders the welcome banner that marks bootstrap completion |
| `co_cli/bootstrap/render_status.py` | On-demand status and security reporting, not inline bootstrap |
| `co_cli/deps.py` | Defines `CoDeps`, path resolution, and sub-agent inheritance rules |
| `co_cli/config/_core.py` | Defines `Settings`, layered config loading, and env override mapping |
| `co_cli/commands/_commands.py` | Loads skills during bootstrap and later refreshes them in the REPL |
| `co_cli/context/session.py` | Session filename generation, latest-session discovery, migration from legacy format, new-path factory |
| `co_cli/knowledge/_store.py` | Implements the indexed knowledge store used when bootstrap enables it |
