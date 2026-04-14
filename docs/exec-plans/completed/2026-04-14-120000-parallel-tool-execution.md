# Plan: Parallel Tool Execution — Write Serialization

**Task type:** code-feature

## Context

pydantic-ai dispatches multiple tool calls in parallel by default (asyncio.create_task) when the
model returns multiple tool_use blocks in one response. co-cli's `_register_tool()` wrapper does not
pass `sequential=True` on any tool, so write_file and edit_file can currently execute concurrently
within a single turn. When two parallel writes target the same path, `ResourceLockStore.try_acquire()`
raises `ResourceBusyError` → `tool_error()` → the model sees an error and must retry. For different
paths, concurrent writes succeed but ordering is non-deterministic.

The pydantic-ai `sequential=True` flag on a `ToolDefinition` causes `ToolManager.get_parallel_execution_mode()`
to return `'sequential'` for the entire batch if any tool in that batch is marked sequential. The
framework then runs the batch one-at-a-time, in declaration order. This is the correct idiomatic
mechanism for serializing mutation tools.

`ResourceLockStore` remains necessary as defense-in-depth for parent+subagent cross-call races (the
sequential flag only controls within-turn batch dispatch, not cross-agent concurrent calls).

**Workflow artifact hygiene:** No stale exec-plans related to tool registration. The tier2-tool-surface
plan (2026-04-09) touches tool surface but not registration flags — no overlap.

**Doc/source accuracy:** `docs/specs/tools.md` "Concurrency Safety" section correctly describes
ResourceLockStore. It does not claim serial-by-default; no stale claim to fix. Non-goals section
says "Parallel MCP execution across servers" — still accurate (cross-server parallelism is still
a non-goal).

## Problem & Outcome

**Problem:** write_file and edit_file lack `sequential=True`, so pydantic-ai can dispatch two write
calls concurrently within a single turn.

**Failure cost:** Same-path concurrent writes produce a `ResourceBusyError` tool_error; the model
sees an error, must interpret it, and may retry or ask the user for clarification — an unnecessary
round-trip. Different-path concurrent writes silently produce non-deterministic ordering.

**Outcome:** write_file and edit_file are marked `sequential=True` at registration. Any turn batch
containing either tool runs in declaration order. ResourceLockStore remains as defense-in-depth.
No user-visible behavior change for the common case (single write per turn); parallel writes become
ordered and error-free.

## Scope

**In:**
- Add `sequential=True` to `write_file` and `edit_file` in `_native_toolset._register_tool()` calls
- Add a test asserting the ToolDefinition sequential flag for write and edit tools
- Keep ResourceLockStore unchanged (cross-agent safety still requires it)

**Out:**
- save_article (UUID keys, no conflict possible)
- start_background_task (independent subprocess per call)
- create_gmail_draft (external resource, independent)
- run_shell_command (approval flow already gates dangerous commands; sequential would needlessly
  serialize shell+file batches)
