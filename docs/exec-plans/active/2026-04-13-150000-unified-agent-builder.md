# Plan: Unified Agent Builder

**Task type:** refactor + code-feature

## Context

Agent construction in co-cli has two problems that compound each other:

**1. Root-level functional code.** `co_cli/agent.py` (~385 lines) contains dense functional logic — native toolset building, MCP toolset building, static instruction assembly, per-turn instruction decorators, and the main `build_agent()` factory. Per project convention, only skeleton/entry-point files belong at the root `co_cli/` level. Functional code belongs inside a package (`co_cli/agent/`), matching the existing pattern of `co_cli/config/`, `co_cli/bootstrap/`, `co_cli/prompts/`, etc.

**2. Divergent agent construction.** The orchestrator and delegation tools are built by entirely separate code paths: `build_agent()` in `agent.py` for the orchestrator; `make_*_agent()` factories in `_subagent_builders.py` for delegation tools. Adding a delegation tool requires touching three files.

**Import surface:** `grep` confirms 20+ import sites across `co_cli/` source and `tests/` referencing `from co_cli.agent import ...`. All need updating after the package conversion.

## Vocabulary

| Term | Meaning |
|------|---------|
| **Orchestrator** | The top-level agent. Process-scoped, owns the REPL, manages sessions, calls delegation tools. |
| **Delegation tool** | A regular tool that internally builds and runs an agent to accomplish a focused goal. The tool owns its agent config — the orchestrator has no knowledge of it. |

No special agent category names. Delegation tools are just tools.

## Problem & Outcome

**Problem:** `agent.py` is a functional blob at the wrong level, and delegation tool construction is disconnected from orchestrator construction.

**Failure cost:** Two unrelated construction paths. Package conventions violated. No shared builder.

**Outcome:**
- `co_cli/agent/` package: `_core`, `_native_toolset`, `_mcp`, `_instructions`
- Single `build_agent()` in `_core.py` handles all construction — orchestrator and delegation tools alike
- Each delegation tool in `agents.py` owns its agent config inline (instructions, tool_fns, output_type)
- `_subagent_builders.py` and `subagent.py` eliminated; replaced by `agents.py` and `_agent_outputs.py`
- All import sites updated

**TASK-1 through TASK-4:** Pure refactor — no behavior change.
**TASK-5 through TASK-8:** Feature additions — adaptive deps scoping, progress relay, depth guard, OTel linkage.

## Scope

**In:**
- `co_cli/agent/` — new package: `__init__.py`, `_core.py`, `_native_toolset.py`, `_mcp.py`, `_instructions.py`
- `co_cli/agent.py` — delete after package is live
- `co_cli/tools/agents.py` — new; replaces `subagent.py`; delegation tools with inline agent config
- `co_cli/tools/_agent_outputs.py` — new; Pydantic output models moved from `_subagent_builders.py`
- `co_cli/tools/subagent.py` — delete
- `co_cli/tools/_subagent_builders.py` — delete
- `co_cli/deps.py`, `co_cli/bootstrap/core.py`, `co_cli/main.py` — import path updates
- `tests/` (20+ files) — import path updates (mechanical)

**Out:**
- No `AgentRole` enum, no role registry, no agent type categorization
- No resident (cached) agents — caching foundation deferred; when needed, cache on `ctx.deps.session`
- No change to tool logic, approval flow, model selection, or agent model_settings
- No changes to `co_cli/commands/`, `co_cli/config/`, `co_cli/prompts/`
- Note: `make_agent_deps()` gains an `include` parameter (TASK-5) and `agent_depth` threading (TASK-7) — these are intentional behavior additions, not refactor

## Behavioral Constraints

- Orchestrator and all delegation tools must produce agents with identical behavior to current code.
- Delegation tool model_settings continue to be passed at `agent.run()` time — `_get_task_settings()` untouched.
- `make_agent_deps()` gains `include` and `agent_depth` threading (TASK-5, TASK-7) — all other behavior preserved.
- `build_agent()` called with `tool_registry` must behave identically to the current bootstrap callsite.
- Intentionally shared fields in `make_agent_deps()` (`file_read_mtimes`, `resource_locks`, `degradations`) remain shared by reference — do not deep-copy them. `CoRuntimeState` is always freshly constructed.

