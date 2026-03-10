# TODO: Unified Capabilities Surface (`_capabilities`, `check_capabilities`, `/doctor`)

**Slug:** `capabilities-surface`
**Type:** Refactor + behavior extension

---

## Context

### Current capability introspection is split and incomplete

The repository currently exposes runtime capability state through several disconnected paths:

- `co_cli/_model_check.py` owns provider/model preflight gating before agent creation.
- `co_cli/_doctor.py` owns non-LLM integration checks.
- `co_cli/tools/capabilities.py` exposes a partial agent-facing runtime tool, `check_capabilities`.
- `co_cli/main.py` computes tool availability locally via `tool_names` and `_discover_mcp_tools(...)`.
- `deps.session.skill_registry` stores skill availability, but there is no equivalent runtime registry for tools.

This creates three concrete problems:

- the agent cannot inspect the same provider/model state that startup and `co status` can inspect
- the agent can inspect skill surface state, but not the full tool surface actually exposed at runtime
- callers do not share one canonical capability model, so bootstrap, `co status`, `/doctor`, and mid-turn tool reasoning can drift

### Current naming is backwards

The present internal and external naming stack is inconsistent:

- `_doctor.py` is an implementation name, not a capability abstraction
- `check_capabilities` sounds broader than what it currently returns
- `/doctor` is user-facing workflow UX

The converged design should use `capabilities` as the canonical abstraction:

- internal shared module: `_capabilities.py`
- shared aggregator: `get_capabilities(...)`
- agent tool: `check_capabilities`
- user-facing workflow: `/doctor`

This is preferable to `capability_surface` because the shorter name is already established in the repo, is clearer in code, and matches the user-facing concept without extra abstraction noise.

---

## Outcome

Introduce a single shared capabilities engine that produces one runtime-facing view of what the system can do right now.

That capabilities view must combine:

- model/provider readiness
- integration readiness
- tool surface state
- skill surface state

The unified capabilities model must be usable by:

- bootstrap startup reporting
- `co status`
- the agent via `check_capabilities`
- `/doctor`

Startup fail-fast semantics remain in `run_model_check()`. The capabilities layer is diagnostic and must not replace startup gating.

---

## Scope

**In scope:**

- replace `_doctor.py` with a broader `_capabilities.py` module
- keep `check_capabilities` as the canonical agent-facing runtime introspection tool
- define one shared capabilities result model consumed by bootstrap, status, and the tool layer
- add a shared runtime tool registry / tool-surface snapshot
- keep `skills` and `tools` as separate sub-surfaces inside one unified capabilities result
- expose three simple tool axes: provenance, approval, and health
- preserve startup fail-fast model gating in `run_model_check()`
- update docs and tests to the converged capabilities vocabulary

**Out of scope:**

- per-tool execution probes during bootstrap
- adding new integrations beyond the current system-health / doctor surface
- reworking unrelated command architecture
- changing provider/model selection policy
- inventing a deep tool taxonomy beyond what drives runtime decisions

---

## Design

### 1. Top-level abstraction: `capabilities`

The top-level abstraction should be named `capabilities`, not `capability_surface`.

Reason:

- the repo already uses `check_capabilities`
- the shorter name is easier to read in code and docs
- the object is not a rendering concern; it is the canonical capability model

Target names:

| Layer | Name |
|------|------|
| Shared module | `_capabilities.py` |
| Shared aggregator | `get_capabilities(...)` |
| Agent tool | `check_capabilities` |
| User-facing workflow | `/doctor` |

### 2. One unified capabilities model, two explicit sub-surfaces

The shared capabilities result should contain two parallel runtime surfaces:

- `tools`
- `skills`

This avoids the current asymmetry where skills are first-class runtime state but tools are only a local list in `main.py`.

Design rule:

- tools and skills belong to the same top-level capabilities model
- they remain different object kinds
- skills are not flattened into tools
- skills may reference tool grants or tool dependencies, but tool availability remains authoritative

### 3. Tool design: tiered enough to drive runtime behavior

Tool tiering should stay intentionally small.

Do not model five layers. Instead, each tool record should expose three orthogonal axes:

- `provenance`: `native` | `external`
- `approval`: `gated` | `not_gated`
- `health`: `healthy` | `degraded` | `unavailable`

Optional metadata may include:

- `source`: native module path or MCP server name
- `server_name`: MCP server name when applicable
- `discovered`: whether the tool was actually discovered at runtime
- `reason`: human-readable health explanation

Why this is enough:

- provenance drives trust and ownership semantics
- approval drives execution policy
- health drives planning and fallback

If a new distinction does not change runtime behavior, it should not be its own tier.

### 4. Skill design: parallel runtime surface

