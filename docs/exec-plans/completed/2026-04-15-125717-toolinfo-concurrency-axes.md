# Plan: ToolInfo Concurrency Axes

**Task type: refactor** — schema addition + registration simplification; observable serialization behavior is unchanged.

---

## Context

`ToolInfo` (co_cli/deps.py:76) is the canonical metadata record for each registered tool. It currently carries seven fields: `name`, `description`, `approval`, `source`, `visibility`, `integration`, `max_result_size`. There is no `is_read_only` or `is_concurrent_safe` field.

Concurrency policy is instead encoded two ways that duplicate each other:

1. `sequential=True` is passed explicitly to `_register_tool()` for `write_file` and `patch` (co_cli/agent/_native_toolset.py:134-137). The SDK uses this to serialize any batch containing those tools.
2. `ResourceLockStore.try_acquire()` is called inside `write_file` and `patch` at execution time (co_cli/tools/files.py:368, 570) as a runtime guard against concurrent writes from parallel agents.

The two mechanisms protect against different failure modes:
- `sequential=True` prevents intra-batch parallel dispatch (pydantic-ai serializes the tool call batch).
- `ResourceLockStore` prevents cross-agent races between parent + background delegation agents, which run in separate asyncio tasks and are not coordinated by the SDK sequential flag.

The **duplication** is between `sequential=True` (explicit param) and the declarative intent of "this tool is not safe to run concurrently." One signal in `ToolInfo` should drive the SDK flag; the explicit param should disappear.

The **gap** is `is_read_only`, which currently has no representation at all. It is a distinct axis: a tool can be `is_read_only=False, is_concurrent_safe=True` (e.g. `save_article` — writes, but to UUID-keyed storage with no shared path conflict). Without `is_read_only`, the approval bit conflates "requires approval" with "never mutates state."

No existing exec-plan covers this. No related DESIGN doc is stale on these fields — `docs/specs/tools.md` describes registration metadata at a high level and does not enumerate ToolInfo fields explicitly.

---

## Problem & Outcome

**Problem:** `ToolInfo` has no `is_read_only` or `is_concurrent_safe` fields. The `sequential=True` flag on write tools is an explicit registration parameter that duplicates what should be a declarative property of the tool contract.

**Failure cost:** Adding a new write tool requires knowing to pass `sequential=True` at registration — there is no ToolInfo field to consult and no test that enforces it at the schema level. A new write tool that omits `sequential=True` silently loses serialization. Additionally, the dispatch layer cannot make policy decisions from metadata because no such metadata exists.

**Outcome:** `ToolInfo` carries `is_read_only` and `is_concurrent_safe`. The `sequential` SDK flag is derived from `not is_concurrent_safe` inside `_register_tool`, eliminating the explicit `sequential=True` call-site parameter. All native tools are annotated with correct values. `ResourceLockStore` is retained for cross-agent coordination (unchanged).

---

## Scope

**In scope:**
- Add `is_read_only: bool = False` and `is_concurrent_safe: bool = False` to `ToolInfo`
- Update `_register_tool()` signature: remove `sequential` param, add `is_read_only` and `is_concurrent_safe`, derive `sequential = not is_concurrent_safe`
- Annotate all native tool registrations with correct axis values
- Update tests to cover new fields and remove `sequential=True` as an explicit assertion point (replace with ToolInfo field assertions)

**Out of scope:**
- Removing `ResourceLockStore` — still required for cross-agent contention (parent + background delegate); not duplicated
- Removing mtime staleness checks — different concern (inter-turn external file changes)
- Building a dispatch scheduler that uses these fields to enable parallel read batches — future capability
- Changing actual serialization behavior: `write_file` and `patch` must serialize exactly as they do today

---

## Behavioral Constraints

