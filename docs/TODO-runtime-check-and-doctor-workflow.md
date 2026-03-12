# TODO: Runtime Check And Doctor Workflow

**Slug:** `runtime-check-and-doctor-workflow`
**Type:** Refactor + behavior extension
**Status:** Approved for Implementation

---

## Context

This TODO is the implementation source of truth.

**Current state verified against source:**

| Item | Status |
|------|--------|
| `co_cli/_probes.py` | Does not exist |
| `co_cli/_runtime_check.py` | Does not exist |
| `co_cli/_startup_check.py` | Does not exist |
| `co_cli/_doctor.py` | Exists — `run_doctor(deps=None) -> DoctorResult` with 6 pure probe functions (google, obsidian, brave, MCP, knowledge, skills) |
| `co_cli/_model_check.py` | Exists — mixes startup policy (`run_model_check`) with provider probing (`_check_llm_provider`, `_check_model_availability`) |
| `co_cli/tools/capabilities.py` | Exists — `check_capabilities(ctx)` wraps `run_doctor(ctx.deps)` — covers integrations + role models but missing tool surface, skill surface, approval context, and session state |
| `co_cli/deps.py` — `CoSessionState` | Has `skill_registry` and `skill_tool_grants`, but no `tool_names`, `tool_approvals`, or `active_skill_name` |
| `co_cli/skills/doctor.md` | Exists but thin — just "call check_capabilities and report conversationally" |

**No phases are shipped. All tasks start from scratch.**

**Regression surface for the refactor:**

- `run_doctor(deps=None)` is called from `_bootstrap.py` (Step 4), `capabilities.py`, `_status.py` — all three callers must continue working
- `run_model_check(deps, frontend)` is called from `main.py` — must be replaced cleanly
- `check_capabilities` return dict shape must stay compatible for existing agent callers

---

## Problem & Outcome

**Problem:** Self-checking logic is split across `_model_check.py` (startup policy mixed with probing), `_doctor.py` (integration probes under a doctor-oriented name), `capabilities.py` (partial runtime view), `_status.py` (separate status view), and `main.py` (local tool/skill state). `/doctor` skill is too thin for troubleshooting. No shared probe layer and no unified runtime diagnostic primitive exist.

**Outcome:** `_probes.py` owns shared factual probes. `check_startup(...)` owns startup gating. `check_runtime(...)` is the main runtime diagnostic primitive combining capabilities and status. `/doctor` is a structured troubleshooting workflow starting from `check_runtime(...)`. Tool and skill surface are persisted in `CoDeps`.

---

## Scope

**In:**
- `_probes.py` — shared factual probe layer
- `_runtime_check.py` — `check_runtime(deps) -> RuntimeCheck`
- `_startup_check.py` — `check_startup(deps, frontend)` startup gate
- `CoSessionState` — `tool_names`, `tool_approvals`, `active_skill_name` fields
- `tools/capabilities.py` — updated to use `check_runtime`
- `skills/doctor.md` — structured troubleshooting rewrite
- `_bootstrap.py` Step 4 — migrated to `check_runtime`
- Tests and DESIGN docs

**Out:**
- Background task probes (defer)
- Deep observability or performance metrics
- Repair actions inside runtime check
- LLM root-cause analysis in runtime check
- `co status` full runtime exposure (keep compact static subset)
- `_status.py` full migration (keep using `run_doctor` for static checks; out of scope for MVP)
- `_doctor.py` deletion (keep as backward-compat shim during transition)
- Context pressure / compaction signal (defer — not yet a user-reported diagnostic gap; add only if it proves diagnostic value)

**Delivery priority:**
TASK-6 (`/doctor` rewrite) is the primary user-visible gate. TASK-7 (bootstrap migration) is secondary — defer it if scope must be cut before TASK-6 ships.

---

## High-Level Design

