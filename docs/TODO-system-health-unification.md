# TODO: Unified System Health Surface (`_system_health`, `check_system_health`, `/doctor`)

**Slug:** `system-health-unification`
**Type:** Refactor + behavior extension

---

## Context

### Current split is technically correct but operationally uneven

The repository currently has two separate health-check paths:

- `co_cli/_model_check.py` owns pre-agent provider/model gating.
- `co_cli/_doctor.py` owns non-LLM integration checks.

That split is valid for startup sequencing, but it creates an uneven capability surface:

- bootstrap knows provider/model health before agent creation
- `co status` can inspect both model state and doctor state
- the agent can only inspect non-LLM integration health through `check_capabilities`

As a result, the system can diagnose more than the agent can. Mid-turn, if the model suspects a provider outage or missing local Ollama model, there is no first-class tool that lets it verify that condition directly.

### Current naming is also misleading

The naming stack is muddled:

- internal shared module is `_doctor.py`
- agent-facing tool is `check_capabilities`
- user-facing workflow is `/doctor`

These names do not describe the same abstraction level:

- `_doctor.py` is an implementation detail
- `check_capabilities` is a runtime introspection tool
- `/doctor` is a UX workflow

Once model/provider diagnostics are added to the runtime tool, `_doctor.py` becomes especially inaccurate as an internal module name.

### Current code locations

| Concern | Current owner |
|--------|---------------|
| Provider/model preflight before agent creation | `co_cli/_model_check.py` |
| Non-LLM integration checks | `co_cli/_doctor.py` |
| Agent-facing health tool | `co_cli/tools/capabilities.py` (`check_capabilities`) |
| User-facing doctor workflow | `co_cli/skills/doctor.md` |
| Startup model check caller | `co_cli/main.py` |
| Startup integration sweep caller | `co_cli/_bootstrap.py` |
| CLI status composition | `co_cli/_status.py` |

---

## Problem & Outcome

**Problem:** The system has no single authoritative runtime health surface. Startup and `co status` can assess model/provider state, but the agent cannot. The current names also obscure intent: `_doctor.py` is not a user-facing doctor command, and `check_capabilities` understates or misstates the health contract.

**Outcome:** Introduce a single shared system-health module, `co_cli/_system_health.py`, and a single agent-facing read-only tool, `check_system_health`, that exposes both integration health and model/provider health in non-blocking diagnostic form. Keep startup fail-fast semantics in `run_model_check()`, but reuse the same probe logic underneath. Make `/doctor` a thin prompt relay that tells the agent to call `check_system_health` and summarize the result.

---

## Scope

**In scope:**
- Rename shared non-LLM health module from `_doctor.py` to `_system_health.py`.
- Expand the shared runtime health report to include model/provider diagnostics.
- Expand the shared runtime health report to include tool-surface loading status.
- Rename the agent-facing tool from `check_capabilities` to `check_system_health`.
- Update bootstrap, status, agent registration, skills, and docs to the new system-health vocabulary.
- Preserve startup fail-fast behavior in `run_model_check()`.
- Add direct tests for the new shared health aggregation layer.

**Out of scope:**
- Changing provider/model selection behavior.
- Changing bootstrap order.
- Replacing the startup preflight gate with the runtime health tool.
- Adding new external integrations beyond what current doctor/model checks already cover.
- Per-tool execution probes or end-to-end validation at bootstrap time.
- Reworking unrelated command or skill architecture.

---

## High-Level Design

### 1. Shared health model

Create `co_cli/_system_health.py` as the canonical shared health aggregation module.

It should own:

- `CheckItem`
- `SystemHealthResult`
- reusable `check_*` probes for integrations
- reusable provider/model diagnostic probes or adapters that wrap `_model_check.py` logic
- `run_system_health(deps: CoDeps | None = None) -> SystemHealthResult`

`SystemHealthResult` should aggregate:

- integration checks: Google, Obsidian, Brave, MCP, knowledge, skills
- provider/model checks: provider readiness, active reasoning chain, reasoning availability, optional role availability
- tool-surface checks: native tool registration count, MCP tool discovery status, and native-only fallback when applicable

The report must support both:

- no-runtime mode for `co status`
- runtime mode for bootstrap and agent tooling

### 2. Separate startup semantics from diagnostic semantics

