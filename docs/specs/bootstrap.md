# Co CLI — System Bootstrap Design


## 1. What & How

Session phases:

```text
CLI start
  -> create_deps
  -> build_orchestrator(ORCHESTRATOR_SPEC, deps)
  -> restore_session
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
├─ setup_observability(LOGS_DIR, app_log_name="co-cli.jsonl",   # shared coordinator:
│      spans_log_name="co-cli-spans.jsonl", errors_log_name="errors.jsonl", settings=...)
│      → co-cli.jsonl (app) + co-cli-spans.jsonl (spans) + noisy-logger suppression
│
co_cli.main.chat() → asyncio.run(_chat_loop())
│
├─ TerminalFrontend()
├─ SlashCommandCompleter()
├─ FileHistory(~/.co-cli/history.txt)        # the REPL Application is built post-create_deps
├─ AsyncExitStack()
│
├─ create_deps(frontend, stack)
│  ├─ config = settings; paths = resolve_workspace_paths(config, cwd)
│  ├─ config.llm.validate_config()
│  ├─ [if ollama] probe_ollama_model() → model_max_ctx = min(probe.num_ctx, llm.max_ctx)
│  ├─ build_model(config.llm)
│  ├─ build_native_toolset() → (native_toolset, tool_catalog)
│  ├─ build_mcp_entries(config, tool_catalog)
│  ├─ enter MCP toolsets on stack (per-entry timeout + failure isolation)
│  ├─ discover_mcp_tools(connected); merge MCP entries into tool_catalog
│  ├─ assemble_routing_toolset(native_toolset, [connected].toolset) → toolset
│  ├─ load_skills(skills_dir, settings=config, user_skills_dir=...) → filter_namespace_conflicts()
│  ├─ _discover_memory_backend(config, frontend, degradations)
│  ├─ _sync_memory_store(store, config, frontend, knowledge_dir)
│  │      indexes every .md in knowledge_dir under source="memory"; on failure closes store and aborts startup
│  ├─ _sync_canon_store(index_store, config, on_status)
│  │      indexes souls/{role}/canon/*.md under source="canon" (no_chunk=True); no-op when store=None or personality empty
│  └─ return CoDeps(...)
│
├─ deps.session.reasoning_display = CLI-selected mode
├─ completer.words = _build_completer_words(deps.skill_catalog)
├─ build_orchestrator(ORCHESTRATOR_SPEC, deps)
│      composes static instructions from ORCHESTRATOR_SPEC.static_instruction_builders (3 builders:
│      static, toolset guidance, personality critique), registers per-turn instructions
│      (safety, current_time, deferred_tool_awareness, skill_manifest), and attaches history processors.
│
├─ restore_session(deps, frontend)
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

`_chat_loop()` creates the terminal frontend, the prompt session, and the async exit stack. The completer starts with built-in slash commands only; skill slash commands are added after bootstrap has loaded them.

### Step 3. Start `create_deps()` and resolve paths

`create_deps()` owns the rest of bootstrap assembly until `CoDeps` exists. It reads the shared `Settings` singleton, resolves workspace-relative paths, and keeps those resolved paths on `CoDeps`, not on `Settings`.

### Step 4. Fail fast on invalid model configuration

Bootstrap calls `config.llm.validate_config()` before building long-lived runtime objects.

| Condition | Behavior |
| --- | --- |
| Gemini provider with missing API key | raise `ValueError`; session never starts |
| Model unknown for provider (no `_LLM_SETTINGS` entry) | raise `ValueError`; session never starts |
| Model is noreason-only (no `reasoning` mode entry) | raise `ValueError`; session never starts |
| Provider connectivity problem | startup continues; first runtime model call surfaces the error |

An unset `llm.model` is auto-resolved to `DEFAULT_LLM_MODELS[provider]` by a pydantic
`model_validator` before `validate_config()` runs, so "no model configured" is not a
reachable bootstrap failure.

If the provider is `ollama`, bootstrap probes the model's runtime `num_ctx` from the Modelfile via `/api/show`. The probe result is capped by `config.llm.max_ctx` and stored as `deps.model_max_ctx`. If the capped value is below the minimum supported agentic context, startup fails immediately.

### Step 5. Build the foreground model and local tool registry

Bootstrap builds the foreground `LlmModel` from the resolved LLM config and constructs the native tool registry. At this point there is still no `CoDeps`; bootstrap is still gathering the pieces that will go into it. MCP env-token expansion happens inside MCP toolset construction in the next step, not as a separate pass here.

### Step 6. Connect MCP servers and discover their tools

Each configured MCP toolset is entered on the caller's `AsyncExitStack` so it stays alive for the session. Failures are isolated per server: a bad server produces a status warning, records a `"mcp.<prefix>"` entry in `degradations`, and is skipped, while successful servers still contribute discovered tools to the merged `tool_catalog`.

### Step 7. Load skills with two-pass precedence

Bootstrap loads skills before `CoDeps` assembly so the resulting `skill_catalog` map can be stored directly on the runtime object.

```text
pass 1: bundled skills (co_cli/skills/)
pass 2: user-global skills (~/.co-cli/skills/)
```

User-global skills override bundled skills on name collision. After `create_deps()` returns, `_chat_loop()` updates `completer.words` so prompt completion expands from built-ins to built-ins plus loaded skills.

### Step 8. Resolve the knowledge backend

Bootstrap decides whether the session can use `hybrid`, `fts5`, or only `grep`. This step intentionally mutates `config.memory.search_backend` to runtime truth, not requested intent.

```text
if configured backend is grep:
    no store
