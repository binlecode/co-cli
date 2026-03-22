# TODO — Bootstrap Pydantic-AI Idiomatic Gaps

This delivery replaces the older bootstrap gap research note with an implementation-focused TODO. It captures only the critical gaps that still exist in code and that should be closed in a way that stays aligned with pydantic-ai idioms already used elsewhere in `co-cli`.

The goal is not to invent a new bootstrap framework. The goal is to tighten the startup contract, make degraded state legible, and keep approval/bootstrap behavior in the caller layer rather than burying policy inside tools or ad hoc wrappers.

## Current Code Facts

- bootstrap dependency construction and graceful backend degradation already exist in [`co_cli/bootstrap/_bootstrap.py`](/Users/binle/workspace_genai/co-cli/co_cli/bootstrap/_bootstrap.py)
- hard startup failure already exits cleanly at the CLI boundary in [`co_cli/main.py`](/Users/binle/workspace_genai/co-cli/co_cli/main.py)
- MCP connection failure during agent context entry already degrades to warning-only behavior in [`co_cli/main.py`](/Users/binle/workspace_genai/co-cli/co_cli/main.py)
- JSON config parse failures are already best-effort in [`co_cli/config.py`](/Users/binle/workspace_genai/co-cli/co_cli/config.py)
- knowledge and reranker degradation paths are already covered by functional tests in [`tests/test_bootstrap.py`](/Users/binle/workspace_genai/co-cli/tests/test_bootstrap.py)

## Problem Summary

Bootstrap is no longer missing architecture. The remaining problems are narrower:

- startup still communicates fatal vs degraded state through exceptions plus status lines rather than one explicit pre-REPL verdict
- the welcome banner still prints a generic ready state even when startup fallbacks are active
- writable runtime paths are still used opportunistically rather than being preflighted before session startup
- config validation failures still surface as raw Pydantic validation errors without clear file attribution

These are all boundary-contract issues. They should be fixed with small, direct changes around `CoConfig`, `CoDeps`, and the CLI entrypoint, not with a second runtime layer over pydantic-ai.

## Non-Goals

- no replacement of `RunContext[CoDeps]` with a custom dependency container
- no approval prompts inside tools
- no doctor-mode redesign
- no MCP-specific bootstrap subsystem
- no generalized state machine framework for startup

## Task 1 — Introduce An Explicit Startup Verdict

### Why

`create_deps()` currently raises on hard bootstrap failures, while degraded conditions are surfaced indirectly through `deps.runtime.startup_statuses`. That works, but it leaves no single caller-facing verdict for `ready`, `degraded`, or `blocked`.

The idiomatic fix is a small structured bootstrap result owned by the startup layer, not by tools and not by the agent loop.

### Files

- `co_cli/bootstrap/_bootstrap.py`
- `co_cli/main.py`
- `tests/test_bootstrap.py`
- `docs/DESIGN-core-loop.md`
- `docs/DESIGN-system.md`

### Changes

1. add a private startup result type in bootstrap code, for example `_StartupResult`
2. make bootstrap return a structured verdict with:
   - `status: "ready" | "degraded" | "blocked"`
   - `deps: CoDeps | None`
   - `blockers: list[str]`
   - `degraded: list[str]`
3. keep provider/model hard failures as blocked startup, but convert them into structured bootstrap output rather than raw control flow
4. let `main.py` own the final user-facing exit behavior from that verdict
5. keep pydantic-ai ownership unchanged: the agent is only built after startup returns a usable `CoDeps`

### Guidance

- keep the result private to bootstrap and CLI startup
- do not create a generic event bus or startup orchestrator
- do not move failure policy into tools

### Done When

- startup has one explicit pre-REPL verdict
- `main.py` branches on that verdict instead of relying on `ValueError` from `create_deps()`
- degraded startup remains usable without pretending the system is fully ready

## Task 2 — Make Banner Readiness Match Actual Runtime State

### Why

The current banner marks the knowledge line as degraded when `startup_statuses` exist, but it still prints `✓ Ready` unconditionally. That makes the startup contract ambiguous.

The fix is small: consume the structured startup verdict and existing degraded statuses instead of rendering a generic success line.

### Files

- `co_cli/bootstrap/_banner.py`
- `co_cli/main.py`
- `tests/test_bootstrap.py`
- `docs/DESIGN-system.md`

### Changes

1. pass startup verdict data into banner rendering
2. render one of:
   - `Ready`
   - `Ready (degraded)`
   - blocked startup message without entering the REPL
3. keep degradation detail compact and user-facing
4. preserve current tool/skill/MCP counts and environment display

### Done When

- the banner no longer claims full readiness during degraded bootstrap
- startup messaging is consistent with actual runtime capability

## Task 3 — Add Filesystem Preflight For Runtime State Paths

### Why

Bootstrap currently derives `session_path`, `tasks_dir`, `memory_dir`, and `library_dir`, then relies on later writes to discover filesystem problems. That defers user-visible failure until session restore, background task storage, or memory sync.

This is a startup contract problem, not a tool problem. The bootstrap layer should preflight the paths it is about to rely on.

### Files

- `co_cli/bootstrap/_bootstrap.py`
- `co_cli/deps.py`
- `co_cli/main.py`
- `tests/test_bootstrap.py`
- `docs/DESIGN-system.md`

### Changes

1. add a small bootstrap preflight for runtime-managed writable paths:
   - `.co-cli/session.json` parent
   - `.co-cli/tasks/`
   - `.co-cli/memory/`
   - configured library dir
2. classify failures as:
   - blocked when startup cannot safely continue
   - degraded only where the runtime already has a real fallback
3. keep the checks in bootstrap, before agent creation and before `TaskStorage` or session restore uses the paths
4. report failures through the structured startup verdict

### Guidance

- prefer direct `Path.mkdir(..., exist_ok=True)` and a minimal writability probe
- do not hide `OSError` inside unrelated tools
- do not add shell-based probes

### Done When

- bootstrap validates runtime state paths before first use
- common permission/path failures are reported as startup blockers with clear messages

## Task 4 — Wrap Pydantic Validation Errors With File Attribution

### Why

Malformed JSON is already handled best-effort, but `Settings.model_validate(...)` still raises raw validation errors after layered merge. That leaves the user without a clear indication of which settings source caused the failure.

The right fix is not to suppress validation. The right fix is to preserve strict validation while annotating the error with the offending config source and field path.

### Files

- `co_cli/config.py`
- `tests/test_config.py`
- `docs/DESIGN-system.md`

### Changes

1. wrap `Settings.model_validate(...)` in `load_config()`
2. on `ValidationError`, raise a clearer exception that includes:
   - whether the invalid value came from user config, project config, or env override
   - the failing field path
3. preserve the original validation semantics
4. keep JSON parse failures as non-blocking, but keep schema validation blocking

### Guidance

- do not weaken `Settings` validation
- do not silently skip invalid structured settings
- keep the implementation local to `load_config()`

### Done When

- invalid config values fail fast with file-attributed messaging
- tests assert the improved error surface rather than raw unannotated validation output

## Exit Criteria

- bootstrap has a single structured readiness verdict before REPL entry
- degraded startup is visibly distinguished from full readiness
- writable runtime paths are preflighted before use
- config validation failures identify the offending source clearly
- docs describe the shipped bootstrap contract instead of the deleted research note
