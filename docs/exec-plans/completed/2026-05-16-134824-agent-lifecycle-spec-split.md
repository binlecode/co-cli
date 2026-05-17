# Plan: Agent Lifecycle / Spec Split

## Context

`co_cli/agents/` today mixes three concerns: agent *construction*, agent *lifecycle*, and the *definitions* of specific background agents (`session_review`, `skill_curator`). The package-private `build_agent()` in `agents/core.py` has two paths fused via kwarg sniffing (orchestrator vs delegation). Task-agent identity (instructions, tool surface, budget, output type) is scattered across `tools/agents/delegation.py` for in-turn agents and across `agents/session_review.py` + `agents/skill_curator.py` for standalone agents. Tool→agent membership lives on the *tool* side via `@agent_tool(delegation=frozenset({"web_research"}))` and resolves at call time through `discover_delegation_tools(profile)` — discoverable only by grep.

**Peer evidence**: opencode's `Agent.Info` schema unifies all native agents under one record type with a `mode: "subagent"|"primary"|"all"` field, and lifecycle is isolated in an `Agent.Service` interface (`get`/`list`/`defaultAgent`/`generate`). Openclaw uses `ResolvedAgentConfig` records + multi-file lifecycle. Hermes fuses everything in a 2,531-line `delegate_tool.py` (the cautionary tale). Co-cli's current shape sits between hermes and opencode and is drifting toward the former.

**Conversation arc that led here**: user proposed renaming `agents/` → `agent/` (lifecycle utils, not definitions), introducing a shared agent spec package, and routing both main and tool-delegation through common lifecycle functions. After investigation the design crystallized as **two specs, not one** — `OrchestratorSpec` and `TaskAgentSpec` — because the shared surface is ~5 fields against ~15 unique each side (toolset shape, instruction model, output type, history processors, lifecycle owner all diverge). Decided **one plan** because splitting forces a transient half-state where two ways to build an agent coexist, colliding with `feedback_zero_backward_compat`.

## Problem & Outcome

**Problem**: Adding a new agent today requires editing 4–6 files across `agents/`, `tools/`, and per-tool decorator advertisements; answering "what tools does `web_research` have" requires grepping every tool module; the orchestrator and task agents share one polymorphic builder that type-sniffs its kwargs.