```
_probes.py
  probe_provider(...)       → ProbeResult
  probe_role_models(...)    → ProbeResult
  probe_google(...)         → ProbeResult
  probe_obsidian(...)       → ProbeResult
  probe_brave(...)          → ProbeResult
  probe_mcp(...)            → ProbeResult
  probe_knowledge(...)      → ProbeResult

_startup_check.py
  check_startup(deps, frontend) → None   [raises RuntimeError on hard failure]
    uses: probe_provider, probe_role_models from _probes.py

_runtime_check.py
  check_runtime(deps: CoDeps) → RuntimeCheck
    capabilities: dict   [all probes assembled]
    status: dict         [session id, tool surface, skill surface, grants, modes]
    findings: list[dict] [degraded/failed conditions]
    fallbacks: list[str] [active fallbacks]
    summary_lines() → list[str]

tools/capabilities.py
  check_capabilities(ctx) → dict   [wraps check_runtime for agent access; adds display string]

skills/doctor.md
  "call check_capabilities → identify degraded conditions → diagnose: issue + still-works + fallback + next step"
```

**`check_runtime` does not call `check_capabilities`. It assembles capabilities directly from `_probes.py`. `check_capabilities` wraps `check_runtime` for agent access.** No circular dependency.

---

## Implementation Plan

### ✓ DONE — TASK-1: Create `_probes.py` with shared factual probes

**files:**
- `co_cli/_probes.py` (create)
- `co_cli/_doctor.py` (modify — import from `_probes.py`; DoctorResult maps ProbeResult → CheckItem)
- `co_cli/_model_check.py` (modify — extract provider probe functions to `_probes.py`)

**work:**
- Define `ProbeResult(ok: bool, status: str, detail: str, extra: dict[str, Any] = field(default_factory=dict))` dataclass
- Move `check_google`, `check_obsidian`, `check_brave`, `check_mcp_server`, `check_knowledge`, `check_skills` from `_doctor.py` to `_probes.py` as `probe_google`, `probe_obsidian`, `probe_brave`, `probe_mcp_server`, `probe_knowledge`, `probe_skills`
- Extract `probe_provider(llm_provider, gemini_api_key, ollama_host) -> ProbeResult` from `_check_llm_provider` logic in `_model_check.py`
- Extract `probe_role_models(llm_provider, ollama_host, role_models) -> ProbeResult` from `_check_model_availability` logic (pure, no mutation; returns `ProbeResult` with `extra={"role_models": updated_chains}` when chain advancement occurs)
- Update `_doctor.py`: import from `_probes.py`; map `ProbeResult` → `CheckItem` in `run_doctor`; keep `DoctorResult` and `run_doctor` for backward compat
- `_model_check.py` private probe functions now delegate to `_probes.py`
- `_probes.py` imports `GOOGLE_TOKEN_PATH, ADC_PATH` from `co_cli.tools._google_auth` for use in `probe_google`

**guard conditions:** `probe_provider` and `probe_role_models` must replicate exact status/error logic from existing `_check_llm_provider` / `_check_model_availability` — verify against current branching logic line by line

**done_when:** `from co_cli._probes import probe_provider, probe_google` works; all three `run_doctor` callsites still work; `uv run pytest -x` passes

---

### ✓ DONE — TASK-2: Persist tool surface and skill surface in `CoSessionState`

**files:**
- `co_cli/deps.py` (modify — add `tool_names`, `tool_approvals`, `active_skill_name` to `CoSessionState`)
- `co_cli/main.py` (modify — populate after final tool resolution)
- `co_cli/_commands.py` (modify — persist active skill name during skill dispatch)

**work:**
- Add `tool_names: list[str] = field(default_factory=list)` to `CoSessionState`
- Add `tool_approvals: dict[str, bool] = field(default_factory=dict)` to `CoSessionState`
- Add `active_skill_name: str | None = None` to `CoSessionState`
- In skill dispatch, set `deps.session.active_skill_name = skill.name` when a skill is activated; clear it in the same finally block that clears `active_skill_env` and `skill_tool_grants`
- In `chat_loop()`, persist `deps.session.tool_approvals` from the final `get_agent(...)` call that survives fallback handling
- In `chat_loop()`, persist `deps.session.tool_names` only after final tool resolution is complete so runtime state includes MCP-discovered tools and excludes the abandoned pre-fallback agent surface
- `make_subagent_deps` already resets `CoSessionState` — sub-agents will have empty tool surface, which is correct (sub-agents have their own tool set)

**done_when:** In a test that calls `get_agent()` directly: `_, _, tool_names, _ = get_agent(); assert len(tool_names) > 0` passes; in a chat-loop integration path, `deps.session.tool_names` reflects the final resolved tool surface after MCP discovery/fallback handling; `uv run pytest -x` passes

---