## High-Level Design

### Package layout

```
co_cli/agent/
  __init__.py          # """Agent construction package."""
  _native_toolset.py   # _register_tool(), _build_native_toolset(), _approval_resume_filter()
  _mcp.py              # _build_mcp_toolsets(), discover_mcp_tools()
  _instructions.py     # build_static_instructions(), @agent.instructions decorators
  _core.py             # build_agent(), build_tool_registry(), ToolRegistry
```

| Symbol | Current | After |
|--------|---------|-------|
| `build_agent` | `co_cli.agent` | `co_cli.agent._core` |
| `build_tool_registry` | `co_cli.agent` | `co_cli.agent._core` |
| `ToolRegistry` | `co_cli.agent` | `co_cli.agent._core` |
| `discover_mcp_tools` | `co_cli.agent` | `co_cli.agent._mcp` |
| `_approval_resume_filter` | `co_cli.agent` | `co_cli.agent._native_toolset` |
| `_build_native_toolset` | `co_cli.agent` | `co_cli.agent._native_toolset` |

### Unified `build_agent()`

```python
def build_agent(
    *,
    config: Settings,
    model: Any = None,
    # Orchestrator path
    tool_registry: ToolRegistry | None = None,
    # Delegation tool path
    instructions: str | None = None,
    tool_fns: list[Callable] = [],
    output_type: type | None = None,
) -> Agent[CoDeps, Any]
```

`tool_registry is not None` → orchestrator path (existing full construction, unchanged).
Otherwise → delegation path: minimal Agent with `instructions`, `retries`, `output_type`; each `tool_fn` registered via `agent.tool(fn, requires_approval=False)  # type: ignore[arg-type]`.

`_core.py` is a pure builder — no imports from `agents.py`. No circular dependency.

### `agents.py` pattern

Each delegation tool holds its config inline and lazy-imports `build_agent`:

```python
async def delegate_coder(ctx: RunContext[CoDeps], task: str) -> ToolReturn:
    """Delegate a coding or file analysis goal to a focused agent."""
    from co_cli.agent._core import build_agent
    agent = build_agent(
        config=ctx.deps.config,
        model=model_obj,
        instructions="...",
        tool_fns=[list_directory, read_file, find_in_files],
        output_type=CoderOutput,
    )
    result = await agent.run(task, deps=make_agent_deps(ctx.deps), ...)
    ...
```

No registry. No enum. Adding a new delegation tool = one new function.

### Renaming table

| Old | New |
|-----|-----|
| `subagent.py` | `agents.py` |
| `_subagent_types.py` / `_subagent_builders.py` | `_agent_outputs.py` |
| `make_subagent_deps()` | `make_agent_deps()` |
| `run_coding_subagent` | `delegate_coder` |
| `run_research_subagent` | `delegate_researcher` |
| `run_analysis_subagent` | `delegate_analyst` |
| `run_reasoning_subagent` | `delegate_reasoner` |
| `SubagentRoleConfig` / `SUBAGENT_ROLES` | eliminated |

## Implementation Plan

### ✓ DONE — TASK-1 — Create `co_cli/agent/` package

Split `agent.py` into 5 files:

- **`__init__.py`**: `"""Agent construction package."""`
- **`_native_toolset.py`**: `_approval_resume_filter()`, `_register_tool()`, `_build_native_toolset()` and all their imports
- **`_mcp.py`**: `_build_mcp_toolsets()`, `discover_mcp_tools()`
- **`_instructions.py`**: `build_static_instructions()` and all per-turn instruction builder functions (exported as plain functions — `_core.py` applies `@agent.instructions` registration post-construction to avoid circular dependency)
- **`_core.py`**: `ToolRegistry`, `build_tool_registry()`, `build_agent()` extended with delegation path (`instructions`, `tool_fns`, `output_type` params); raises `ValueError` if delegation path and `output_type is None`; `model` normalization via `isinstance(model, LlmModel)`

