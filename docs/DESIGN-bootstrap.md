# Co CLI — System Bootstrap Design

Canonical startup flow for co-cli. This doc is the sole owner for startup and wakeup behavior, covering the full sequence from settings loading through `display_welcome_banner()`: settings loading, deps initialization (`create_deps()`), model registry and agent creation, MCP init, session capabilities (MCP discovery + skill load), and four inline wakeup steps (knowledge resolution, knowledge sync, session restore, skills report). Skill file format, load gates, and dispatch semantics live in [DESIGN-skills.md](DESIGN-skills.md).

Bootstrap owns sequencing. Integration health checks (`check_runtime()` in `co_cli/bootstrap/_check.py`) are not called during bootstrap; they are invoked on-demand by the `/status` tool in `co_cli/tools/capabilities.py`.

```
co_cli.main  (module load)
│
├─ co_cli.display._core → co_cli.config.settings (lazy init)
│      _ensure_dirs() → load_config()
│          Layer 1: ~/.config/co-cli/settings.json
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
│  ├─ _discover_knowledge_backend(config, frontend) → (config, KnowledgeStore | None)
│  │      grep → return (config, None)
│  │      _resolve_reranker() → probe embedder → resolve backend
│  │      config fields mutated directly to reflect runtime backend
│  │      degradation recorded in deps.degradations dict
│  │      construct KnowledgeStore with resolved config
│  │      on fail → hybrid falls back to fts5, then grep (returns None)
│  ├─ _sync_knowledge_store(store, config, frontend)
│  │      reconcile store with memory + library files on disk
│  │      hash-based — skips unchanged files
│  │      on fail → store closed, returns None
│  └─ → CoDeps(shell, config=settings, paths, model, knowledge_store, tool_index, skill_commands, runtime)
│
├─ completer.words updated with skills
├─ build_agent(config=deps.config, model=deps.model) → Agent
│
├─ restore_session(deps, frontend)
│      fresh → restore session_id; stale → new session
│
├─ frontend.on_status("{skill_count} skill(s) loaded")
│
├─ display_welcome_banner(deps)
▼
REPL loop begins
```

## 1. Settings Loading And Deps Initialization

### Settings Loading (`config/`)

`Settings` is a Pydantic `BaseModel` with nested sub-models (`LlmSettings`, `KnowledgeSettings`, `WebSettings`, `MemorySettings`, `SubagentSettings`, `ShellSettings`), built by `load_config()` in `co_cli/config/_core.py` and accessed via a lazy module-level singleton (`settings`). First access triggers `_ensure_dirs()` to create `~/.config/co-cli/` and `~/.local/share/co-cli/` if missing, then `load_config()`.

Three-layer merge, later layers win:

```text
Layer 1: ~/.config/co-cli/settings.json
Layer 2: <cwd>/.co-cli/settings.json via `_deep_merge_settings()`
Layer 3: env vars via fill_from_env model_validator
```

`fill_from_env` runs as `model_validator(mode='before')`, so env vars override both config files before validation.

`llm.model` defaults to a provider-specific model name when not configured. `CO_LLM_MODEL` env var overrides it. `fill_from_env` maps env vars into the nested structure before validation.

Singleton access pattern:

```text
from co_cli.config import settings
```

First access resolves and caches `_settings`; later accesses reuse the singleton. Startup mutations such as `settings.theme = theme` modify that singleton in place.

### Deps Initialization (`create_deps()` In `bootstrap/_bootstrap.py`)

`create_deps()` (in `bootstrap/_bootstrap.py`) is async. It uses the `Settings` singleton directly (no `CoConfig` conversion), builds `LlmModel` and `ToolRegistry`, connects MCP servers and discovers their tools, and assembles `CoDeps` with resolved workspace paths. It calls `config.llm.validate_config()` as a config-shape gate: checks that a model is configured and (for Gemini) that the API key is present — no HTTP probes. Provider connectivity is deferred to runtime; `run_turn()` handles `ModelHTTPError`/`ModelAPIError` with retries and clean error messages.

