# TODO: Probe Settings Isolation

**Task type: refactor** — structural cleanup; no behavior change.
Regression surface: `bootstrap/_check.py`, `bootstrap/_render_status.py`, `co_cli/deps.py`, `bootstrap/_bootstrap.py`, `co_cli/main.py`, `co_cli/commands/_commands.py`.

---

## Context

**Architecture rule being enforced:**

`settings` is a module-level singleton that belongs exclusively to the bootstrap translation boundary (`CoConfig.from_settings(settings)` + `dataclasses.replace()` inside `create_deps()`). After that boundary, every component reads configuration from `CoConfig` — either via `ctx.deps.config` (in-agent) or a directly-passed `CoConfig` instance (pre-agent). No component-level logic imports or reads `settings` after the bootstrap boundary.

**Current violations (confirmed by source scan):**

`co_cli/bootstrap/_check.py` has two violations:

1. `run_checks(deps: CoDeps | None = None)` — when `deps is None`, imports and reads `settings.*` directly (google creds, obsidian vault, brave key, mcp_servers). This is the pre-agent `co config` path; `CoDeps` does not exist yet at this callsite.
2. `check_runtime(deps: CoDeps)` — receives full `CoDeps` but still imports `settings.mcp_servers` (line 335 and 357) because `CoConfig` did not previously carry `mcp_servers`.

**Partial work already landed (in-session, not yet tested as a unit):**

- `co_cli/deps.py`: `mcp_servers: dict[str, MCPServerConfig]` field added to `CoConfig`; `from_settings()` populates it.
- `co_cli/bootstrap/_bootstrap.py`: `mcp_servers=dict(settings.mcp_servers)` added to `dataclasses.replace()` call in `create_deps()`.

These two changes unblock TASK-1 and TASK-2 below. They must be verified as part of this delivery.

**Callsite map (as-is):**

```
main.py::status()                              ← DELETED in this delivery
    └─▶ get_status()                           ← _render_status.py; reads settings directly
            └─▶ run_checks()                   ← _check.py; reads settings directly

commands/_commands.py::_cmd_status (no args)
    └─▶ get_status(tool_count=len(ctx.tool_names))  ← ctx.deps.config available but not passed
    └─▶ check_security()

bootstrap/_render_status.py::render_status_table()
    └─▶ settings.obsidian_vault_path           ← direct settings leak in display function

main.py::chat_loop()
    └─▶ (via bootstrap) check_runtime(deps)    ← _check.py; reads settings.mcp_servers
    └─▶ display_welcome_banner(runtime_check)  ← _render_status.py; reads settings directly (TASK-6)

tools/capabilities.py
    └─▶ check_runtime(deps)                    ← _check.py; reads settings.mcp_servers
```

**Callsite map (target):**

```
main.py::config()  [new Typer command]
    └─▶ get_status(CoConfig.from_settings(settings))  ← inline translation; no helper

main.py::status()  → DELETED

commands/_commands.py::_cmd_status (no args)
    └─▶ get_status(ctx.deps.config, tool_count=len(ctx.tool_names))  ← uses live deps

main.py::chat_loop()
    └─▶ check_runtime(deps)              ← reads deps.config.mcp_servers only; no settings import

tools/capabilities.py
    └─▶ check_runtime(deps)              ← unchanged callsite; internals fixed
```

---

## Problem & Outcome

**Problem:** `_check.py` (the probe/health-check module) imports and reads `settings` directly inside function bodies. This creates two data paths for the same logical values — probes can produce different results depending on whether they read from `CoConfig` (which may differ from settings after backend degradation or model-chain pruning) or from the raw settings singleton. It also bypasses sub-agent isolation.

**Outcome:**
- `_check.py` imports `settings` nowhere — zero references
- `run_checks()` takes `config: CoConfig` as its sole config source
- `check_runtime(deps)` reads `deps.config.mcp_servers`; no `settings` import
- `_render_status.py::get_status()` accepts `CoConfig` as a parameter
- `main.py::config()` (new) calls `get_status(CoConfig.from_settings(settings))` inline
- `main.py::status()` (old Typer command) deleted — `co status` requires an active session; use `/status` inside `co chat`
- `_commands.py::_cmd_status` calls `get_status(ctx.deps.config, ...)` — in-agent path uses live deps
- `CoConfig` carries `mcp_servers` (already landed; verify here)
- All paths read the same `CoConfig` instance — one source of truth for probe logic

---

## Scope