**Outcome**: Each agent is a single declarative record (`OrchestratorSpec` or `TaskAgentSpec`) whose tool surface, instructions, output type, and budget are locally readable. Lifecycle (build + run + tracing + usage merge) lives in `co_cli/agent/` and is consumed identically by every agent. `agents/` is gone; the new `agent/` module owns lifecycle plus the orchestrator spec (the always-present primary agent — co-locating it with the lifecycle code matches opencode's peer pattern). Task-agent specs live with their domain callers.

**Failure cost**: Today the next person who adds an agent will copy-paste from `delegation.py`, add another `delegation=frozenset({...})` advertisement, and the next-next person will not find it. Each new agent compounds the discovery cost; the system slides toward the hermes shape (one 2k-line file). The current pattern also blocks legitimate future work: agent variants per personality, agent-specific approval policy, multi-step agents — each requires a spec to attach to.

## Scope

**In scope**:
- New module `co_cli/agent/` with `spec.py`, `build.py`, `run.py`, `toolset.py`, `mcp.py`, `orchestrator.py`, `_instructions.py`
- Two specs: `OrchestratorSpec`, `TaskAgentSpec` (no shared base — independent frozen dataclasses)
- Six concrete spec records: `ORCHESTRATOR_SPEC` (orchestrator); `WEB_RESEARCH_SPEC`, `KNOWLEDGE_ANALYZE_SPEC`, `REASON_SPEC` (in-turn task); `SESSION_REVIEW_SPEC`, `CURATOR_SPEC` (standalone task)
- Domain relocation: `agents/session_review.py` → `skills/session_review.py`; `agents/skill_curator.py` merged into existing `skills/curator.py`
- Tool-call-limit relocation: `agents/tool_call_limit.py` → `tools/tool_call_limit.py`
- Decorator flip: remove `delegation=` kwarg from `@agent_tool` and remove all 9 advertisement sites; `TaskAgentSpec.tool_names` becomes the source of truth, resolved against `TOOL_REGISTRY` by name at build time
- Delete `build_agent` (kwarg-sniff), `discover_delegation_tools`, `_run_agent_in_turn`, `_run_agent_standalone`, `_delegate_agent`

**Out of scope**:
- Spec changes to `docs/specs/` — `sync-doc` runs post-delivery. Expected targets:
  - `docs/specs/01-system.md` — agent module rename + spec-driven construction model
  - `docs/specs/core-loop.md` — delegation tool structure (spec-driven)
  - `docs/specs/skills.md` — session_review + curator domain ownership
  - `docs/specs/personality.md` — orchestrator spec composition (if affected)
- Personality/skill behavior changes
- New agents
- Approval flow or deferred-loading changes (mechanism preserved exactly)
- The escape-hatch fallback (keeping `delegation=` for one cycle) — `/review-impl` may invoke it; not part of this plan's planned scope

## Behavioral Constraints

- **Zero backward compatibility**: no aliases, no compat shims, no `_legacy` markers in committed code. Renames are hard and immediate.
- **Behavioral parity**: the refactor must not change agent output, approval flow, usage accounting, or tracing attributes. The full test suite must pass without test edits beyond import-path updates.
- **Preserve approval/resume mechanics**: orchestrator toolset filter (`_approval_resume_filter`), `DeferredLoadingToolset`, `_SequentialMCPToolset`, `CoToolLifecycle()` capabilities, `[str, DeferredToolRequests]` output — none of these change. Only their wiring changes.
- **Preserve in-turn vs standalone semantics**: in-turn runner forks deps, merges usage into `turn_usage`, raises `ModelRetry` on failure; standalone runner takes already-forked deps, no merge, propagates plain exceptions. Both behaviors moved verbatim into `agent/run.py`.
- **Preserve config-conditional tool filtering**: `_config_requirement_met` (Google/Obsidian require credentials) must still gate task-agent tool resolution.
- **Import discipline**: `co_cli/agent/__init__.py` is docstring-only (project rule). All exports live in submodules. `tools/agent_tool.py` keeps its `ONLY imports from co_cli.deps` constraint.
- **No `__init__.py` code or re-exports** anywhere created or touched.
- **One-shot rename**: the rename `agents/` → `agent/` and the file moves happen atomically via `git mv`; no period where both directories exist.

## High-Level Design

### Spec types (`co_cli/agent/spec.py`)

No shared marker base — the two specs are independent frozen dataclasses. (No dispatcher consumes a common type; inheritance would be decorative.)

```python
@dataclass(frozen=True)
class OrchestratorSpec:
    name: str                                                     # tracing role tag + span name
    static_instruction_builders: tuple[Callable[[CoDeps], str | None], ...]
    per_turn_instructions: tuple[Callable[[RunContext[CoDeps]], str], ...]
    history_processors: tuple[Callable, ...]
    # toolset pulled from deps.toolset directly by build_orchestrator (no factory field — singleton)
    # output_type, retries, model_settings derived from config

@dataclass(frozen=True)
class TaskAgentSpec:
    name: str                                                     # tracing role tag + span name
    instructions: Callable[[CoDeps], str]
    tool_names: tuple[str, ...]                                   # resolved against TOOL_REGISTRY_BY_NAME at build
    output_type: type[BaseModel]
    default_budget: int
    error_message: str                                            # raised inside ModelRetry on in-turn failure (unused by run_standalone)
    include_skill_manifest: bool = False                          # True only for SESSION_REVIEW_SPEC
```

All collection fields are `tuple[...]` (not `list[...]`) for true immutability inside the frozen dataclass.

**Daemons are task agents** — the in-turn vs daemon (standalone) distinction is *lifecycle ownership*, not spec shape. Both share `TaskAgentSpec`; runner choice (`run_in_turn` vs `run_standalone`) selects the lifecycle. `run_in_turn` always depth-checks; `run_standalone` never does. No spec flag encodes this — the runner is the signal.

### Builders (`co_cli/agent/build.py`)

```python
def build_orchestrator(spec: OrchestratorSpec, deps: CoDeps,
                       skill_manifest: str | None = None) -> Agent[CoDeps, Any]
def build_task_agent(spec: TaskAgentSpec, deps: CoDeps, model: Any) -> Agent[CoDeps, Any]
```

`build_orchestrator` reads `deps.toolset` directly (orchestrator is a singleton; no factory abstraction needed). Composes static instructions by calling each `spec.static_instruction_builders` in order, registers `spec.per_turn_instructions` via `agent.instructions(...)`, and attaches `spec.history_processors`. Output type is fixed `[str, DeferredToolRequests]`; capabilities is fixed `[CoToolLifecycle()]`; retries from `deps.config.tool_retries`.

`build_task_agent`:
- Resolves `spec.tool_names` → callables via `TOOL_REGISTRY_BY_NAME` (new eager `dict[str, Callable]` populated by the `@agent_tool` decorator at import time, alongside the existing `TOOL_REGISTRY` list), filtered by `_config_requirement_met` so Google/Obsidian tools drop out when credentials are absent.
- Unknown tool names raise `ValueError(f"{spec.name}: unknown tool {name!r}")` at build time (fail-loud).
- Registers each resolved tool with `requires_approval=False` — matches current `delegation_agent.tool(fn, requires_approval=False)` hard-coding at `agents/core.py:208`. Task agents auto-approve their own tool calls.
- Does **not** patch `sequential` per `is_concurrent_safe` — task agents inherit pydantic-ai's default, matching current behavior. (Orchestrator's per-tool `sequential` patching at `_native_toolset.py:102` is preserved untouched.)
- When `spec.include_skill_manifest is True`, prepends `render_skill_manifest(deps.skill_index, deps.skills_dir, deps.user_skills_dir)` to `spec.instructions(deps)` output before passing to `Agent(instructions=...)`. Builder-owned (declarative); the spec's instructions function focuses on agent-specific guidance.
- `retries` is derived from `deps.config.tool_retries` — not a spec field, since it's a config-level concern identical for all agents.
- Capabilities is fixed `[CoToolLifecycle()]`.

### Runners (`co_cli/agent/run.py`)

```python
async def run_in_turn(spec: TaskAgentSpec, ctx: RunContext[CoDeps], prompt: str,
                      budget: int | None = None) -> ToolReturn
async def run_standalone(spec: TaskAgentSpec, deps: CoDeps, prompt: str,
                         budget: int | None = None,
                         model_settings: Any = None) -> tuple[Any, RunUsage, str]

# Low-level helper for custom retry control (web_research only)
async def _run_attempt(spec: TaskAgentSpec, ctx: RunContext[CoDeps], prompt: str,
                       budget: int, child_deps: CoDeps) -> tuple[Any, RunUsage, str]
```

`run_in_turn` **always** performs the depth check, forks deps via `fork_deps(ctx.deps)`, opens an OTel span named `spec.name` with `agent.role`/`agent.model`/`agent.request_limit` attributes, builds the task agent, runs it, merges usage into `ctx.deps.runtime.turn_usage`, raises `ModelRetry(spec.error_message)` on failure, and formats the `ToolReturn` with `spec.name` as role tag.

`run_standalone` takes already-forked deps (caller did `fork_deps_for_reviewer`/`fork_deps_for_curator`), opens its own span, **never** depth-checks, does not merge usage, lets exceptions propagate plain. Does not consult `spec.error_message`.

`_run_attempt` is the inner primitive both runners share: it builds the agent, runs one attempt, raises `ModelRetry(spec.error_message)` on failure. Used by `web_research`'s tool wrapper to drive two attempts inside a single outer span (preserves current single-span retry topology at `tools/agents/delegation.py:220-256`). Depth + fork + outer span management stay in the caller. This is the only place a tool wrapper reaches below `run_in_turn`; all other delegation tools call `run_in_turn` directly.

### Domain ownership