else:
    resolve reranker availability
    probe embedder
    choose hybrid or fts5
    build MemoryStore
    if hybrid construction fails: degrade once to fts5
    if fts5 construction fails: raise unless grep was explicitly configured
```

When degradation happens, bootstrap records the reason in `deps.degradations`. Downstream code reads runtime truth from `deps.config` and explanation text from `deps.degradations`.

### Step 9. Sync the knowledge store

If a `MemoryStore` exists, bootstrap syncs every `.md` file under `knowledge_dir` into the index under a single `source="memory"` label — extracted facts and articles alike are indexed into `docs` + `chunks_fts` (and `chunks_vec` in hybrid mode). Sync is hash-based, so unchanged files are skipped. A sync failure closes the store and raises a startup error instead of silently losing indexed memory retrieval.

### Step 9b. Sync canon scenes

If a `MemoryStore` and a `config.personality` exist, bootstrap calls `_sync_canon_store()` to index the active role's character memory files (`souls/{role}/canon/*.md`) into the FTS pipeline under `source='canon'`. Each file is stored as a single unchunked chunk (full body) so recall can return complete scenes. Hash-skip prevents re-indexing unchanged files. This step no-ops when `store is None` or `config.personality` is empty.

### Step 10. Assemble `CoDeps`

After model setup, MCP discovery, skill loading, backend resolution, and sync, bootstrap creates `CoDeps`.

`CoDeps` is the runtime object shared by tools and sub-agents. It holds:

- `config`: the session `Settings` instance
- `model`, `memory_store`, and `shell`: service handles
- `toolset`, `tool_catalog`, and `skill_catalog`: bootstrap-built registries
- resolved workspace paths
- `degradations`: runtime downgrade reasons
- mutable `session` and `runtime` state groups

After bootstrap completes, `deps.config` is treated as read-only by convention even though a few fields were deliberately rewritten during startup to reflect runtime reality.

### Step 11. Build the foreground agent

Once `create_deps()` returns, `_chat_loop()` stores the chosen reasoning display mode in session state, refreshes the completer, and calls `build_orchestrator(ORCHESTRATOR_SPEC, deps)`. The orchestrator builder composes its static instructions from the spec's three static builders (static instructions, toolset guidance, personality critique) and registers four per-turn callbacks (safety, current_time, deferred_tool_awareness, skill_manifest) — each pulls from `deps` (or `ctx.deps` for per-turn) directly. Prompt instruction assembly belongs to agent construction, not to bootstrap.

### Step 12. Restore or create the session

Bootstrap scans `*.jsonl` by filename and sets `deps.session.session_path` to the most recent file.

```text
path = find_latest_session(deps.sessions_dir)
if found:
    deps.session.session_path = path
else:
    deps.session.session_path = new_session_path(deps.sessions_dir)  # path only, no file write
```

No session file is written at startup — the file is created on the first `append_messages` call after the first turn. Session filename format and ongoing session lifecycle are owned by [sessions.md](sessions.md). There is no session-index step: `session_search` reads the transcript files directly (see [sessions.md](sessions.md)), so past sessions need no startup sync.

### Step 13. Print startup status and enter the REPL boundary

After session restore, `_chat_loop()` reports loaded skill count, optionally shows the resume hint when a transcript exists, queries `deps.memory_store.count()` (memory items) and `deps.session_store.count()` (number of `*.jsonl` transcript files) for the banner counts, then calls `display_welcome_banner(deps, memory_count=..., session_count=...)`. The banner shows a `Memory:` row with the backend label, optional degradation notice, and both counts (memory count omitted when the memory store is `None`). After the banner, `render_security_findings(check_security())` prints any security posture warnings, then the transient status line clears. The banner and security warnings together form the boundary between bootstrap and normal interactive operation.

Everything from `create_deps()` through banner display runs inside `_chat_loop()` cleanup guards. If startup exits early, the shell backend, background tasks, pending extraction work, and MCP stack still unwind.

### Failure And Degradation Behavior

| Condition | Outcome |
| --- | --- |
| Config validation fails in `load_config()` | startup stops before `chat_loop()` begins |
| `create_deps()` raises `ValueError` | `_chat_loop()` prints a startup error and exits |
| MCP server fails to connect | status warning + `degradations["mcp.<prefix>"]` recorded; native tools and other MCP servers still work |
| Memory backend construction fails | hybrid may degrade to `fts5`; `fts5` failure aborts startup unless `memory.search_backend="grep"` was explicitly configured |
| Memory sync fails | close the index and abort startup with a memory sync error |
| Session restore fails to find usable state | create a new session |
| One skill file fails to load | skip that file and continue loading others |

## 3. Config

These settings most directly affect bootstrap behavior.

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `llm.provider` | `CO_LLM_PROVIDER` | `ollama` | Selects provider-specific bootstrap checks and model wiring |
| `llm.host` | `CO_LLM_HOST` | `http://localhost:11433` | Host used by Ollama checks and runtime model calls (multi-instance router; `11434` bypasses to primary Ollama) |
| `llm.model` | `CO_LLM_MODEL` | provider default | Primary foreground model built during startup |
| `llm.max_ctx` | — | `65536` | Ceiling on probed Ollama context window; `deps.model_max_ctx = min(probe, max_ctx)` |
| `memory.search_backend` | `CO_MEMORY_SEARCH_BACKEND` | `hybrid` | Preferred retrieval backend before degradation |
| `memory.embedding_provider` | `CO_MEMORY_EMBEDDING_PROVIDER` | `tei` | Determines whether hybrid search can stay enabled |
| `memory_path` | `CO_MEMORY_PATH` | `~/.co-cli/memory` | User-global memory item directory synced during bootstrap |
| `mcp_servers` | `CO_MCP_SERVERS` | bundled defaults | MCP server definitions connected during startup |
| `personality` | `CO_PERSONALITY` | `tars` | Personality selected before agent instruction assembly |
| `reasoning_display` | `CO_REASONING_DISPLAY` | `summary` | Default reasoning-display mode at startup; CLI flags can override it before REPL entry |

## 4. Public Interface

| Symbol | Source | Contract |
| --- | --- | --- |
| `create_deps(frontend, stack) -> CoDeps` | `co_cli/bootstrap/core.py` | Async — assembles the runtime context from settings (validates config, probes model, builds tool registry, connects MCP, loads skills, builds memory store) |
| `restore_session(deps, frontend) -> Path` | `co_cli/bootstrap/core.py` | Picks the most recent `*.jsonl` under `sessions_dir` and writes it to `deps.session.session_path`; mints a new path when none exists |
| `probe_ollama_model(host, model) -> OllamaModelProbe` | `co_cli/bootstrap/check.py` | Posts to `/api/show`; returns `num_ctx` (floor validation) and the `vision` capability flag (the `image_view` gate) from one payload; degrades to `(None, False)` on error |
| `check_security() -> list[SecurityFinding]` | `co_cli/bootstrap/security.py` | Runs the once-per-session security posture checks consumed by `render_security_findings()` |
| `render_security_findings(findings) -> None` | `co_cli/bootstrap/security.py` | Prints any findings to the console at REPL handoff |
| `display_welcome_banner(deps, *, memory_count, session_count) -> None` | `co_cli/bootstrap/banner.py` | Renders the boundary banner; `Memory:` row shows backend + degradation + counts |

## 5. Files

| File | Purpose |
| --- | --- |
| `co_cli/main.py` | Owns module-load logging and telemetry setup, `_chat_loop()` startup orchestration, and the REPL boundary |
| `co_cli/bootstrap/core.py` | Owns `create_deps()` and `restore_session()` |
| `co_cli/bootstrap/check.py` | Provider, embedder, reranker, and Ollama model probe checks |
| `co_cli/bootstrap/banner.py` | Renders the welcome banner that marks bootstrap completion |
| `co_cli/bootstrap/security.py` | Security posture checks run once at startup (`check_security`, `render_security_findings`) |
| `co_cli/deps.py` | Defines `CoDeps`, path resolution, and sub-agent inheritance rules |
| `co_cli/config/core.py` | Defines `Settings`, layered config loading, and env override mapping |
| `co_cli/skills/loader.py` | `load_skills` — two-tier skill file loading used during bootstrap and `/skills reload` |
| `co_cli/commands/core.py` | Slash-command `dispatch` and `BUILTIN_COMMANDS` registrations |
| `co_cli/commands/skills.py` | `/skills` REPL command family (list/check/lint/reload/usage/pin/unpin) |
| `co_cli/commands/registry.py` | `BUILTIN_COMMANDS`, `filter_namespace_conflicts` — called after `load_skills` to drop namespace conflicts |
| `co_cli/session/filename.py` | Session filename generation, latest-session discovery, new-path factory |
| `co_cli/memory/store.py` | Implements the `MemoryStore` used when bootstrap enables indexed retrieval |
| `co_cli/session/transcript.py` | Extracts user-prompt and assistant-text parts from JSONL transcripts |