**In scope:**
- `_check.py`: change `run_checks` signature; remove all `settings` imports from both `run_checks` and `check_runtime`
- `_render_status.py`: change `get_status` signature to accept `CoConfig`; call `run_checks(config)` with it; fix `render_status_table` settings leak
- `_commands.py`: update `_cmd_status` callsite to pass `ctx.deps.config` to `get_status`
- `main.py`: delete `status()` Typer command; add `config()` Typer command calling `get_status(CoConfig.from_settings(settings))` inline
- `deps.py` + `_bootstrap.py`: verify the already-landed `mcp_servers` changes are correct and pass tests

**In scope (continued):**
- `bootstrap/_banner.py` (new file): `display_welcome_banner(runtime_check, config)` moves here from `_render_status.py`
- `deps.py`: add `theme: str` to `CoConfig`; populate in `from_settings()`
- `_render_status.py`: remove `display_welcome_banner` entirely; after TASK-3 + TASK-6 the module-level `settings` import is removed completely
- `main.py`: update import of `display_welcome_banner` to `bootstrap._banner`

**Out of scope:**
- `get_status()` fields sourced from non-`CoConfig` values: `version` (tomllib), `git_branch` (subprocess), `db_size` (filesystem) — these are not config and should stay as-is.
- Any change to `DoctorResult`, `RuntimeCheck`, `CheckResult`, `CheckItem`, `StatusInfo` data classes.
- `check_security()` in `_render_status.py` — security posture checks; separate concern.

---

## High-Level Design

### Settings Translation Boundary (target state)

```
settings singleton  (config.py)
    │
    │  [ONLY HERE] CoConfig.from_settings(settings)
    │              + dataclasses.replace() for runtime-resolved fields
    ▼
CoConfig  (complete, frozen after create_deps returns)
    │
    ├── in-agent path:  CoDeps(config=config, ...)
    │       └── check_runtime(deps)  →  deps.config.*
    │       └── get_status(ctx.deps.config, ...)  →  config.*
    │
    └── pre-agent path:  CoConfig instance passed explicitly
            └── run_checks(config)  →  config.*
            └── get_status(config, ...)  →  config.*
```

After this delivery, `settings` is read in exactly two places:
1. `CoConfig.from_settings(settings)` in `deps.py` (the translation)
2. `dataclasses.replace(...)` in `bootstrap/_bootstrap.py` (the runtime overlay)

Nowhere else.

### `run_checks` signature change

Before:
```python
def run_checks(deps: CoDeps | None = None) -> DoctorResult:
    from co_cli.config import settings, GOOGLE_TOKEN_PATH, ADC_PATH
    creds = settings.google_credentials_path
    ...
```

After:
```python
def run_checks(config: CoConfig) -> DoctorResult:
    from co_cli.config import GOOGLE_TOKEN_PATH, ADC_PATH
    creds = config.google_credentials_path
    ...
```

`deps` parameter removed entirely. The `deps is not None` branch (knowledge + skills checks) moves to a separate call or is dropped — callers that need runtime checks use `check_runtime(deps)` instead. See TASK-1 notes on the `deps is not None` branch.

### `check_runtime` fix

Before:
```python
def check_runtime(deps: CoDeps) -> RuntimeCheck:
    from co_cli.config import settings, ADC_PATH, GOOGLE_TOKEN_PATH
    ...
    for name, cfg in (settings.mcp_servers or {}).items():
```

After:
```python
def check_runtime(deps: CoDeps) -> RuntimeCheck:
    from co_cli.config import ADC_PATH, GOOGLE_TOKEN_PATH
    ...
    for name, cfg in (deps.config.mcp_servers or {}).items():
```

`settings` import removed; one-line swap for the mcp_servers loop.

### `get_status` signature change

Before:
```python
def get_status(tool_count: int = 0) -> StatusInfo:
    ...
    provider = settings.llm_provider.lower()
    ...
    doctor = run_checks()
```

After:
```python
def get_status(config: CoConfig, tool_count: int = 0) -> StatusInfo:
    ...
    provider = config.llm_provider.lower()
    ...
    doctor = run_checks(config)
```

`config` parameter added as first positional arg. All `settings.*` reads inside `get_status()` and `render_status_table()` replaced with `config.*` / `info.*`. The module-level `settings` import is removed entirely after TASK-6.

### `co config` Typer command (replaces deleted `co status`)

`co status` required an active agent session to access live `deps`. Calling it from the terminal violated the command → agent → tools → deps architecture. It is deleted.