Skills should follow the same runtime-surface philosophy as tools:

- represent what is actually available now
- record whether a skill is invocable by the user and/or the model
- surface degradation or load problems

But skills are not tools. Recommended skill fields:

- `name`
- `user_invocable: bool`
- `model_invocable: bool`
- `health: "healthy" | "degraded" | "unavailable"`
- `reason: str`
- `allowed_tools: list[str]`

### 5. Health semantics

Health semantics should be simple and shared across the capabilities system.

Definitions:

- `healthy`: expected to work as designed
- `degraded`: available with fallback, reduced fidelity, or partial functionality
- `unavailable`: not usable right now

Examples:

- MCP startup failed and the session continued native-only -> external tool surface is `degraded`
- knowledge index unavailable and grep fallback is active -> knowledge capability is `degraded`
- configured MCP server binary missing -> corresponding external capability is `unavailable`
- optional model role chain removed but session still functions -> that role is `degraded` or `unavailable`, depending on whether a fallback path remains

### 6. Fallback policy semantics

Fallback policy is not a separate taxonomy. It is a rule attached to degraded or unavailable capabilities.

Definition:

- fallback policy describes what the system does when a preferred capability is not fully healthy

Examples in this repo:

- MCP unavailable -> continue with native tools only
- FTS/hybrid unavailable -> continue with grep fallback
- optional role chain unavailable -> continue with the remaining configured roles, or use fallback model resolution where already defined

Design rule:

- the capabilities engine reports the resulting runtime state after fallback
- startup and callers may also include a short reason describing the fallback taken

### 7. Runtime tool registry is required

The converged design requires a shared runtime tool registry or snapshot.

Without that, tool-surface health is not representable from shared state because:

- native tool names are returned from `get_agent(...)`
- MCP tool discovery happens after agent startup in `_discover_mcp_tools(...)`
- fallback to native-only mode currently lives only in `main.py`

The design must therefore add shared runtime state.

Recommended shape:

```python
@dataclass
class ToolCapability:
    name: str
    provenance: Literal["native", "external"]
    approval: Literal["gated", "not_gated"]
    health: Literal["healthy", "degraded", "unavailable"]
    source: str = ""
    server_name: str = ""
    discovered: bool = True
    reason: str = ""


@dataclass
class SkillCapability:
    name: str
    user_invocable: bool
    model_invocable: bool
    health: Literal["healthy", "degraded", "unavailable"]
    reason: str = ""
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class Capabilities:
    checks: list[CheckItem] = field(default_factory=list)

    provider: str = ""
    provider_health: Literal["healthy", "degraded", "unavailable"] = "healthy"
    provider_reason: str = ""

    reasoning_health: Literal["healthy", "degraded", "unavailable"] = "healthy"
    reasoning_reason: str = ""
    reasoning_models: list[str] = field(default_factory=list)

    optional_roles: dict[str, dict[str, Any]] = field(default_factory=dict)

    tools: list[ToolCapability] = field(default_factory=list)
    skills: list[SkillCapability] = field(default_factory=list)

    native_tool_count: int = 0
    external_tool_count: int = 0
    healthy_tool_count: int = 0
    degraded_tool_count: int = 0
    unavailable_tool_count: int = 0

    healthy_skill_count: int = 0
    degraded_skill_count: int = 0
    unavailable_skill_count: int = 0

    mcp_mode: Literal["none", "connected", "degraded_native_only"] = "none"
    mcp_reason: str = ""

    @property
    def has_errors(self) -> bool: ...

    @property
    def has_warnings(self) -> bool: ...

    def by_name(self, name: str) -> CheckItem | None: ...

    def summary_lines(self) -> list[str]: ...
```

Recommended storage:

- add runtime capability state to `CoRuntimeState`
- populate it in `main.py` after `get_agent(...)`, after MCP startup/fallback, and after `_discover_mcp_tools(...)`
- keep the snapshot authoritative for bootstrap, the tool layer, and status mapping

### 8. Shared result contract rules

Implementation rules:

- `checks` remains a flat display-friendly check list
- top-level structured fields are authoritative for machine consumers
- `tools` and `skills` are authoritative sub-surfaces
- counts are denormalized convenience fields
- capability health reflects actual runtime state after fallback, not only configured intent

### 9. Model/provider semantics

The capabilities engine should reuse model/provider probe logic from `_model_check.py`, but it must not blindly reuse startup interpretation.

Important distinction:

- active provider health is not the same thing as optional cross-provider feature availability

Example:

- an Ollama session without a Gemini key should not mark the active provider as `degraded` solely because Gemini-dependent features are unavailable

Recommended reporting split:

