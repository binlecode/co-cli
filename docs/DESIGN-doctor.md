# Design: Doctor — Integration Health Checks

## 1. What & How

Doctor is the centralized system-level design for shared resource and integration checks in co-cli. It owns the reusable health-check engine (`run_doctor(...)`), the probe set it runs, the `DoctorResult`/`CheckItem` contract returned to callers, and the behavior shared across all callsites. Bootstrap, runtime capability introspection, and static status are callers of Doctor; they are not independent health-check systems.

Doctor runs at three callsites:
- bootstrap Step 4 for automatic startup visibility
- `check_capabilities(ctx)` for runtime introspection with live `CoDeps`
- `_status.py` / `get_status()` for static status outside the agent runtime

The user-facing `/doctor` flow is not a separate engine. It is a skill prompt in `co_cli/skills/doctor.md` that instructs the agent to call `check_capabilities`, which in turn calls `run_doctor(ctx.deps)`. The design rule is therefore: one shared check engine, multiple product surfaces.

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

### Ownership boundary

Doctor owns:
- which resource and integration checks exist in the shared sweep
- the data contract returned by the sweep
- the distinction between static-only checks and runtime-aware checks
- the semantics of `"ok"`, `"warn"`, `"error"`, and `"skipped"`
- how bootstrap, runtime, and status consume the same result shape

Doctor does not own:
- startup sequencing before or after the Doctor call
- model/provider preflight gating in `run_model_check()`
- banner rendering, REPL startup, or turn execution

Those concerns stay in `DESIGN-system-bootstrap.md` and the model-check docs. Bootstrap owns when Doctor is invoked; Doctor owns what that invocation means.

### Relationship between the three surfaces

Doctor appears in three product surfaces, but only one underlying probe engine exists:

| Surface | User trigger | Code path | Purpose | Runtime context |
|---------|--------------|-----------|---------|-----------------|
| Bootstrap system check | automatic during session startup | `_bootstrap.py` Step 4 -> `run_doctor(deps)` | Emit startup health lines so the user sees problems immediately | Full `CoDeps` available |
| Runtime system check | agent calls capability introspection mid-turn | `check_capabilities(ctx)` -> `run_doctor(ctx.deps)` | Give the running agent structured health/capability data | Full `CoDeps` available |
| `/doctor` skill | user types `/doctor` | `co_cli/skills/doctor.md` -> agent invokes `check_capabilities(ctx)` -> `run_doctor(ctx.deps)` | User-facing UX layer over the runtime system check | Full `CoDeps` available |

`/doctor` therefore does not bypass the agent and does not talk to `_doctor.py` directly. It is a thin prompt relay over `check_capabilities`.

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

### Probe model

Probe logic lives in `co_cli/_probes.py`. Doctor is a thin compatibility layer that converts `ProbeResult` objects into `CheckItem` results for callers that depend on the Doctor interface.

The current shared probe set is:
- Google credential availability
- Obsidian vault availability
- Brave Search credential presence
- one MCP probe per configured server
- knowledge backend state when live `deps` is available
- loaded skill count when live `deps` is available

Static callers run only the settings-based probes plus MCP checks. Runtime-aware callers add knowledge and skills because those depend on live `CoDeps`.

### Probe semantics

Each probe returns a `ProbeResult`, then Doctor maps that to a `CheckItem` via `_to_check_item(...)`.

Current probe behavior:

**Google**
- explicit credential file present → `"ok"`
- cached OAuth token present → `"ok"`
- ADC present → `"ok"`
- no credential source found → `"warn"`

**Obsidian**
- vault path unset → `"skipped"`
- configured path exists → `"ok"`
- configured path missing → `"warn"`

**Brave**
- API key present → `"ok"`
- key absent → `"skipped"`

**MCP server**
- remote URL configured → `"ok"` with `"remote url"`
- local command found on PATH → `"ok"`
- local command missing → `"error"`

**Knowledge**
- `deps.services.knowledge_index is not None` → `"ok"` with active backend detail
- index unavailable → `"warn"` with grep-fallback detail

**Skills**
- one or more skills loaded → `"ok"`
- zero skills → `"skipped"`

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
    for name, cfg in (s.mcp_servers or {}).items():
        checks.append(check_mcp_server(name, cfg.command, cfg.url))

    # Runtime-aware checks — requires live deps
    if deps is not None:
        checks.append(check_knowledge(
            backend=deps.config.knowledge_search_backend,
            index_active=deps.services.knowledge_index is not None
        ))
        checks.append(check_skills(count=len(deps.session.skill_registry)))

    return DoctorResult(checks=checks)
