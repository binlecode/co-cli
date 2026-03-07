# TODO: CoDeps Refactor

Task type: design + implementation

## Context

Source review:
- local code inspection of `co_cli/deps.py`, `co_cli/agent.py`, `co_cli/_orchestrate.py`, `co_cli/tools/shell.py`
- local `pydantic-ai` repo updated to HEAD `20d29857` on 2026-03-06 for comparison
- current `pydantic-ai` docs reviewed: `dependencies.md`, `deferred-tools.md`, `message-history.md`

**Current design summary**

`CoDeps` is the declared `deps_type` for the main agent and all native tools use
`RunContext[CoDeps]`. That part is correct and idiomatic.

The problem is not the use of a dataclass. The problem is that the dataclass no longer
has a single responsibility.

Today `CoDeps` mixes four concern classes:

1. **Injected services/resources**
   - `shell`
   - `knowledge_index`
   - `task_runner`
2. **Injected runtime configuration**
   - API keys
   - path settings
   - tool limits
   - model/context limits
3. **Mutable session state**
   - `auto_approved_tools`
   - `active_skill_env`
   - `active_skill_allowed_tools`
   - `drive_page_tokens`
   - `session_todos`
4. **Mutable orchestration / processor state**
   - `precomputed_compaction`
   - `turn_usage`
   - `_opening_ctx_state`
   - `_safety_state`

This makes `ctx.deps` semantically unclear:
- sometimes it means dependency injection
- sometimes it means session state
- sometimes it means orchestration scratch space
- sometimes it means processor-private internals

That ambiguity is the core design flaw.

## Problem & Outcome

**Problem**

The current `CoDeps` shape is messy and not clear because it acts as a general runtime
state bag instead of a focused dependency container. This has three concrete costs:

1. **Unclear ownership**
   - tools, history processors, slash commands, and orchestration all mutate the same object
   - behavior depends on prior mutations not obvious from the tool signature

2. **Weak conceptual boundaries**
   - injected dependencies and mutable state are collapsed together
   - internal processor state is stored on the same object exposed to every tool

3. **Sub-agent copy complexity**
   - `make_subagent_deps()` must manually reset selected mutable fields
   - each new mutable field increases the chance of accidental state bleed

**Outcome**

After this refactor:

- `RunContext[CoDeps]` remains the stable pydantic-ai dependency type.
- `CoDeps` becomes a clearly layered container rather than a dumping ground.
- injected services/config are separated from mutable session/runtime state.
- processor-private state is no longer hidden as ad hoc underscore fields on the main deps bag.
- sub-agent isolation becomes explicit and mechanically safer.
- tool code becomes easier to read because field location encodes meaning.

## Pydantic-AI Idiomatic Target

The refactor must follow current `pydantic-ai` best practice, not invent a framework on top
of it.

Relevant idioms from current `pydantic-ai`:

- `deps_type` should be a normal Python type, commonly a dataclass.
- `RunContext[Deps]` is the typed access path for tools, instructions, and validators.
- dependencies should primarily represent data, services, and connections needed during the run.
- deferred approval state should flow through `DeferredToolRequests` / `DeferredToolResults`,
  not through custom parallel abstractions.
- message history and orchestration remain outside `deps` unless a dependency is truly needed by tools.

**Implication for co-cli**

We should keep:
- `CoDeps` as the top-level dependency type
- `RunContext[CoDeps]` for tools and instructions
- direct Python dataclasses rather than introducing a config registry or service locator

We should avoid:
- turning `CoDeps` into a global mutable runtime store
- keeping processor-private scratch fields directly on `CoDeps`
- creating a heavy wrapper layer that obscures normal `ctx.deps` usage

## Scope

In scope:
- refactor `CoDeps` into explicit nested responsibility groups
- move mutable processor/orchestration state out of the top-level flat deps bag
- update tools, orchestration, commands, and processors to use the new shape
- tighten approval/session state ownership where it currently lives in deps
- update design docs to match the new structure
- add focused regression tests for state ownership and sub-agent isolation