- `write_file` and `patch` must continue to have `tool_def.sequential is True` in the SDK toolset after this change.
- `read_file`, `glob`, `grep` must continue to have `tool_def.sequential is False`.
- `save_article`, `run_shell_command`, `write_todos` must continue to have `tool_def.sequential is False`.
- All existing tests in `tests/test_tool_registry.py` must pass without modification except for the sequential tests, which are updated to also assert `ToolInfo` field values.
- MCP tool `ToolInfo` entries created by `discover_mcp_tools()` must receive `is_read_only=False, is_concurrent_safe=False` via field defaults (no code change required in `_mcp.py`). Note: `is_concurrent_safe` is informational only for MCP tools — the SDK `sequential` flag does not apply to `DeferredLoadingToolset`; enforcement for MCP tools remains with `ResourceLockStore`.
- `ResourceLockStore` and mtime checks in `co_cli/tools/files.py` must not be touched.

---

## High-Level Design

### ToolInfo schema after this change

```python
@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    approval: bool
    source: ToolSourceEnum
    visibility: VisibilityPolicyEnum
    integration: str | None = None
    max_result_size: int = 50_000
    is_read_only: bool = False        # ← new: tool never mutates any state
    is_concurrent_safe: bool = False  # ← new: tool may run in parallel with others
```

Defaults `False, False` are conservative — untrusted (MCP) tools get safe values with no code change.

### Axis semantics

| is_read_only | is_concurrent_safe | Meaning | SDK sequential |
|---|---|---|---|
| True | True | Read-only; parallel-safe | False |
| False | True | Writes, but to independent resource (UUID key, subprocess, in-memory) | False |
| False | False | Writes to a potentially-shared file path | True |
| True | False | **INVALID** — asserted at registration | — |

`is_read_only=True` implies `is_concurrent_safe=True`. The `_register_tool()` helper asserts this invariant: `assert not (is_read_only and not is_concurrent_safe)`.

### `_register_tool()` signature change

Before:
```python
def _register_tool(fn, *, approval=False, sequential=False, visibility, ...)
```

After:
```python
def _register_tool(fn, *, approval=False, is_read_only=False, is_concurrent_safe=False, visibility, ...)
    # derive sequential from concurrency declaration
    sequential = not is_concurrent_safe
```

### Native tool annotations

| Tool | is_read_only | is_concurrent_safe | Rationale |
|---|---|---|---|
| check_capabilities | True | True | pure introspection |
| read_todos | True | True | reads session state |
| write_todos | False | True | writes in-memory session list; no file path conflict |
| search_memories, search_knowledge, search_articles, read_article, list_memories | True | True | reads |
| glob, read_file, grep | True | True | reads |
| web_search, web_fetch | True | True | reads (external) |
| run_shell_command | False | True | independent subprocess; in-process state not shared |
| write_file | False | False | writes to a shared file path |
| patch | False | False | read-modify-write on a shared file path |
| save_article | False | True | writes to UUID-keyed store; no path conflict |
| start_background_task | False | True | creates new task ID; no shared resource |
| check_task_status | True | True | reads task state |
| cancel_background_task | False | True | mutates task state; no file path conflict |
| list_background_tasks | True | True | reads task state |
| delegate_coder/researcher/analyst/reasoner | False | True | spawns subagent; no local resource conflict at spawn |
| session_search | True | True | reads session history |
| list_notes, search_notes, read_note | True | True | Obsidian reads |
| search_drive_files, read_drive_file | True | True | Drive reads |
| list_gmail_emails, search_gmail_emails | True | True | Gmail reads |
| list_calendar_events, search_calendar_events | True | True | Calendar reads |
| create_gmail_draft | False | True | external API write; no local file path conflict |

### MCP tools

`discover_mcp_tools()` constructs `ToolInfo(...)` without `is_read_only` or `is_concurrent_safe`. Field defaults (`False, False`) apply automatically — no change to `_mcp.py`.

For MCP tools, `is_concurrent_safe=False` in `ToolInfo` is informational only: the `sequential` SDK flag is set on `FunctionToolset` tools, not on `DeferredLoadingToolset` (MCP) tools. Enforcement for MCP tools remains with `ResourceLockStore`.

---

## Implementation Plan

### ✓ DONE — TASK-1 — Add `is_read_only` and `is_concurrent_safe` to `ToolInfo`

