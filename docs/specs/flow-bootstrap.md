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

Canonical startup flow for `co-cli`, from settings resolution to the point where the REPL prompt is ready. Bootstrap owns sequencing, degradation, and the runtime object handed to the agent. It does not own runtime health checks; `check_runtime()` is invoked later by `/status`, not during startup.

```
co_cli.main  (module import)
│
├─ settings resolved via config/display imports
├─ setup_file_logging(...settings.observability...)
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
│  ├─ config = settings; paths = resolve_workspace_paths(config, cwd)
│  ├─ config.llm.validate_config()
│  ├─ [if ollama-openai] probe_ollama_context() → maybe overwrite llm.num_ctx
│  ├─ config.mcp_servers = _resolve_mcp_env_tokens(config)
│  ├─ build_model(config.llm)
│  ├─ build_tool_registry(config)
│  ├─ enter MCP toolsets on stack; discover_mcp_tools(); merge MCP tool_index
│  ├─ _load_skills(skills_dir, settings=config, user_skills_dir=...)
│  ├─ _discover_knowledge_backend(config, frontend, degradations)
│  ├─ _sync_knowledge_store(store, config, frontend, memory_dir, library_dir)
│  │      syncs library articles only; on failure closes store and falls back to grep
│  └─ return CoDeps(...)
│
├─ deps.session.reasoning_display = CLI-selected mode
├─ completer.words = _build_completer_words(deps.skill_commands)
├─ build_agent(config=deps.config, model=deps.model, tool_registry=deps.tool_registry)
│
├─ restore_session(deps, frontend) → current_session_path
├─ _init_session_index(deps, current_session_path, frontend)
├─ frontend.on_status("  {skill_count} skill(s) loaded")
├─ [if restored path exists] console.print("Previous session available — /resume to continue")
├─ display_welcome_banner(deps)
├─ frontend.clear_status()
│
▼
REPL loop begins
```

## 2. Core Logic

Bootstrap is one ordered path. The sections below follow the same order as the diagram above.

### Step 1. Load `Settings`

The first access to `co_cli.config.settings` creates `~/.co-cli/` if needed, loads user config, deep-merges project config, applies env overrides, validates the result, and caches the singleton `Settings` instance. In practice this happens during module import because the display layer and logging setup both read settings before `chat()` starts.

Config precedence is:

```text
1. ~/.co-cli/settings.json
2. <cwd>/.co-cli/settings.json
3. env vars
```

### Step 2. Enter `chat_loop()` and construct shell-local UI objects

`_chat_loop()` creates the terminal frontend, the prompt session, and the async exit stack. The completer starts with built-in slash commands only; skill commands are added later after bootstrap has loaded them.

### Step 3. Start `create_deps()` and resolve paths

`create_deps()` owns the rest of bootstrap assembly until `CoDeps` exists. It reads the shared `Settings` singleton, resolves workspace-relative paths, and keeps those resolved paths on `CoDeps`, not on `Settings`.

### Step 4. Fail fast on invalid model configuration

Bootstrap calls `config.llm.validate_config()` before building long-lived runtime objects.

| Condition | Behavior |
| --- | --- |
| No model configured | raise `ValueError`; session never starts |
| Gemini provider with missing API key | raise `ValueError`; session never starts |
| Provider connectivity problem | startup continues; first runtime model call surfaces the error |

If the provider is `ollama-openai`, bootstrap also probes the model's runtime `num_ctx`. When the runtime value differs from config, bootstrap overwrites `config.llm.num_ctx` so runtime state reflects the actual allocation. If the probed value is below the minimum supported agentic context, startup fails immediately.

### Step 5. Resolve MCP env tokens and build the local registry

Bootstrap resolves env-derived MCP credentials, builds the foreground `LlmModel`, and constructs the tool registry. At this point there is still no `CoDeps`; bootstrap is still gathering the pieces that will go into it.

### Step 6. Connect MCP servers and discover their tools

Each configured MCP toolset is entered on the caller's `AsyncExitStack` so it stays alive for the session. Failures are isolated per server: a bad server produces a status warning and is skipped, while successful servers still contribute discovered tools to the merged `tool_index`.

### Step 7. Load skills with three-pass precedence

Bootstrap loads skills before `CoDeps` assembly so the resulting `skill_commands` map can be stored directly on the runtime object.

```text
pass 1: built-in skills
pass 2: user-global skills
pass 3: project-local skills
```

Later passes override earlier ones. After `create_deps()` returns, `_chat_loop()` updates `completer.words` so prompt completion expands from built-ins to built-ins plus loaded skills.

### Step 8. Resolve the knowledge backend

Bootstrap decides whether the session can use `hybrid`, `fts5`, or only `grep`. This step intentionally mutates `config.knowledge.search_backend` to runtime truth, not requested intent.

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

When degradation happens, bootstrap records the reason in `deps.degradations`. Downstream code reads runtime truth from `deps.config` and explanation text from `deps.degradations`.

### Step 9. Sync the knowledge store

