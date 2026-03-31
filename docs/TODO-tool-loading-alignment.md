# TODO: Tool Loading And Exposure Alignment

**Slug:** `tool-loading-alignment`
**Task type:** `code-refactor` + orchestration/perf alignment
**Status:** Draft — awaiting Gate 1

---

## Context

Deep review of current `co` source and peer systems found that `co` is already
partly progressive, but only in one narrow place.

Today the runtime behavior is:

- native tools are registered eagerly in `_build_filtered_toolset()`
- unavailable integrations are excluded at registration time
- MCP tool names are discovered once at session startup via `discover_mcp_tools()`
- main turns expose the full registered tool surface
- approval-resume turns narrow schemas to the deferred tool names plus a small
  always-on set
- all tool execution remains lazy and call-driven

That is not bad, but it is not fully aligned with common peer practice. Peers
converge on:

- eager or session-level registry for built-ins
- lazy execution
- scoped tool visibility by mode, command, or turn
- connector/MCP connection or discovery only when needed, where practical

**Current-state validation notes:**

- `_build_filtered_toolset()` registers all native tools into one
  `FunctionToolset`, then wraps it with `.filtered(_filter)` in
  `co_cli/agent.py`.
- `_filter(...)` reads `ctx.deps.runtime.active_tool_filter`; when the filter is
  `None`, every registered tool is visible.
- `CoRuntimeState.active_tool_filter` is documented as a per-resume-segment
  filter and is reset to `None` at each `run_turn()` entry.
- `run_turn()` does not set `active_tool_filter` before the first segment, so
  main turns always expose the full tool surface.
- `_run_approval_loop()` does set `active_tool_filter` to the deferred tool
  names plus `_ALWAYS_ON_TOOL_NAMES`, so resume turns are already progressive.
- `_ALWAYS_ON_TOOL_NAMES` currently contains `check_capabilities`, `todo_read`,
  and `todo_write`.
- `discover_mcp_tools()` currently calls `list_tools()` on connected MCP
  servers and returns `(tool_names, errors)` only.
- `initialize_session_capabilities()` calls `discover_mcp_tools()` during
  startup and appends all discovered MCP tool names into
  `deps.capabilities.tool_names`.

**Peer-system convergence used in this review:**

- Gemini uses an explicit tool registry plus scoped configuration surfaces such
  as `coreTools`, `excludeTools`, and `allowedTools`.
- Claude plugin guidance documents on-demand MCP connection and command-level
  pre-allowed tool subsets.
- OpenCode exposes distinct `build` and `plan` modes with materially different
  access patterns.
- Codex normalizes internal and external tools through a unified routing layer
  rather than treating the entire tool surface as a flat always-visible set.

---

## Problem & Outcome

**Problem:** `co` already has lazy execution and one progressive schema path for
approval resumes, but the main turn still exposes the entire registered tool
surface. MCP capability discovery is also performed eagerly at session startup
for inventory purposes.

That creates three costs:

- the model sees more tool schema than it needs on ordinary turns
- startup work includes eager MCP tool listing even when the user never touches
  those tools
- `active_tool_filter` is a good mechanism but is artificially scoped to one
  orchestration phase

**Outcome:** keep eager native registration and lazy execution, but align the
runtime with best practice by making tool schema exposure progressive across all
turn phases and making MCP capability inventory less eagerly coupled to startup.

Target high-level behavior:

- built-in tools stay registered eagerly
- tool execution stays lazy
- main-turn schema exposure becomes scoped instead of all-tools-by-default
- approval-resume narrowing stays and becomes the general segment-filter model
- MCP discovery for inventory/reporting becomes less eager where possible

---

## Scope

In scope:

- `active_tool_filter` lifecycle and semantics
- main-turn tool schema exposure policy
- approval-resume exposure policy cleanup
- MCP discovery timing for capability inventory
- capability/status reporting implications of less-eager MCP discovery

Out of scope:

- changing tool implementations themselves
- replacing pydantic-ai `FunctionToolset.filtered(...)`
- redesigning approvals
- adding new tool families or connector types
- DESIGN doc edits as planned tasks; sync-doc happens after delivery

---

## Design Constraints