```
files:
  - co_cli/deps.py

done_when: >
  uv run pytest tests/test_tool_registry.py -x passes with zero changes to any
  test file (new fields use defaults; all existing ToolInfo(...) constructions remain valid).
  Also: python -c "from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum;
  t = ToolInfo(name='x', description='x', approval=False,
  source=ToolSourceEnum.NATIVE, visibility=VisibilityPolicyEnum.ALWAYS);
  assert hasattr(t, 'is_read_only') and hasattr(t, 'is_concurrent_safe');
  assert t.is_read_only is False; assert t.is_concurrent_safe is False; print('ok')" exits 0.

success_signal: N/A

prerequisites: []
```

**Implementation notes:**
- Append `is_read_only: bool = False` and `is_concurrent_safe: bool = False` after `max_result_size` in the `ToolInfo` dataclass.
- Defaults must be `False` (not `True`) — fail-safe for untrusted tools.
- No changes to `_mcp.py` — existing `ToolInfo(...)` calls receive defaults.
- No changes to any test file — all existing `ToolInfo(...)` constructions remain valid.

---

### ✓ DONE — TASK-2 — Update `_register_tool`, annotate all tools, update tests

```
files:
  - co_cli/agent/_native_toolset.py
  - tests/test_tool_registry.py

done_when: >
  uv run pytest tests/test_tool_registry.py -x passes, including:
  - test_write_tools_are_sequential: write_file and patch still have sequential=True
    in SDK toolset; native_index captured as second return value of
    _build_native_toolset(_CONFIG): native_index["write_file"].is_concurrent_safe is False
    AND native_index["write_file"].is_read_only is False
  - test_excluded_tools_are_not_sequential: save_article, run_shell_command, write_todos
    still have sequential=False AND their native_index[...].is_concurrent_safe is True
  - new test_toolinfo_read_only_tools: native_index["read_file"].is_read_only is True
    AND native_index["read_file"].is_concurrent_safe is True
    AND native_index["glob"].is_read_only is True
    AND native_index["grep"].is_read_only is True
  - new test_sequential_tool_count: exactly 2 tools in the native toolset have
    sequential=True (write_file and patch only); no other tool is serialized

success_signal: N/A

prerequisites: [TASK-1]
```

**Implementation notes:**

1. In `_register_tool()`:
   - Remove `sequential: bool = False` parameter.
   - Add `is_read_only: bool = False` and `is_concurrent_safe: bool = False` parameters.
   - Add invariant assertion: `assert not (is_read_only and not is_concurrent_safe), f"{fn.__name__}: is_read_only=True requires is_concurrent_safe=True"`.
   - Derive: `sequential = not is_concurrent_safe`.
   - Pass `is_read_only=is_read_only, is_concurrent_safe=is_concurrent_safe` to `ToolInfo(...)`.

2. Update **every** `_register_tool(...)` call site — including all Google integration tools and Obsidian tools — per the annotation table in the High-Level Design section. Every call site that is not `write_file` or `patch` must pass `is_concurrent_safe=True` (and `is_read_only=True` for pure reads). A missed annotation silently serializes that tool. The two calls that previously passed `sequential=True` (write_file, patch) now pass `is_concurrent_safe=False` instead; omitting `is_concurrent_safe` at other sites is not valid.

3. In `tests/test_tool_registry.py`:
   - `test_write_tools_are_sequential`: capture `toolset, native_index = _build_native_toolset(_CONFIG)` (not `toolset, _ = ...`). Add assertions: `native_index["write_file"].is_concurrent_safe is False`, `native_index["write_file"].is_read_only is False`, `native_index["patch"].is_concurrent_safe is False`, `native_index["read_file"].is_read_only is True`, `native_index["read_file"].is_concurrent_safe is True`.
   - `test_excluded_tools_are_not_sequential`: capture `native_index` as above. Add assertions: `native_index["save_article"].is_concurrent_safe is True`, `native_index["run_shell_command"].is_concurrent_safe is True`, `native_index["write_todos"].is_concurrent_safe is True`.
   - Add `test_toolinfo_read_only_tools`: capture `native_index`, assert `is_read_only=True, is_concurrent_safe=True` for `read_file`, `glob`, `grep`.
   - Add `test_sequential_tool_count`: capture `toolset, _ = _build_native_toolset(_CONFIG)`, call `tools = await toolset.get_tools(ctx)`, assert `sum(1 for t in tools.values() if t.tool_def.sequential) == 2` and that the two are exactly `write_file` and `patch`.

