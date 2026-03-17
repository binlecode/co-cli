# Co CLI — System Bootstrap Design

Canonical startup flow for co-cli. This doc is the sole owner for startup and wakeup behavior, covering the full sequence from settings loading through `display_welcome_banner()`: settings loading, deps initialization (`create_deps()`), model dependency check, skills load, agent creation, MCP init, and the four inline wakeup steps (knowledge sync, session restore, skills report, integration health).

Bootstrap owns sequencing. Shared resource and integration check design does not live here; Step 4 delegates to `check_runtime()` in `co_cli/bootstrap/_check.py`.

```
co_cli.main  (module load)
│
├─ co_cli.display.console  ← import triggers settings load:
│      co_cli.config.settings  ← first access triggers lazy init:
│          co_cli.config._ensure_dirs()  # mkdir ~/.config/co-cli, ~/.local/share/co-cli
│          co_cli.config.load_config()
│              Layer 1: json.loads(~/.config/co-cli/settings.json)
│              Layer 2: json.loads(<cwd>/.co-cli/settings.json)
│                       co_cli.config._deep_merge_settings(base, override)
│              Layer 3: Settings.model_validate(merged)
│                       → fill_from_env model_validator(mode='before')
│                         reads CO_CLI_* env vars, overrides merged dict
│              → Settings instance cached as co_cli.config._settings
│
├─ co_cli.observability._telemetry.SQLiteSpanExporter()          # Needs DATA_DIR created by _ensure_dirs()
├─ opentelemetry.sdk.trace.TracerProvider(resource=...)
└─ pydantic_ai.Agent.instrument_all(InstrumentationSettings(...))

co_cli.main.chat() → asyncio.run(_chat_loop())
│
│ ── PHASE 1: Pre-Agent Setup ──────────────────────────────────────────────
│
├─ co_cli.display.TerminalFrontend()
├─ prompt_toolkit.completion.WordCompleter(["/cmd", ...])        # COMMANDS-only; updated after Phase 2
├─ prompt_toolkit.PromptSession(
│      history=FileHistory(DATA_DIR / "history.txt"),
│      completer=completer,
│  )
│
└─ co_cli.bootstrap._bootstrap.create_deps()
       │
       ├─ co_cli.deps.CoConfig.from_settings(settings)           # bulk copy of Settings scalars
       │     (includes library_dir resolution: settings.library_path or DATA_DIR/"library")
       ├─ co_cli.prompts.personalities._loader.load_soul_critique(personality)
       │
       ├─ dataclasses.replace(_base_config,                      # runtime-resolved fields grafted on
       │      exec_approvals_path, memory_dir, skills_dir,
       │      session_path, tasks_dir,
       │      personality_critique,
       │      knowledge_search_backend=resolved_backend,
       │      mcp_count, mcp_servers,
       │  )
       │
       ├─ co_cli.bootstrap._check.check_llm(config)
       │     "error" → raise ValueError  ← session never starts
       │
       ├─ co_cli.bootstrap._check.check_model_availability(config)
       │     "error" → raise ValueError
       │
       ├─ Knowledge backend init (if backend in "fts5"/"hybrid"):
       │     co_cli.knowledge._index.KnowledgeIndex(config=config)
       │
       ├─ co_cli.tools._background.TaskStorage(config.tasks_dir)
       ├─ co_cli.tools._background.TaskRunner(storage, config.background_max_concurrent, ...)
       │
       ├─ co_cli.tools._shell_backend.ShellBackend()
       ├─ co_cli.deps.CoServices(shell, knowledge_index, task_runner, model_registry)
       │
       ├─ co_cli._model_factory.ModelRegistry.from_config(config) → services.model_registry
       ├─ co_cli.context._history.OpeningContextState()
       ├─ co_cli.context._history.SafetyState()
       ├─ co_cli.deps.CoRuntimeState(opening_ctx_state, safety_state)
       └─ co_cli.deps.CoDeps(services, config, runtime)  ← returned; session field is default-fresh

co_cli.agent.get_agent(config=config)
│
├─ co_cli._model_factory.prepare_provider(provider_name, llm_api_key)  # Gemini env-var injection
├─ co_cli.prompts.assemble_prompt(...)                           # system prompt assembly
│
├─ pydantic_ai.Agent(
│      model=None,                                               # per-call model via ModelRegistry
│      system_prompt=assembled_prompt,
│      deps_type=CoDeps,
│      result_type=str | DeferredToolRequests,
│  )
├─ agent.tool(run_shell_command, requires_approval=False)        # all tools registered here
├─ agent.tool(save_memory), agent.tool(recall_memory), ...
├─ agent.tool(delegate_coder), ...
├─ [if mcp_servers] MCPServerStdio/HTTP added as toolsets
│
└─ returns (agent, tool_names, tool_approvals)

main._chat_loop():
    deps.session.tool_names     = tool_names
    deps.session.tool_approvals = tool_approvals

│ ── PHASE 2: Agent Context (async with agent via AsyncExitStack) ──────────
│
├─ contextlib.AsyncExitStack.enter_async_context(agent)
│     ← starts MCP server subprocesses, connects stdio/HTTP transports
│     ← Exception → console.print error, raise SystemExit(1)
│
├─ [if mcp_servers]
│     co_cli.agent.discover_mcp_tools(agent, tool_names)
│         → deps.session.tool_names = native + MCP tools
│
├─ co_cli.commands._commands._load_skills(skills_dir, settings)
│     pass 1: scan co_cli/skills/*.md    (package defaults)
│     pass 2: scan .co-cli/skills/*.md  (project-local; wins on collision)
│
├─ co_cli.commands._commands.set_skill_commands(skill_commands, deps.session)
│     → SKILL_COMMANDS dict updated, deps.session.skill_registry populated
├─ completer.words = _build_completer_words()                    # COMMANDS + skills
│
│ ── INLINE WAKEUP (sequential) ────────────────────────────────────────────
│
├─ Step 1  co_cli.bootstrap._bootstrap.sync_knowledge(deps, frontend)
│     co_cli.knowledge._index.KnowledgeIndex.sync_dir("memory", memory_dir, kind_filter="memory")
│     co_cli.knowledge._index.KnowledgeIndex.sync_dir("library", library_dir, kind_filter="article")
│     on fail → knowledge_index.close(); deps.services.knowledge_index = None
│
├─ Step 2  co_cli.bootstrap._bootstrap.restore_session(deps, frontend, session_path, ttl)
│     co_cli.context._session.load_session(session_path)
│     co_cli.context._session.is_fresh(session_data, ttl)?
│         yes → deps.config.session_id = session_data["session_id"]
│         no  → co_cli.context._session.new_session()
│               deps.config.session_id = new UUID
│               co_cli.context._session.save_session(session_path, session_data)
│
├─ Step 3  frontend.on_status(f"{len(deps.session.skill_registry)} skill(s) loaded")
│
├─ Step 4  co_cli.bootstrap._bootstrap.check_integration_health(deps, frontend)
│     co_cli.bootstrap._check.check_runtime(deps) → RuntimeCheck
│     runtime_check.summary_lines() → each line → frontend.on_status(line)
│     on fail → fallback RuntimeCheck(capabilities={}, status={}, findings=[], fallbacks=[])
│
└─ co_cli.bootstrap._banner.display_welcome_banner(runtime_check, deps.config)
   ▲
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
- `ollama-openai` / `ollama-native`: all five roles populated with hardcoded defaults in `config.py`

Merge order is provider defaults for missing roles, then explicit config values, then env var overrides.

The `reasoning` role is validated post-construction. Missing or empty raises `ValueError` at startup.

Singleton access pattern:

```text
from co_cli.config import settings
```

First access resolves and caches `_settings`; later accesses reuse the singleton. Startup mutations such as `settings.theme = theme` modify that singleton in place.

### Deps Initialization (`create_deps()` In `bootstrap/_bootstrap.py`)

`create_deps()` (in `bootstrap/_bootstrap.py`) converts the `Settings` singleton into `CoServices`, `CoConfig`, `CoSessionState`, and `CoRuntimeState`, then assembles `CoDeps`. It calls `check_llm` and `check_model_availability` (from `bootstrap/_check.py`) directly as fail-fast gates: two sync HTTP calls to Ollama (`/api/tags`, timeout=5 each) when the configured provider is Ollama. Hard errors raise `ValueError` immediately; the session never starts.

```text
create_deps():
    _base_config = CoConfig.from_settings(settings)
    # from_settings() resolves library_dir, session_ttl_minutes, and background task settings
    personality_critique = load_soul_critique(_base_config.personality) if _base_config.personality else ""

    config = dataclasses.replace(
        _base_config,  # bulk copy; obsidian_vault_path and Settings-backed fields copied here
        # session_id left at default "" — single write in inline restore_session step
        exec_approvals_path=Path.cwd() / ".co-cli/exec-approvals.json",
        memory_dir=Path.cwd() / ".co-cli/memory",
        skills_dir=Path.cwd() / ".co-cli/skills",
        session_path=Path.cwd() / ".co-cli/session.json",
        tasks_dir=Path.cwd() / ".co-cli/tasks",
        personality_critique=personality_critique,
        knowledge_search_backend=settings.knowledge_search_backend,
        mcp_count=len(settings.mcp_servers),
    )

    result = check_llm(config)
    if result.status == "error":
        raise ValueError(result.detail)

    result = check_model_availability(config)
    if result.status == "error":
        raise ValueError(result.detail)

    knowledge_index = KnowledgeIndex(config=config) if backend in ("fts5", "hybrid") else None
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
    runtime = CoRuntimeState(opening_ctx_state=OpeningContextState(), safety_state=SafetyState())
    return CoDeps(services=services, config=config, runtime=runtime)