Do **not** collapse `run_model_check()` into the runtime tool.

Keep this distinction:

- `run_model_check()` remains a startup gate and may raise `RuntimeError`.
- `run_system_health()` is diagnostic and should never hard-fail normal runtime inspection for expected degradation states.

That means model/provider probes should be shared, but the consequence model should stay separate:

- startup: hard error or warning
- runtime health tool: structured status item

### 3. Tool surface becomes the agent contract

Replace `check_capabilities` with `check_system_health`.

The tool should return:

- `display`
- `checks`
- explicit provider/model fields
- explicit integration summary fields
- session/skill-grant context when useful

The tool should be the single runtime self-check primitive available to the agent.

**Non-negotiable requirement:** `check_system_health` must include model/provider diagnostics, not just non-LLM integrations. The new tool is incomplete if it cannot tell the agent whether:

- Gemini is missing required credentials
- Ollama is unreachable
- no reasoning model is available
- the reasoning chain advanced to a fallback
- optional role chains were disabled or degraded

### 4. `/doctor` becomes a thin UX relay

Keep `/doctor` as a user-facing workflow, but reduce it to prompt guidance:

- call `check_system_health`
- summarize results clearly
- distinguish healthy, degraded, and missing setup

The skill should not encode business logic or duplicate the tool contract.

### 5. Naming alignment

Target naming after refactor:

| Layer | Name |
|------|------|
| Shared module | `_system_health.py` |
| Shared aggregator | `run_system_health()` |
| Agent tool | `check_system_health` |
| User-facing workflow | `/doctor` |

This preserves `/doctor` as user vocabulary while making the internal architecture literal and consistent.

### 6. Concrete result contract

The new shared module should have an explicit result shape so bootstrap, status, and the agent tool all read the same semantics instead of each recomputing them.

Recommended data model:

```python
@dataclass
class CheckItem:
    name: str
    status: Literal["ok", "warn", "error", "skipped"]
    detail: str
    extra: str = ""


@dataclass
class SystemHealthResult:
    checks: list[CheckItem] = field(default_factory=list)
    provider: str = ""
    provider_status: str = ""          # "ok" | "warn" | "error"
    provider_detail: str = ""
    reasoning_status: str = ""         # "ok" | "warn" | "error" | "skipped"
    reasoning_detail: str = ""
    reasoning_models: list[str] = field(default_factory=list)
    optional_role_statuses: dict[str, str] = field(default_factory=dict)
    optional_role_details: dict[str, str] = field(default_factory=dict)
    native_tool_count: int = 0
    mcp_tool_count: int = 0
    mcp_tools_status: str = ""         # "ok" | "warn" | "error" | "skipped"
    mcp_tools_detail: str = ""
    tool_loading_detail: str = ""

    @property
    def has_errors(self) -> bool: ...

    @property
    def has_warnings(self) -> bool: ...

    def by_name(self, name: str) -> CheckItem | None: ...

    def summary_lines(self) -> list[str]: ...
```

Implementation rules:

- `checks` remains the flat list for integration-style items and for display stability.
- provider/model summary fields exist in addition to `checks`; callers should not need to parse English text from `checks` to answer model-health questions.
- `reasoning_models` reflects the configured or currently effective reasoning chain, whichever is more useful diagnostically, but the contract must document which one it is.
- `optional_role_statuses` keys should be limited to: `summarization`, `coding`, `research`, `analysis`.
- tool-loading fields are read-only visibility about the exposed capability surface, not a mandate to execute tools during bootstrap.

### 7. Recommended check naming

To keep output stable and parseable, use consistent check names:

- `google`
- `obsidian`
- `brave`
- `mcp:{name}`
- `knowledge`
- `skills`
- `tool_surface`
- `mcp_tools`
- `reranker` if added in this refactor
- `session` if added in this refactor
- `tasks` if added in this refactor

Model/provider details should also appear in top-level fields, even if mirrored into checks as:

- `provider`
- `reasoning`
- `role:summarization`
- `role:coding`
- `role:research`
- `role:analysis`

If mirrored into `checks`, keep the top-level fields authoritative for tool consumers.

### 8. Probe reuse strategy

To avoid duplicated logic between `_model_check.py`, `_status.py`, and `_system_health.py`, the preferred extraction is:

- keep `PreflightResult` and `run_model_check()` in `_model_check.py`
- add one or two pure helpers that `_system_health.py` can reuse without startup semantics

Recommended helpers:

```python
def check_provider_runtime(
    llm_provider: str,
    gemini_api_key: str | None,
    ollama_host: str,
) -> PreflightResult: ...


def check_model_runtime(
    llm_provider: str,
    ollama_host: str,
    role_models: dict[str, list[ModelEntry]],
) -> PreflightResult: ...
```

These can be thin renames of the current `_check_llm_provider()` and `_check_model_availability()`, or the current names can remain private and `_system_health.py` can import them directly. The important part is that the runtime health layer uses the same probe semantics, not a second implementation.

### 9. Tool payload contract

`check_system_health(ctx)` should return a dict with a stable machine-usable shape.

Recommended minimum payload:

```python
{
    "display": str,
    "checks": [{"name": str, "status": str, "detail": str}],
    "provider": str,
    "provider_status": str,
    "provider_detail": str,
    "reasoning_models": list[str],
    "reasoning_status": str,
    "reasoning_detail": str,
    "optional_roles": {
        "summarization": {"status": str, "detail": str, "models": list[str]},
        "coding": {"status": str, "detail": str, "models": list[str]},
        "research": {"status": str, "detail": str, "models": list[str]},
        "analysis": {"status": str, "detail": str, "models": list[str]},
    },
    "knowledge_backend": str,
    "reranker": str,
    "mcp_count": int,
    "skill_grants": list[str],
}
```

Contract rules:

- `provider_status` and `reasoning_status` must reflect actual health, not just configuration presence.
- `reasoning_models` must not be the only readiness signal.
- `optional_roles` may report configured-but-unhealthy or disabled states; empty model lists are valid.
- `display` is derived from the structured fields, not the source of truth.

### 10. Caller-specific mapping rules

The shared result should be consumed differently at each layer, but without semantic drift:

- **bootstrap**
  - call `run_system_health(deps)`
  - print `summary_lines()`
  - do not abort startup from this layer

- **agent tool**
  - call `run_system_health(ctx.deps)`
  - return full structured payload
  - use top-level fields for model/provider reasoning

- **`co status`**
  - call `run_system_health()` with no deps
  - map to compact `StatusInfo`
  - if runtime-only checks are unavailable, map to compact statuses without inventing health claims

### 11. Compatibility cut decision

This refactor should be a **clean cut**, not a long-lived compatibility layer.

Implementation guidance:

- no alias tool named `check_capabilities` after the refactor is complete
- no production imports from `_doctor.py` after the refactor is complete
- if a temporary shim is used during the patch series, remove it before closing the TODO

This keeps the system vocabulary crisp and avoids carrying two health abstractions.

---

## Missing Checks Inventory

The current implementation does not only miss model/provider runtime diagnostics. It also has several shallow or absent checks that should be evaluated as part of the new system-health contract.

### Priority 1: must include in the unified health tool

These are necessary to close the current self-check gap.

- **Model/provider health**
  - missing now from agent runtime tooling
  - currently only checked pre-agent in `_model_check.py` and partially surfaced by `_status.py`
  - must be included in `check_system_health`

- **Resolved reasoning availability**
  - the runtime tool must report not just configured reasoning models, but whether the active reasoning path is actually usable
  - current `check_capabilities` only reports `reasoning_models` and `reasoning_ready = bool(reasoning_chain)`, which is config-level, not health-level

- **Tool loading status**
  - the unified runtime health surface should report what tool surface is actually exposed
  - at minimum this means native tool count, MCP tool count when discovered, and whether startup degraded to native-only mode
  - this is a capability-surface visibility check, not a per-tool execution probe

### Priority 2: should be added if inexpensive and read-only

These close obvious blind spots in the present health checks without introducing risky side effects.

- **Knowledge backend actual readiness**
  - current check only verifies `knowledge_index is not None`
  - missing:
    - whether configured backend degraded from requested mode
    - whether the index/database is readable
    - whether search is actually operational

- **Reranker readiness**
  - current tool reports configured reranker provider only
  - missing whether the reranker is actually available and usable

- **Session persistence readiness**
  - current bootstrap restores/saves session, but no reusable health probe reports whether session storage is healthy