- Keep native tool registration in `_build_filtered_toolset()`.
- Keep tool execution lazy and call-driven.
- Reuse `active_tool_filter` unless a concrete blocker is found.
- Preserve `_ALWAYS_ON_TOOL_NAMES` semantics for tools needed during reduced
  schema turns.
- Avoid speculative intent classifiers or prompt-side hacks as the first step.
- Any MCP laziness change must preserve truthful diagnostics; status must not
  claim tools are available when they have not been verified.
- Changes should be incremental and backward-compatible with the current
  `build_agent()`, `build_task_agent()`, and `discover_mcp_tools()` structure.

---

## High-Level Design

### Loading model after the fix

Post-fix, the loading model should be:

- **registration:** eager for native tools
- **execution:** lazy
- **schema exposure:** progressive for all segments, not only approval resumes
- **MCP capability inventory:** deferred or cached where possible, instead of
  unconditional startup-wide enumeration

### Segment-scoped exposure using the existing filter

The current code already has the right primitive:
`ctx.deps.runtime.active_tool_filter`, consumed by `_filter(...)` in
`_build_filtered_toolset()`.

The alignment plan should extend that primitive from:

- resume segments only

to:

- main foreground segments
- approval-resume segments
- optional future mode-specific segments

without introducing a second schema-filter mechanism.

### Conservative first-step exposure policy

The first aligned version should not depend on speculative NLP routing.
Instead, it should use deterministic segment policy.

Recommended policy progression:

1. keep the current resume-time narrowing
2. add an explicit main-turn filter computation step before the first segment
3. start with a conservative default subset plus always-on tools
4. widen only when the current mode or command requires it

If the tool-taxonomy work lands first, this subset should be family-based.
If not, the first implementation may temporarily be name-based, but the final
target should consume structured family metadata.

### MCP discovery alignment

Current startup discovery is capability-inventory work, not execution work.
It exists so `deps.capabilities.tool_names` and diagnostics know all MCP tools
up front.

Best-practice alignment does not require removing all startup MCP handling.
It does require separating:

- "server configured"
- "server connected"
- "tool list verified"

Status and `/doctor` should report those states accurately instead of forcing
full tool enumeration at startup when not needed.

---

## Implementation Plan

### TASK-1: Generalize `active_tool_filter` from resume-only to segment-wide

**prerequisites:** none

Today `active_tool_filter` is documented and used as a resume-only schema
filter. Make it the canonical segment-scoped tool exposure mechanism.

**What to do:**

- Update `CoRuntimeState.active_tool_filter` comments to describe it as a
  per-segment schema filter, not only a resume filter.
- Add a small orchestration helper in `co_cli/context/_orchestrate.py` that
  computes the filter for a segment.
- Keep `_build_filtered_toolset()` and `_filter(...)` as the single schema-gate.
- Do not add a second filtering mechanism in prompt assembly or tool modules.

**files:**

- `co_cli/deps.py`
- `co_cli/context/_orchestrate.py`
- `co_cli/agent.py`

**done_when:**

- One mechanism, `active_tool_filter`, governs reduced schema exposure for all
  segment types.

---

### TASK-2: Apply progressive filtering on the first segment of `run_turn()`

**prerequisites:** TASK-1

Current `run_turn()` resets runtime state and immediately executes the first
stream segment with `active_tool_filter=None`, which exposes all registered
tools. Change that flow so the first segment uses an explicit policy.

**What to do:**

- Set `deps.runtime.active_tool_filter` before the first `_execute_stream_segment()`
  call in `run_turn()`.
- Use a deterministic default policy rather than a free-form intent guesser.
- Preserve `_ALWAYS_ON_TOOL_NAMES`.
- Ensure the filter is cleared or recomputed correctly when the turn exits.
- Keep approval-resume behavior unchanged until TASK-3 consolidates it.

**files:**

- `co_cli/context/_orchestrate.py`
- `co_cli/agent.py`

**done_when:**

- Main turns no longer implicitly expose every registered tool just because no
  filter was set.

---

### TASK-3: Unify main-turn and approval-resume exposure policy

**prerequisites:** TASK-1, TASK-2

After TASK-2, main turns and resume turns will both use the same primitive, but
the policy logic will still be split. Consolidate it so the orchestration path
is easier to reason about and maintain.

**What to do:**

- Replace the inline approval-loop filter construction with the shared segment
  policy helper.