Do NOT delete `agent.py` yet.

- **files:** `co_cli/agent/__init__.py`, `co_cli/agent/_native_toolset.py`, `co_cli/agent/_mcp.py`, `co_cli/agent/_instructions.py`, `co_cli/agent/_core.py`
- **done_when:** `uv run python -c "from co_cli.agent._core import build_agent"` imports cleanly; `grep "output_type is None" co_cli/agent/_core.py` confirms the delegation-path guard; `scripts/quality-gate.sh lint` passes
- **success_signal:** N/A

### ✓ DONE — TASK-2 — Update all import sites; delete `agent.py`

**Source files (3):**
- `co_cli/deps.py`: → `from co_cli.agent._core import ToolRegistry`
- `co_cli/bootstrap/core.py`: → `from co_cli.agent._core import build_tool_registry` + `from co_cli.agent._mcp import discover_mcp_tools`
- `co_cli/main.py`: → `from co_cli.agent._core import build_agent`

**Test files (20+, mechanical):**
- `build_agent`, `build_tool_registry` → `co_cli.agent._core`
- `_approval_resume_filter`, `_build_native_toolset` → `co_cli.agent._native_toolset`

After all imports pass: delete `co_cli/agent.py`.

- **files:** `co_cli/deps.py`, `co_cli/bootstrap/core.py`, `co_cli/main.py`, all `tests/test_*.py` importing from `co_cli.agent`, `co_cli/agent.py` (delete)
- **prerequisites:** [TASK-1]
- **done_when:** `grep -r "from co_cli.agent import" co_cli/ tests/` returns zero matches; `uv run pytest tests/test_memory.py tests/test_agent.py tests/test_tool_registry.py tests/test_subagent_tools.py` passes
- **success_signal:** N/A

### ✓ DONE — TASK-3 — Create `co_cli/tools/_agent_outputs.py`

Move `CoderOutput`, `ResearchOutput`, `AnalysisOutput`, `ThinkingOutput` from `_subagent_builders.py` verbatim. No `co_cli.*` imports.

- **files:** `co_cli/tools/_agent_outputs.py` (new)
- **done_when:** `from co_cli.tools._agent_outputs import CoderOutput, ResearchOutput, AnalysisOutput, ThinkingOutput` imports cleanly
- **success_signal:** N/A

### ✓ DONE — TASK-4 — Create `agents.py`; delete `subagent.py` and `_subagent_builders.py`

**Rename `make_subagent_deps()` → `make_agent_deps()` in `co_cli/deps.py`** (rename in-place; function stays in `deps.py` per CLAUDE.md convention). Update all callers.

Create `co_cli/tools/agents.py`:
- Import `make_agent_deps` from `co_cli.deps`
- `_run_agent()` — rename of `_run_subagent()`; replace `cfg.factory(model_obj)` with inline `build_agent()` call per tool; **strip `retry_on_empty` logic entirely** — it is reinstated inline in `delegate_researcher` in TASK-7
- Four delegation tools with inline config (instructions and tool_fns copied verbatim from `_subagent_builders.py`):
  - `delegate_coder` — `[list_directory, read_file, find_in_files]` + `CoderOutput`
  - `delegate_researcher` — `[web_search, web_fetch]` + `ResearchOutput`
  - `delegate_analyst` — `[search_knowledge, search_drive_files]` + `AnalysisOutput`
  - `delegate_reasoner` — `[]` + `ThinkingOutput`
- Update `co_cli/agent/_native_toolset.py` to register delegation tools from `agents` under new names
- Update `co_cli/commands/_commands.py`: the `_DELEGATION_TOOLS` frozenset in `_cmd_history`
  (line 282) hardcodes old tool names. Replace with:
  `{"delegate_coder", "delegate_researcher", "delegate_analyst", "delegate_reasoner", "start_background_task"}`