```

Key transformation:
- `settings.knowledge_search_backend` is the configured backend
- `deps.config.knowledge_search_backend` is the resolved backend after degradation
- `CoConfig.from_settings()` copies pure config fields only; runtime-resolved and session-scoped fields are applied afterward via `dataclasses.replace()`

### `CoDeps` Group Semantics

| Group | Type | Lifetime | Sub-agent inheritance |
|-------|------|----------|-----------------------|
| `services` | `CoServices` | Session | Shared by reference |
| `config` | `CoConfig` | Session | Shared by reference |
| `session` | `CoSessionState` | Session, fresh for sub-agents | Reset for sub-agents |
| `runtime` | `CoRuntimeState` | Orchestration-layer transient state | Reset for sub-agents |

The session group holds tool-visible mutable state such as approvals, todos, and skill grants. The runtime group holds orchestration-layer transient state such as compaction, usage, and safety state.

## 2. Provider And Model Checks

`check_llm` and `check_model_availability` (from `bootstrap/_check.py`) run inside `create_deps()` as fail-fast gates. Error status raises `ValueError` immediately — session never starts. Any other status continues; `ModelRegistry` is built immediately after both checks pass.

| Condition | Behavior |
|-----------|----------|
| Gemini provider with missing key | `ValueError`; session never starts |
| Reasoning chain fully unavailable under Ollama | `ValueError`; session never starts |
| Ollama unreachable | Continues; model registry built with configured chains |
| One or more chains advanced | Continues; pruned `role_models` written to config |

## 3. Entry Conditions

The inline wakeup steps run once per `chat_loop()` startup after deps initialization:

- `create_deps()` returned without raising (provider/model probes passed)
- `PromptSession` is constructed before `create_deps()` with a COMMANDS-only completer; `completer.words` is updated after Phase 2 skills load using `_build_completer_words()`
- `get_agent()` has returned an agent instance; `deps.session.tool_names` and `tool_approvals` are set immediately after
- `async with agent` has been entered; if MCP init fails, a clean error is printed and the session exits
- skills are loaded inside `async with agent` after MCP init (`_load_skills`), then `set_skill_commands()` populates `SKILL_COMMANDS` and `skill_registry`, and `completer.words` is updated immediately after

The four inline steps run inside the `async with agent` block after MCP init and skills load have completed.

## 4. Full Startup Sequence

```text
chat_loop():
    # Phase 1
    frontend = TerminalFrontend()

    completer = WordCompleter([f"/{name}" for name in COMMANDS], sentence=True)  ← COMMANDS-only
    session = PromptSession(history=..., completer=completer, ...)
    _skills_watch_snapshot: dict[str, float] = {}

    _prune_stale_approvals(exec-approvals.json, max_age_days=90)
    deps = create_deps()   ← fail-fast on provider/model error; TaskRunner/TaskStorage constructed inside

    agent, tool_names, tool_approvals = get_agent(config=deps.config)
    deps.session.tool_names = tool_names        ← set immediately after get_agent
    deps.session.tool_approvals = tool_approvals

    # Phase 2 (inside async with agent)
    stack = AsyncExitStack()
    message_history = []
    last_interrupt_time = 0.0
    bg_compaction_task = None
    try:
        try:
            await stack.enter_async_context(agent)
        except Exception as e:
            print clean error message
            raise SystemExit(1)              ← finally cleanup runs; session exits with code 1

        if mcp_servers:
            tool_names = await discover_mcp_tools(agent, tool_names)
            deps.session.tool_names = tool_names

        skill_commands = _load_skills(deps.config.skills_dir, settings=settings)
        set_skill_commands(skill_commands, deps.session)   ← SKILL_COMMANDS + skill_registry
        _skills_watch_snapshot = _skills_snapshot(deps.config.skills_dir)
        completer.words = _build_completer_words()   ← updated to COMMANDS + skills

        [inline wakeup steps 1–4]
        display_welcome_banner(runtime_check)
        begin REPL loop
    finally:
        cleanup