- Keep the current resume rule intact:
  deferred tool names plus `_ALWAYS_ON_TOOL_NAMES`.
- Ensure the task agent path continues to see only the necessary schema subset
  during resume hops.
- Update comments in `_run_approval_loop()` and `_build_filtered_toolset()` to
  reflect the broader progressive-loading model.

**files:**

- `co_cli/context/_orchestrate.py`
- `co_cli/agent.py`

**done_when:**

- Main and resume segments use one explicit policy path for tool exposure.

---

### TASK-4: Reduce eager MCP inventory loading at startup

**prerequisites:** TASK-1

`discover_mcp_tools()` currently forces `list_tools()` during startup so
capability state can hold a flat MCP tool list. Align this with best practice by
making inventory less eager where practical.

**What to do:**

- Review whether startup must enumerate every MCP tool name, or whether it only
  needs per-server status at startup.
- If full enumeration is not required, defer `discover_mcp_tools()` until a path
  that truly needs exact MCP tool names.
- If exact names must still be shown somewhere, add a cached or on-demand path
  rather than unconditional startup enumeration.
- Preserve error capture for servers whose tool listing fails.
- Keep status truthful: distinguish configured, connected, and tool-list-verified.

**files:**

- `co_cli/bootstrap/_bootstrap.py`
- `co_cli/agent.py`
- `co_cli/tools/capabilities.py`
- `co_cli/bootstrap/_check.py`

**done_when:**

- MCP inventory work is no longer unconditionally coupled to session startup
  unless a concrete runtime requirement proves it must be.

---

### TASK-5: Make diagnostics reflect progressive and deferred loading truthfully

**prerequisites:** TASK-2, TASK-4

Progressive exposure and deferred MCP discovery change what the runtime knows at
any given moment. Diagnostics must become explicit about that.

**What to do:**

- Update `check_capabilities()` so it no longer assumes "all tool names are
  known up front" as an invariant.
- Adjust `/doctor` metadata and display text to report exposure and MCP state
  accurately.
- If startup stops enumerating MCP tool names, report server counts separately
  from verified tool counts.
- Keep the default output concise; expose extra detail as metadata rather than
  verbose prose by default.

**files:**

- `co_cli/tools/capabilities.py`
- `co_cli/bootstrap/_check.py`
- `co_cli/bootstrap/_render_status.py`
- `co_cli/bootstrap/_banner.py`

**done_when:**

- Diagnostics accurately distinguish registered tools, currently exposed tools,
  and verified MCP inventory.

---

### TASK-6: Add focused regression tests for exposure policy and MCP timing

**prerequisites:** TASK-1, TASK-2, TASK-3, TASK-4, TASK-5

This change alters orchestration behavior, not just docs or metadata. It needs
regression coverage at the policy boundary.

**What to do:**

- Extend tests around `build_agent()` / tool registration so registration stays
  eager even if exposure becomes scoped.
- Add orchestration tests proving the first segment uses a non-`None`
  `active_tool_filter` when the new policy applies.
- Keep approval-resume tests proving deferred tools still narrow correctly.
- Add bootstrap/capabilities tests covering any deferred or cached MCP
  enumeration path.
- Preserve current behavior assertions around `tool_names` and approvals unless
  the implementation intentionally changes them.

**files:**

- `tests/test_agent.py`
- `tests/test_bootstrap.py`
- `tests/test_capabilities.py`
- `tests/test_capabilities_mcp.py`
- `tests/` orchestration-focused test file if needed

**done_when:**

- The suite catches regressions in registration, exposure policy, and MCP
  inventory timing separately.

---

## Dependency Order

```text
TASK-1 generalize active_tool_filter semantics
  -> TASK-2 main-turn progressive filtering
  -> TASK-3 unify segment exposure policy
  -> TASK-5 diagnostics alignment
  -> TASK-6 tests

TASK-4 MCP inventory timing can proceed after TASK-1
```

Recommended ship order:

1. TASK-1
2. TASK-2
3. TASK-3
4. TASK-4
5. TASK-5
6. TASK-6

---

## Notes For Delivery

- This TODO is intentionally about tool loading and schema exposure, not about
  redefining the tool taxonomy itself.
- The taxonomy TODO and this loading TODO complement each other: taxonomy
  provides clean family metadata; loading alignment uses that metadata to decide
  what to expose when.