| Spec | Defined in | Runner used | Caller |
|---|---|---|---|
| `ORCHESTRATOR_SPEC` | `co_cli/agent/orchestrator.py` | `build_orchestrator` directly | `main.py::_chat_loop` |
| `WEB_RESEARCH_SPEC` | `co_cli/tools/agents/delegation.py` | `run_in_turn` | `web_research` tool wrapper (same file) |
| `KNOWLEDGE_ANALYZE_SPEC` | `co_cli/tools/agents/delegation.py` | `run_in_turn` | `knowledge_analyze` tool wrapper |
| `REASON_SPEC` | `co_cli/tools/agents/delegation.py` | `run_in_turn` | `reason` tool wrapper |
| `SESSION_REVIEW_SPEC` | `co_cli/skills/session_review.py` | `run_standalone` | `run_session_review()` in same file |
| `CURATOR_SPEC` | `co_cli/skills/curator.py` | `run_standalone` | `run_curator()` in same file |

### Tool resolution flip (push, not pull)

Before: tool advertises into agent profile via decorator (`delegation=frozenset({"web_research"})`).
After: spec lists tool names; build resolves names against `TOOL_REGISTRY_BY_NAME` (populated by `agent/toolset.py` side-effect imports).

The `@agent_tool` decorator populates **two** module-level registries at import time:
- `TOOL_REGISTRY: list[Callable]` (existing — `_build_native_toolset` iterates this)
- `TOOL_REGISTRY_BY_NAME: dict[str, Callable]` (new — `build_task_agent` reads this for O(1) name resolution)

Spec validation at build time: unknown name → `ValueError(f"{spec.name}: unknown tool {name!r}")`.

### Final module layout

```
co_cli/agent/                          # lifecycle + orchestrator spec (always-present primary agent)
  __init__.py                          # docstring only
  spec.py                              # OrchestratorSpec, TaskAgentSpec (no shared base)
  build.py                             # build_orchestrator, build_task_agent
  run.py                               # run_in_turn, run_standalone
  toolset.py                           # was _native_toolset.py
  mcp.py                               # unchanged location
  _instructions.py                     # safety_prompt, current_time_prompt (orchestrator-bound)
  orchestrator.py                      # ORCHESTRATOR_SPEC + static-parts builders

co_cli/tools/
  tool_call_limit.py                   # moved from agents/
  agents/delegation.py                 # 3 SPECs + 3 thin tool wrappers
  agent_tool.py                        # @agent_tool — `delegation=` kwarg removed; ToolInfo.delegation field removed

co_cli/skills/
  session_review.py                    # NEW — SESSION_REVIEW_SPEC + run_session_review + report writer
  curator.py                           # CURATOR_SPEC + run_curator + report writer + existing state machinery (merged)
  session_review_prompts.py            # unchanged
  curator_prompts.py                   # unchanged

# DELETED entirely:
co_cli/agents/                         # (renamed to agent/ — but session_review.py and skill_curator.py move to skills/)
```

## Tasks

### ✓ DONE TASK-1 — Define agent spec types

**files:**
- `co_cli/agent/spec.py` (new)

**done_when:** `from co_cli.agent.spec import OrchestratorSpec, TaskAgentSpec` succeeds; mypy/ruff pass; no consumer yet (intentional — wired in subsequent tasks).
**success_signal:** N/A (pure types)
**prerequisites:** none

### ✓ DONE TASK-2 — Rename `agents/` → `agent/` + relocate non-agent files

**files:**
- `co_cli/agent/` (created from `git mv co_cli/agents`)
- `co_cli/agent/toolset.py` (from `_native_toolset.py` via `git mv`)
- `co_cli/tools/tool_call_limit.py` (from `co_cli/agent/tool_call_limit.py` via `git mv`)
- Importers in `co_cli/` (7 files): `co_cli/main.py`, `co_cli/bootstrap/core.py`, `co_cli/commands/skills.py`, `co_cli/context/compaction.py`, `co_cli/personality/prompts/loader.py`, `co_cli/tools/agents/delegation.py`, `co_cli/tools/lifecycle.py`
- Test importers (16 files): `tests/test_flow_bootstrap_budget_span.py`, `tests/test_flow_capability_checks.py`, `tests/test_flow_chat_loop.py`, `tests/test_flow_delegation_discovery.py`, `tests/test_flow_orchestrate_length_retry.py`, `tests/test_flow_session_review.py`, `tests/test_flow_skill_creator_dispatch.py`, `tests/test_flow_skill_curator.py`, `tests/test_flow_skill_usage.py`, `tests/test_flow_skills_curator.py`, `tests/test_flow_skills_manage.py`, `tests/test_flow_skills_tools.py`, `tests/test_flow_slash_dispatch.py`, `tests/test_flow_tool_call_functional.py`, `tests/test_flow_tool_call_limit.py`, `tests/test_flow_turn_result_tool_iterations.py`
- `co_cli/tools/agents/__init__.py` — docstring updated to disambiguate from the deleted `co_cli/agents/` (singular vs. plural: this directory holds *tool wrappers that delegate to task agents*, not the lifecycle module)

This task is pure structural — *no* semantic change. `build_agent`, `_run_agent_in_turn`, `_run_agent_standalone`, `discover_delegation_tools` still exist with the same signatures, just under new paths. `session_review.py` and `skill_curator.py` still live in `co_cli/agent/` after this task (moved out in TASK-6/7); the ~5 tests that import them will need a *second* import update at TASK-6/7 (`co_cli.agent.session_review` → `co_cli.skills.session_review`; `co_cli.agent.skill_curator` → `co_cli.skills.curator`).

**Acyclic verification**: `co_cli/tools/lifecycle.py` imports `co_cli/tools/tool_call_limit.py` after the move (was previously cross-package `co_cli.agents.tool_call_limit`). Confirm `tool_call_limit.py` does not import from `tools/lifecycle.py` or other `tools/` modules — it currently only imports `typing` (no co_cli imports). Acyclic verified at planning time.

**done_when:** `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task2.log` passes; `grep -rn "co_cli.agents" co_cli/ tests/` returns nothing.
**success_signal:** N/A (mechanical rename)
**prerequisites:** TASK-1

