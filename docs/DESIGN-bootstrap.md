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
│          → Settings singleton cached
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
│  ├─ CoConfig.from_settings(settings, cwd=Path.cwd())
│  ├─ config.validate() → error: raise ValueError (config shape only)
│  ├─ ModelRegistry.from_config(config) → model_registry
│  ├─ build_tool_registry(config) → ToolRegistry(toolset, mcp_toolsets, tool_index)
│  ├─ [if mcp_toolsets]
│  │      enter each MCP server on stack (stays alive for session)
│  │      on fail → warning per server
│  │      discover_mcp_tools(mcp_toolsets, exclude=native_tools) → mcp_index
│  │      tool_index.update(mcp_index)
│  ├─ _load_skills(skills_dir, settings, user_skills_dir) → skill_commands
│  ├─ resolve_knowledge_backend(config) → resolved config + KnowledgeStore | None
│  │      grep → skip reranker, no index
│  │      otherwise → _resolve_reranker() → try hybrid → fts5 → grep
│  │      reports degradation statuses via frontend.on_status()
│  ├─ _sync_knowledge(config, knowledge_store, frontend)
│  │      sync_dir("memory", ...) + sync_dir("library", ...)
│  │      on fail → store closed, returns None
│  ├─ [if ROLE_TASK configured] build_task_agent(config, role_model) → task_agents
│  └─ → CoDeps(shell, config, model_registry, knowledge_store, tool_index, skill_commands, task_agents, runtime)
│
├─ completer.words updated with skills
├─ build_agent(config=deps.config, model_registry=deps.model_registry) → Agent
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

### Settings Loading (`config.py`)

`Settings` is a Pydantic `BaseModel` built by `load_config()` and accessed via a lazy module-level singleton (`settings`). First access triggers `_ensure_dirs()` to create `~/.config/co-cli/` and `~/.local/share/co-cli/` if missing, then `load_config()`.

Three-layer merge, later layers win:

```text
Layer 1: ~/.config/co-cli/settings.json
Layer 2: <cwd>/.co-cli/settings.json via `_deep_merge_settings()`
Layer 3: env vars via fill_from_env model_validator
```

`fill_from_env` runs as `model_validator(mode='before')`, so env vars override both config files before validation.

`role_models` defaults:
- `gemini`: reasoning only → `gemini-3-flash-preview`; all other roles empty
- `ollama-openai`: all six roles populated with hardcoded defaults in `config.py`

Merge order is provider defaults for missing roles, then explicit config values, then env var overrides.

The `reasoning` role is validated post-construction. Missing or empty raises `ValueError` at startup.

Singleton access pattern:

```text
from co_cli.config import settings
```

First access resolves and caches `_settings`; later accesses reuse the singleton. Startup mutations such as `settings.theme = theme` modify that singleton in place.

### Deps Initialization (`create_deps()` In `bootstrap/_bootstrap.py`)

`create_deps()` (in `bootstrap/_bootstrap.py`) is async. It converts the `Settings` singleton into `CoConfig`, builds `ModelRegistry` and `ToolRegistry`, connects MCP servers and discovers their tools, builds the task agent, and assembles `CoDeps`. It calls `config.validate()` as a config-shape gate: checks that a reasoning model is configured and (for Gemini) that the API key is present — no HTTP probes. Provider connectivity is deferred to runtime; `run_turn()` handles `ModelHTTPError`/`ModelAPIError` with retries and clean error messages.

```text
create_deps(frontend, stack):
    config = CoConfig.from_settings(settings, cwd=Path.cwd())
    config.validate() → error: raise ValueError
    model_registry = ModelRegistry.from_config(config)
    tool_registry = build_tool_registry(config)  # native toolset + mcp toolsets
    [if mcp_toolsets]
        enter each MCP server on stack  # stays alive for session
        discover_mcp_tools(mcp_toolsets) → mcp_index
        tool_index.update(mcp_index)
    [if ROLE_TASK] build_task_agent → task_agents
    return CoDeps(shell, config, model_registry, tool_index, task_agents, runtime)
```