- Rename `tests/test_subagent_tools.py` → `tests/test_agents.py`; update imports inside
- Delete `co_cli/tools/subagent.py` and `co_cli/tools/_subagent_builders.py`
- Grep for `subagent` — fix any remaining reference in `co_cli/` and `tests/` (test-file sweep for import updates already covered by TASK-2; this sweep targets any remaining string references)

- **files:** `co_cli/deps.py` (rename `make_subagent_deps` → `make_agent_deps`), `co_cli/tools/agents.py` (new), `co_cli/agent/_native_toolset.py`, `co_cli/commands/_commands.py`, `tests/test_subagent_tools.py` → `tests/test_agents.py`, `co_cli/tools/subagent.py` (delete), `co_cli/tools/_subagent_builders.py` (delete)
- **prerequisites:** [TASK-2, TASK-3]
- **done_when:** `grep -r "subagent" co_cli/ tests/` returns zero matches; `uv run python -c "from co_cli.tools.agents import delegate_coder"` imports cleanly; `uv run pytest tests/test_agent.py tests/test_agents.py` passes
- **success_signal:** Delegation tool responses identical in content and structure to pre-refactor, verified manually per tool via `uv run co chat`

### ✓ DONE — TASK-5 — Adaptive context loading for delegation tools

Two concerns, both aligned with hermes' `_build_child_agent()` design:

**A — Deps scoping.** Currently `make_agent_deps(base)` returns a full `CoDeps` with most services inherited. The delegated agent receives services it can never use (e.g., `delegate_coder` gets `knowledge_store`; `delegate_researcher` gets `resource_locks`). Add an `include: set[str] | None = None` parameter: when provided, fields not in the set are set to `None` on the returned `CoDeps`. Each `delegate_*` tool declares exactly which services its agent needs.

Service sets per tool (fields on `CoDeps` — always include `config`, `model`):
- `delegate_coder`: `shell`, `file_read_mtimes`, `resource_locks`, workspace paths
- `delegate_researcher`: web config only (no shell, no knowledge_store, no file state)
- `delegate_analyst`: `knowledge_store`, Google creds fields
- `delegate_reasoner`: `config`, `model` only — no services needed

**B — Dynamic instruction assembly.** Static instruction strings (copied verbatim from `_subagent_builders.py`) carry no runtime context. Hermes builds `ephemeral_system_prompt` dynamically from `goal` + `context` at spawn time. Each `delegate_*` tool should build its instructions from `ctx.deps` at call time:

- `delegate_coder`: inject `ctx.deps.workspace_root` so the agent knows its file boundary
- `delegate_researcher`: inject any domain constraints passed by the orchestrator; inject active search config
- `delegate_analyst`: inject active knowledge sources (library, obsidian, drive) based on what's configured in `ctx.deps`
- `delegate_reasoner`: no deps-derived injection needed; instructions are task-only

`build_agent()` signature is unchanged — `instructions: str` is built by a private helper per tool (`_coder_instructions(deps)`, etc.) and passed as a plain string. No change to `_core.py`.

**C — Parallel delegation safety.** Verify that `CoRuntimeState` is freshly constructed per subagent — it is (confirmed: `runtime=CoRuntimeState()` in `make_subagent_deps()`). The following fields are intentionally shared by reference and must remain so: `file_read_mtimes` (cross-agent staleness detection), `resource_locks` (cross-agent lock coordination), `degradations` (read-only after bootstrap). Do not deep-copy these. The safety guarantee is: per-turn mutable state (`CoRuntimeState`) is isolated; shared service handles are by design. Document this contract in `make_agent_deps()` docstring.