Out of scope:
- redesigning the overall tool set
- changing `pydantic-ai` integration style away from `RunContext[CoDeps]`
- redesigning memory lifecycle semantics
- redesigning approval UX beyond the ownership changes needed for the refactor

## High-Level Design

Keep `CoDeps` as the single `deps_type`, but make it a small composition root.

Proposed structure:

```python
@dataclass
class CoServices:
    shell: ShellBackend
    knowledge_index: Any | None = None
    task_runner: Any | None = None


@dataclass
class CoConfig:
    session_id: str = ""
    obsidian_vault_path: Path | None = None
    google_credentials_path: str | None = None
    shell_safe_commands: list[str] = field(default_factory=list)
    shell_max_timeout: int = 600
    exec_approvals_path: Path = ...
    skills_dir: Path = ...
    memory_dir: Path = ...
    library_dir: Path = ...
    gemini_api_key: str | None = None
    brave_search_api_key: str | None = None
    web_fetch_allowed_domains: list[str] = field(default_factory=list)
    web_fetch_blocked_domains: list[str] = field(default_factory=list)
    web_policy: WebPolicy = field(default_factory=WebPolicy)
    web_http_max_retries: int = 2
    web_http_backoff_base_seconds: float = 1.0
    web_http_backoff_max_seconds: float = 8.0
    web_http_jitter_ratio: float = 0.2
    memory_max_count: int = 200
    memory_dedup_window_days: int = 7
    memory_dedup_threshold: int = 85
    memory_recall_half_life_days: int = 30
    memory_consolidation_top_k: int = 5
    memory_consolidation_timeout_seconds: int = 20
    personality: str | None = None
    personality_critique: str = ""
    max_history_messages: int = 40
    tool_output_trim_chars: int = 2000
    summarization_model: str = ""
    doom_loop_threshold: int = 3
    max_reflections: int = 3
    knowledge_search_backend: str = "fts5"
    knowledge_reranker_provider: str = "local"
    mcp_count: int = 0
    approval_risk_enabled: bool = False
    approval_auto_low_risk: bool = False
    model_roles: dict[str, list[str]] = field(default_factory=dict)
    ollama_host: str = "http://localhost:11434"
    llm_provider: str = ...
    ollama_num_ctx: int = ...
    ctx_warn_threshold: float = ...
    ctx_overflow_threshold: float = ...


@dataclass
class CoSessionState:
    google_creds: Any | None = field(default=None, repr=False)
    google_creds_resolved: bool = False
    auto_approved_tools: set[str] = field(default_factory=set)
    active_skill_env: dict[str, str] = field(default_factory=dict)
    active_skill_allowed_tools: set[str] = field(default_factory=set)
    drive_page_tokens: dict[str, list[str]] = field(default_factory=dict)
    session_todos: list[dict] = field(default_factory=list)
    skill_registry: list[dict] = field(default_factory=list)


@dataclass
class CoRuntimeState:
    precomputed_compaction: Any = field(default=None, repr=False)
    turn_usage: RunUsage | None = None
    opening_ctx_state: Any = field(default=None, repr=False)
    safety_state: Any = field(default=None, repr=False)


@dataclass
class CoDeps:
    services: CoServices
    config: CoConfig
    session: CoSessionState = field(default_factory=CoSessionState)
    runtime: CoRuntimeState = field(default_factory=CoRuntimeState)
```

This preserves a single dependency object for `pydantic-ai` while restoring meaning:

- `ctx.deps.services` = injected capabilities
- `ctx.deps.config` = injected run configuration
- `ctx.deps.session` = mutable user/session state
- `ctx.deps.runtime` = orchestration and processor transient state

## Why This Design Is Idiomatic

This is still idiomatic `pydantic-ai` because:

- the agent still receives one normal dataclass as `deps_type`
- tools still use normal typed access via `RunContext[CoDeps]`
- no custom DI layer is introduced
- no service locator API is introduced
- no framework abstraction hides ordinary Python access

This refactor improves idiomaticity because it narrows what each part of `deps` means.
The goal is not “more abstraction”; the goal is “clearer dependency semantics”.

## Detailed Refactoring Logic

### Design rule 1: Top-level `CoDeps` is a composition root, not a state bag

Top-level `CoDeps` should contain only named responsibility groups.

It should not contain:
- ad hoc scalar config fields
- private underscore processor state
- mutable session caches mixed with service handles

It should contain:
- `services`
- `config`
- `session`
- `runtime`

### Design rule 2: `config` is for injected inputs, not mutable state

`CoConfig` holds values that are determined at session/bootstrap time and used read-only
throughout the run.

Examples:
- API keys
- path configuration
- model/context limits
- policy settings
- memory thresholds

`config` should not contain:
- pagination tokens
- approval grants
- tool execution results
- compaction caches

### Design rule 3: `session` is mutable, tool-visible state

`CoSessionState` is for mutable state that tools or slash commands legitimately need to
read or write during the session.

Examples:
- cached Google credentials
- approval grants
- active skill grants/env
- todo list
- pagination tokens

This is still runtime state, but it is state whose consumers include tools.

### Design rule 4: `runtime` is mutable, orchestration-owned state

`CoRuntimeState` is for state owned by orchestration or processors, not by ordinary tools.

Examples:
- safety processor state
- opening context processor state
- precomputed compaction payload
- turn usage accumulator if retained

Tools should not normally reach into `ctx.deps.runtime`.
If they do, that is a design smell and should be justified explicitly.

### Design rule 5: remove underscore-private fields from `CoDeps`

Current fields:
- `_opening_ctx_state`
- `_safety_state`

These should move to:
- `ctx.deps.runtime.opening_ctx_state`
- `ctx.deps.runtime.safety_state`

Reason:
- underscore names on the shared deps object imply “private” but are not actually private
- they hide important design ownership
- sub-agent copy/reset logic becomes easier when state is grouped explicitly

### Design rule 6: sub-agent copying should copy groups intentionally

`make_subagent_deps()` should become a group-aware constructor rather than a broad
`dataclasses.replace()` with scattered field resets.

Target behavior:

- `services`:
  - share service handles when safe (`shell`, `knowledge_index`)
  - share read-only runner references only if intended
- `config`:
  - copy as-is
- `session`:
  - start with clean mutable state by default
  - optionally inherit specific values only when explicitly safe
- `runtime`:
  - always reset

This turns sub-agent isolation from “remember every mutable field” into “reset the mutable groups”.

## Implementation Plan

### TASK-1: Introduce grouped dependency dataclasses

files:
- `co_cli/deps.py`

Changes:
- add `CoServices`
- add `CoConfig`
- add `CoSessionState`
- add `CoRuntimeState`
- rewrite `CoDeps` to contain those groups
- rewrite `make_subagent_deps()` to copy/reset by group

Implementation notes:
- keep type hints explicit
- preserve current field defaults where possible
- do not change tool behavior in this task beyond field relocation

done_when:
- `CoDeps` contains grouped fields instead of the current large flat field list
- no underscore-private processor fields remain on top-level `CoDeps`
- `make_subagent_deps()` no longer relies on resetting a long list of top-level mutable fields

### TASK-2: Update dependency construction in `main.py`

files:
- `co_cli/main.py`

Changes:
- rewrite `create_deps()` to build `CoServices`, `CoConfig`, `CoSessionState`, `CoRuntimeState`
- initialize runtime processor state in `deps.runtime`, not on top-level deps
- keep session bootstrap behavior identical

Implementation notes:
- `settings` are read once here, as today
- no tool should import `settings`
- `knowledge_index` stays injected as a service