`co config` is the new out-of-agent pre-flight configuration check. Translation is one line, inlined:

```python
# main.py
@app.command()
def config():
    """Show system configuration and integration health (pre-agent check)."""
    from co_cli.deps import CoConfig
    sys_status = get_status(CoConfig.from_settings(settings))
    console.print(render_status_table(sys_status))
    findings = check_security()
    render_security_findings(findings)
```

No bootstrap helper needed. `settings` is already imported at module level in `main.py`.

In-agent status remains `/status` REPL slash command (routes through `ctx.deps`).

---

## Implementation Plan

### TASK-1: Fix `run_checks` signature — remove `settings` dependency

**files:**
- `co_cli/bootstrap/_check.py`

**done_when:**
`grep -n "from co_cli.config import settings" co_cli/bootstrap/_check.py` returns no match inside `run_checks`.

**prerequisites:** none

**Steps:**

1. Change signature: `run_checks(deps: "CoDeps | None" = None)` → `run_checks(config: "CoConfig")`.
   Add `CoConfig` to the `TYPE_CHECKING` block if not already present.

2. Inside `run_checks`, replace the lazy import:
   ```
   from co_cli.config import settings, GOOGLE_TOKEN_PATH, ADC_PATH
   ```
   with:
   ```
   from co_cli.config import GOOGLE_TOKEN_PATH, ADC_PATH
   ```

3. Replace all `settings.*` reads with `config.*`:
   - `settings.google_credentials_path` → `config.google_credentials_path`
   - `settings.obsidian_vault_path` → `str(config.obsidian_vault_path) if config.obsidian_vault_path else None`
     (note: `CoConfig.obsidian_vault_path` is `Path | None`; `settings.obsidian_vault_path` is `str | None` — caller must adapt or helper must handle Path)
   - `settings.brave_search_api_key` → `config.brave_search_api_key`
   - `settings.mcp_servers` → `config.mcp_servers`

4. The `if deps is not None:` branch at the bottom of `run_checks` (knowledge + skills checks):
   **Remove it entirely.** `run_checks(config)` is the settings-level health check; it has no runtime services. Callers that need knowledge and skills checks use `check_runtime(deps)` which already covers those. The `deps` parameter was the only thing feeding this branch — removing it removes the branch cleanly.

5. Update module docstring: remove the `run_checks(deps)` caller entry; update the `Callers` section to reflect `run_checks(config: CoConfig)` called from `bootstrap/_render_status.py`.

**Note:** `_render_status.py` (the only callsite of `run_checks`) is updated in TASK-3.

---

### TASK-2: Fix `check_runtime` — use `deps.config.mcp_servers`

**files:**
- `co_cli/bootstrap/_check.py`

**done_when:**
`grep -n "from co_cli.config import settings" co_cli/bootstrap/_check.py` returns no match inside `check_runtime`.

**prerequisites:** none (independent of TASK-1; both touch `_check.py` — batch reads, sequential edits)

**Steps:**

1. Inside `check_runtime`, change the lazy import from:
   ```
   from co_cli.config import settings, ADC_PATH, GOOGLE_TOKEN_PATH
   ```
   to:
   ```
   from co_cli.config import ADC_PATH, GOOGLE_TOKEN_PATH
   ```

2. Replace the MCP server loop:
   ```
   for name, cfg in (settings.mcp_servers or {}).items():
   ```
   with:
   ```
   for name, cfg in (deps.config.mcp_servers or {}).items():
   ```
   This is the only `settings.*` read in `check_runtime`; removing it eliminates the import.

3. Verify `deps.config.mcp_servers` is populated: `CoConfig` now carries `mcp_servers` (landed in the current session). Confirm field is present before editing.

---

### TASK-3: Fix `get_status` and `render_status_table` — accept `CoConfig`, eliminate settings reads

**files:**
- `co_cli/bootstrap/_render_status.py`
- `co_cli/commands/_commands.py`

**done_when:**
- `grep -c "run_checks(config)" co_cli/bootstrap/_render_status.py` returns `1`
- `grep -n "settings\." co_cli/bootstrap/_render_status.py` returns only lines inside `display_welcome_banner` (display function — handled in TASK-6) and the module-level import line
- `grep -n "settings\." co_cli/commands/_commands.py` returns no match inside `_cmd_status` scope

**prerequisites:** [TASK-1]

**Steps:**