- **Background task subsystem readiness**
  - no current check for `TaskRunner` presence, task storage availability, or basic runtime readiness

- **Skills load health**
  - current check only counts `skill_registry`
  - missing partial-load failure detection, malformed-skill diagnostics, and distinction between loaded skills vs invocable skills

### Priority 3: keep shallow by default unless explicitly needed

These are valid health dimensions, but full validation may require network calls, startup cost, or behavior that goes beyond cheap diagnostics.

- **MCP connectivity / handshake**
  - current check only verifies remote URL presence or local binary existence
  - missing whether the server is actually reachable or starts successfully

- **Google credential validity**
  - current check only verifies credential presence
  - missing token freshness / actual API usability

- **Brave API key validity**
  - current check only verifies key presence
  - missing whether the key is accepted by the service

- **Obsidian readability**
  - current check only verifies vault path existence
  - missing whether files are actually readable

- **Workspace path accessibility**
  - memory/library/skills/task paths are assumed by runtime code
  - current health checks do not verify read/write accessibility

---

## Design Decisions

### Decision A: include model checks in runtime health

**Adopted.**

Reason:
- the agent needs a first-class way to verify provider/model degradation mid-turn
- `co status` already composes this information outside the agent
- boot and runtime should not drift on what "system health" means

### Decision B: keep model startup gating separate

**Adopted.**

Reason:
- provider/model readiness determines whether the agent can exist at all
- startup preflight and runtime introspection have different failure semantics
- using one implementation layer for probes is good; using one consequence model is not

### Decision C: rename tool to `check_system_health`, not `call_doctor`

**Adopted.**

Reason:
- tool names should describe the capability, not the invocation
- `call_doctor` is metaphorical and weakly typed
- `check_system_health` matches the intended runtime contract

### Decision D: keep `/doctor`

**Adopted.**

Reason:
- `/doctor` is useful as a stable user-facing affordance
- it is appropriate as workflow UX, even if the underlying primitive is `check_system_health`

---

## Implementation Plan

### TASK-1: Introduce `co_cli/_system_health.py`

**files:** `co_cli/_system_health.py`

**done_when:** New module exists and owns the shared health data model and aggregation entrypoint. At minimum it contains `CheckItem`, `SystemHealthResult`, integration probe functions migrated from `_doctor.py`, and `run_system_health(deps: CoDeps | None = None)`. The module docstring describes it as the shared health engine for bootstrap, status, and agent runtime introspection. `SystemHealthResult` exposes explicit top-level provider/model health fields in addition to the generic `checks` list.

**prerequisites:** none

---

### TASK-2: Fold non-blocking model/provider diagnostics into shared runtime health

**files:** `co_cli/_system_health.py`, `co_cli/_model_check.py`

**done_when:** `run_system_health()` includes provider/model diagnostic output using shared probe logic from `_model_check.py` or extracted helpers reused by both modules. Runtime system health can report all of the following without raising for expected degradation: missing Gemini key, Ollama unreachable, no reasoning model available, reasoning chain advanced, optional role chains disabled. Startup `run_model_check()` still raises on hard failures exactly as before. This task is not complete unless these model/provider statuses are visible through the shared result object and consumable by the tool layer. `_status.py` no longer needs to call `_check_llm_provider()` or `_check_model_availability()` directly.

**prerequisites:** TASK-1

---

### TASK-3: Rename agent tool to `check_system_health`

**files:** `co_cli/tools/capabilities.py` or replacement file, `co_cli/agent.py`, any imports/callers

**done_when:** The agent-facing read-only health tool is named `check_system_health`. Tool registration in the agent uses the new name. The tool delegates to `run_system_health(ctx.deps)` and returns a structured payload that includes integration checks, provider/model health fields, and tool-surface loading fields. At minimum the payload exposes structured status for provider readiness, reasoning readiness, reasoning chain status, optional role degradation, native tool count, and MCP tool discovery/degraded-native-only status. The returned dict follows the tool payload contract in this doc. No runtime code still depends on the old `check_capabilities` symbol.

**prerequisites:** TASK-2

---

### TASK-4: Make `/doctor` a thin relay to `check_system_health`

**files:** `co_cli/skills/doctor.md`

**done_when:** The doctor skill explicitly instructs the agent to call `check_system_health` and summarize the result. Skill text no longer refers to `check_capabilities`. The skill contains presentation guidance only, not duplicated health logic.