```

The `deps=None` path runs only settings-based checks plus one MCP check per configured server. The `deps` path adds runtime-aware knowledge and skills checks on top. This split is the core Doctor contract: same API, different coverage depending on runtime context availability.

### Callers

Doctor is designed to be reusable across both system-owned flows (startup, CLI commands) and agent-owned flows (mid-turn tool calls).

#### System-owned callers

System-owned callers run as part of startup or CLI infrastructure — no agent is running when they execute.

**`_bootstrap.py` Step 4** calls `run_doctor(deps)` with the fully-initialized `CoDeps`. Bootstrap is a caller only: it does not own probe semantics. It iterates `result.summary_lines()` and emits each line via `frontend.on_status`, giving the user an integration health summary at session start.

**`_status.py` `get_status()`** calls `run_doctor()` (no deps). It maps the returned `CheckItem` list to the `StatusInfo` dataclass fields for the `co status` command display. The no-deps path is appropriate here because `co status` runs entirely outside the agent stack — no agent is initialized, no `RunContext` exists, no `CoDeps` is constructed. Knowledge and skills checks are genuinely unavailable at this callsite.

#### Agent-owned callers

Agent-owned callers run mid-turn with the full runtime already live.

**`capabilities.py`** calls `run_doctor(ctx.deps)` inside the `check_capabilities` tool. The tool return dict includes `checks` (list of `{name, status, detail}` dicts — one per Doctor check) alongside other capability fields (`display`, `knowledge_backend`, `reranker`, `mcp_count`, `reasoning_models`, `reasoning_ready`, `skill_grants`, `google`, `obsidian`, `brave`), enabling the agent to reason about integration health on demand. This path is reachable in two ways: the user explicitly invokes `/doctor`, whose skill body instructs the agent to call `check_capabilities`, or the model decides to call `check_capabilities` during a normal turn.

Doctor stays reusable across both call paths — it is not a bootstrap-internal helper and not a runtime-only helper.

### Error handling

Normal missing-resource outcomes are encoded in probe results as `"warn"`, `"error"`, or `"skipped"`; they are not exceptional control flow. `run_doctor` itself does not catch unexpected exceptions. Callers are responsible for guarding: `_bootstrap.py` Step 4 wraps `run_doctor(deps)` in `try/except` so any unexpected Doctor failure emits a single warning line and the session continues uninterrupted.

## 3. Config

No new settings. Doctor reads from existing `CoConfig`/`Settings` fields:

| Field read | Setting | Env Var | Purpose |
|------------|---------|---------|---------|
| `google_credentials_path` | `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | Explicit Google OAuth credential file |
| `obsidian_vault_path` | `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | Obsidian vault root |
| `brave_search_api_key` | `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | Brave Search credential |
| `mcp_servers` | `mcp_servers` | `CO_CLI_MCP_SERVERS` | MCP server configs (command, url) |
| `knowledge_search_backend` | `knowledge_search_backend` | `CO_CLI_KNOWLEDGE_SEARCH_BACKEND` | Active resolved search backend name reported by the knowledge probe |

See [DESIGN-index.md](DESIGN-index.md) §Config Reference for full setting details.

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_doctor.py` | Doctor compatibility layer: `CheckItem`, `DoctorResult`, `_to_check_item()`, `run_doctor()` |
| `co_cli/_probes.py` | Probe implementations used by Doctor (`probe_google`, `probe_obsidian`, `probe_brave`, `probe_mcp_server`, `probe_knowledge`, `probe_skills`) |
| `co_cli/_bootstrap.py` | Bootstrap Step 4 caller: invokes `run_doctor(deps)` and emits `summary_lines()` via `frontend.on_status` |
| `co_cli/tools/capabilities.py` | Calls `run_doctor(ctx.deps)` inside `check_capabilities`; maps result to tool return dict |
| `co_cli/_status.py` | Calls `run_doctor()` in `get_status()`; maps `CheckItem` list to `StatusInfo` fields |
| `tests/test_bootstrap.py` | Bootstrap integration tests: knowledge sync, session restore, index-disable-on-failure, stale session handling |