If a `KnowledgeStore` exists, bootstrap syncs library articles from disk into the index. Memory files are not indexed during bootstrap. Sync is hash-based, so unchanged library files are skipped. A sync failure closes the store and disables indexed retrieval for the session; the CLI continues without aborting startup.

### Step 10. Assemble `CoDeps`

After model setup, MCP discovery, skill loading, backend resolution, and sync, bootstrap creates `CoDeps`.

`CoDeps` is the runtime object shared by tools and sub-agents. It holds:

- `config`: the session `Settings` instance
- `model`, `knowledge_store`, and `shell`: service handles
- `tool_registry`, `tool_index`, and `skill_commands`: bootstrap-built registries
- resolved workspace paths
- `degradations`: runtime downgrade reasons
- mutable `session` and `runtime` state groups

After bootstrap completes, `deps.config` is treated as read-only by convention even though a few fields were deliberately rewritten during startup to reflect runtime reality.

### Step 11. Build the foreground agent

Once `create_deps()` returns, `_chat_loop()` stores the chosen reasoning display mode in session state, refreshes the completer, and calls `build_agent(config=deps.config, model=deps.model, tool_registry=deps.tool_registry)`. Prompt instruction assembly belongs to agent construction, not to bootstrap.

### Step 12. Restore or create the session

Bootstrap scans `*.jsonl` by filename and sets `deps.session.session_path` to the most recent file.

```text
path = find_latest_session(deps.sessions_dir)
if found:
    deps.session.session_path = path
else:
    deps.session.session_path = new_session_path(deps.sessions_dir)  # path only, no file write
```

No session file is written at startup — the file is created on the first `append_messages` call after the first turn. Session filename format and ongoing lifecycle are owned by [context.md](context.md).

### Step 12b. Initialise the session index

After `restore_session()` returns, `_init_session_index()` opens or creates the project-local FTS5 session index at `.co-cli/session-index.db` and syncs past sessions into it. The current session path is excluded from sync.

```text
store = SessionIndex(db_path=sessions_dir.parent / "session-index.db")
store.sync_sessions(sessions_dir, exclude=current_session_path)
deps.session_index = store

on failure:
    log warning
    deps.session_index = None  # graceful degradation; session_search returns empty
```

The index is derived and rebuildable: deleting `.co-cli/session-index.db` and restarting rebuilds cleanly from `*.jsonl` files. Change detection is size-based (append-only transcripts).

### Step 13. Print startup status and enter the REPL boundary

After session restore, `_chat_loop()` reports loaded skill count, optionally shows the resume hint when a transcript exists, calls `display_welcome_banner(deps)`, then clears the transient status line. The banner is the boundary between bootstrap and normal interactive operation.

Everything from `create_deps()` through banner display runs inside `_chat_loop()` cleanup guards. If startup exits early, the shell backend, background tasks, pending extraction work, and MCP stack still unwind.

### Failure And Degradation Behavior

| Condition | Outcome |
| --- | --- |
| Config validation fails in `load_config()` | startup stops before `chat_loop()` begins |
| `create_deps()` raises `ValueError` | `_chat_loop()` prints a startup error and exits |
| MCP server fails to connect | warning only; native tools and other MCP servers still work |
| Knowledge backend construction fails | degrade `hybrid → fts5 → grep` |
| Knowledge sync fails | close the store and continue without indexed retrieval |
| Session restore fails to find usable state | create a new session |
| Session index fails to open or sync | `deps.session_index = None`; `session_search` returns empty; startup continues |
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
| `reasoning_display` | `CO_CLI_REASONING_DISPLAY` | `summary` | Default reasoning-display mode at startup; CLI flags can override it before REPL entry |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/main.py` | Owns module-load logging and telemetry setup, `_chat_loop()` startup orchestration, and the REPL boundary |
| `co_cli/bootstrap/core.py` | Owns `create_deps()`, `restore_session()`, and `_init_session_index()` |
| `co_cli/bootstrap/check.py` | Provider, embedder, reranker, and Ollama `num_ctx` checks |
| `co_cli/bootstrap/banner.py` | Renders the welcome banner that marks bootstrap completion |
| `co_cli/bootstrap/render_status.py` | On-demand status and security reporting, not inline bootstrap |
| `co_cli/deps.py` | Defines `CoDeps`, path resolution, and sub-agent inheritance rules |
| `co_cli/config/_core.py` | Defines `Settings`, layered config loading, and env override mapping |
| `co_cli/commands/_commands.py` | Loads skills during bootstrap and later refreshes them in the REPL |
| `co_cli/context/session.py` | Session filename generation, latest-session discovery, new-path factory |
| `co_cli/knowledge/_store.py` | Implements the indexed knowledge store used when bootstrap enables it |
| `co_cli/session_index/_store.py` | `SessionIndex` — FTS5 session index opened and synced during bootstrap |
| `co_cli/session_index/_extractor.py` | Extracts user-prompt and assistant-text parts from JSONL transcripts |