```text
create_deps(frontend, stack):
    config = settings  # Settings singleton used directly
    paths = resolve_workspace_paths(settings, cwd=Path.cwd())
    config.llm.validate_config() → error: raise ValueError
    [if ollama-openai] probe_ollama_context(host, model)  # Step 2b: fail-fast on undersized num_ctx; override config.llm.num_ctx with runtime value
    llm_model = build_model(config.llm)  # single LlmModel for the session
    tool_registry = build_tool_registry(config)  # native toolset + mcp toolsets
    [if mcp_toolsets]
        enter each MCP server on stack  # stays alive for session
        discover_mcp_tools(mcp_toolsets) → mcp_index
        tool_index.update(mcp_index)
    return CoDeps(config=settings, **paths, shell, model=llm_model, tool_index, runtime)
```

Knowledge backend resolution (IO probes to embedder/reranker, `KnowledgeStore` construction) and file sync happen inside `create_deps()` as Steps 6-7, before CoDeps assembly.

### Settings And Path Resolution

`Settings` is a Pydantic `BaseModel` with nested sub-models. `CoDeps.config` holds the `Settings` instance directly — there is no intermediate `CoConfig` dataclass. During bootstrap, `create_deps()` may mutate Settings fields in-place as degradation resolves runtime values:

```text
config = settings                                 # Settings singleton used directly
paths = resolve_workspace_paths(settings, cwd)    # cwd-relative paths for CoDeps
_discover_knowledge_backend(config, ...)          # mutates config fields in-place (backend, reranker)
→ CoDeps(config=settings, **paths, degradations)  # final instance; paths and degradations on CoDeps
```

After entering `CoDeps`, config is read-only by convention — sub-agents share the same instance by reference and nothing mutates it once bootstrap completes. Degradation records live on `deps.degradations` (a `dict[str, str]` keyed by service name on `CoDeps`, not on Settings), and workspace paths live on `CoDeps` fields (`deps.memory_dir`, `deps.library_dir`, etc.).

Key points:
- Config reflects runtime reality — `deps.config.knowledge.search_backend` is the actual backend after degradation, not the user's original setting
- `deps.degradations` records what changed and why (e.g. `{"knowledge": "hybrid → fts5 (embedder unavailable)"}`) — generic, any service can use it
- Path fields (`memory_dir`, `library_dir`, `skills_dir`, `sessions_dir`, `workspace_root`, `tool_results_dir`, `knowledge_db_path`, `obsidian_vault_path`) live on `CoDeps`, resolved via `resolve_workspace_paths()`
- `deps.config.llm.num_ctx` may be overridden during Step 2b with the runtime Modelfile value probed from Ollama — it reflects the actual allocated context window, not just the settings value
- Static instructions (personality, rules, counter-steering) are assembled inside `build_agent()`, not here — they are an agent concern

### `CoDeps` Structure

`CoDeps` is flat — service handles (`shell`, `knowledge_store`), the model (`model`), registries (`tool_index`, `skill_commands`), workspace paths, degradations, and config are top-level fields. Two grouped sub-objects hold mutable state:

| Field / Group | Lifetime | Sub-agent inheritance |
|---------------|----------|-----------------------|
| `shell`, `knowledge_store`, `model` | Session | Copied by reference (shared) |
| `tool_index`, `skill_commands` | Session | Copied by reference (shared) |
| `config` (`Settings`) | Session | Copied by reference (read-only by convention after bootstrap) |
| workspace paths (`memory_dir`, `library_dir`, etc.) | Session | Copied by reference (shared) |
| `degradations` | Session | Copied by reference (shared) |
| `session` (`CoSessionState`) | Session, partially inherited | Credentials and approval rules inherited; per-session fields reset |
| `runtime` (`CoRuntimeState`) | Per-turn transient | Reset for sub-agents |

## 2. Provider And Model Checks

`config.llm.validate_config()` (on `LlmSettings` in `config/_llm.py`) runs inside `create_deps()` as the config-shape gate — no IO. It checks that minimum config is present (model configured, Gemini API key present). Provider connectivity is deferred to runtime; `run_turn()` handles connection and model errors with retries and clean REPL messages.

| Condition | Behavior |
|-----------|----------|
| No model in config | `ValueError`; session never starts |
| Gemini provider with missing key | `ValueError`; session never starts |
| Ollama unreachable | Startup succeeds; first LLM call gets `ModelAPIError`, user sees error in REPL |
| Ollama model missing | Startup succeeds; first LLM call gets `ModelHTTPError`, user sees error in REPL |

On-demand diagnostics: `check_agent_llm` (IO probe) is available via `co config` and `check_capabilities` for users who want to verify connectivity.

### Step 2b — Ollama Context Probe

