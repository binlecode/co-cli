# Co CLI — System Bootstrap Design

Canonical startup flow for co-cli. This doc is the sole owner for startup and wakeup behavior, covering the full sequence from settings loading through `display_welcome_banner()`: settings loading, deps initialization (`create_deps()`), model dependency check, skills load, agent creation, MCP init, and the three inline wakeup steps (knowledge sync, session restore, skills report). Skill file format, load gates, and dispatch semantics live in [DESIGN-skills.md](DESIGN-skills.md).

Bootstrap owns sequencing. Integration health checks (`check_runtime()` in `co_cli/bootstrap/_check.py`) are not called during bootstrap; they are invoked on-demand by the `/status` tool in `co_cli/tools/capabilities.py`.

```
co_cli.main  (module load)
│
├─ co_cli.display._core                      # import triggers settings load
│     co_cli.config.settings                 # first access triggers lazy init
│         _ensure_dirs()                     # mkdir ~/.config/co-cli, ~/.local/share/co-cli
│         load_config()
│             Layer 1: ~/.config/co-cli/settings.json
│             Layer 2: <cwd>/.co-cli/settings.json  → _deep_merge_settings(base, override)
│             Layer 3: Settings.model_validate(merged)
│                          fill_from_env model_validator(mode='before')
│                          reads CO_CLI_* env vars, overrides merged dict
│             → Settings instance cached as co_cli.config._settings
│
├─ SQLiteSpanExporter()                      # DATA_DIR already created by _ensure_dirs()
├─ TracerProvider(resource=...)
└─ Agent.instrument_all(InstrumentationSettings(...))

co_cli.main.chat() → asyncio.run(_chat_loop())
│
├─ TerminalFrontend()
├─ WordCompleter(["/cmd", ...])              # COMMANDS-only; updated after skills load
├─ PromptSession(
│      history=FileHistory(DATA_DIR / "history.txt"),
│      completer=completer,
│      complete_while_typing=False,
│  )
│
├─ create_deps()                             # bootstrap/_bootstrap.py
│  │
│  │  Step 1 — build config
│  ├─ CoConfig.from_settings(settings, cwd=Path.cwd())
│  │      # resolves all cwd-relative paths and settings fields in one call
│  │
│  │  Step 2 — fail-fast gate (provider credentials + model availability)
│  ├─ check_agent_llm(config)  → "error": raise ValueError  # session never starts
│  ├─ _build_system_prompt(provider, model_name, config)  # file I/O; safe after gate
│  │      loads soul seed, memories, mindsets, examples, critique
│  │      └─ assemble_prompt(...)  →  config = dataclasses.replace(config, system_prompt=assembled_prompt)
│  │
│  │  Step 3 — construct services
│  ├─ resolve_reranker(config)
│  │      → (config, reranker_statuses)   # nulls unavailable reranker fields
│  ├─ resolve_knowledge_backend(config)
│  │      "grep" in config → hard stop, grep only (no index)
│  │      otherwise        → try hybrid first (if embedder available), then fts5, then grep
│  │      returns (resolved_config, knowledge_index, knowledge_statuses)
│  │      startup_statuses = reranker_statuses + knowledge_statuses
│  ├─ TaskRunner(
│  │      storage=TaskStorage(resolved_config.tasks_dir),
│  │      max_concurrent, inactivity_timeout, auto_cleanup, retention_days,
│  │  )
│  ├─ CoServices(
│  │      shell=ShellBackend(), knowledge_index=knowledge_index,
│  │      task_runner=task_runner, model_registry=ModelRegistry.from_config(resolved_config),
│  │  )
│  ├─ CoRuntimeState()
│  └─ (CoDeps(services, resolved_config, capabilities=CoCapabilityState(), runtime=runtime), startup_statuses)   # tuple return
│
├─ deps, startup_statuses = create_deps()
├─ for status in startup_statuses: frontend.on_status(status)
│
├─ resolved = model_registry.get(ROLE_REASONING, fallback)  # ResolvedModel(model, settings)
│      if model_registry else ResolvedModel(model=None, settings=None)
├─ primary_model = resolved.model            # for signal detection and background compaction
│
├─ agent_result = build_agent(config=deps.config, resolved=resolved)  # agent.py — pure construction, no I/O
│  │
│  ├─ [if mcp_servers] build MCPServerStdio/HTTP toolset objects → mcp_toolsets
│  ├─ Agent(
│  │      resolved.model,                    # baked in — same pattern as sub-agent factories
│  │      instructions=config.system_prompt,  # pre-assembled by create_deps() Step 2
│  │      model_settings=resolved.settings,
│  │      deps_type=CoDeps,
│  │      retries=config.tool_retries,
│  │      output_type=[str, DeferredToolRequests],
│  │      history_processors=[truncate_tool_returns, detect_safety_issues,
│  │                          inject_opening_context, truncate_history_window],
│  │      toolsets=mcp_toolsets,             # None if no mcp_servers
│  │  )
│  ├─ @agent.instructions add_current_date          # today's date, fresh each turn
│  ├─ @agent.instructions add_shell_guidance        # shell policy reminder
│  ├─ @agent.instructions add_project_instructions  # .co-cli/instructions.md if present
│  ├─ @agent.instructions add_always_on_memories    # always_on standing-context memories
│  ├─ @agent.instructions add_personality_memories  # personality-context memories
│  ├─ agent.tool(run_shell_command, requires_approval=False)  # shell approval is command-scoped inside the tool
│  ├─ agent.tool(save_memory), agent.tool(recall_article), ...
│  ├─ agent.tool(run_coder_subagent), agent.tool(run_thinking_subagent), ...  # only if role model is configured
│  └─ returns AgentCapabilityResult
│
├─ agent = agent_result.agent
├─ deps.capabilities.tool_names     = agent_result.tool_names
├─ deps.capabilities.tool_approvals = agent_result.tool_approvals
│
├─ AsyncExitStack.enter_async_context(agent)  # MCP server subprocesses start here
│      Exception → print warning, _mcp_init_ok stays False
│
├─ session_cap = await initialize_session_capabilities(agent, deps, frontend, _mcp_init_ok)
│      [if mcp_servers and _mcp_init_ok] discover_mcp_tools() → deps.capabilities updated
│      _load_skills() + set_skill_commands() → deps.capabilities.skill_commands + skill_registry
├─ completer.words = _build_completer_words(deps.capabilities.skill_commands)  # COMMANDS + skills
│
├─ sync_knowledge(deps, frontend)
│      knowledge_index.sync_dir("memory", memory_dir, kind_filter="memory")
│      knowledge_index.sync_dir("library", library_dir, kind_filter="article")
│      on fail → knowledge_index.close(); deps.services.knowledge_index = None
│
├─ restore_session(deps, frontend)
│      load_session(session_path)
│      is_fresh? → yes: deps.session.session_id = session_data["session_id"]
│               → no:  new_session(); deps.session.session_id = new UUID; save_session()
│      returns session_data  # kept in chat_loop local state for per-turn persistence
│
├─ frontend.on_status(f"{session_cap.skill_count} skill(s) loaded")
│
└─ display_welcome_banner(deps, startup_statuses)
   │    tool_count  = len(deps.capabilities.tool_names)
   │    skill_count = len(deps.capabilities.skill_registry)
   │    mcp_count   = len(deps.config.mcp_servers or {})
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

`create_deps()` (in `bootstrap/_bootstrap.py`) converts the `Settings` singleton into `CoServices`, `CoConfig`, `CoSessionState`, and `CoRuntimeState`, then assembles `CoDeps`. It calls `check_agent_llm` (from `bootstrap/_check.py`) as a single fail-fast gate: one sync HTTP call to Ollama (`/api/tags`, timeout=5) checking both reachability and model availability. Hard errors raise `ValueError` immediately; the session never starts.

```text
create_deps():
    # Step 1: build config (single call, fully resolved)
    config = CoConfig.from_settings(settings, cwd=Path.cwd())
    # from_settings() resolves all cwd-relative paths, library_dir, and settings fields

    # Step 2: fail-fast gates (ordered: provider → model → prompt assembly)
    result = check_agent_llm(config)
    if result.status == "error":
        raise ValueError(result.detail)

    config = dataclasses.replace(
        config,
        system_prompt=_build_system_prompt(provider, normalized_model, config),
    )
    # _build_system_prompt: loads soul seed, memories, mindsets, examples, critique; calls assemble_prompt()

    # Step 3: resolve reranker and knowledge backend, construct services (lazy imports inside function body)
    config, reranker_statuses = resolve_reranker(config)
    config, knowledge_index, knowledge_statuses = resolve_knowledge_backend(config)
    startup_statuses = reranker_statuses + knowledge_statuses
    task_runner = TaskRunner(
        storage=TaskStorage(config.tasks_dir),
        max_concurrent=config.background_max_concurrent,
        inactivity_timeout=config.background_task_inactivity_timeout,
        auto_cleanup=config.background_auto_cleanup,
        retention_days=config.background_task_retention_days,
    )
    services = CoServices(
        shell=ShellBackend(),
        knowledge_index=knowledge_index,
        task_runner=task_runner,
        model_registry=ModelRegistry.from_config(config),
    )
    runtime = CoRuntimeState()
    return CoDeps(services=services, config=config, runtime=runtime), startup_statuses