1. Add `CoConfig` to imports in `_render_status.py`:
   ```python
   from co_cli.deps import CoConfig
   ```

2. Change `get_status` signature:
   ```python
   def get_status(config: CoConfig, tool_count: int = 0) -> StatusInfo:
   ```

3. Replace `settings.*` reads inside `get_status` body with `config.*`:
   - `settings.llm_provider` → `config.llm_provider`
   - `settings.role_models` → `config.role_models`
   - `settings.gemini_api_key` → `config.gemini_api_key`
   - `settings.ollama_host` → `config.ollama_host`
   - `settings.obsidian_vault_path` → `str(config.obsidian_vault_path) if config.obsidian_vault_path else None`
   - `settings.mcp_servers` (implicit via `run_checks`) → covered by passing config to `run_checks`

4. Change the `run_checks()` callsite:
   ```python
   doctor = run_checks()
   ```
   →
   ```python
   doctor = run_checks(config)
   ```

5. Fix `render_status_table()` settings leak: verify `StatusInfo` has `obsidian_vault_path` field.
   - If yes: replace `settings.obsidian_vault_path` with `info.obsidian_vault_path` — `StatusInfo` already carries this value from `get_status`; the render function must not bypass it.
   - If no: change `render_status_table(info: StatusInfo)` → `render_status_table(info: StatusInfo, config: CoConfig)` and use `config.obsidian_vault_path`; update all callsites accordingly.

6. Do not touch the `settings` import line yet — `display_welcome_banner` still reads `settings.*` and lives in this file. That import will be removed in TASK-6 after the banner is extracted.

7. In `co_cli/commands/_commands.py`, fix the `_cmd_status` callsite:
   ```python
   info = get_status(tool_count=len(ctx.tool_names))
   ```
   →
   ```python
   info = get_status(ctx.deps.config, tool_count=len(ctx.tool_names))
   ```
   `ctx.deps.config` is available in `CommandContext`. No import changes needed (the actual variable name may differ — adapt to the real code).

---

### TASK-4: Delete `status()` Typer command; add `config()` Typer command

**files:**
- `co_cli/main.py`

**done_when:**
- `grep -c "^def config" co_cli/main.py` returns `1`
- `grep -n "^def status\b" co_cli/main.py` returns no match

**prerequisites:** [TASK-3]

**Steps:**

1. In `main.py`, delete the `@app.command() def status():` block entirely (current lines 282-288).

2. Add a new `@app.command() def config():` block after `chat()`:
   ```python
   @app.command()
   def config():
       """Show system configuration and integration health (pre-agent check)."""
       from co_cli.deps import CoConfig
       sys_status = get_status(CoConfig.from_settings(settings))
       console.print(render_status_table(sys_status))
       findings = check_security()
       render_security_findings(findings)
   ```
   `settings` is already imported at module level in `main.py`. `CoConfig.from_settings(settings)` is the inline translation boundary — no helper needed.

3. No change to `main.py` top-level imports needed: `get_status`, `render_status_table`, `check_security`, `render_security_findings` are already imported from `_render_status`.

4. Audit `main.py` for any `settings.*` reads outside the bootstrap/chat-loop path. Document in task done note.

**Note:** `co status` as a Typer CLI command is deleted permanently. In-agent status is `/status` REPL slash command (already exists in `_commands.py`, routes through `ctx.deps.config`). `co status` from the terminal bypassed the agent — architecturally wrong.

---

### TASK-5: Verify `CoConfig.mcp_servers` — confirm partial work is correct

**files:**
- `co_cli/deps.py`
- `co_cli/bootstrap/_bootstrap.py`

**done_when:**
- `grep -c "mcp_servers" co_cli/deps.py` returns `≥ 2` (field definition + `from_settings` population)
- `grep -c "mcp_servers" co_cli/bootstrap/_bootstrap.py` returns `1` (the `dataclasses.replace` line)
- `uv run pytest tests/test_bootstrap.py tests/test_capabilities.py -v` passes

**prerequisites:** none (verify existing work; no new edits expected)

**Steps:**

1. Read `co_cli/deps.py` and confirm:
   - `mcp_servers: dict[str, "MCPServerConfig"]` field exists in `CoConfig`
   - `MCPServerConfig` is imported from `co_cli.config` at the top of `deps.py`
   - `from_settings()` populates `mcp_servers=dict(s.mcp_servers) if s.mcp_servers else {}`