done_when:
- `create_deps()` constructs grouped deps cleanly
- session-start bootstrap still works
- no top-level field assignments like `deps._opening_ctx_state = ...` remain

### TASK-3: Migrate tools to explicit group access

files:
- all tool modules under `co_cli/tools/`
- any helper modules that access `ctx.deps`

Changes:
- replace flat access with grouped access

Examples:
- `ctx.deps.shell` → `ctx.deps.services.shell`
- `ctx.deps.shell_max_timeout` → `ctx.deps.config.shell_max_timeout`
- `ctx.deps.exec_approvals_path` → `ctx.deps.config.exec_approvals_path`
- `ctx.deps.google_creds` → `ctx.deps.session.google_creds`
- `ctx.deps.memory_dir` → `ctx.deps.config.memory_dir`
- `ctx.deps.knowledge_index` → `ctx.deps.services.knowledge_index`

Implementation notes:
- use this migration to make ownership obvious
- if a tool reaches into `runtime`, stop and verify that it really should

done_when:
- all tools compile with grouped access
- each mutable field reference is located in either `session` or `runtime`, never ambiguously at top level

### TASK-4: Migrate orchestration and history processors

files:
- `co_cli/_orchestrate.py`
- `co_cli/_history.py`
- any helper modules touching processor/safety state

Changes:
- replace top-level state access with `deps.runtime.*`
- keep approval/session state in `deps.session.*`
- keep policy/config reads in `deps.config.*`

Critical mappings:
- `deps._safety_state` → `deps.runtime.safety_state`
- `deps._opening_ctx_state` → `deps.runtime.opening_ctx_state`
- `deps.precomputed_compaction` → `deps.runtime.precomputed_compaction`
- `deps.turn_usage` → `deps.runtime.turn_usage`
- `deps.auto_approved_tools` → `deps.session.auto_approved_tools`
- `deps.active_skill_allowed_tools` → `deps.session.active_skill_allowed_tools`

Implementation notes:
- `_check_skill_grant()` should read from `deps.session`
- approval flow should remain functionally identical in this task

done_when:
- orchestration and processors use explicit ownership groups
- no runtime processor state is stored on top-level deps

### TASK-5: Migrate slash commands and ancillary runtime code

files:
- `co_cli/_commands.py`
- `co_cli/background.py`
- `co_cli/status.py`
- any other module touching deps directly

Changes:
- replace flat field access with grouped access
- ensure slash commands mutate `session` or `runtime` only where appropriate

Implementation notes:
- slash commands that inspect or mutate conversation/session state should not reach into `config` except for read-only settings

done_when:
- command layer compiles and uses grouped access consistently

### TASK-6: Tighten approval ownership and naming while preserving current behavior

files:
- `co_cli/_orchestrate.py`
- optionally small helpers in dedicated approval/session modules

Changes:
- keep current behavior initially, but move approval grants into `deps.session`
- rename ambiguous fields where helpful during migration

Recommended naming:
- `auto_approved_tools` → `session_tool_approvals`
- `active_skill_allowed_tools` → `skill_tool_grants`

Reason:
- current names are implementation-shaped rather than meaning-shaped
- better naming reduces confusion in the approval pipeline

Note:
- this task does not yet change the semantics of broad `"always approve"`
- semantic tightening can be a follow-up task

done_when:
- approval/session state has a clear home under `deps.session`
- naming reflects ownership and purpose

### TASK-7: Update tests for new ownership boundaries

files:
- `tests/test_agent.py`
- `tests/test_orchestrate.py`
- `tests/test_commands.py`
- `tests/test_shell.py`
- any tests referencing `CoDeps` fields directly

Changes:
- update fixture construction to grouped deps
- add regression tests for sub-agent isolation
- add tests that assert processor state lives in `runtime`
- add tests that assert session state lives in `session`

