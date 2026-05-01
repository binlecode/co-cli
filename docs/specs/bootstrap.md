# Co CLI — System Bootstrap Design


## 1. What & How

Session phases:

```text
CLI start
  -> create_deps
  -> build_agent
  -> restore_session
  -> init_session_store
  -> enter REPL
      -> local command or agent turn
      -> approvals / tools / persistence / post-turn writes as needed
  -> cleanup on exit
```

Bootstrap owns all steps up to "enter REPL". Turn execution is in [core-loop.md](core-loop.md). Teardown drains background work, cleans up the shell backend, and closes async resources (MCP connections, async exit stack).

Canonical startup flow for `co-cli`, from settings resolution to the point where the REPL prompt is ready. Bootstrap owns sequencing, degradation, and the runtime object handed to the agent. It does not own runtime health checks; `check_runtime()` is invoked later by `capabilities_check` (the agent tool exercised by `/doctor`), not during startup.

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
│  ├─ build_model(config.llm)
│  ├─ build_tool_registry(config)
│  ├─ enter MCP toolsets on stack; discover_mcp_tools(); merge MCP tool_index
│  ├─ load_skills(skills_dir, settings=config, user_skills_dir=...) → filter_namespace_conflicts()
│  ├─ _discover_knowledge_backend(config, frontend, degradations)
│  ├─ _sync_knowledge_store(store, config, frontend, knowledge_dir)
│  │      indexes every .md in knowledge_dir under source="knowledge"; on failure closes store and falls back to grep
│  └─ return CoDeps(...)
│
├─ deps.session.reasoning_display = CLI-selected mode
├─ completer.words = _build_completer_words(deps.skill_commands)
├─ build_agent(config=deps.config, model=deps.model, tool_registry=deps.tool_registry)
│
├─ restore_session(deps, frontend) → current_session_path
├─ init_session_store(deps, current_session_path, frontend)
├─ frontend.on_status("  {skill_count} skill(s) loaded")
├─ [if restored path exists] console.print("Previous session available — /resume to continue")
├─ display_welcome_banner(deps)
├─ render_security_findings(check_security())   ← printed once per session
├─ frontend.clear_status()
│
▼
REPL loop begins
```

## 2. Core Logic

Bootstrap is one ordered path. The sections below follow the same order as the diagram above.

### Step 1. Load `Settings`

The first access to `co_cli.config.settings` creates `~/.co-cli/` if needed, loads user settings, applies env overrides, validates the result, and caches the singleton `Settings` instance. In practice this happens during module import because the display layer and logging setup both read settings before `chat()` starts.

Config precedence is (lowest → highest):

```text
1. ~/.co-cli/settings.json
2. ~/.co-cli/.env            (secrets file; does not overwrite shell-set vars)
3. env vars (shell)
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

### Step 5. Build the foreground model and local tool registry

Bootstrap builds the foreground `LlmModel` from the resolved LLM config and constructs the native tool registry. At this point there is still no `CoDeps`; bootstrap is still gathering the pieces that will go into it. MCP env-token expansion happens inside MCP toolset construction in the next step, not as a separate pass here.

### Step 6. Connect MCP servers and discover their tools

Each configured MCP toolset is entered on the caller's `AsyncExitStack` so it stays alive for the session. Failures are isolated per server: a bad server produces a status warning, records a `"mcp.<prefix>"` entry in `degradations`, and is skipped, while successful servers still contribute discovered tools to the merged `tool_index`.

### Step 7. Load skills with two-pass precedence

Bootstrap loads skills before `CoDeps` assembly so the resulting `skill_commands` map can be stored directly on the runtime object.

```text
pass 1: bundled skills (co_cli/skills/)
pass 2: user-global skills (~/.co-cli/skills/)
```

User-global skills override bundled skills on name collision. After `create_deps()` returns, `_chat_loop()` updates `completer.words` so prompt completion expands from built-ins to built-ins plus loaded skills.

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

If a `KnowledgeStore` exists, bootstrap syncs every `.md` file under `knowledge_dir` into the index under a single `source="knowledge"` label — extracted facts and articles alike are indexed into `docs` + `chunks_fts` (and `chunks_vec` in hybrid mode). Sync is hash-based, so unchanged files are skipped. A sync failure closes the store and disables indexed retrieval for the session; the CLI continues without aborting startup.

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

No session file is written at startup — the file is created on the first `append_messages` call after the first turn. Session filename format and ongoing memory/session lifecycle are owned by [memory.md](memory.md).

### Step 12b. Initialise the memory index

After `restore_session()` returns, `init_session_store()` opens or creates the user-global FTS5 session index (`~/.co-cli/session-index.db`) and syncs past sessions into it. The current session path is excluded from sync.

