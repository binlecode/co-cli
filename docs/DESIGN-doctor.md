# Design: Doctor — Integration Health Checks

## 1. What & How

The Doctor module provides system-wide integration health checks — a single sweep that probes every external dependency co-cli may rely on (Google credentials, Obsidian vault, Brave Search API key, MCP server binaries, knowledge index, loaded skills) and returns a structured result. It runs at three callsites: bootstrap Step 4 (runtime sweep with full `CoDeps`), the `check_capabilities` tool (agent-facing introspection), and `_status.py`/`get_status()` (static sweep without a running agent).

```
                    ┌─────────────────────────────────────────┐
                    │            co_cli/_doctor.py            │
                    │                                         │
                    │  run_doctor(deps=None)                  │
                    │  ┌───────────────────────────────────┐  │
                    │  │  Always (from settings singleton) │  │
                    │  │  check_google(...)                │  │
                    │  │  check_obsidian(...)              │  │
                    │  │  check_brave(...)                 │  │
                    │  │  check_mcp_server(...) × N        │  │
                    │  └───────────────────────────────────┘  │
                    │  ┌───────────────────────────────────┐  │
                    │  │  Runtime only (when deps given)   │  │
                    │  │  check_knowledge(...)             │  │
                    │  │  check_skills(...)                │  │
                    │  └───────────────────────────────────┘  │
                    │              │                           │
                    │              ▼                           │
                    │         DoctorResult                     │
                    └─────┬──────────────┬──────────────────┬───┘
                          │              │                   │
          ┌───────────────┘              │             ┌─────┘
          ▼                              ▼             ▼
_bootstrap.py Step 4      capabilities.py     _status.py get_status()
run_doctor(deps)          run_doctor(ctx.deps)  run_doctor()
emit summary_lines        map to dict +         map to StatusInfo fields
via frontend.on_status    checks field
```

## 2. Core Logic

### Data model

**`CheckItem`** is the unit of a single probe:

```
CheckItem:
    name: str           # check identifier (e.g. "google", "mcp:github")
    status: str         # "ok" | "warn" | "error" | "skipped"
    detail: str         # human-readable explanation of the outcome
    extra: str = ""     # optional secondary detail (credential path, url, etc.)
```

**`DoctorResult`** collects all checks from one sweep:

```
DoctorResult:
    checks: list[CheckItem]

    has_errors   → bool   # True if any check has status "error"
    has_warnings → bool   # True if any check has status "warn"
    by_name(name) → CheckItem | None
    summary_lines() → list[str]   # pre-formatted lines for display
```

`summary_lines()` produces one formatted line per check: icon + name + detail, suitable for direct output to the terminal via `frontend.on_status`.

### Check functions

Each check function is pure — no I/O beyond `os.path.exists` and `shutil.which`. All return a single `CheckItem`.

**`check_google(credentials_path, token_path, adc_path) -> CheckItem`**

Resolves Google credential availability using the same three-path chain as `_google_auth.py`:
- If `credentials_path` is set and file exists → `"ok"` (explicit credential)
- Else if `token_path` exists → `"ok"` (cached OAuth token)
- Else if `adc_path` exists → `"ok"` (Application Default Credentials)
- Otherwise → `"warn"` (no credential found, Google tools will be unavailable)

**`check_obsidian(vault_path: Path | None) -> CheckItem`**

- If `vault_path` is `None` → `"skipped"` (not configured)
- If path exists on disk → `"ok"`
- Otherwise → `"warn"` (configured but directory not found)

**`check_brave(api_key) -> CheckItem`**

- If `api_key` is set and non-empty → `"ok"`
- Otherwise → `"skipped"` (optional integration; absent key is not an error)

**`check_mcp_server(name, command, url) -> CheckItem`**

`CheckItem.name` is set to `"mcp:{name}"` (e.g. `"mcp:github"`).
- If `url` is set → `"ok"` with detail `"remote url"` (no local binary required)
- Else if `shutil.which(command)` finds the binary → `"ok"` with detail `"{command} found"`
- Otherwise → `"error"` with detail `"{command} not found"` (when `command` is `None`, the label is `"(no command)"` — the error reads `"(no command) not found"`)

**`check_knowledge(backend, index_active) -> CheckItem`**

- `backend` is the configured search backend string (e.g. `"fts5"`, `"hybrid"`)
- `index_active` is `True` when `KnowledgeIndex` was successfully initialized
- If `index_active` → `"ok"` with detail `"{backend} active"`
- Otherwise → `"warn"` (index unavailable, search tools will degrade to grep fallback)

**`check_skills(count) -> CheckItem`**

- `count` is the number of skills successfully loaded at bootstrap
- `count > 0` → `"ok"` with detail `"{count} skill(s) loaded"`
- `count == 0` → `"skipped"` with detail `"no skills found"` (zero skills is valid, not an error)