### ✓ DONE — TASK-3: Create `_runtime_check.py` with `check_runtime(deps) -> RuntimeCheck`

**files:**
- `co_cli/_runtime_check.py` (create)

**prerequisites:** [TASK-1, TASK-2]

**work:**

Define the `RuntimeCheck` dataclass:

```python
@dataclass
class RuntimeCheck:
    capabilities: dict[str, Any]
    status: dict[str, Any]
    findings: list[dict[str, str]] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]: ...
```

Implement `check_runtime(deps: CoDeps) -> RuntimeCheck`:

**capabilities dict** — assembled from `_probes.py` probe results, structured as:
- `provider`: `{ok, status, detail}` from `probe_provider`
- `role_models`: `{reasoning, optional_roles}` from `probe_role_models`
- `reasoning_chain`: `list[ModelEntry]` — `deps.config.role_models.get("reasoning", [])`; exposed for `check_capabilities` to map to `reasoning_models` return field
- `reasoning_ready`: bool
- `google`: bool — from `probe_google`
- `obsidian`: bool — from `probe_obsidian`
- `brave`: bool — from `probe_brave`
- `mcp_count`: int — from `probe_mcp` over configured servers
- `knowledge_backend`: str — from `probe_knowledge`
- `checks`: list of `{name, status, detail}` for all probe results (for display compatibility with current `check_capabilities`)

**status dict** — assembled from `deps`:
- `session_id`: `deps.config.session_id`
- `active_skill`: `deps.session.active_skill_name`
- `skill_grants`: list from `deps.session.skill_tool_grants`
- `tool_names`: `deps.session.tool_names`
- `tool_approvals`: `deps.session.tool_approvals`
- `tool_count`: `len(deps.session.tool_names)`
- `skill_count`: `len(deps.session.skill_registry)`
- `mcp_mode`: "mcp" if `deps.config.mcp_count > 0` else "native-only"
- `knowledge_mode`: `deps.config.knowledge_search_backend`

**findings**: append a `{component, issue, severity}` entry for each probe result where `ok=False`

**fallbacks**: append human-readable string for each active fallback detected (e.g. `"knowledge: grep fallback"`, `"mcp: native-only"`)

**summary_lines**: format 3–6 bullet lines summarizing findings and fallbacks

**done_when:** `from co_cli._runtime_check import check_runtime, RuntimeCheck` works; `check_runtime(real_deps)` returns `RuntimeCheck` with non-empty `capabilities` and populated `status`; new test file `tests/test_runtime_check.py` has at least one passing test

---

### ✓ DONE — TASK-4: Create `_startup_check.py` with `check_startup(deps, frontend) -> None`

**Rationale:** `_model_check.py` currently mixes startup policy (fail-fast gating, error emission) with factual probing. This conflation makes it impossible to reuse the same provider/model facts at runtime without triggering startup side-effects. Separating them gives `check_runtime` a clean probe layer to call without startup policy interference.

**files:**
- `co_cli/_startup_check.py` (create)
- `co_cli/_model_check.py` (modify — remove startup policy; `run_model_check` deleted; private helpers delegate to `_probes.py`)
- `co_cli/_status.py` (modify — update imports: replace `_check_llm_provider` / `_check_model_availability` imports from `_model_check` with equivalent calls using `_probes.py` functions)
- `co_cli/main.py` (modify — call `check_startup(deps, frontend)` instead of `run_model_check`)
- `tests/test_model_check.py` (modify — migrate `run_model_check` tests to `tests/test_startup_check.py` exercising `check_startup`; remove deleted-function tests)
- `tests/test_startup_check.py` (create — test `check_startup` with real `CoDeps`)

**prerequisites:** [TASK-1]

**work:**
- Create `check_startup(deps: CoDeps, frontend: TerminalFrontend) -> None` in `_startup_check.py`
- Implement using `probe_provider(...)` and `probe_role_models(...)` from `_probes.py`
- Startup policy is identical to current `run_model_check`: hard error (Gemini key missing, reasoning model unavailable) → `RuntimeError`; soft warning (Ollama unreachable, optional role unavailable) → `frontend.on_status()`
- Role model chain advancement: `probe_role_models` returns updated role models in `ProbeResult.extra["role_models"]`; `check_startup` applies to `deps.config.role_models` (same mutation as current `run_model_check`)
- `_model_check.py` retains `_check_llm_provider` and `_check_model_availability` as private helpers (delegating to `_probes.py`), but `run_model_check` is deleted
- `_status.py`: replace `from co_cli._model_check import _check_llm_provider, _check_model_availability` with `from co_cli._probes import probe_provider, probe_role_models`; update call sites accordingly. **Note:** old check results used `.message`; `ProbeResult` uses `.detail` — update all attribute access at call sites.
- `main.py` replaces `run_model_check(deps, frontend)` with `check_startup(deps, frontend)`