**prerequisites:** TASK-3

---

### TASK-5: Update startup and status callers to shared system-health module

**files:** `co_cli/_bootstrap.py`, `co_cli/_status.py`, `co_cli/main.py` if imports shift

**done_when:** Bootstrap Step 4 uses `run_system_health(deps)` from `_system_health.py`. `get_status()` uses the same shared module for integrations and model/provider health rather than composing doctor + model checks separately. No production caller imports `_doctor.py`. `co status` still renders the same high-level table shape unless an intentional UX change is explicitly included in this refactor.

**prerequisites:** TASK-2

---

### TASK-6: Remove or replace `_doctor.py`

**files:** `co_cli/_doctor.py`, import sites

**done_when:** `_doctor.py` is deleted, or replaced with a temporary compatibility shim that imports from `_system_health.py` and is marked for removal. Preferred end state: no references to `_doctor.py` remain in production code, tests, or docs.

**prerequisites:** TASK-5

---

### TASK-7: Expand direct functional coverage for shared health aggregation

**files:** `tests/test_system_health.py` (new), existing affected tests

**done_when:** There is direct test coverage for:

- Google credential precedence
- Obsidian `skipped` vs `warn` vs `ok`
- Brave configured vs skipped
- MCP remote URL vs found binary vs missing command
- runtime-only knowledge and skills checks
- provider/model runtime diagnostics for Gemini and Ollama cases
- config-present vs actually-healthy distinction for reasoning/model status
- tool-surface loading visibility, including native count and MCP discovery/degraded-native-only cases
- `SystemHealthResult` helper methods and summary formatting
- tool payload from `check_system_health`

Additional desired coverage if implemented in this refactor:

- reranker readiness reporting
- session persistence readiness
- task subsystem readiness
- richer skill load status beyond raw count

No coverage depends solely on indirect caller tests for the shared health engine.

**prerequisites:** TASK-3, TASK-5

---

### TASK-8: Update docs to the new system-health contract

**files:** `docs/DESIGN-doctor.md`, `docs/DESIGN-system-bootstrap.md`, `docs/DESIGN-llm-models.md`, `docs/DESIGN-tools-execution.md`, `docs/DESIGN-index.md`, any review docs that mention old names

**done_when:** Docs consistently describe:

- `_system_health.py` as the shared health engine
- `check_system_health` as the runtime introspection tool
- `/doctor` as the user-facing skill/workflow
- `run_model_check()` as the pre-agent fail-fast gate

`DESIGN-doctor.md` is either renamed/merged to match the new abstraction or rewritten so its scope is accurate. No doc claims that doctor is the agent tool or that runtime health excludes model/provider status unless that is still intentionally true.

**prerequisites:** TASK-6

---

## Risks

### Risk 1: startup and runtime semantics accidentally collapse

If the implementation reuses `run_model_check()` directly inside runtime tooling, the tool may raise where it should report degradation. The shared layer must reuse probe logic, not startup consequence logic.

### Risk 2: naming churn without surface simplification

If `_doctor.py` is renamed but the tool contract remains partial or confusing, the repo will incur churn without solving the agent self-check gap.

### Risk 3: `co status` drift

If `co status` continues bespoke composition while bootstrap/tooling move to `_system_health.py`, health reporting will diverge again. The refactor should converge callers, not multiply them.

---

## Acceptance Criteria

- The only blocking pre-agent check remains `run_model_check()`.
- The agent has one read-only self-check tool: `check_system_health`.
- `/doctor` is a thin user-facing workflow that steers to `check_system_health`.
- Shared health aggregation includes both integration state and provider/model diagnostics.
- Shared health aggregation includes tool-surface loading visibility without performing per-tool bootstrap execution.
- The health tool distinguishes configured model state from actually-healthy model state.
- Bootstrap, status, and agent runtime introspection all use the same shared health engine.
- `_doctor.py` is no longer the canonical internal module name.

---

## Open Questions

- Whether `_status.py` should expose every detailed runtime health field or continue mapping to a compact `StatusInfo` summary while sourcing from `run_system_health()`.
- Whether `DESIGN-doctor.md` should remain as a named doc for user vocabulary reasons, or be renamed to a broader system-health design doc.