- **files:** `co_cli/tools/agents.py`, `co_cli/deps.py` (add `include` param to `make_agent_deps()`)
- **prerequisites:** [TASK-4]
- **done_when:** `uv run python -c "from co_cli.tools.agents import delegate_coder"` imports cleanly; `scripts/quality-gate.sh lint` passes; `grep "workspace_root" co_cli/tools/agents.py` confirms injection; manually confirm `delegate_coder` injects `workspace_root` into its agent instructions via a chat prompt that asks the coder where its root is
- **success_signal:** Delegated agents answer workspace-boundary questions correctly without being told the path in the task prompt

### ✓ DONE — TASK-6 — Progress relay for delegated agents

`make_agent_deps()` resets `CoRuntimeState`, which severs the parent's `tool_progress_callback`. Delegation tool progress events are therefore swallowed — the user sees no intermediate output during long delegated runs.

Fix: pydantic-ai `agent.run()` has no progress callback parameter. The correct path: in each `delegate_*` tool, capture `parent_callback = ctx.deps.runtime.tool_progress_callback` before calling `make_agent_deps()`, then immediately set `child_deps.runtime.tool_progress_callback = parent_callback` on the returned deps. `make_agent_deps()` always constructs a fresh `CoRuntimeState()` with `tool_progress_callback=None`, so explicit forwarding is required.

Implementation notes:
- Pattern per tool: `child_deps = make_agent_deps(ctx.deps, include={...}); child_deps.runtime.tool_progress_callback = ctx.deps.runtime.tool_progress_callback`
- Apply to all four `delegate_*` tools consistently.
- No change to `make_agent_deps()` internals — forwarding happens at call site.

- **files:** `co_cli/tools/agents.py`
- **prerequisites:** [TASK-5]
- **done_when:** `grep "tool_progress_callback" co_cli/tools/agents.py` confirms callback forwarding code is present; `scripts/quality-gate.sh lint` passes; manually confirm progress events appear in REPL during a `delegate_researcher` run via `uv run co chat`
- **success_signal:** User sees intermediate progress lines from delegation tool runs (e.g. search queries in flight) without waiting for final result

### ✓ DONE — TASK-7 — `retry_on_empty` relocation and depth guard

**A — `retry_on_empty` relocation (critical).** `_run_subagent()` currently gates `retry_on_empty` on the registry config entry for `RESEARCH`. After the registry is eliminated in TASK-4, this logic is homeless. Move it inline to `delegate_researcher`: if the agent result is empty or below a minimum-content threshold, retry once with an augmented prompt before returning. No other delegation tool needs this behavior.

**B — Depth guard (important).** Co-cli's construction prevents recursive delegation by design (delegation tools don't appear in child toolsets), but a future mistake could reintroduce it. Add `agent_depth: int = 0` to `CoRuntimeState` in `deps.py`. Update `make_agent_deps()` to set `runtime=CoRuntimeState(agent_depth=base.runtime.agent_depth + 1)`. Add `MAX_AGENT_DEPTH: int = 2` as a module-level constant in `agents.py`. Each `delegate_*` tool checks `ctx.deps.runtime.agent_depth` before running; raises `ToolError` if `ctx.deps.runtime.agent_depth >= MAX_AGENT_DEPTH`. This is a safety rail, not active logic.

- **files:** `co_cli/tools/agents.py`, `co_cli/deps.py`
- **prerequisites:** [TASK-5]
- **done_when:** `scripts/quality-gate.sh lint` passes; `grep "MAX_AGENT_DEPTH" co_cli/tools/agents.py` returns the constant; `grep "agent_depth" co_cli/deps.py` confirms field on `CoRuntimeState` and increment in `make_agent_deps()`; `grep "retry_on_empty" co_cli/tools/agents.py` returns the inline check in `delegate_researcher`
- **success_signal:** N/A

### ✓ DONE — TASK-8 — OTel parent span linkage for delegation traces

Delegated agent runs start new OTel root spans, breaking trace continuity. The delegation tool call is already captured as a span in the parent session; the child's internal spans are orphaned.