Immediately after `config.llm.validate_config()`, when the provider is `ollama-openai`, `create_deps()` calls `probe_ollama_context(llm_host, reasoning_model)` from `_check.py`. This probes `/api/show` and extracts `num_ctx` from the Modelfile `parameters` string — the actual runtime allocation, not the model's theoretical maximum from `model_info`.

```text
probe_ollama_context(host, model):
    POST /api/show → parse "parameters" string for "num_ctx N"
    num_ctx <= 0     → warn (no Modelfile param found; proceed)
    num_ctx < 65536  → error: raise ValueError (fail-fast; session never starts)
    num_ctx >= 65536 → ok; return extra={"num_ctx": num_ctx}

if ok and runtime num_ctx != config.llm.num_ctx:
    config.llm.num_ctx = runtime_num_ctx   # direct field assignment
```

`MIN_AGENTIC_CONTEXT = 65_536` (64K) is the hard floor defined in `_check.py`. Below this, system prompt + tools + working history + one tool result + compaction headroom + output reserve + safety margin cannot fit. The agent would compact every turn, and the summarizer call itself would consume most of the remaining context.

| Condition | Behavior |
|-----------|----------|
| Ollama unreachable (probe fails) | `warn`; probe skipped; startup continues with settings `llm.num_ctx` |
| Model not found in `/api/show` | `warn`; probe skipped; startup continues |
| `num_ctx` absent from Modelfile | `warn`; no override; startup continues |
| `num_ctx < MIN_AGENTIC_CONTEXT` | `error`; `ValueError` raised; session never starts |
| `num_ctx >= MIN_AGENTIC_CONTEXT` and differs from config | `config.llm.num_ctx` overridden with runtime value |
| `num_ctx` matches config exactly | no override; startup continues |

## 3. Entry Conditions

The inline wakeup steps run once per `chat_loop()` startup after deps initialization:

- `create_deps()` returned without raising (config-shape validation passed)
- `PromptSession` is constructed before `create_deps()` with a COMMANDS-only completer; `completer.words` is updated after skills load using `_build_completer_words()`
- `build_agent()` has returned an `Agent`; `deps.tool_index` was already set by `create_deps()` via `build_tool_registry()`
- Skills are loaded inside `create_deps()` via `_load_skills()` and passed into the `CoDeps` constructor; `completer.words` is updated immediately after `create_deps()` returns

The inline wakeup steps run after `create_deps()` and `build_agent()`: `restore_session()` and the skills loaded report. Knowledge backend resolution and sync now happen inside `create_deps()`.

## 4. Full Startup Sequence

See the callstack sequence diagram at the top of this doc. The key structural detail: everything from `create_deps()` through `display_welcome_banner()` runs inside a `try/finally` block that guarantees cleanup (background task kill, MCP stack close, shell cleanup) on any exit path.

### MCP Init Failure

MCP server connection and tool discovery are handled inside `create_deps()`. Each MCP server is entered individually on the caller's `AsyncExitStack`. If a server fails to connect, a warning is reported via `frontend.on_status()` and that server is skipped. Tool discovery runs on successfully connected servers only.

## 5. Inline Wakeup Steps

Two steps run inline in `chat_loop()` after `create_deps()` and `build_agent()` complete, reporting status via `frontend.on_status()`. Knowledge backend resolution and sync now happen inside `create_deps()`.

```text
inline wakeup (in chat_loop()):
    Step 0: restore_session  →  session_data local variable
    Step 1: skills_loaded_report  (reads len(deps.skill_commands) directly)
```

`session_data` stays in `chat_loop()` local state for turn-by-turn persistence. After Step 1, `display_welcome_banner(deps)` reads tool counts and skill counts from `deps`, and MCP server count from `deps.config` — no separate health probe.

### Knowledge Sync (inside create_deps)

Knowledge sync runs inside `create_deps()` as Step 7, after backend discovery and store construction (Step 6):

```text
if knowledge_store is not None and (memory_dir.exists() or library_dir.exists()):
    try:
        mem_count = knowledge_store.sync_dir("memory", memory_dir, kind_filter="memory")
        art_count = knowledge_store.sync_dir("library", library_dir, kind_filter="article")
        frontend.on_status("Knowledge synced ...")
    except Exception:
        knowledge_store.close()
        knowledge_store = None
        frontend.on_status("Knowledge sync failed - index disabled")
else:
    frontend.on_status("Knowledge index not available - skipped")
```