Knowledge backend resolution (IO probes to embedder/reranker, `KnowledgeStore` construction) and file sync happen inside `create_deps()` as Steps 6-7, before CoDeps assembly.

Key points:
- `settings.knowledge_search_backend` is the configured backend; `deps.config.knowledge_search_backend` is the resolved backend after degradation (set by `resolve_knowledge_backend` inside `create_deps()`)
- `CoConfig.from_settings(settings, cwd)` resolves all fields in a single call
- Static instructions (personality, rules, counter-steering) are assembled inside `build_agent()`, not here — they are an agent concern

### `CoDeps` Structure

`CoDeps` is flat — service handles (`shell`, `knowledge_store`), registries (`model_registry`, `tool_index`, `skill_commands`, `task_agents`), and config are top-level fields. Three grouped sub-objects hold mutable state:

| Field / Group | Lifetime | Sub-agent inheritance |
|---------------|----------|-----------------------|
| `shell`, `knowledge_store`, `model_registry`, `task_agents` | Session | Copied by reference (shared) |
| `tool_index`, `skill_commands` | Session | Copied by reference (shared) |
| `config` (`CoConfig`) | Session | Copied by reference (frozen) |
| `session` (`CoSessionState`) | Session, partially inherited | Credentials and approval rules inherited; per-session fields reset |
| `runtime` (`CoRuntimeState`) | Per-turn transient | Reset for sub-agents |

## 2. Provider And Model Checks

`config.validate()` (on `CoConfig` in `deps.py`) runs inside `create_deps()` as the config-shape gate — no IO. It checks that minimum config is present (reasoning role configured, Gemini API key present). Provider connectivity is deferred to runtime; `run_turn()` handles connection and model errors with retries and clean REPL messages.

| Condition | Behavior |
|-----------|----------|
| No reasoning model in config | `ValueError`; session never starts |
| Gemini provider with missing key | `ValueError`; session never starts |
| Ollama unreachable | Startup succeeds; first LLM call gets `ModelAPIError`, user sees error in REPL |
| Ollama model missing | Startup succeeds; first LLM call gets `ModelHTTPError`, user sees error in REPL |

On-demand diagnostics: `check_agent_llm` (IO probe) is available via `co config` and `check_capabilities` for users who want to verify connectivity.

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

Knowledge sync runs inside `create_deps()` as Step 7, after backend resolution (Step 6):

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

### Step 2 - Session Restore

```text
session_data = load_session(deps.config.session_path)

if is_fresh(session_data, deps.config.session_ttl_minutes):
    deps.session.session_id = session_data["session_id"]
    frontend.on_status("Session restored ...")
else:
    session_data = new_session()
    deps.session.session_id = session_data["session_id"]
    try:
        save_session(deps.config.session_path, session_data)
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
    skill_commands = _load_skills(config.skills_dir, settings, user_skills_dir=config.user_skills_dir)
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

`resolve_knowledge_backend(config)` runs inside `create_deps()` as Step 6, before sync (Step 7) and CoDeps assembly (Step 9):

```text
"grep" in config → hard stop, grep only (no index)
otherwise        → try hybrid first (if embedder available), then fts5, then grep
                   (configured "fts5" still attempts hybrid when embedder is reachable)