- save_article (UUID keys, no conflict possible)
- start_background_task (independent subprocess per call)
- create_gmail_draft (external resource, independent)
- write_todos (writes session-scoped in-memory state, not filesystem paths; concurrent calls on
  different todo lists don't share a lock key; no read-modify-write window)
- run_shell_command (sequential would serialize shell+read batches; shell commands are independent
  by nature — each spawns a distinct subprocess, so there is no shared state to corrupt. The
  approval gate controls which commands execute; it does not address concurrency)
- Any changes to the approval model, resource lock implementation, or MCP toolsets

## Behavioral Constraints

- `sequential=True` serializes the ENTIRE batch if any tool in the batch is marked sequential.
  A read_file + write_file batch will run sequentially in model-return order (the order the model
  listed the calls in its response), not registration order. This is safe and expected — models
  nearly always read before writing, so the model-return order will typically be read-then-write.
- ResourceLockStore must not be removed. Parent+subagent concurrent calls operate outside the
  per-turn batch dispatch and are not affected by the sequential flag.
- No changes to `ToolInfo` schema or `tool_index`. `sequential` is a pydantic-ai `ToolDefinition`
  field, not a co-cli metadata field.

## High-Level Design

`FunctionToolset.add_function()` accepts `sequential: bool = False`. The `_register_tool()` helper
in `_native_toolset.py` currently forwards: `requires_approval`, `defer_loading`, `retries`. Add
`sequential` as a forwarded kwarg. Set `sequential=True` only on `write_file` and `edit_file`.

Dispatch path in pydantic-ai:
```
model returns [ToolCallPart("write_file", ...), ToolCallPart("edit_file", ...)]
  → ToolManager.get_parallel_execution_mode(calls)
      → tool_def.sequential is True for write_file → return 'sequential'
  → _agent_graph: for call in tool_calls:  # model-return order
        await handle_call_or_result(...)
```

Sequential execution order is model-return order — the order the model listed the calls in its
response. It is NOT toolset registration order.

If neither tool in a batch is sequential (e.g., two read_file calls), the batch still runs in
parallel — no regression.

## Implementation Plan

### ✓ DONE — TASK-1 — Add `sequential=True` to write_file and edit_file at registration

**files:** `co_cli/agent/_native_toolset.py`

Add `sequential: bool = False` parameter to `_register_tool()` and forward it to
`native_toolset.add_function()`. Set `sequential=True` on the two write-path tools:

```python
_register_tool(write_file, approval=True, sequential=True, visibility=_deferred_visible, retries=1)
_register_tool(edit_file,  approval=True, sequential=True, visibility=_deferred_visible, retries=1)
```

**done_when:**
`python -c "from co_cli.agent._native_toolset import _build_native_toolset; from co_cli.config._core import settings; ts, _ = _build_native_toolset(settings); print('ok')"` exits 0
— confirms the `sequential` kwarg is accepted and forwarded without error at import/call time.

**success_signal:** N/A for this registration-only change — TASK-2 provides behavioral verification.

---

### ✓ DONE — TASK-2 — Test: write_file and edit_file ToolDefinitions carry sequential=True

**files:** `tests/test_tool_registry.py`

**prerequisites:** [TASK-1]

Add a test that builds the native toolset and asserts `sequential=True` on write_file and edit_file,
`sequential=False` on read_file.

Use `FunctionToolset.get_tools(ctx)` — returns `dict[str, ToolsetTool]`; access `ToolDefinition`
via `.tool_def`. Import `_build_native_toolset` directly (acceptable: this tests internal
registration). Reuse `_make_ctx(_make_deps())` from the existing test module.

```python
@pytest.mark.asyncio
async def test_write_tools_are_sequential() -> None:
    toolset, _ = _build_native_toolset(_CONFIG)
    ctx = _make_ctx(_make_deps())
    tools = await toolset.get_tools(ctx)
    assert tools["write_file"].tool_def.sequential is True
    assert tools["edit_file"].tool_def.sequential is True
    assert tools["read_file"].tool_def.sequential is False
```

**done_when:**
`uv run pytest tests/test_tool_registry.py -x` passes with the new test included, asserting
`tools["write_file"].tool_def.sequential is True` and `tools["edit_file"].tool_def.sequential is True`.

**success_signal:** write_file and edit_file ToolDefinitions carry sequential=True; any parallel
write batch is serialized by the framework without producing a ResourceBusyError.

## Testing

- Red: add the assertions in TASK-2 first → they fail (sequential is False by default)
- Green: apply TASK-1 → assertions pass
- Existing `tests/test_resource_lock.py` and `tests/test_tools_files.py` must continue to pass —
  ResourceLockStore behavior is unchanged

## Open Questions

None — all questions answered by reading source files.
- "Does FunctionToolset.add_function() accept sequential?" → Yes, confirmed in pydantic-ai source
  (`toolsets/function.py`).
- "Does sequential affect the entire batch or just the flagged tool?" → Entire batch serializes if
  any tool has sequential=True (confirmed in `_tool_manager.py`).
- "Is ResourceLockStore still needed?" → Yes — it guards cross-agent concurrent calls, which bypass
  the per-turn sequential flag.

## Delivery Summary — 2026-04-14

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | python -c "from co_cli.agent._native_toolset import _build_native_toolset; ..." exits 0 | ✓ pass |
| TASK-2 | uv run pytest tests/test_tool_registry.py -x passes | ✓ pass |

**Tests:** full suite — 402 passed, 0 failed
**Independent Review:** clean
**Doc Sync:** fixed (added within-turn serialization paragraph to `docs/specs/tools.md` Concurrency Safety section)

**Overall: DELIVERED**
write_file and edit_file now carry sequential=True; any within-turn batch containing either tool is serialized by pydantic-ai in model-return order, eliminating ResourceBusyError races on same-path concurrent writes.

---

## Implementation Review — 2026-04-14

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | python -c "..." exits 0 | ✓ pass | `_native_toolset.py:74` — `sequential: bool = False` added; `:84` — forwarded in kwargs; `:134,137` — `write_file` and `edit_file` registered with `sequential=True` |
| TASK-2 | uv run pytest tests/test_tool_registry.py -x passes | ✓ pass | `test_tool_registry.py:212-214` — asserts `sequential is True` on write/edit, `sequential is False` on read; real toolset, no mocks |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -v`
- Result: 402 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: narrow — changes confined to `_native_toolset.py` and `tests/test_tool_registry.py`; `docs/specs/tools.md` already updated by delivery with correct within-turn serialization paragraph
- Result: clean

### Behavioral Verification
- `uv run co config`: ✓ healthy — system starts, all components report expected status
- No user-facing CLI surface changed — internal registration flag only; `success_signal` for TASK-1 is N/A; TASK-2 behavioral signal confirmed by passing test

### Overall: PASS
write_file and edit_file carry `sequential=True`; within-turn parallel write batches are serialized by the framework; ResourceLockStore preserved as cross-agent defense-in-depth; 402 tests green.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev parallel-tool-execution`
