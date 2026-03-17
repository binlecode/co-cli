# TODO: Remove `all_approval` Flag from `get_agent()`

**Task type: refactor** — eval infrastructure cleanup; no behavior change in production.

## Context

`get_agent()` in `co_cli/agent.py` has an `all_approval: bool = False` parameter that,
when `True`, registers every tool with `requires_approval=True`. This forces all tool
calls to return `DeferredToolRequests` without executing, so tests can inspect tool
selection and args without side effects.

This is eval-specific behavior baked into production registration code. It is the wrong
layer: production code should not change behavior based on a test flag. The same
interception is available at the eval layer via pydantic-ai's `agent.iter()` event
stream, which emits `FunctionToolCallEvent` before the tool executes — letting tests
capture tool name and args, then break the loop without running the tool.

The flag also has an existing gap: sub-agents spawned by `delegate_research` and
`delegate_analysis` ignore `all_approval` (they hardcode `requires_approval=False`),
so evals that call those delegation tools get real network calls instead of deferred
intercepts.

**Prerequisite:** `docs/TODO-delegation-policy-gate.md` must ship first. Once the
policy gate lands, `delegate_research` and `delegate_analysis` raise `ModelRetry` when
web policy is non-`"allow"` — the sub-agent gap becomes moot in evals that run with
default settings.

## Problem & Outcome

**Problem:** `all_approval` in `get_agent()` is test-only infrastructure in production
code. It also fails silently for sub-agents.

**Outcome:** `all_approval` parameter removed from `get_agent()`. The four test
call sites are rewritten to use `agent.iter()` + `FunctionToolCallEvent`.
`get_agent()` is a pure production factory.

## Scope

**In:** Remove `all_approval` from `get_agent()` and all call sites. Rewrite the four
affected tests to use the event stream.

**Out:** Logic changes to any tool. Changes to `web_policy` wiring (covered by
`TODO-delegation-policy-gate.md`). No new config settings.

## Affected Call Sites

| File | Lines | Usage |
|------|-------|-------|
| `tests/test_agent.py` | 123 | `test_all_approval_gates_precision_write_tools` — tests the flag itself; **delete this test** |
| `tests/test_tool_calling_functional.py` | 93 | `test_tool_selection_and_arg_extraction` — checks tool name + args; rewrite to event stream |
| `tests/test_tool_calling_functional.py` | 160 | `test_refusal_no_tool_for_simple_math` — checks no tool fires; rewrite to event stream |
| `tests/test_tool_calling_functional.py` | 180 | `test_intent_routing_observation_no_tool` — checks no tool fires; rewrite to event stream |

## Implementation Plan

### TASK-1 — Rewrite `test_tool_selection_and_arg_extraction`

Replace `get_agent(all_approval=True)` + `DeferredToolRequests` inspection with
`agent.iter()`. Use `FunctionToolCallEvent` to capture the first tool call and break:

```python
# pseudocode — capture first tool call from event stream
async with agent.iter(prompt, deps=deps, ...) as run:
    async for event in run:
        if isinstance(event, FunctionToolCallEvent):
            tool_name = event.part.tool_name
            args = event.part.args_as_dict()
            # assert tool_name and args, then return
            break
```

Remove `DeferredToolRequests` import if no longer used.

**files:**
- `tests/test_tool_calling_functional.py` — rewrite `test_tool_selection_and_arg_extraction`

**done_when:** Test passes without `all_approval=True` or `DeferredToolRequests`.

---

### TASK-2 — Rewrite `test_refusal_no_tool_for_simple_math` and `test_intent_routing_observation_no_tool`

Both tests assert that no tool call fires. Replace with event stream: run through all
events and assert no `FunctionToolCallEvent` is emitted.

```python
# pseudocode — assert no tool call fires
async with agent.iter(prompt, deps=deps, ...) as run:
    async for event in run:
        assert not isinstance(event, FunctionToolCallEvent), \
            f"Expected no tool call, got {event.part.tool_name!r}"
```

**files:**
- `tests/test_tool_calling_functional.py` — rewrite both no-tool tests

**done_when:** Both tests pass without `all_approval=True`.

---

### TASK-3 — Delete `test_all_approval_gates_precision_write_tools`

This test exists solely to verify the `all_approval` flag mechanism. Once the flag is
removed, the test is stale. Delete it.

**files:**
- `tests/test_agent.py` — delete `test_all_approval_gates_precision_write_tools`

**done_when:** Test deleted; `all_approval` no longer referenced in `test_agent.py`.

---

### TASK-4 — Remove `all_approval` from `get_agent()`

Remove the parameter, the `all_approval` usages in `_register()` calls, and the
inline comment at line 253. The `_register()` calls revert to their natural approval
values (each tool's correct production default).

**files:**
- `co_cli/agent.py` — remove `all_approval` parameter and all usages

**done_when:** `grep -n "all_approval" co_cli/agent.py` returns no matches.

---

### TASK-5 — Regression tests

```bash
mkdir -p .pytest-logs
uv run pytest tests/test_agent.py tests/test_tool_calling_functional.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-agent-tool.log
uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log
```

**done_when:** Full pytest suite exits 0 with no `all_approval` references in source.

---

## Testing Strategy

The rewritten tests exercise the same behavioral assertions (tool selected, args correct,
no tool fired) via the event stream instead of the deferred-approval hack. No new test
scope — same coverage, better mechanism.

## Open Questions

None — event stream API (`agent.iter()`, `FunctionToolCallEvent`) is stable in
pydantic-ai and already imported in the orchestration layer.