- `provider_health` describes the currently selected provider
- cross-provider optional feature gaps may appear in `checks` or supplementary fields, but must not poison active provider health

### 10. Caller behavior

#### Bootstrap

- call `get_capabilities(deps)`
- emit `summary_lines()`
- never abort from this layer
- include skill and tool surface status in startup visibility

#### `check_capabilities`

- call `get_capabilities(ctx.deps)`
- return the full structured result
- `display` is derived from the structured result
- the tool is the single runtime self-check primitive for the agent

#### `co status`

- call the same shared capabilities engine
- when runtime-only fields are unavailable, map them to compact “not available in static mode” semantics instead of inventing claims
- if the design chooses to support a runtime snapshot only, `co status` should explicitly say when it is showing configured/static state rather than live runtime state

### 11. Check naming

Keep stable flat check names for summary formatting:

- `provider`
- `reasoning`
- `role:summarization`
- `role:coding`
- `role:research`
- `role:analysis`
- `google`
- `obsidian`
- `brave`
- `mcp:{name}`
- `knowledge`
- `tools`
- `skills`
- `session` if added
- `tasks` if added

### 12. Why this benefits agentic flow

This design is aligned with frontier agent best practice because the agent plans over a live capability model rather than over static assumptions.

Concrete benefits:

- the agent can route around unavailable or degraded external tools
- the agent can distinguish provider/model degradation from tool-surface degradation
- the agent can understand both workflow affordances (`skills`) and executable affordances (`tools`) from one shared model
- `/doctor`, bootstrap, and `co status` stop disagreeing about what is available

This is intentionally not a per-tool execution harness. Functional tool correctness remains a test concern.

---

## Refactoring Spec

### New module

Create:

- `co_cli/_capabilities.py`

This module becomes the single source of truth for:

- capability checks
- capability result data models
- capability summary formatting
- runtime capability aggregation

### Runtime state additions

Add a runtime capability snapshot to `CoRuntimeState`.

Recommended field:

```python
capabilities: Capabilities | None = None
```

Or, if avoiding import cycles:

```python
capabilities_snapshot: Any = field(default=None, repr=False)
```

Preferred end state is a typed field.

### Main-loop population points

`main.py` must populate the runtime capability snapshot at these points:

1. after `get_agent(...)` returns native tool names and approval flags
2. after MCP startup succeeds or fallback to native-only occurs
3. after `_discover_mcp_tools(...)` completes
4. after skills load or skills reload changes `skill_registry`

Design requirement:

- the runtime snapshot must stay current enough that `check_capabilities` can trust it mid-turn

### Tool surface derivation

Native tools:

- derive from `get_agent(...)` returned `tool_names`
- derive approval from `tool_approval`
- mark `provenance="native"`

External tools:

- derive from discovered MCP tool names
- attach `server_name` when known
- mark `provenance="external"`
- if MCP startup failed and the system continued without MCP, record `mcp_mode="degraded_native_only"`

### Skill surface derivation

Skill surface should derive from the loaded skill objects plus `deps.session.skill_registry`.

Recommended logic:

- loaded and user/model invocable -> `healthy`
- loaded but hidden from model or user by config -> still available, but mark invocability explicitly
- failed to load or malformed -> `unavailable` or `degraded`, depending on whether the skill set as a whole remains usable

### Compatibility decision

Keep `check_capabilities` as the stable tool name.

Reason:

- it already exists
- it matches the refined top-level concept
- changing it to `check_system_health` now would add churn while the design has converged on broader `capabilities`, not just health

`/doctor` remains the workflow affordance that tells the agent to call `check_capabilities`.

---

## Implementation Plan

### TASK-1: Introduce the shared capabilities engine

**files:** `co_cli/_capabilities.py`

**done_when:** New module exists and owns `CheckItem`, `ToolCapability`, `SkillCapability`, `Capabilities`, and `get_capabilities(...)`. The module docstring describes it as the shared runtime capabilities engine for bootstrap, status, and agent introspection.

**prerequisites:** none

---

### TASK-2: Add runtime capability snapshot state

**files:** `co_cli/deps.py`

**done_when:** `CoRuntimeState` can store a capability snapshot representing the currently-exposed tools and skills surfaces plus provider/integration state. The field is safe for bootstrap and mid-turn reads.

**prerequisites:** TASK-1

---

### TASK-3: Populate tool surface state in the main loop

**files:** `co_cli/main.py`, possibly `co_cli/agent.py`

**done_when:** Native tool registration, MCP startup result, MCP discovery result, and native-only fallback state are persisted into the runtime capability snapshot. Tool records include provenance, approval, and health. The design no longer relies on a local-only `tool_names` list as the sole source of truth for runtime tool surface state.

**prerequisites:** TASK-2