---

## Testing

No new test files. All coverage lives in `tests/test_tool_registry.py`.

Regression surface: `test_write_tools_are_sequential` and `test_excluded_tools_are_not_sequential` both verify that serialization behavior is preserved after the refactor. Both must pass.

Full suite gate before ship: `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log`.

---

## Open Questions

None — all design decisions are resolvable from the existing source.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev toolinfo-concurrency-axes`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| co_cli/deps.py | Fields added with correct defaults | - | TASK-1 |
| co_cli/agent/_native_toolset.py | `sequential` param removed; `is_read_only`/`is_concurrent_safe` replace it; invariant assertion and derivation correct | - | TASK-2 |
| co_cli/agent/_native_toolset.py | All 35 tool registrations annotated per axis table | - | TASK-2 |
| tests/test_tool_registry.py | Existing tests enhanced; 2 new tests added; no mocks/fakes | - | TASK-2 |
| All | No dead code, stale imports, security issues, or over-engineering | - | All |

**Overall: clean / 0 blocking / 0 minor**

## Delivery Summary — 2026-04-15

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | pytest tests/test_tool_registry.py -x passes; Python one-liner confirms fields with False defaults | ✓ pass |
| TASK-2 | pytest tests/test_tool_registry.py -x passes with all four specified test criteria | ✓ pass |

**Tests:** full suite — 470 passed, 0 failed
**Independent Review:** clean / 0 blocking / 0 minor
**Doc Sync:** fixed (updated docs/specs/tools.md — concurrency serialization description, Axes of Registration section)

**Overall: DELIVERED**
`ToolInfo` now carries `is_read_only` and `is_concurrent_safe` axes. The explicit `sequential=True` call-site parameter is eliminated; `sequential` is now derived from `not is_concurrent_safe` inside `_register_tool()`. All 35 native tool registrations are annotated. Behavioral constraints unchanged: `write_file` and `patch` remain sequential; all other tools do not.

## Implementation Review — 2026-04-15

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | pytest passes; python one-liner exits 0 | ✓ pass | `deps.py:86-87` — `is_read_only: bool = False`, `is_concurrent_safe: bool = False` after `max_result_size`; frozen dataclass preserved |
| TASK-2 | pytest passes with all four test criteria | ✓ pass | `_native_toolset.py:75-87` — params replaced, invariant assertion, `sequential = not is_concurrent_safe`; `_native_toolset.py:178-179` — write_file/patch omit `is_concurrent_safe` → default `False` → `sequential=True`; `_mcp.py:87-94` — MCP ToolInfo gets defaults with no code change |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `test_toolinfo_read_only_tools` decorated `@pytest.mark.asyncio` and `async def` but never awaits anything — function is purely synchronous | `tests/test_tool_registry.py:261` | minor | Removed `@pytest.mark.asyncio` decorator and `async def` → `def` |

### Tests
- Command: `uv run pytest -v`
- Result: 470 passed, 0 failed
- Log: `.pytest-logs/*-review-impl.log`

### Doc Sync
- Scope: narrow — tasks confined to `ToolInfo` schema and `_register_tool` helper; no public API renames
- Result: clean (docs/specs/tools.md already updated in delivery run; re-verified accurate)

### Behavioral Verification
- `uv run co config`: ✓ healthy — all components running, tool registration unaffected
- No user-facing surface changed — `success_signal: N/A` for both tasks

### Overall: PASS
Schema refactor complete and correct. `sequential` is now fully derived from `is_concurrent_safe`; the explicit call-site parameter is gone; all 35 native tools annotated; invariant enforced at registration; behavioral constraints preserved.