Recommended regression cases:
- sub-agent does not inherit parent `session_todos`
- sub-agent does not inherit parent skill grants
- runtime safety state resets without touching config/services
- tool code can still access required configuration after the reshape

done_when:
- all affected tests pass against the new deps structure
- at least one test covers grouped ownership explicitly

### TASK-8: Sync design docs

files:
- `docs/DESIGN-core.md`
- `docs/DESIGN-tools.md`
- `docs/DESIGN-prompt-design.md`
- `docs/DESIGN-tools-execution.md`
- any other DESIGN docs that describe `CoDeps`

Changes:
- rewrite `CoDeps` descriptions to reflect grouped ownership
- remove references to flat top-level fields where outdated
- document the ownership rule:
  - services = injected capabilities
  - config = injected read-only settings
  - session = mutable tool-visible session state
  - runtime = mutable orchestration/processor state

done_when:
- DESIGN docs match the actual refactored structure
- no doc claims that `CoDeps` is flat if the implementation is grouped

## Suggested Migration Sequence

Use an incremental migration that keeps the codebase runnable at each step:

1. add grouped dataclasses in `deps.py`
2. rewrite `create_deps()` to build them
3. migrate top-level access in orchestration and processors first
4. migrate tools module-by-module
5. migrate commands/helpers
6. remove any compatibility aliases if temporarily introduced
7. update docs and tests last in the same delivery branch before final verification

Avoid a “big bang” rewrite that changes every field reference in one unstructured pass.

## Implementation Notes and Tradeoffs

### Why not keep flat fields and only improve naming?

Because the core problem is ownership, not just naming. Better names help, but they do not
solve the fact that config, services, session state, and runtime internals are all peers
on the same object.

### Why not move everything out of `CoDeps` entirely?

That would overshoot. `pydantic-ai` expects a normal dependency object, and co-cli benefits
from one stable `deps_type`. A grouped `CoDeps` keeps the ergonomic and typing advantages
without turning the system into a custom dependency framework.

### Why not keep processor state on underscore fields?

Because underscore names on a shared dependency object are weak signaling only. Explicit
grouping communicates actual ownership and reduces accidental coupling.

### Why not put session state in message history instead?

Some state is not conversation content:
- pagination tokens
- cached credentials
- session todos
- approval grants

Those are runtime/session mechanics, not model-visible history.

## Follow-Up Work After This Refactor

These are related but should be separate tasks unless they are trivial during implementation:

1. tighten `"always approve"` semantics from tool-wide session grant to narrower approval scopes
2. use `DeferredToolResults.metadata` / `ctx.tool_call_metadata` where approval review needs structured context
3. review which mutable session fields truly belong in `session` versus dedicated subsystem objects
4. consider making `CoConfig` frozen if that does not create practical friction

## Testing

Required verification:

- run targeted pytest for deps/orchestration/tool coverage:
  - `tests/test_agent.py`
  - `tests/test_orchestrate.py`
  - `tests/test_commands.py`
  - `tests/test_shell.py`
  - any tests that construct `CoDeps` directly
- run repo-wide grep for stale flat field access on removed fields
- verify docs are in sync with final implementation

Recommended grep checks after migration:

- no references remain to top-level `_opening_ctx_state`
- no references remain to top-level `_safety_state`
- no references remain to top-level `precomputed_compaction`
- no references remain to top-level `auto_approved_tools`

## Open Questions

1. Should `turn_usage` stay in `deps.runtime`, or should it move fully into orchestration local state if only delegation needs it?
2. Should cached Google credentials remain in `session`, or move into a more explicit integration-specific state object later?
3. Should `CoConfig` be frozen in this refactor or in a follow-up once the grouped shape stabilizes?

## Final — Team Lead

This refactor is worth doing because the current pain is structural, not cosmetic.
The target state stays idiomatic to `pydantic-ai`: one normal typed deps object, cleaner
ownership boundaries, less hidden mutable coupling.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev codeps-refactor`