Details:
- `sync_dir()` is hash-based and skips unchanged content
- both memory and library trees sync through the same API with source-specific `kind_filter`
- on failure, FTS is disabled for the session and the system continues with grep fallback

### Step 2 - Session Restore (see [DESIGN-context.md](DESIGN-context.md) §2.3)

```text
session_data = find_latest_session(deps.sessions_dir)  # mtime scan

if session_data is not None:
    deps.session.session_id = session_data["session_id"]
    frontend.on_status("Session restored ...")
else:
    session_data = new_session()   # standard UUID with dashes
    deps.session.session_id = session_data["session_id"]
    try:
        save_session(deps.sessions_dir, session_data)
    except OSError:
        frontend.on_status("Session save failed — session will not persist")
    frontend.on_status("Session new ...")

return session_data
```

Session freshness uses `last_used_at` against current time. Future timestamps caused by clock skew are treated as fresh.

Session dict fields:
- `session_id`
- `created_at`
- `last_used_at`
- `compaction_count`

The returned `session_data` stays in `chat_loop()` local state and is reused for turn-by-turn persistence.
- After each completed LLM turn: `touch_session(session_data)` then `save_session()`
- On `/compact`: `increment_compaction(session_data)` then `save_session()`
- On exit: no extra save; cleanup only

### Step 3 - Skills Loaded Report

```text
frontend.on_status("{len(deps.skill_commands)} skill(s) loaded")
```

This is a visibility step only. Skill loading already happened inside `create_deps()`. The count reflects wired-available skills (description present, not `disable_model_invocation`) — the same set visible to the model via `skill_registry`.

## 6. Pre-Wakeup Subflows

### Skills Load

Skills load inside `create_deps()` as part of deps assembly:

```text
create_deps() step 5:
    skill_commands = _load_skills(deps.skills_dir, settings, user_skills_dir=deps.user_skills_dir)
        pass 1: co_cli/skills/*.md (built-in, lowest precedence)
        pass 2: user_skills_dir/*.md (user-global)
        pass 3: skills_dir/*.md (project-local, highest precedence)
        parse frontmatter, check requires, scan for security issues
    set_skill_commands(skill_commands, deps)   ← deps.skill_commands
    → int (skill_count)

completer.words = _build_completer_words(deps.skill_commands)   ← extends COMMANDS-only list to COMMANDS + skills
```

`PromptSession` is built before `create_deps()` with a COMMANDS-only completer. After `create_deps()` returns, `_build_completer_words()` updates `completer.words` in-place to include skill names from `deps.skill_commands`.

`disable_model_invocation: true` skills stay available to the REPL but are hidden from the model-facing `skill_registry`.

Live skill reloading happens after startup in the main loop: before each REPL prompt, `.co-cli/skills/` mtimes are checked, `_load_skills()` reruns when files changed, and `completer.words` is refreshed via `_build_completer_words()`. That post-startup path is covered in [DESIGN-core-loop.md](DESIGN-core-loop.md).

### Knowledge Backend Resolution

`_discover_knowledge_backend(config, frontend)` runs inside `create_deps()` as Step 6, before sync (Step 7) and CoDeps assembly (Step 9). It resolves reranker availability, probes embedder, constructs the store, and returns `(config, store)`. Config fields are mutated directly to reflect the runtime backend.

```text
"grep" in config → return (config, None)
otherwise        → resolve reranker, probe embedder
                   update config: knowledge.search_backend = resolved backend
                   record degradation in deps.degradations dict
                   construct KnowledgeStore with resolved config
                   on construction failure: hybrid falls back to fts5, then grep (None)
                   degradation statuses reported via frontend.on_status()
```

Config reflects runtime reality: `deps.config.knowledge.search_backend` is the actual backend. `deps.degradations["knowledge"]` records what changed and why when degradation occurred. Tools and status read the backend from config.

## 7. Boundary, State Mutations, And Failure Paths

### Welcome Banner Boundary

`display_welcome_banner(deps)` is called immediately after the inline wakeup steps complete:

```text
[inline steps 0-3]
display_welcome_banner(deps)
begin REPL loop
```

The banner marks the boundary between startup and interactive use. All status messages from knowledge resolution, wakeup steps, and skills loading appear above it. The banner reads version, model, cwd, tool count (`len(deps.tool_index)`), skill count (`len(deps.skill_commands)`), and MCP server count (`len(deps.config.mcp_servers or {})`) directly from `deps` — no health probe needed. The knowledge line shows the current runtime backend from config; when `deps.degradations` has a `"knowledge"` entry, it appends the degradation detail. The readiness headline shows `✓ Ready` normally, or `✓ Ready  (degraded)` when `deps.degradations` is non-empty. Reranker-only degradation is already reported by individual status lines before the banner.