```text
store = SessionStore(db_path=sessions_dir.parent / "session-index.db")
store.sync_sessions(sessions_dir, exclude=current_session_path)
deps.memory_index = store

on failure:
    log warning
    deps.memory_index = None  # graceful degradation; memory_search returns empty
```

The session index is derived and rebuildable: deleting `~/.co-cli/session-index.db` and restarting rebuilds cleanly from `*.jsonl` files. Change detection is size-based (append-only transcripts). (The knowledge index at `~/.co-cli/co-cli-search.db` is separate — owned by Step 8/9 and rebuilt from `knowledge_dir/*.md`.)

### Step 13. Print startup status and enter the REPL boundary

After session restore, `_chat_loop()` reports loaded skill count, optionally shows the resume hint when a transcript exists, calls `display_welcome_banner(deps)`, runs `render_security_findings(check_security())` to print any security posture warnings, then clears the transient status line. The banner and security warnings together form the boundary between bootstrap and normal interactive operation.

Everything from `create_deps()` through banner display runs inside `_chat_loop()` cleanup guards. If startup exits early, the shell backend, background tasks, pending extraction work, and MCP stack still unwind.

### Failure And Degradation Behavior

| Condition | Outcome |
| --- | --- |
| Config validation fails in `load_config()` | startup stops before `chat_loop()` begins |
| `create_deps()` raises `ValueError` | `_chat_loop()` prints a startup error and exits |
| MCP server fails to connect | status warning + `degradations["mcp.<prefix>"]` recorded; native tools and other MCP servers still work |
| Knowledge backend construction fails | degrade `hybrid → fts5 → grep` |
| Knowledge sync fails | close the store and continue without indexed retrieval |
| Session restore fails to find usable state | create a new session |
| Session index fails to open or sync | `deps.memory_index = None`; `memory_search` returns empty; startup continues |
| One skill file fails to load | skip that file and continue loading others |

## 3. Config

These settings most directly affect bootstrap behavior.

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `llm.provider` | `CO_LLM_PROVIDER` | `ollama` | Selects provider-specific bootstrap checks and model wiring |
| `llm.host` | `CO_LLM_HOST` | `http://localhost:11434` | Host used by Ollama checks and runtime model calls |
| `llm.model` | `CO_LLM_MODEL` | provider default | Primary foreground model built during startup |
| `llm.num_ctx` | `CO_LLM_NUM_CTX` | provider default | Context window target; may be overwritten by the Ollama runtime probe |
| `knowledge.search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `hybrid` | Preferred retrieval backend before degradation |
| `knowledge.embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `tei` | Determines whether hybrid search can stay enabled |
| `knowledge_path` | `CO_KNOWLEDGE_PATH` | `~/.co-cli/knowledge` | User-global knowledge artifact directory synced during bootstrap (extracted facts, articles, notes) |
| `mcp_servers` | `CO_MCP_SERVERS` | bundled defaults | MCP server definitions connected during startup |
| `personality` | `CO_PERSONALITY` | `tars` | Personality selected before agent instruction assembly |
| `reasoning_display` | `CO_REASONING_DISPLAY` | `summary` | Default reasoning-display mode at startup; CLI flags can override it before REPL entry |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/main.py` | Owns module-load logging and telemetry setup, `_chat_loop()` startup orchestration, and the REPL boundary |
| `co_cli/bootstrap/core.py` | Owns `create_deps()`, `restore_session()`, and `init_session_store()` |
| `co_cli/bootstrap/check.py` | Provider, embedder, reranker, and Ollama `num_ctx` checks |
| `co_cli/bootstrap/banner.py` | Renders the welcome banner that marks bootstrap completion |
| `co_cli/bootstrap/security.py` | Security posture checks run once at startup (`check_security`, `render_security_findings`) |
| `co_cli/deps.py` | Defines `CoDeps`, path resolution, and sub-agent inheritance rules |
| `co_cli/config/core.py` | Defines `Settings`, layered config loading, and env override mapping |
| `co_cli/skills/loader.py` | `load_skills` — two-tier skill file loading used during bootstrap and `/skills reload` |
| `co_cli/commands/core.py` | Slash-command `dispatch` and `BUILTIN_COMMANDS` registrations |
| `co_cli/commands/skills.py` | `/skills` REPL command family and `get_skill_registry` |
| `co_cli/commands/registry.py` | `BUILTIN_COMMANDS`, `filter_namespace_conflicts` — called after `load_skills` to drop namespace conflicts |
| `co_cli/memory/session.py` | Session filename generation, latest-session discovery, new-path factory |
| `co_cli/memory/knowledge_store.py` | Implements the indexed knowledge store used when bootstrap enables it |
| `co_cli/memory/session_store.py` | `SessionStore` — FTS5 session index opened and synced during bootstrap |
| `co_cli/memory/indexer.py` | Extracts user-prompt and assistant-text parts from JSONL transcripts |