---

### TASK-4: Populate skill surface state in the shared capabilities engine

**files:** `co_cli/_capabilities.py`, `co_cli/main.py`, skill loading code if needed

**done_when:** Skills are represented as a first-class surface inside `Capabilities`. The engine can report loaded, invocable, degraded, or unavailable skills without conflating them with tools.

**prerequisites:** TASK-2

---

### TASK-5: Fold model/provider diagnostics into capabilities

**files:** `co_cli/_capabilities.py`, `co_cli/_model_check.py`

**done_when:** `get_capabilities(...)` reports selected-provider health, reasoning health, and optional role degradation using shared probe logic from `_model_check.py` without inheriting startup fail-fast behavior. The active-provider semantics are explicitly correct for Ollama-without-Gemini-key cases.

**prerequisites:** TASK-1

---

### TASK-6: Replace `_doctor.py` usage with the shared capabilities engine

**files:** `co_cli/_bootstrap.py`, `co_cli/_status.py`, `co_cli/tools/capabilities.py`, import sites

**done_when:** Bootstrap, `co status`, and the `check_capabilities` tool all source their data from `_capabilities.py`. No production path still relies on `_doctor.py` as the canonical health abstraction.

**prerequisites:** TASK-3, TASK-4, TASK-5

---

### TASK-7: Keep `/doctor` as a thin UX relay

**files:** `co_cli/skills/doctor.md`

**done_when:** The skill explicitly tells the agent to call `check_capabilities` and summarize the result. The skill contains presentation guidance only, not duplicated capability logic.

**prerequisites:** TASK-6

---

### TASK-8: Expand direct functional coverage

**files:** `tests/test_capabilities.py`, `tests/test_agent.py`, new `tests/test_capabilities_engine.py`, other affected tests

**done_when:** There is direct coverage for:

- provider and reasoning health across Gemini and Ollama cases
- native tool registration reflected in the shared capabilities engine
- MCP discovery success and native-only fallback reflected in tool records
- tool approval metadata reflected in tool records
- skills surface reflected separately from tool surface
- counts and helper properties on `Capabilities`
- `check_capabilities` tool payload and `display`
- `co status` mapping behavior when runtime-only capability details are unavailable

**prerequisites:** TASK-6

---

### TASK-9: Rewrite docs to the converged capabilities vocabulary

**files:** `docs/DESIGN-doctor.md`, `docs/DESIGN-system-bootstrap.md`, `docs/DESIGN-tools.md`, `docs/DESIGN-skills.md`, `docs/DESIGN-index.md`, review docs that mention doctor/system-health naming

**done_when:** Docs consistently describe:

- `capabilities` as the top-level shared abstraction
- `tools` and `skills` as separate capability sub-surfaces
- `check_capabilities` as the runtime introspection tool
- `/doctor` as user-facing workflow UX
- `run_model_check()` as startup fail-fast gating, distinct from capability introspection

**prerequisites:** TASK-6

---

## Acceptance Criteria

- There is one shared capabilities engine used by bootstrap, `co status`, and the agent tool.
- `capabilities` is the canonical abstraction name; `capability_surface` is not introduced.
- `check_capabilities` remains the agent-facing runtime introspection tool.
- The capabilities result contains both `tools` and `skills` as separate sub-surfaces.
- Tool metadata is intentionally small: provenance, approval, and health.
- Tool-surface state is stored in shared runtime state rather than being reconstructable only from local variables in `main.py`.
- Startup fail-fast semantics remain in `run_model_check()`.
- `/doctor` is a thin prompt relay over `check_capabilities`.
- No bootstrap path performs per-tool execution probes.

---

## Risks

### Risk 1: capability scope expands into a pseudo-orchestrator

If `_capabilities.py` starts owning unrelated startup behavior, the module will become another orchestration hub instead of a shared introspection layer.

### Risk 2: tool surface remains local-only despite the new contract

If runtime tool state is not persisted into shared state, the design will claim unified capabilities while continuing to rely on caller-local reconstruction.

### Risk 3: active-provider semantics are polluted by optional feature checks

If missing Gemini credentials are treated as active-provider degradation during Ollama sessions, the capability model will mislead the agent.

### Risk 4: tools and skills are flattened together

If the implementation collapses skills into the tool list, the resulting model will be harder for both code and the agent to reason about.

---

## Open Questions

- Should `co status` show only compact capability summaries, or should it expose explicit tools/skills counts and degraded counts?
- Should malformed or hidden skills appear in the capabilities result as `unavailable`, or should hidden skills simply be omitted from the surfaced skill list?
- Should the runtime capability snapshot be rebuilt on every skill reload and MCP rediscovery event, or should it be incrementally updated in place?