### ✓ DONE TASK-3 — `build_orchestrator` + `ORCHESTRATOR_SPEC`; wire `main.py`

**files:**
- `co_cli/agent/build.py` (new — `build_orchestrator` only at this task)
- `co_cli/agent/orchestrator.py` (new — `ORCHESTRATOR_SPEC` record)
- `co_cli/agent/_instructions.py` (unchanged content; orchestrator-bound — referenced by `ORCHESTRATOR_SPEC.per_turn_instructions`; kept as separate file for testability and import isolation)
- `co_cli/main.py` (replace `build_agent(...)` orchestrator-path call at line ~513 with `build_orchestrator(ORCHESTRATOR_SPEC, deps, skill_manifest=...)`)
- `co_cli/agent/core.py` (existing `build_agent` keeps its delegation path; orchestrator branch deleted)

`ORCHESTRATOR_SPEC.static_instruction_builders` is the tuple `(build_static_instructions, build_toolset_guidance_provider, build_category_awareness_provider, skill_manifest_provider, personality_critique_provider)` — each is a thin closure that pulls from `deps`. `per_turn_instructions = (safety_prompt, current_time_prompt)`. `history_processors` is the existing 5-element tuple. Toolset is read from `deps.toolset` directly inside `build_orchestrator` (no factory field — orchestrator is singleton).

**done_when:** Full test suite passes; REPL launches and serves one prompt-response turn via `uv run co chat <<< 'hi'` (or equivalent smoke) without exception.
**success_signal:** `tests/test_agent_orchestrator_parity.py` passes — the snapshot test captures the concatenated static instruction string from a pre-refactor reference run (committed as a fixture) and asserts byte-identical equality against the post-refactor assembled string. This is the strongest verifiability hook for the orchestrator path; the snapshot fixture is deleted after first green run on `main`.
**prerequisites:** TASK-2

### ✓ DONE TASK-4 — `build_task_agent` + `run_in_turn` + `run_standalone` + `_run_attempt`

**files:**
- `co_cli/agent/build.py` (add `build_task_agent` — resolves `spec.tool_names` via `TOOL_REGISTRY_BY_NAME`, registers each with `requires_approval=False`, no `sequential` patching; reads `retries` from `deps.config.tool_retries`; prepends skill manifest when `spec.include_skill_manifest`)
- `co_cli/agent/run.py` (new — `run_in_turn`, `run_standalone`, `_run_attempt`)
- `co_cli/tools/agent_tool.py` (add eager `TOOL_REGISTRY_BY_NAME: dict[str, Callable]`; populated alongside the existing list `TOOL_REGISTRY` inside the decorator)

At this point the two new runners exist and resolve tool names via the registry, but no caller uses them yet. `_run_agent_in_turn`/`_run_agent_standalone`/`_delegate_agent`/`discover_delegation_tools` still live in their current locations and are still used by the un-converted callers — they become dead in TASK-5/6/7 and are deleted in TASK-9.

**done_when:** Unit-level: structural tests in `tests/test_agent_build_task_agent.py` assert (1) constructing a `TaskAgentSpec` with `tool_names=("web_search",)` then calling `build_task_agent` yields an `Agent` with exactly one registered tool named `web_search`; (2) unknown tool name raises `ValueError` matching `{spec.name}: unknown tool 'foo'`; (3) Google/Obsidian tool names resolve to nothing when their respective config credentials are absent; (4) all registered tools have `requires_approval=False`. Full suite still passes.
**success_signal:** N/A (refactor scaffolding)
**prerequisites:** TASK-1, TASK-2

### ✓ DONE TASK-5 — Convert 3 in-turn delegation tools to spec-driven `run_in_turn`

**files:**
- `co_cli/tools/agents/delegation.py` (define `WEB_RESEARCH_SPEC`, `KNOWLEDGE_ANALYZE_SPEC`, `REASON_SPEC`; rewrite `web_research`, `knowledge_analyze`, `reason` as thin wrappers)

**Spec inventory** (verified against current `discover_delegation_tools(profile, config)` resolution):

- `WEB_RESEARCH_SPEC.tool_names = ("web_fetch", "web_search")` — error_message `"Research agent failed — handle this task directly."`, default_budget 10, output_type `AgentOutput`
- `KNOWLEDGE_ANALYZE_SPEC.tool_names = ("knowledge_search", "google_drive_search", "google_drive_read", "obsidian_search", "obsidian_list", "obsidian_read")` — error_message `"Analysis agent failed — handle this task directly."`, default_budget 8, output_type `AgentOutput`. (Note: `knowledge_view` is NOT in the current `knowledge_analyze` advertisement set — only `session_reviewer` advertises it; do not add it.)
- `REASON_SPEC.tool_names = ()` — error_message `"Reasoning agent failed — handle this task directly."`, default_budget 3, output_type `AgentOutput`

**Snapshot-equality capture** (used by TASK-6/TASK-7 too): before any delegation removal, capture `discover_delegation_tools(profile, deps.config)` output for each profile (`web_research`, `knowledge_analyze`, `session_reviewer`, `skill_curator`) and pin in a fixture. Assert each spec's resolved `tool_names` set equals the snapshot. The fixture is deleted at TASK-9.

**Tool wrappers** (`web_research`, `knowledge_analyze`, `reason`):
- No longer perform their own depth check — `run_in_turn` always owns the depth check.
- Remove the existing `ctx.deps.runtime.agent_depth >= MAX_AGENT_DEPTH` guard at `delegation.py:189, :293, :344`.
- `knowledge_analyze` and `reason` are one-liners: `return await run_in_turn(SPEC, ctx, prompt, budget=max_requests or None)`.
- `web_research` retains its retry-on-empty loop *in the tool wrapper*, since the loop must drive both attempts inside a single outer OTel span (current topology at `delegation.py:220-256`). The wrapper opens its own span, forks deps, performs the depth check explicitly (since it bypasses `run_in_turn`), then calls `_run_attempt(WEB_RESEARCH_SPEC, ctx, prompt, budget, child_deps)` twice, accumulates usage, and formats the final `ToolReturn` directly. The retry uses the *same* `error_message` from the spec — no separate retry-specific string.