**done_when:** `uv run co chat` starts correctly; hard LLM credential errors still block startup; soft Ollama warnings still appear; `_model_check.py` contains no startup policy logic; `uv run pytest -x` passes

---

### ✓ DONE — TASK-5: Update `check_capabilities` to use `check_runtime`

**files:**
- `co_cli/tools/capabilities.py` (modify)

**prerequisites:** [TASK-3]

**work:**
- Replace `run_doctor(ctx.deps)` with `check_runtime(ctx.deps)` in `check_capabilities`
- Preserve all existing return dict fields: `display`, `knowledge_backend`, `reranker`, `google`, `obsidian`, `brave`, `mcp_count`, `reasoning_models`, `reasoning_ready`, `checks`, `skill_grants` — map from `RuntimeCheck.capabilities` and `RuntimeCheck.status`; specifically `reasoning_models` maps from `result.capabilities["reasoning_chain"]`
- Add new return dict fields sourced from `RuntimeCheck.status`: `tool_count`, `active_skill`, `mcp_mode`, `knowledge_mode`
- Regenerate `display` string to include new status fields alongside existing capability fields
- `reranker` field: source from `deps.config` or probe result (keep existing logic)

**done_when:** `check_capabilities` return dict contains all original fields with correct values; new fields `tool_count` and `mcp_mode` are present; agent calling `/doctor` sees richer output; `uv run pytest -x` passes

---

### ✓ DONE — TASK-6: Rewrite `/doctor` skill as structured troubleshooting workflow

**files:**
- `co_cli/skills/doctor.md` (modify)

**prerequisites:** [TASK-5]

**work:**

Rewrite skill body from "report conversationally" to:

1. Call `check_capabilities` to get the full runtime picture
2. Identify the most relevant degraded or blocking conditions for the current task or problem context
3. If needed, run one targeted read-only follow-up check (e.g. `read_file` for credentials, `web_search` for MCP tool docs, second `check_capabilities` call is not needed)
4. Structured output:
   - **likely issue**: what is wrong or degraded
   - **what still works**: functioning capabilities
   - **active fallback**: any degraded-mode operation in effect
   - **what Co should do next**: concrete next step