```

Key transformation:
- `settings.knowledge_search_backend` is the configured backend
- `deps.config.knowledge_search_backend` is the resolved backend after degradation
- `CoConfig.from_settings(settings, cwd)` resolves all fields in a single call; `system_prompt` is the one field set afterward via `dataclasses.replace()` — after the fail-fast gate passes so credential validity is established before file I/O

### `CoDeps` Group Semantics

| Group | Type | Lifetime | Sub-agent inheritance |
|-------|------|----------|-----------------------|
| `services` | `CoServices` | Session | Shared by reference |
| `config` | `CoConfig` | Session | Shared by reference |
| `capabilities` | `CoCapabilityState` | Set during startup | Shared by reference |
| `session` | `CoSessionState` | Session, partially inherited for sub-agents | Credentials and approval rules inherited; per-session fields reset |
| `runtime` | `CoRuntimeState` | Orchestration-layer transient state | Reset for sub-agents |

The `capabilities` group holds bootstrap-set capability metadata (tool names, approval map, MCP discovery errors, skill registry) that is constant once startup completes. The session group holds tool-visible mutable state such as approvals and todos. The runtime group holds orchestration-layer transient state such as compaction, usage, and safety state.
`startup_statuses` is returned as the second element of `create_deps()`'s tuple; the chat loop prints these one-time bootstrap status lines (e.g. knowledge backend degradation notices) before the welcome banner.

## 2. Provider And Model Checks

`check_agent_llm` (from `bootstrap/_check.py`) runs inside `create_deps()` as the single fail-fast gate. Error status raises `ValueError` immediately — session never starts. Any other status continues. After the gate passes, `_build_system_prompt()` assembles the static system prompt (file I/O; safe here because credentials are known-valid). `ModelRegistry` is built in Step 3 immediately after.

| Condition | Behavior |
|-----------|----------|
| Gemini provider with missing key | `ValueError`; session never starts |
| Reasoning model missing from Ollama (host reachable) | `ValueError`; session never starts |
| Ollama unreachable | `warn`; session continues, model registry built with configured role_models |
| Optional role model missing (host reachable) | `warn`; session continues, affected role tools degrade silently |

## 3. Entry Conditions

The inline wakeup steps run once per `chat_loop()` startup after deps initialization:

- `create_deps()` returned without raising (provider/model probes passed)
- `PromptSession` is constructed before `create_deps()` with a COMMANDS-only completer; `completer.words` is updated after skills load (inside `async with agent`) using `_build_completer_words()`
- `build_agent()` has returned an `AgentCapabilityResult`; `deps.capabilities.tool_names` and `deps.capabilities.tool_approvals` are set immediately after from `agent_result` attributes
- `async with agent` has been entered; if MCP init fails, a warning is printed and startup continues with native tools only
- `initialize_session_capabilities()` runs inside `async with agent` after MCP connect; it handles MCP discovery (when `mcp_init_ok=True`) and skill loading, returning `SessionCapabilityResult`; `completer.words` is updated immediately after from `deps.capabilities.skill_commands`

The three inline wakeup steps run inside the `async with agent` block after `initialize_session_capabilities()` completes.

## 4. Full Startup Sequence

```text
chat_loop():
    frontend = TerminalFrontend()

    completer = WordCompleter([f"/{name}" for name in BUILTIN_COMMANDS], sentence=True)  ← BUILTIN_COMMANDS-only
    session = PromptSession(history=..., completer=completer, ...)

    deps = create_deps()   ← fail-fast on provider/model error; TaskRunner/TaskStorage constructed inside

    resolved = model_registry.get(ROLE_REASONING, fallback)  # ResolvedModel(model, settings)
    #          if model_registry else ResolvedModel(model=None, settings=None)
    primary_model = resolved.model              ← for signal detection and background compaction

    agent_result = build_agent(config=deps.config, resolved=resolved)
    agent = agent_result.agent
    deps.capabilities.tool_names     = agent_result.tool_names     ← set immediately after build_agent
    deps.capabilities.tool_approvals = agent_result.tool_approvals

    # inside async with agent
    stack = AsyncExitStack()
    message_history: list = []
    session_data: Any = None
    last_interrupt_time = 0.0
    try:
        _mcp_init_ok = False
        try:
            await stack.enter_async_context(agent)
            _mcp_init_ok = True
        except Exception as e:
            console.print("[warn]MCP server failed to connect: {e} — running without MCP tools.[/warn]")
            # startup continues with native tools only

        session_cap = await initialize_session_capabilities(agent, deps, frontend, _mcp_init_ok)
            # [if mcp_servers and _mcp_init_ok] discover_mcp_tools() → deps.capabilities updated
            # _load_skills() + set_skill_commands() → capabilities.skill_commands + skill_registry
        completer.words = _build_completer_words(deps.capabilities.skill_commands)   ← updated to COMMANDS + skills

        [inline wakeup steps 1–3]
        display_welcome_banner(deps, startup_statuses)
        begin REPL loop
    finally:
        cleanup