```

### MCP Init Failure

If `stack.enter_async_context(agent)` raises:

```text
except Exception as e:
    console.print("[error]MCP server failed to connect: {e}[/error]")
    console.print("[dim]Fix MCP server config in settings.json or remove mcp_servers.[/dim]")
    raise SystemExit(1)
```

`SystemExit` is a `BaseException` — not caught by `except Exception` in the REPL inner loop. The `finally` block (task runner shutdown, stack close, shell cleanup) always runs. Exit code 1 signals failure to the shell. MCP failure is a hard error; no silent rebuild without MCP.

## 5. Inline Wakeup Steps

Four sequential steps run inline in `chat_loop()` after MCP init, reporting status via `frontend.on_status()`.

```text
inline wakeup (in chat_loop()):
    Step 1: sync_knowledge
    Step 2: restore_session  →  session_data local variable
    Step 3: skills_loaded_report  (reads len(deps.session.skill_registry) directly)
    Step 4: integration_health_sweep  →  runtime_check local variable
```

`session_data` stays in `chat_loop()` local state for turn-by-turn persistence. `runtime_check` is captured from Step 4 and passed to `display_welcome_banner(runtime_check)`.

The `try/except` around Step 4 is retained for graceful degradation: if `check_runtime()` raises unexpectedly, a warning line is emitted and a fallback `RuntimeCheck` (empty, no findings) is used so `display_welcome_banner()` still runs.

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
    deps.config.session_id = session_data["session_id"]
    frontend.on_status("Session restored ...")
else:
    session_data = new_session()
    deps.config.session_id = session_data["session_id"]
    save_session(deps.config.session_path, session_data)
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
frontend.on_status("{len(deps.session.skill_registry)} skill(s) loaded")
```