2. Read `co_cli/bootstrap/_bootstrap.py` and confirm `dataclasses.replace(...)` includes:
   ```python
   mcp_servers=dict(settings.mcp_servers) if settings.mcp_servers else {},
   ```

3. Run targeted tests. If they fail, fix `deps.py` / `_bootstrap.py` before proceeding to TASK-1/2/3/4.

---

### TASK-6: Extract `display_welcome_banner` → `bootstrap/_banner.py`; add `theme` to `CoConfig`

**files:**
- `co_cli/bootstrap/_banner.py` (new)
- `co_cli/bootstrap/_render_status.py`
- `co_cli/deps.py`
- `co_cli/main.py`

**done_when:**
- `grep -r "display_welcome_banner" co_cli/bootstrap/_render_status.py` returns no matches
- `grep -c "def display_welcome_banner" co_cli/bootstrap/_banner.py` returns `1`
- `grep -n "settings" co_cli/bootstrap/_render_status.py` returns no matches (module is settings-free)
- `grep -c "theme" co_cli/deps.py` returns `≥ 1`

**prerequisites:** [TASK-3, TASK-4]

**Why `_banner.py` and not `_bootstrap.py`:**
`display_welcome_banner` is the final step of the startup sequence but it is purely display logic — it takes data, renders a Rich Panel, writes to console. It owns no state, no side effects, no resource lifecycle. `_bootstrap.py` owns startup steps with resource and state implications (`create_deps`, `sync_knowledge`, `restore_session`, `check_integration_health`). Keeping the banner separate avoids polluting `_bootstrap.py` with Rich rendering imports and keeps the module focused.

**Steps:**

1. In `co_cli/deps.py`: add `theme: str = "light"` to `CoConfig`. In `from_settings()`, add `theme=s.theme`.

2. Create `co_cli/bootstrap/_banner.py`:
   - Move `display_welcome_banner` body from `_render_status.py` verbatim
   - Change signature: `display_welcome_banner(runtime_check: RuntimeCheck, config: CoConfig) -> None`
   - Replace `settings.theme` → `config.theme`
   - Replace `settings.role_models` → `config.role_models`
   - Replace `settings.llm_provider` → `config.llm_provider`
   - Add necessary imports: `RuntimeCheck` from `co_cli.bootstrap._check`, `CoConfig` from `co_cli.deps`, `console` from `co_cli.display`
   - No `settings` import in this file
   - Also move `_ASCII_ART` dict and the duplicate `_PYPROJECT` assignment if they are only used by `display_welcome_banner`; leave `_PYPROJECT` in `_render_status.py` if `get_status` also uses it

3. In `co_cli/bootstrap/_render_status.py`:
   - Delete `display_welcome_banner` function body
   - Delete `_ASCII_ART` dict (if moved to `_banner.py`)
   - Remove `settings` from the module-level import line — after TASK-3 removed it from `get_status` and this task removes it from `display_welcome_banner`, no `settings.*` reads remain
   - Final import line becomes: `from co_cli.config import DATA_DIR, LOGS_DB, project_config_path, CONFIG_DIR`

4. In `co_cli/main.py`:
   - Remove `display_welcome_banner` from the `_render_status` import
   - Add: `from co_cli.bootstrap._banner import display_welcome_banner`
   - Update callsite: `display_welcome_banner(runtime_check)` → `display_welcome_banner(runtime_check, deps.config)`

5. In `co_cli/bootstrap/_render_status.py`, confirm `RuntimeCheck` import is still needed (used by `display_welcome_banner` type hint if kept as re-export, or remove it if `_render_status.py` no longer references `RuntimeCheck` at all).

---

## Testing

- After TASK-5: `uv run pytest tests/test_capabilities.py tests/test_bootstrap.py -v` (verify partial work)
- After TASK-1 + TASK-2: `uv run pytest tests/test_capabilities.py tests/test_bootstrap.py -v`
- After TASK-3 + TASK-4: `uv run pytest tests/test_status.py tests/test_capabilities.py -v` (confirms `get_status` callpath and `_commands.py` fix)
- After TASK-6: `uv run pytest tests/test_bootstrap.py -v` (banner extraction; no behavioral change)
- Full regression after all tasks: `uv run pytest -v`

No new tests required. The refactor is purely mechanical; existing functional tests cover all touched paths. If a test imports `run_checks` with the old `deps=None` signature, update it to `run_checks(config)` with a real `CoConfig` instance.

---

## Open Questions

None. Source confirmed, callsites mapped, partial work inventoried, scope bounded.