Keep skill body under 400 tokens. Doctor recommends — does not repair. Do not duplicate runtime aggregation logic (that is `check_capabilities`'s job).

**done_when:** `/doctor` invocation produces a structured 4-part diagnosis (not just a status dump); the diagnosis is contextually relevant to any prior conversation context (not just a generic health report)

---

### ✓ DONE — TASK-7: Migrate `_bootstrap.py` Step 4 to `check_runtime`

**files:**
- `co_cli/_bootstrap.py` (modify — Step 4 integration health)

**prerequisites:** [TASK-3]

**work:**
- Replace `run_doctor(deps)` in Step 4 with `check_runtime(deps)`
- Use `result.summary_lines()` for status output via `frontend.on_status()` (same display contract as current `DoctorResult.summary_lines()`)
- Preserve try/except around Step 4 (graceful degradation: failure → warning, session continues)
- `_status.py` is **not** migrated in this task — keep using `run_doctor(deps=None)` (static context, out of scope)

**done_when:** `run_bootstrap()` completes Step 4 using `check_runtime`; welcome banner shows integration health correctly; `uv run pytest -x` passes

---

### ✓ DONE — TASK-8: Tests

**files:**
- `tests/test_runtime_check.py` (create)
- `tests/test_capabilities.py` (modify if exists)
- `tests/test_bootstrap.py` (modify if exists)

**prerequisites:** [TASK-3, TASK-5, TASK-7]

**work:**

`tests/test_runtime_check.py`:

```python
async def test_check_runtime_returns_runtime_check():
    # Build real CoDeps with no knowledge index, no MCP
    deps = make_minimal_co_deps()
    result = check_runtime(deps)
    assert isinstance(result, RuntimeCheck)
    assert "provider" in result.capabilities
    assert "session_id" in result.status

async def test_check_runtime_knowledge_fallback():
    deps = make_co_deps_with_grep_fallback()
    result = check_runtime(deps)
    assert any("knowledge" in f for f in result.fallbacks)

async def test_check_runtime_status_has_tool_surface():
    deps = make_co_deps_with_tool_names(["check_capabilities", "read_file"])
    result = check_runtime(deps)
    assert result.status["tool_count"] == 2
    assert "check_capabilities" in result.status["tool_names"]

def test_summary_lines_non_empty():
    deps = make_minimal_co_deps()
    result = check_runtime(deps)
    assert len(result.summary_lines()) >= 1
```

Note: `make_minimal_co_deps`, `make_co_deps_with_grep_fallback`, and `make_co_deps_with_tool_names` are **local factory functions** defined at the top of `tests/test_runtime_check.py`. They follow the inline-construction pattern used in existing test files — building real `CoDeps(services=CoServices(shell=ShellBackend(), knowledge_index=None), config=CoConfig(...))` with real field values. No fake dataclasses. No `conftest.py`.

Update `test_capabilities.py` if it exists: assert that `check_capabilities` return dict now includes `tool_count` and `mcp_mode` fields.

**done_when:** `uv run pytest tests/test_runtime_check.py -x -v` passes; `uv run pytest -x` passes

---

### ✓ DONE — TASK-9: Update DESIGN docs

**files:**
- `docs/DESIGN-doctor.md` (modify — reflect `_probes.py` + `check_runtime` + doctor as workflow)
- `docs/DESIGN-system-bootstrap.md` (modify — update startup sequence: `check_startup` separate from probing; Step 4 uses `check_runtime`)
- `docs/DESIGN-system.md` (modify — add `tool_names`, `tool_approvals` to `CoSessionState` table)
- `docs/DESIGN-tools-execution.md` (modify — add `_probes.py` and `_runtime_check.py` entries)
- `docs/DESIGN-index.md` (modify — add `_probes.py`, `_runtime_check.py`, `_startup_check.py` to module index)

**prerequisites:** [TASK-7, TASK-8]

**work:**
- For each doc: read the file, check every factual claim against source, correct inaccuracies, add new module entries
- `DESIGN-doctor.md`: rewrite architecture section to show `_probes.py` → `_runtime_check.py` → `check_capabilities` → `/doctor`; update caller table to show 3 surfaces (bootstrap, capabilities tool, status command)
- `DESIGN-system-bootstrap.md`: update startup sequence diagram to show `check_startup` replacing `run_model_check`; update Step 4 description
- Do not paste source code — use pseudocode per CLAUDE.md doc conventions

**done_when:** `grep "run_doctor\|run_model_check" docs/DESIGN-doctor.md` returns no results (old function names purged); each doc accurately describes the implemented code

---

## Testing Strategy

- All tests are functional: real `CoDeps`, real SQLite, real filesystem
- No mocks, stubs, or monkeypatching (per CLAUDE.md policy)
- Probe tests rely on real path checks (existing test files or None)
- `uv run pytest -x` must pass after every task before proceeding

---

## Open Questions (resolved for MVP)

1. **`check_capabilities` public long-term?** — Keep as public tool; it formats `check_runtime` output for agent access. Revisit post-MVP.
2. **`co status` expose full runtime?** — No. Keep compact static subset. `_status.py` runs without agent context.
3. **Context-pressure in MVP?** — Deferred. Not included in `check_runtime` status dict for MVP.

## Final — PO Gate 1 Check

Verdict: APPROVE

Assessment:
- Right problem: yes. The plan addresses the actual product gap: startup checks, runtime checks, and `/doctor` are split across the wrong boundaries and do not currently produce a coherent troubleshooting workflow.
- Correct scope: yes. The probe/startup/runtime split stays within MVP, keeps `co status` intentionally compact, and treats the `/doctor` rewrite as the main user-visible outcome.
- Blocking issues: none. The earlier migration and compatibility gaps are already resolved in the plan body and audit log.

## Final — Team Lead

Plan approved.

> Gate 1 complete.
> Implementation source of truth: this TODO.
> Next step: `/orchestrate-dev runtime-check-and-doctor-workflow`