This is a visibility step only. Skill loading already happened in Phase 2 after MCP init. The count reflects wired-available skills (description present, not `disable_model_invocation`) — the same set visible to the model via `skill_registry`.

### Step 4 - Integration Health Sweep

```text
try:
    result = check_runtime(deps)
    for line in result.summary_lines():
        frontend.on_status(line)
    span.set_attribute("has_errors", bool(result.findings))
    span.set_attribute("has_warnings", bool(result.fallbacks))
except Exception as e:
    frontend.on_status("integration health check failed ...")
```

Bootstrap is a caller of the probe layer, not the owner of resource-check semantics. Step 4 exists to sequence the call and render the result during startup. The probe layer owns:
- which checks are included
- which are static-only vs runtime-aware
- result/status semantics
- shared behavior across bootstrap, runtime, and status surfaces

Bootstrap-specific guarantees:
- the sweep is non-blocking for normal degraded states
- unexpected Doctor exceptions are swallowed and rendered as a warning line
- startup continues after Step 4 whether the sweep reports warnings/errors or raises unexpectedly

For the integration check logic and `RuntimeCheck` contract, see `co_cli/bootstrap/_check.py`.

## 6. Pre-Wakeup Subflows

### Skills Load

Skills load in Phase 2, inside `async with agent` after MCP init, not before `get_agent()`:

```text
skill_commands = _load_skills(deps.config.skills_dir, settings)
    pass 1: scan co_cli/skills/*.md
    pass 2: scan deps.config.skills_dir/*.md
    parse frontmatter, check requires, scan for security issues

set_skill_commands(skill_commands, deps.session)   ← SKILL_COMMANDS.clear/update + skill_registry
_skills_watch_snapshot = _skills_snapshot(deps.config.skills_dir)
completer.words = _build_completer_words()   ← extends COMMANDS-only list to COMMANDS + skills
```

`PromptSession` is built in Phase 1 with a COMMANDS-only completer. After Phase 2 skills load, `_build_completer_words()` updates `completer.words` in-place to include skill names — the same pattern used during live reload.

`disable_model_invocation: true` skills stay available to the REPL but are hidden from the model-facing `skill_registry`.

Live skill reloading happens after startup in the main loop: before each REPL prompt, `.co-cli/skills/` mtimes are checked, `_load_skills()` reruns when files changed, and `completer.words` is refreshed via `_build_completer_words()`. That post-startup path is covered in [DESIGN-core-loop.md](DESIGN-core-loop.md).