### State Mutations Summary

| Field | Set by | Value |
|-------|--------|-------|
| `deps.knowledge_store` | `create_deps()` Step 6-7 | `KnowledgeStore` or `None` (sync failure disables FTS) — set at construction, never mutated |
| `deps.session.session_id` | `restore_session()` (inline wakeup) | Single write: restored or new UUID hex |
| `deps.model` | `create_deps()` Step 3 | `LlmModel` built via `build_model(config.llm)` |
| `deps.tool_index` | Native + MCP entries set by `create_deps()` (native via `build_tool_registry()`, MCP via `discover_mcp_tools()`) | Full `dict[str, ToolInfo]` map with per-tool `LoadPolicy` enum (`ALWAYS`/`DEFERRED`) |
| `deps.skill_commands` | `create_deps()` via `_load_skills()` | Full dict of all loaded skills; model-facing registry derived via `get_skill_registry()` |
| `completer.words` | Before `create_deps()`: COMMANDS-only; after skills load: updated by `_build_completer_words()` | COMMANDS-only at startup; COMMANDS + skills after skills load |

### Failure Paths

| Condition | Outcome |
|-----------|---------|
| `create_deps()` raises `ValueError` (provider error, missing model, or Ollama `num_ctx` below `MIN_AGENTIC_CONTEXT`) | `_chat_loop()` catches `ValueError`, prints `"Startup error: …"` and exits with code 1 via `SystemExit(1)` |
| `load_config()` schema validation fails (`ValidationError`) | re-raised as `ValueError` by `load_config()`; caught by `get_settings()`, which prints `"Configuration error: …"` to stderr and raises `SystemExit(1)` — never reaches `_chat_loop()` |
| Knowledge sync raises | Index closed, `knowledge_store = None`, grep fallback, session continues |
| Session file missing or unreadable | New session created |
| Session TTL expired | New session created |
| MCP server connection fails | Warning printed, startup continues with native tools only |
| One skill file fails to load | File skipped with warning; other skills still load |

### Recovery And Fallback

- Knowledge index unavailable: search tools fall back to grep-based search
- MCP unavailable: session continues with native tools only
- Session corruption: delete `.co-cli/sessions/` directory to force a fresh session

## 8. Owning Code

| File | Role |
|------|------|
| `co_cli/main.py` | `_chat_loop()` startup assembly |
| `co_cli/bootstrap/_bootstrap.py` | `create_deps()` (config, registries, MCP, knowledge, skills), `restore_session()` |
| `co_cli/bootstrap/_check.py` | IO check functions (`check_agent_llm`, `check_reranker_llm`, `check_embedder`, `check_cross_encoder`, `check_mcp_server`, `check_tei`), bootstrap context probe `probe_ollama_context()` + `MIN_AGENTIC_CONTEXT` constant (Step 2b), settings-level entry point `check_settings()` (used by `_render_status.py`), runtime entry point `check_runtime()` / `RuntimeCheck` (used by `/status` tool), data types `CheckResult`, `CheckItem`, `DoctorResult` |
| `co_cli/bootstrap/_render_status.py` | `get_status()` / `StatusResult` / `render_status_table()` — system status assembly and display; `check_security()` / `SecurityCheckResult` / `render_security_findings()` — security posture checks: user config file permissions (Check 1), project config file permissions (Check 2), `shell_safe_commands` wildcard `"*"` entries (Check 3) |
| `co_cli/context/session.py` | Session helpers: new/load/save/find_latest/touch/increment_compaction |
| `co_cli/bootstrap/_banner.py` | `display_welcome_banner(deps: CoDeps)` — welcome banner |
| `co_cli/commands/_commands.py` | Skill loading helpers |
| `co_cli/deps.py` | `CoDeps` groups and sub-agent isolation |
| `co_cli/knowledge/_store.py` | `KnowledgeStore` — shared search store for memory and library |

## 9. See Also

- [DESIGN-system.md](DESIGN-system.md) - system architecture and capability surface
- [DESIGN-core-loop.md](DESIGN-core-loop.md) - main loop and turn state machine
- [DESIGN-tools.md](DESIGN-tools.md) - MCP lifecycle and tool surface