config = resolved config (returned from resolve_knowledge_backend)
knowledge_store = KnowledgeStore instance or None
degradation statuses reported directly via frontend.on_status()
```

Config is fully resolved before CoDeps construction — no post-construction mutation.

## 7. Boundary, State Mutations, And Failure Paths

### Welcome Banner Boundary

`display_welcome_banner(deps)` is called immediately after the inline wakeup steps complete:

```text
[inline steps 0-3]
display_welcome_banner(deps)
begin REPL loop
```

The banner marks the boundary between startup and interactive use. All status messages from knowledge resolution, wakeup steps, and skills loading appear above it. The banner reads version, model, cwd, tool count (`len(deps.tool_index)`), skill count (`len(deps.skill_commands)`), and MCP server count (`len(deps.config.mcp_servers or {})`) directly from `deps` — no health probe needed. The readiness headline shows `✓ Ready` normally, or `✓ Ready  (degraded)` when knowledge-index degradation is detected (`deps.knowledge_store is None and deps.config.knowledge_search_backend != "grep"`). Reranker-only degradation is already reported by individual status lines before the banner.

### State Mutations Summary

| Field | Set by | Value |
|-------|--------|-------|
| `deps.knowledge_store` | `create_deps()` Step 6-7 | `KnowledgeStore` or `None` (sync failure disables FTS) — set at construction, never mutated |
| `deps.session.session_id` | `restore_session()` (inline wakeup) | Single write: restored or new UUID hex |
| `deps.model_registry` | `create_deps()` Step 3 | `ModelRegistry` built from resolved `CoConfig` |
| `deps.tool_index` | Native + MCP entries set by `create_deps()` (native via `build_tool_registry()`, MCP via `discover_mcp_tools()`) | Full `dict[str, ToolConfig]` map with per-tool loading policy (`always_load`/`should_defer`) |
| `deps.skill_commands` | `create_deps()` via `_load_skills()` | Full dict of all loaded skills; model-facing registry derived via `get_skill_registry()` |
| `completer.words` | Before `create_deps()`: COMMANDS-only; after skills load: updated by `_build_completer_words()` | COMMANDS-only at startup; COMMANDS + skills after skills load |

### Failure Paths

| Condition | Outcome |
|-----------|---------|
| `create_deps()` raises `ValueError` (provider error, missing reasoning model) | `_chat_loop()` catches `ValueError`, prints `"Startup error: …"` and exits with code 1 via `SystemExit(1)` |
| `load_config()` schema validation fails (`ValidationError`) | re-raised as `ValueError` by `load_config()`; caught by `get_settings()`, which prints `"Configuration error: …"` to stderr and raises `SystemExit(1)` — never reaches `_chat_loop()` |
| Knowledge sync raises | Index closed, `knowledge_store = None`, grep fallback, session continues |
| Session file missing or unreadable | New session created |
| Session TTL expired | New session created |
| MCP server connection fails | Warning printed, startup continues with native tools only |
| One skill file fails to load | File skipped with warning; other skills still load |

### Recovery And Fallback

- Knowledge index unavailable: search tools fall back to grep-based search
- MCP unavailable: session continues with native tools only
- Session corruption: delete `.co-cli/session.json` to force a fresh session

## 8. Owning Code

| File | Role |
|------|------|
| `co_cli/main.py` | `_chat_loop()` startup assembly |
| `co_cli/bootstrap/_bootstrap.py` | `create_deps()` (config, registries, MCP, knowledge, skills, task agent), `restore_session()` |
| `co_cli/bootstrap/_check.py` | IO check functions (`check_agent_llm`, `check_reranker_llm`, `check_embedder`, `check_cross_encoder`, `check_mcp_server`, `check_tei`), settings-level entry point `check_settings()` (used by `_render_status.py`), runtime entry point `check_runtime()` / `RuntimeCheck` (used by `/status` tool), data types `CheckResult`, `CheckItem`, `DoctorResult` |
| `co_cli/bootstrap/_render_status.py` | `get_status()` / `StatusResult` / `render_status_table()` — system status assembly and display; `check_security()` / `SecurityCheckResult` / `render_security_findings()` — security posture checks: user config file permissions (Check 1), project config file permissions (Check 2), `shell_safe_commands` wildcard `"*"` entries (Check 3) |
| `co_cli/context/_session.py` | Session helpers: new/load/save/touch/is_fresh/increment_compaction |
| `co_cli/bootstrap/_banner.py` | `display_welcome_banner(deps: CoDeps)` — welcome banner |
| `co_cli/commands/_commands.py` | Skill loading helpers |
| `co_cli/deps.py` | `CoDeps` groups and sub-agent isolation |
| `co_cli/knowledge/_store.py` | `KnowledgeStore` — shared search store for memory and library |

## 9. See Also

- [DESIGN-system.md](DESIGN-system.md) - system architecture and capability surface
- [DESIGN-core-loop.md](DESIGN-core-loop.md) - main loop and turn state machine
- [DESIGN-tools.md](DESIGN-tools.md) - MCP lifecycle and tool surface