### Knowledge Backend Resolution

`create_deps()` resolves the knowledge backend before bootstrap:

```text
configured "hybrid" -> try hybrid -> fallback to fts5 -> fallback to grep
configured "fts5" -> try fts5 -> fallback to grep
configured "grep" -> use grep

deps.config.knowledge_search_backend = resolved backend
deps.services.knowledge_index = KnowledgeIndex instance or None
```

By the time the inline wakeup steps run, the system already knows whether FTS or grep is active.

## 7. Boundary, State Mutations, And Failure Paths

### Welcome Banner Boundary

`display_welcome_banner(runtime_check)` is called immediately after the four inline wakeup steps complete, still inside the `async with agent` block:

```text
[inline steps 1-4]
runtime_check = result of Step 4 (or empty fallback if Step 4 raises)
display_welcome_banner(runtime_check)
begin REPL loop
```

The banner marks the boundary between startup and interactive use. All status messages from model check, wakeup steps, and skills loading appear above it. The banner reads version, model, and cwd inline; `runtime_check.status["tool_count"]` provides the tool count; findings and fallbacks drive the readiness verdict line.

### State Mutations Summary

| Field | Set by | Value |
|-------|--------|-------|
| `deps.services.knowledge_index` | Step 1 on error | `None` to disable FTS for the session |
| `deps.config.session_id` | Step 2 (inline restore_session) | Single write: restored or new UUID hex |
| `deps.config.role_models` | `create_deps()` in `main.py` | Pruned chain if Ollama models are missing |
| `deps.services.task_runner` | Constructed inside `create_deps()` | `TaskRunner` instance |
| `deps.services.model_registry` | `create_deps()` | `ModelRegistry` built from resolved `CoConfig` |
| `deps.session.tool_names` | Set immediately after `get_agent(config=deps.config)`; extended after MCP discovery | Native tool list, then MCP-extended |
| `deps.session.tool_approvals` | Set immediately after `get_agent(config=deps.config)` | Tool approval map |
| `deps.session.skill_registry` | Phase 2 in `main.py` — `set_skill_commands()` (after MCP init) | Non-hidden skill dicts |
| `SKILL_COMMANDS` | Phase 2 in `main.py` — `set_skill_commands()` (after MCP init) | Module-level dict of all loaded skills |
| `completer.words` | Phase 1: COMMANDS-only; Phase 2: updated by `_build_completer_words()` after `set_skill_commands()` | COMMANDS-only at startup; COMMANDS + skills after Phase 2 skills load |

### Failure Paths

| Condition | Outcome |
|-----------|---------|
| Knowledge sync raises | Index closed, `knowledge_index = None`, grep fallback, session continues |
| Session file missing or unreadable | New session created |
| Session TTL expired | New session created |
| MCP server connection fails | Clean error printed, `SystemExit(1)` raised, cleanup runs, session exits |
| One skill file fails to load | File skipped with warning; other skills still load |
| Integration sweep raises unexpectedly | Warning line emitted; fallback `RuntimeCheck` used; session continues |

### Recovery And Fallback

- Knowledge index unavailable: search tools fall back to grep-based search
- MCP unavailable: session continues with native tools only
- Session corruption: delete `.co-cli/session.json` to force a fresh session

## 8. Owning Code

| File | Role |
|------|------|
| `co_cli/main.py` | `_chat_loop()` startup assembly |
| `co_cli/bootstrap/_bootstrap.py` | `create_deps()`, `sync_knowledge()`, `restore_session()`, `check_integration_health()` — all startup functions live here |
| `co_cli/bootstrap/_check.py` | `check_runtime(deps)` — integration health aggregator invoked by Step 4 |
| `co_cli/context/_session.py` | Session helpers: new/load/save/touch/is_fresh/increment_compaction |
| `co_cli/bootstrap/_render_status.py` | `display_welcome_banner(runtime_check: RuntimeCheck)` — welcome banner |
| `co_cli/commands/_commands.py` | Skill loading helpers |
| `co_cli/deps.py` | `CoDeps` groups and sub-agent isolation |
| `co_cli/knowledge/_index.py` | `KnowledgeIndex.sync_dir()` and `close()` |

## 9. See Also

- [DESIGN-system.md](DESIGN-system.md) - system architecture and capability surface
- [DESIGN-core-loop.md](DESIGN-core-loop.md) - main loop and turn state machine
- [DESIGN-tools.md](DESIGN-tools.md) - MCP lifecycle and tool surface