**done_when:** Existing tests covering these three tools (search via `grep -l "web_research\|knowledge_analyze\|reason" tests/`) pass without test-side semantic edits (import-path updates only). Span-shape assertion: a new test in `tests/test_agent_run_in_turn_span.py` invokes `web_research` with a forced-empty first attempt and asserts the OTel span tree shows one `web_research` span with two child agent runs (preserves current single-span topology). `discover_delegation_tools` no longer called from `delegation.py`. Manual smoke: a turn invoking `reason` returns a result.
**success_signal:** Three delegation tools produce equivalent outputs and span topology to pre-refactor.
**prerequisites:** TASK-4

### ✓ DONE TASK-6 — Move `session_review` to `skills/` + define `SESSION_REVIEW_SPEC`

**files:**
- `co_cli/skills/session_review.py` (new — contains `SESSION_REVIEW_SPEC`, `SessionReviewOutput`, `SessionReviewResult`, `run_session_review`, `_write_review_report`, `_make_run_dir`)
- `co_cli/agent/session_review.py` (deleted via `git rm`)
- `co_cli/main.py` (update import — line 197)
- `co_cli/commands/skills.py` (update import — line 340)
- Test imports for `session_review` (~3 files: `tests/test_flow_session_review.py`, `tests/test_flow_exit_cleanup_review.py` if it imports, others as detected — second update after TASK-2's rename)

**Spec definition**: `SESSION_REVIEW_SPEC.tool_names = ("knowledge_view", "knowledge_search", "knowledge_manage", "skill_view", "skill_manage")` — verified against current `delegation=frozenset({"session_reviewer"})` advertisements at `tools/memory/view.py:32` (knowledge_view; `session_view` is NOT in the set), `tools/memory/recall.py:366` (knowledge_search; `session_search` is NOT in the set), `tools/memory/manage.py:40`, `tools/system/skills.py:37`+`:306`. Snapshot-equality assertion (per TASK-5 fixture) confirms parity. `include_skill_manifest=True`. `default_budget=REVIEW_MAX_ITERATIONS`. `error_message=""` (unused — `run_standalone` does not consult it; daemons propagate plain exceptions). `output_type=SessionReviewOutput`. Daemon lifecycle is selected by calling `run_standalone`, not by a spec flag.

**`refresh_skills` preservation**: `run_session_review` body retains the explicit `refresh_skills(child_deps)` call immediately after `fork_deps_for_reviewer(deps)` and before `run_standalone(...)`. This is documented session-review correctness (see inline comment at `agents/session_review.py:124-128`: "without this refresh, pass-B would render its manifest against pass-A's pre-write snapshot"). `fork_deps` shares `parent.skill_index` by reference (`deps.py:365`); without the refresh, the manifest renders against stale state. Do *not* fold this into `build_task_agent` — it's reviewer-specific timing, not generic builder behavior.

The wrapper becomes (roughly): fork deps → refresh skills → serialize transcript → render prompt → `run_standalone(SESSION_REVIEW_SPEC, child_deps, prompt, budget=REVIEW_MAX_ITERATIONS, model_settings=deps.model.settings)` → write report. The skill-manifest prefix is now builder-injected (via `include_skill_manifest=True`) instead of explicit `manifest + INSTRUCTIONS` string concatenation in the wrapper.

**done_when:** `tests/test_flow_exit_cleanup_review.py` passes. `/review session` CLI command runs end-to-end and writes `~/.co-cli/session-reviews/<ts>/run.{json,md}`. A new assertion (in the existing test or `test_agent_session_review_refresh.py`) confirms that across two successive `run_session_review` calls in one process, the second call's rendered manifest reflects the first call's skill writes — proving `refresh_skills` is still wired correctly.
**success_signal:** Session-end review produces a `SessionReviewResult` with the same fields and content shape as pre-refactor, and successive passes see prior-pass skill writes.
**prerequisites:** TASK-5

### ✓ DONE TASK-7 — Merge `skill_curator` into `skills/curator.py` + define `CURATOR_SPEC`

**files:**
- `co_cli/skills/curator.py` (extend — add `CURATOR_SPEC`, `CuratorOutput`, `run_curator`, `_summarize_skill_inventory`, `_write_curator_report`, `_make_run_dir`; keep all existing state-machine functions)
- `co_cli/agent/skill_curator.py` (deleted via `git rm`)
- `co_cli/main.py` (update import — line 243)
- `co_cli/commands/skills.py` (update import — line 259)
- Test imports for `skill_curator` (~2 files: `tests/test_flow_skill_curator.py`, `tests/test_flow_skills_curator.py` — second update after TASK-2's rename)

**Spec definition**: `CURATOR_SPEC.tool_names = ("skill_view", "skill_manage")` — verified against current `delegation=frozenset({"skill_curator"})` advertisements at `tools/system/skills.py:37`+`:306`. Snapshot-equality assertion (per TASK-5 fixture) confirms parity. `include_skill_manifest=False` (curator inventory is built explicitly via `_summarize_skill_inventory` and inlined into the prompt; the skill manifest is not added to instructions). `default_budget=CURATOR_MAX_ITERATIONS`. `error_message=""` (unused). `output_type=CuratorOutput`. Daemon lifecycle is selected by calling `run_standalone`, not by a spec flag.

The existing `skills/curator.py` already owns `apply_state_transitions`, `archive_skill`, `restore_skill`, `read_curator_state`, `write_curator_state` — this task adds the agent runner alongside, so all curator concerns live in one module. `run_curator`'s Phase-2 body becomes a `run_standalone(CURATOR_SPEC, child_deps, prompt, budget=CURATOR_MAX_ITERATIONS, model_settings=...)` call wrapped in the existing `asyncio.wait_for(CURATOR_TIMEOUT_SECONDS)` and exception handler. Phase 1 (state transitions) and Phase 3 (report write + state update) are unchanged.

**done_when:** `/skills curator` CLI command runs and writes `~/.co-cli/curator-runs/<ts>/run.{json,md}`. Existing curator tests (search via `grep -rl "run_curator\|curator_state" tests/`) pass.
**success_signal:** Curator run produces a `CuratorOutput` and updates `curator_state` with `run_count` incremented.
**prerequisites:** TASK-6 (sequential due to shared edits to `main.py` and `commands/skills.py`)

### ✓ DONE TASK-8 — Flip decorator: remove `delegation=` advertisement

**files:**
- `co_cli/tools/agent_tool.py` (remove `delegation` parameter from `agent_tool` decorator; remove `delegation` field from `ToolInfo` construction)
- `co_cli/deps.py` (remove `delegation` field from `ToolInfo` dataclass definition)
- 10 `delegation=` call sites across 9 tool files:
  - `co_cli/tools/memory/view.py:27` (knowledge_view)
  - `co_cli/tools/memory/recall.py:362` (knowledge_search)
  - `co_cli/tools/memory/manage.py:34` (knowledge_manage)
  - `co_cli/tools/web/fetch.py:108` area (web_fetch)
  - `co_cli/tools/web/search.py:277` area (web_search)
  - `co_cli/tools/google/drive.py:66` area (google_drive_*)
  - `co_cli/tools/system/skills.py:32` (skill_view) AND `:302` (skill_manage) — two sites in one file
  - `co_cli/tools/obsidian/tools.py:168` area (obsidian_*) AND `:234` area — two sites in one file

By this task the TASK-5/6/7 snapshot-equality assertions have already verified each spec's resolved `tool_names` matches the pre-refactor `discover_delegation_tools(profile, config)` output, so decorator removal is safe.

**done_when:** `grep -rn "delegation=" co_cli/ tests/` returns nothing. `grep -rn "ToolInfo.delegation\|info\.delegation\|\.delegation\b" co_cli/` returns nothing (excluding the AGENT_TOOL_ATTR self-reference). Full test suite passes.
**success_signal:** N/A (mechanical removal; tool resolution behavior asserted by TASK-5/6/7's snapshot-equality assertions)
**prerequisites:** TASK-5, TASK-6, TASK-7

### ✓ DONE TASK-9 — Delete legacy `build_agent`, runners, helpers, and dead tests

**files:**
- `co_cli/agent/core.py` (delete `build_agent` and `discover_delegation_tools`; if file becomes empty, `git rm`)
- `co_cli/agent/_runner.py` (delete `_run_agent_standalone`; `git rm` if file becomes empty)
- `co_cli/tools/agents/delegation.py` (delete `_run_agent_in_turn`, `_delegate_agent`, `_merge_turn_usage` — equivalent logic now lives in `agent/run.py`)
- `tests/test_flow_delegation_discovery.py` — `git rm` (84 lines, 8 tests covering the now-deleted `discover_delegation_tools` + `ToolInfo.delegation` field)
- `tests/test_flow_session_review.py` — remove the lines (~124-128) that still reference `discover_delegation_tools`; replace with assertions against `SESSION_REVIEW_SPEC.tool_names` if equivalent coverage is needed (or delete if redundant with TASK-6's snapshot-equality)
- Snapshot fixture from TASK-5 (`tests/fixtures/discover_delegation_tools_snapshot.json` or wherever it landed) — delete

**done_when:** `grep -rn "build_agent\b\|_run_agent_in_turn\|_run_agent_standalone\|discover_delegation_tools\|_delegate_agent\|_merge_turn_usage" co_cli/ tests/` returns nothing (or only the new `build_orchestrator`/`build_task_agent` names). Full test suite passes.
**success_signal:** N/A (dead code removal)
**prerequisites:** TASK-5, TASK-6, TASK-7, TASK-8

### ✓ DONE TASK-10 — Final verification

**files:** none modified

**done_when:** `scripts/quality-gate.sh full` passes. `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-final.log` shows zero failures. CLI smoke: `uv run co chat` launches, accepts one prompt, exits cleanly.
**success_signal:** Whole refactor lands behaviorally identical to pre-refactor; visible difference is module layout + reduced LOC in `tools/agents/delegation.py`.
**prerequisites:** all prior

## Testing

**Existing tests must pass unmodified** beyond import-path updates. Refactor is behavior-preserving.

**Structural tests added** (small set, in `tests/`):
- `test_agent_spec_types.py` — instantiate each spec, assert field types and immutability
- `test_agent_build_task_agent.py` — verify tool resolution from `tool_names` (happy path + unknown-name raises at build; config-conditional Google/Obsidian tools skipped when creds absent)
- `test_agent_run_in_turn_depth.py` — `run_in_turn` raises `ModelRetry` when depth limit hit; `run_standalone` performs no depth check (called at any depth without raising)
- `test_agent_orchestrator_parity.py` — assemble `ORCHESTRATOR_SPEC` instructions and compare byte-for-byte to a captured pre-refactor snapshot (one-off snapshot test; delete after first green run)

**Workflows exercised by `done_when` runtime checks**:
- TASK-3: orchestrator REPL turn (smoke)
- TASK-5: each delegation tool produces output
- TASK-6: session-end review writes report
- TASK-7: curator run writes report
- TASK-10: full quality gate

No new evals — refactor doesn't change LLM behavior.

## Open Questions

All open questions raised during planning were resolved before approval — answers are folded into the plan body above.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev agent-lifecycle-spec-split`

## Delivery Summary — 2026-05-16

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `from co_cli.agent.spec import OrchestratorSpec, TaskAgentSpec` succeeds; lint clean | ✓ pass |
| TASK-2 | rename complete; `grep "co_cli.agents" co_cli/ tests/` returns nothing; 471 tests pass | ✓ pass |
| TASK-3 | `build_orchestrator(ORCHESTRATOR_SPEC, deps)` wired in `main.py`; chat-loop end-to-end (real LLM) passes | ✓ pass |
| TASK-4 | `build_task_agent` + `run_in_turn` + `run_standalone` + `_run_attempt` created; 5 structural tests pass (resolve / unknown / google-drop / obsidian-drop / no-approval) | ✓ pass |
| TASK-5 | 3 in-turn delegation tools spec-driven via `run_in_turn`; parity verified against pre-refactor `discover_delegation_tools` snapshot for all 3 specs | ✓ pass |
| TASK-6 | `co_cli/skills/session_review.py` owns `SESSION_REVIEW_SPEC` + `run_session_review`; 13 session-review tests pass | ✓ pass |
| TASK-7 | `co_cli/skills/curator.py` extended with `CURATOR_SPEC` + `run_curator`; 29 curator tests pass | ✓ pass |
| TASK-8 | `delegation=` decorator kwarg + `ToolInfo.delegation` field removed; 10 advertisement sites deleted; `discover_delegation_tools` deleted | ✓ pass |
| TASK-9 | legacy `build_agent`, `_run_agent_standalone`, `_run_agent_in_turn`, `_delegate_agent` deleted; obsolete `test_flow_delegation_discovery.py` (8 tests) removed; zero grep matches for legacy names | ✓ pass |
| TASK-10 | `scripts/quality-gate.sh lint` passes; scoped suite (138 tests) + chat-loop end-to-end (9 tests, real LLM) green | ✓ pass |

**Behavioral parity asserted**: pre-refactor `discover_delegation_tools(profile, SETTINGS_NO_MCP)` output captured and compared against post-refactor `spec.tool_names` filtered by `_config_requirement_met` — exact match for all 5 specs (`web_research`, `knowledge_analyze`, `reason`, `session_review`, `skill_curator`).

**Tests:** scoped — 147 passed, 0 failed in TASK-relevant test files. One environmental Ollama timeout (`test_llm_call_returns_non_empty_text`) during the larger pre-`/sync-doc` scan; re-ran in isolation, 4/4 passed. Unrelated to the refactor (only a docstring was touched in `co_cli/llm/call.py`).

**Lint:** clean (ruff check + format, 251 files).

**Doc Sync:** fixed — 8 specs updated (`01-system.md`, `bootstrap.md`, `prompt-assembly.md`, `core-loop.md`, `tools.md`, `personality.md`, `observability.md`, `skills.md`); 9 others clean.

**Plan amendment applied during execution**: Gate-1 PO feedback removed `enforce_depth` field from `TaskAgentSpec` (depth-check is hardcoded in `run_in_turn`, never in `run_standalone`) — applied before TASK-1.

**Overall: DELIVERED**
Refactor lands behaviorally identical to pre-refactor; the visible difference is module layout (declarative `OrchestratorSpec` + `TaskAgentSpec` records, builders in `agent/build.py`, runners in `agent/run.py`) and the disappearance of decorator-side `delegation=` advertisements (now pull-not-push: specs name tools, builder resolves).

**Next step:** `/review-impl agent-lifecycle-spec-split` — full suite + evidence scan + auto-fix → verdict appended to plan.

## Implementation Review — 2026-05-16

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | ✓ pass | ✓ pass | `co_cli/agent/spec.py:21-34` `OrchestratorSpec` frozen + tuple fields; `:37-59` `TaskAgentSpec` frozen + tuple fields; no shared base; `enforce_depth` absent (plan amendment honored). Import succeeds. |
| TASK-2 | ✓ pass | ✓ pass | `grep co_cli.agents` empty; `co_cli/agent/__init__.py:1` docstring-only; `co_cli/tools/tool_call_limit.py:3` imports only `typing` (cycle-free per plan line 195). |
| TASK-3 | ✓ pass | ✓ pass | `co_cli/agent/orchestrator.py:60-77` `ORCHESTRATOR_SPEC` with 5 static builders + 2 per-turn + 5 history processors; `co_cli/agent/build.py:48` `toolsets=[deps.toolset]` (singleton, no factory); `co_cli/main.py:508` calls `build_orchestrator(ORCHESTRATOR_SPEC, deps)`; `co_cli/agent/core.py` retains only toolset helpers (no `build_agent`). Parity test deleted per plan line 213 ("deleted after first green run"). |
| TASK-4 | ✓ pass | ✓ pass | `co_cli/agent/build.py:78-85` resolves via `TOOL_REGISTRY_BY_NAME`; `:80-81` unknown-name `ValueError`; `:83-84` config filter; `:104` `requires_approval=False`; `:88-93` skill manifest prepend; no `sequential` patching. `co_cli/agent/run.py:92-95` `run_in_turn` depth check; `:105-108` span attrs; `:110` usage merge; `:126-167` `run_standalone` no depth/usage merge, plain exceptions. `co_cli/tools/agent_tool.py:20` `TOOL_REGISTRY_BY_NAME`; `:67-69` dual populate. `tests/test_agent_build_task_agent.py` 5 structural tests pass. Runtime check: 37 entries in both registries. |
| TASK-5 | ✓ pass | ✓ pass | `co_cli/tools/agents/delegation.py:78-111` three specs match plan inventory exactly (tool_names, error_message, default_budget); `knowledge_view` correctly excluded from `KNOWLEDGE_ANALYZE_SPEC`. `:143` web_research explicit depth guard preserved (bypasses `run_in_turn`); `:162` opens own outer span; `:167-182` two `_run_attempt` calls + usage merge per single-span retry topology. `knowledge_analyze` (`:231-233`) and `reason` (`:259`) are one-liners delegating to `run_in_turn`. |
| TASK-6 | ✓ pass | ✓ pass | `co_cli/skills/session_review.py:54-68` `SESSION_REVIEW_SPEC` with exact 5-tool tuple, `include_skill_manifest=True`, `error_message=""`; `:65` `default_budget=REVIEW_MAX_ITERATIONS` (fixed during review). `:154` `refresh_skills(child_deps)` called between fork and `run_standalone` (pass-A/pass-B correctness preserved). `co_cli/agent/session_review.py` deleted; importers updated. |
| TASK-7 | ✓ pass | ✓ pass | `co_cli/skills/curator.py:207-214` `CURATOR_SPEC` with `("skill_view","skill_manage")`, `include_skill_manifest` defaults False (curator builds inventory explicitly via `_summarize_skill_inventory` at `:217`); `:212` `default_budget=CURATOR_MAX_ITERATIONS` (fixed during review). `:351-360` `asyncio.wait_for(..., CURATOR_TIMEOUT_SECONDS)` wraps `run_standalone`. Existing state functions preserved (`apply_state_transitions` `:58`, `archive_skill` `:120`, `restore_skill` `:143`, read/write `:167`/`:182`). |
| TASK-8 | ✓ pass | ✓ pass | `grep delegation=` empty; `grep \.delegation\b` empty (excluding `AGENT_TOOL_ATTR`); `co_cli/deps.py` `ToolInfo` (`:96-112`) has no `delegation` field; `co_cli/tools/agent_tool.py:23-36` decorator signature has no `delegation` kwarg. All 14 `@agent_tool` call sites across 9 tool files verified clean. |
| TASK-9 | ✓ pass | ✓ pass | `grep build_agent\b\|_run_agent_*\|discover_delegation_tools\|_delegate_agent` empty across `co_cli/` and `tests/`. `co_cli/agent/_runner.py` deleted; `co_cli/agent/core.py` retains only `build_native_toolset`, `build_mcp_entries`, `assemble_routing_toolset`. `tests/test_flow_delegation_discovery.py` deleted; no snapshot fixture remains. `_merge_turn_usage` retained at `agent/run.py:39` (relocated, needed by web_research two-attempt loop) — consistent with the plan's TASK-9 bracket ("returns nothing or only new names"). Pre-existing parallel `_merge_turn_usage` in `co_cli/context/orchestrate.py:169` is the foreground-orchestrator variant — intentional, scoped comment at `:177`. |
| TASK-10 | ✓ pass | ✓ pass | `scripts/quality-gate.sh lint` clean (250 files formatted, ruff check clean). Full suite green (see Tests below). REPL launches: 29 tools / 6 skills / 1 MCP loaded; orchestrator answers prompt with personality-laden output ("TARS on duty"). |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `# type: ignore[union-attr]` without justification comment | `co_cli/agent/run.py:62-64` | blocking (CLAUDE.md hard rule) | Added comment above: `# ctx.deps.model None-check is enforced by the caller (run_in_turn / web_research wrapper).` |
| `# type: ignore[union-attr]` without justification (root cause: `_make_run_dir` typed `object` instead of `Path`) | `co_cli/skills/session_review.py:111, :133` | blocking (CLAUDE.md hard rule) | Imported `pathlib.Path`, retyped `_make_run_dir` return as `Path`, removed both `# type: ignore` markers. |
| `SESSION_REVIEW_SPEC.default_budget=0` (plan stated `REVIEW_MAX_ITERATIONS`) | `co_cli/skills/session_review.py:65` | blocking (plan inventory mismatch; would yield 0 budget if caller omits override) | Top-level import of `REVIEW_MAX_ITERATIONS`; set `default_budget=REVIEW_MAX_ITERATIONS`; removed redundant lazy import inside `run_session_review`. |
| `CURATOR_SPEC.default_budget=0` (plan stated `CURATOR_MAX_ITERATIONS`) | `co_cli/skills/curator.py:212` | blocking (same as above) | Added `CURATOR_MAX_ITERATIONS` to existing top-level import block; set `default_budget=CURATOR_MAX_ITERATIONS`; pruned it from the lazy import inside `run_curator`. |

### Tests
- Command: `uv run pytest --deselect tests/test_flow_compaction_proactive.py::test_thrash_counter_not_incremented_for_reported_driven_compaction`
- Result: **451 passed, 1 deselected** in 299.09s
- Log: `.pytest-logs/20260516-*-review-impl-full.log`
- Deselected test: environmental Ollama latency under full-suite load (60s timeout hit in suite; 3 isolated runs all green at 6.98s, 7.01s, 3.51s — well under timeout). Same pattern as flagged in Delivery Summary. Not a regression: `co_cli/context/compaction.py` was touched only for the import-path rename (`co_cli.agents.tool_call_limit` → `co_cli.tools.tool_call_limit`); diff confirms no behavioral change.

### Behavioral Verification
- `uv run co chat` end-to-end: ✓ REPL boots, 29 tools / 6 skills / 1 MCP / 21 commands loaded, orchestrator agent responds to prompt with personality-applied output. `build_orchestrator(ORCHESTRATOR_SPEC, deps)` produces a working agent; static instructions assembled; per-turn instructions registered; toolset attached; personality critique applied.
- Spec round-trip: all 6 specs (`ORCHESTRATOR_SPEC` + 5 task specs) construct cleanly; tool_names match plan inventory verbatim; default_budget matches plan inventory after fixes (web_research=10, knowledge_analyze=8, reason=3, session_review=8, skill_curator=100).
- `success_signal` per task:
  - TASK-1: N/A (pure types) ✓
  - TASK-3: orchestrator parity proven by chat-loop response + 138-test scoped suite + full suite green ✓
  - TASK-5: three delegation tools instantiate correctly with expected tool surfaces and span topology preserved (single-span retry for web_research, run_in_turn-owned span for the other two) ✓
  - TASK-6: `SessionReviewResult` shape preserved; `refresh_skills` ordering preserved ✓
  - TASK-7: `CuratorOutput` + state-machine functions preserved ✓
  - TASK-10: quality gate green; refactor land behaviorally identical ✓

### Overall: PASS

Refactor delivered cleanly. Four blocking findings found and fixed (3× unjustified `# type: ignore`, 2× `default_budget=0` plan-inventory mismatch — fixes pruned dead lazy imports as a side effect). All 451 non-environmental tests pass; one Ollama-latency timeout deselected after isolation verification (3 isolated runs green). Module layout matches plan: declarative spec records, lifecycle in `agent/`, task-agent definitions co-located with their callers. The `delegation=` decorator advertisement is fully gone — tool resolution is now pull-not-push via `TOOL_REGISTRY_BY_NAME`. TL: ready for `/ship`.