```

### MCP Init Failure

If `stack.enter_async_context(agent)` raises:

```text
except Exception as e:
    console.print("[warn]MCP server failed to connect: {e} — running without MCP tools.[/warn]")
    # startup continues
```

MCP failure is non-fatal. A warning is printed and startup continues with native tools only. MCP tool discovery (`discover_mcp_tools`) is skipped when the context manager failed to enter.

## 5. Inline Wakeup Steps

Three sequential steps run inline in `chat_loop()` after `initialize_session_capabilities()` completes, reporting status via `frontend.on_status()`.

```text
inline wakeup (in chat_loop()):
    Step 1: sync_knowledge
    Step 2: restore_session  →  session_data local variable
    Step 3: skills_loaded_report  (reads len(deps.capabilities.skill_registry) directly)
```

`session_data` stays in `chat_loop()` local state for turn-by-turn persistence. After Step 3, `display_welcome_banner(deps, startup_statuses)` reads tool counts and skill counts from `deps.capabilities`, and MCP server count from `deps.config` — no separate health probe.

### Step 1 - Knowledge Sync

```text
if deps.services.knowledge_index is not None and (memory_dir.exists() or library_dir.exists()):
    try:
        mem_count = knowledge_index.sync_dir("memory", memory_dir, kind_filter="memory")
        art_count = knowledge_index.sync_dir("library", library_dir, kind_filter="article")
        frontend.on_status("Knowledge synced ...")
    except Exception:
        knowledge_index.close()
        deps.services.knowledge_index = None
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
frontend.on_status("{len(deps.capabilities.skill_registry)} skill(s) loaded")
```

This is a visibility step only. Skill loading already happened inside `async with agent`, after MCP init. The count reflects wired-available skills (description present, not `disable_model_invocation`) — the same set visible to the model via `skill_registry`.

## 6. Pre-Wakeup Subflows

### Skills Load

Skills load inside `initialize_session_capabilities()` (called inside `async with agent`, after MCP connect), not before `build_agent()`:

```text
initialize_session_capabilities(agent, deps, frontend, mcp_init_ok):
    [if mcp_servers and mcp_init_ok] discover_mcp_tools() → deps.capabilities.mcp_discovery_errors, deps.capabilities.tool_names extended
    skill_commands = _load_skills(deps.config.skills_dir, settings)
        pass 1: scan co_cli/skills/*.md
        pass 2: scan deps.config.skills_dir/*.md
        parse frontmatter, check requires, scan for security issues
    set_skill_commands(skill_commands, deps.capabilities)   ← capabilities.skill_commands + skill_registry
    return SessionCapabilityResult(skill_count=len(deps.capabilities.skill_registry))

completer.words = _build_completer_words(deps.capabilities.skill_commands)   ← extends COMMANDS-only list to COMMANDS + skills
```

`PromptSession` is built before `create_deps()` with a COMMANDS-only completer. After `initialize_session_capabilities()` returns, `_build_completer_words()` updates `completer.words` in-place to include skill names.

`disable_model_invocation: true` skills stay available to the REPL but are hidden from the model-facing `skill_registry`.

Live skill reloading happens after startup in the main loop: before each REPL prompt, `.co-cli/skills/` mtimes are checked, `_load_skills()` reruns when files changed, and `completer.words` is refreshed via `_build_completer_words()`. That post-startup path is covered in [DESIGN-core-loop.md](DESIGN-core-loop.md).

### Knowledge Backend Resolution

`create_deps()` resolves the knowledge backend before bootstrap:

```text
"grep" in config → hard stop, grep only (no index)
otherwise        → try hybrid first (if embedder available), then fts5, then grep
                   (configured "fts5" still attempts hybrid when embedder is reachable)

deps.config.knowledge_search_backend = resolved backend
deps.services.knowledge_index = KnowledgeIndex instance or None
startup_statuses = one-time degradation messages returned from create_deps() and shown before the banner
```

By the time the inline wakeup steps run, the system already knows whether FTS or grep is active.

## 7. Boundary, State Mutations, And Failure Paths

### Welcome Banner Boundary

`display_welcome_banner(deps, startup_statuses)` is called immediately after the three inline wakeup steps complete, still inside the `async with agent` block:

```text
[inline steps 1-3]
display_welcome_banner(deps, startup_statuses)
begin REPL loop
```

The banner marks the boundary between startup and interactive use. All status messages from model check, wakeup steps, and skills loading appear above it. The banner reads version, model, cwd, tool count (`len(deps.capabilities.tool_names)`), skill count (`len(deps.capabilities.skill_registry)`), and MCP server count (`len(deps.config.mcp_servers or {})`) directly from `deps` — no health probe needed. The readiness headline shows `✓ Ready` when `startup_statuses` is empty, or `✓ Ready  (degraded)` when one or more startup fallbacks are active (e.g., hybrid-to-fts5 degradation or reranker unavailability).

### State Mutations Summary

| Field | Set by | Value |
|-------|--------|-------|
| `deps.services.knowledge_index` | Step 1 on error | `None` to disable FTS for the session |
| `deps.session.session_id` | Step 2 (inline restore_session) | Single write: restored or new UUID hex |
| `deps.services.task_runner` | Constructed inside `create_deps()` | `TaskRunner` instance |
| `deps.services.model_registry` | `create_deps()` | `ModelRegistry` built from resolved `CoConfig` |
| `deps.capabilities.tool_names` | Set immediately after `build_agent(config=deps.config, resolved=resolved)`; extended after MCP discovery | Native tool list, then MCP-extended |
| `deps.capabilities.tool_approvals` | Set immediately after `build_agent(config=deps.config, resolved=resolved)` | Tool approval map |
| `deps.capabilities.mcp_discovery_errors` | Set after MCP discovery (`discover_mcp_tools()`); empty dict when no MCP servers | Maps server prefix to error string for servers where `list_tools()` failed; read by `check_runtime()` to report real connectivity |
| `deps.capabilities.skill_registry` | `initialize_session_capabilities()` via `set_skill_commands()` | Non-hidden skill dicts |
| `deps.capabilities.skill_commands` | `initialize_session_capabilities()` via `set_skill_commands()` | Full dict of all loaded skills |
| `completer.words` | Before `create_deps()`: COMMANDS-only; after skills load: updated by `_build_completer_words()` | COMMANDS-only at startup; COMMANDS + skills after skills load |

### Failure Paths

| Condition | Outcome |
|-----------|---------|
| `create_deps()` raises `ValueError` (provider error, missing reasoning model) | `_chat_loop()` catches `ValueError`, prints `"Startup error: …"` and exits with code 1 via `SystemExit(1)` |
| `load_config()` schema validation fails (`ValidationError`) | re-raised as `ValueError` by `load_config()`; caught by `get_settings()`, which prints `"Configuration error: …"` to stderr and raises `SystemExit(1)` — never reaches `_chat_loop()` |
| Knowledge sync raises | Index closed, `knowledge_index = None`, grep fallback, session continues |
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
| `co_cli/bootstrap/_bootstrap.py` | `create_deps()`, `sync_knowledge()`, `restore_session()`, `initialize_session_capabilities()`, `SessionCapabilityResult` — all startup functions and result types live here |
| `co_cli/bootstrap/_check.py` | IO check functions (`check_agent_llm`, `check_reranker_llm`, `check_embedder`, `check_cross_encoder`, `check_mcp_server`, `check_tei`), settings-level entry point `check_settings()` (used by `_render_status.py`), runtime entry point `check_runtime()` / `RuntimeCheck` (used by `/status` tool), data types `CheckResult`, `CheckItem`, `DoctorResult` |
| `co_cli/context/_session.py` | Session helpers: new/load/save/touch/is_fresh/increment_compaction |
| `co_cli/bootstrap/_banner.py` | `display_welcome_banner(deps: CoDeps, startup_statuses: list[str])` — welcome banner |
| `co_cli/commands/_commands.py` | Skill loading helpers |
| `co_cli/deps.py` | `CoDeps` groups and sub-agent isolation |
| `co_cli/knowledge/_index_store.py` | `KnowledgeIndex.sync_dir()` and `close()` |

## 9. See Also

- [DESIGN-system.md](DESIGN-system.md) - system architecture and capability surface
- [DESIGN-core-loop.md](DESIGN-core-loop.md) - main loop and turn state machine
- [DESIGN-tools.md](DESIGN-tools.md) - MCP lifecycle and tool surface