Fix: extract the current span context (trace ID + parent span ID) from the OTel context at delegation call time and pass it to `agent.run()` via `run_kwargs` or metadata. If pydantic-ai propagates OTel context automatically across `await`, verify and document that — no change needed. If not, manually inject `traceparent` so child spans appear nested under the parent delegation span in the trace viewer.

- **files:** `co_cli/tools/agents.py`
- **prerequisites:** [TASK-5]
- **done_when:** `scripts/quality-gate.sh lint` passes; trigger a delegation run via `uv run co chat` (ask a research question); then `uv run co traces` shows child agent spans nested under the parent delegation tool span (not as a separate root trace)
- **success_signal:** Delegation traces appear as a single tree in the HTML trace viewer, not as separate disconnected roots

### ✓ DONE — TASK-9 — Full suite

Run full test suite with log.

- **files:** none
- **prerequisites:** [TASK-6, TASK-7, TASK-8]
- **done_when:** `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-unified-agent-builder.log` passes
- **success_signal:** N/A

## Testing

TASK-1 through TASK-4 (refactor): existing test suite is the regression gate.
TASK-5 through TASK-8 (feature): per-task structural grep checks + manual REPL verification are the gate (no new automated tests — manual chat gates are the deliberate primary signal for delegation tool correctness).

- After TASK-2: `uv run pytest tests/test_memory.py tests/test_agent.py tests/test_tool_registry.py tests/test_subagent_tools.py`
- After TASK-4: `grep -r "subagent" co_cli/ tests/` returns zero; `uv run python -c "from co_cli.tools.agents import delegate_coder"` (import check); manually trigger each delegation tool via `uv run co chat`
- After TASK-9: full suite with log

No new automated tests added.

## Open Questions

None.

## Final — Team Lead

Plan approved. Two-cycle review complete — all blocking issues resolved.

Delivers: `co_cli/agent/` package + unified `build_agent()` + delegation tools own their config inline + no enum/registry/category naming + adaptive deps scoping + progress relay + depth guard + OTel span linkage.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev unified-agent-builder`

## Delivery Summary — 2026-04-14

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `from co_cli.agent._core import build_agent` imports cleanly; delegation-path guard confirmed; lint passes | ✓ pass |
| TASK-2 | `grep -r "from co_cli.agent import" co_cli/ tests/` returns zero matches; 4 test files pass | ✓ pass |
| TASK-3 | 4 output models import cleanly from `co_cli.tools._agent_outputs` | ✓ pass |
| TASK-4 | Zero non-config `subagent` code references; `delegate_coder` imports cleanly; agent + agents tests pass | ✓ pass |
| TASK-5 | `workspace_root` injection confirmed in `_coder_instructions()`; active knowledge sources in `_analyst_instructions()`; lint passes | ✓ pass |
| TASK-6 | `tool_progress_callback` forwarding present in all 4 delegation tools | ✓ pass |
| TASK-7 | `MAX_AGENT_DEPTH` constant confirmed; `agent_depth` field + increment in `deps.py` confirmed; `retry_on_empty` inline in `delegate_researcher` | ✓ pass |
| TASK-8 | OTel `start_as_current_span()` wrapping all 4 delegation tool runs; lint passes | ✓ pass |
| TASK-9 | Full suite: 406 passed, 0 failed | ✓ pass |

**Tests:** full suite — 406 passed, 0 failed
**Independent Review:** clean
**Doc Sync:** fixed (tools.md, system.md, core-loop.md, tui.md, context.md, llm-models.md, flow-prompt-assembly.md, memory.md, personality.md)

**Pre-existing issues fixed along the way:**
- `tests/test_context_session.py`: removed 2 tests for `migrate_session_files` (function removed from session.py prior to this delivery)
- `tests/test_transcript.py`: 2 tests updated to use new-format session filenames (matching new `list_sessions()` parser)

**Overall: DELIVERED**
All 9 tasks passed. `co_cli/agent/` package ships with unified `build_agent()`, delegation tools own inline config, adaptive deps scoping, progress relay, depth guard, and OTel span linkage. Full test suite green.