### Entry point: `run_doctor`

```
run_doctor(deps: CoDeps | None = None) -> DoctorResult:

    s = settings singleton

    checks = []

    # Always — static checks, no runtime required
    checks.append(check_google(
        s.google_credentials_path,
        token_path=~/.config/co-cli/google_token.json,
        adc_path=~/.config/gcloud/application_default_credentials.json
    ))
    checks.append(check_obsidian(s.obsidian_vault_path))
    checks.append(check_brave(s.brave_search_api_key))
    for name, cfg in s.mcp_servers.items():
        checks.append(check_mcp_server(name, cfg.command, cfg.url))

    # Runtime — requires live deps
    if deps is not None:
        checks.append(check_knowledge(
            backend=deps.config.knowledge_search_backend,
            index_active=deps.services.knowledge_index is not None
        ))
        checks.append(check_skills(count=len(deps.session.skill_registry)))

    return DoctorResult(checks=checks)
```

The `deps=None` path (used by `_status.py`) runs only the four static checks plus one MCP check per configured server. The `deps` path (used by `_bootstrap.py` and `capabilities.py`) adds knowledge and skills checks on top.

### Callers

Doctor is designed to be reusable across both system-owned flows (startup, CLI commands) and agent-owned flows (mid-turn tool calls).

#### System-owned callers

System-owned callers run as part of startup or CLI infrastructure — no agent is running when they execute.

**`_bootstrap.py` Step 4** calls `run_doctor(deps)` with the fully-initialized `CoDeps`. It iterates `result.summary_lines()` and emits each line via `frontend.on_status`, giving the user an integration health summary at session start.

**`_status.py` `get_status()`** calls `run_doctor()` (no deps). It maps the returned `CheckItem` list to the `StatusInfo` dataclass fields for the `co status` command display. The no-deps path is appropriate here because `co status` runs entirely outside the agent stack — no agent is initialized, no `RunContext` exists, no `CoDeps` is constructed. Knowledge and skills checks are genuinely unavailable at this callsite, which is also why `StatusInfo` has no corresponding fields for them.

#### Agent-owned callers

Agent-owned callers run mid-turn with the full runtime already live.

**`capabilities.py`** calls `run_doctor(ctx.deps)` inside the `check_capabilities` tool. The tool return dict includes `checks` (list of `{name, status, detail}` dicts — one per doctor check) alongside other capability fields (`display`, `knowledge_backend`, `reranker`, `mcp_count`, `reasoning_models`, `reasoning_ready`, `skill_grants`, `google`, `obsidian`, `brave`), enabling the agent to reason about integration health on demand. This path is accessible via the existing `/doctor` skill (`co_cli/skills/doctor.md`) or triggered by the LLM during a turn.

The `deps=None` path (system callers) runs only the four static checks plus one MCP check per configured server. The `deps` path (agent callers) adds knowledge and skills checks on top. Doctor stays reusable across both call paths — it is not a bootstrap-internal helper.

### Error handling

Check functions are pure probes using `os.path.exists` and `shutil.which` — they do not raise on missing paths or absent binaries; those are normal outcomes encoded as `"warn"` or `"error"` status. `run_doctor` itself does not catch exceptions. Callers are responsible for guarding: `_bootstrap.py` Step 4 wraps `run_doctor(deps)` in `try/except` so any unexpected failure emits a single warning line and the session continues uninterrupted.

## 3. Config

No new settings. Doctor reads from existing `CoConfig`/`Settings` fields:

| Field read | Setting | Env Var | Purpose |
|------------|---------|---------|---------|
| `google_credentials_path` | `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | Explicit Google OAuth credential file |
| `obsidian_vault_path` | `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | Obsidian vault root |
| `brave_search_api_key` | `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | Brave Search credential |
| `mcp_servers` | `mcp_servers` | `CO_CLI_MCP_SERVERS` | MCP server configs (command, url) |
| `knowledge_search_backend` | `knowledge_search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | Active search backend name |

See [DESIGN-index.md](DESIGN-index.md) §Config Reference for full setting details.

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_doctor.py` | `CheckItem`, `DoctorResult`, all `check_*` functions, `run_doctor()` entry point |
| `co_cli/_bootstrap.py` | Step 4: calls `run_doctor(deps)` and emits `summary_lines()` via `frontend.on_status` |
| `co_cli/tools/capabilities.py` | Calls `run_doctor(ctx.deps)` inside `check_capabilities`; maps result to tool return dict |
| `co_cli/_status.py` | Calls `run_doctor()` in `get_status()`; maps `CheckItem` list to `StatusInfo` fields |
| `tests/test_bootstrap.py` | Bootstrap integration tests: knowledge sync, session restore, index-disable-on-failure, stale session handling |
