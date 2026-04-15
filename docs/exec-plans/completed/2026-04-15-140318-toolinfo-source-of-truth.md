# Plan: ToolInfo as Source of Truth for SDK Wiring

**Task type: refactor** — adds one missing field to `ToolInfo` and reorders construction so `ToolInfo` is built before SDK kwargs are derived. No behavior change.

---

## Context

`ToolInfo` (`co_cli/deps.py:76`) is described as "the canonical metadata record for one registered tool — set once at registration, never mutated." In practice, the `_register_tool()` helper in `co_cli/agent/_native_toolset.py` violates this contract: it builds SDK kwargs from raw params first, calls `native_toolset.add_function()`, and only then constructs `ToolInfo` as an afterthought from the same params.

The current execution order in `_register_tool()` (lines 82–106):

```
params arrive
  → assert invariant
  → derive sequential = not is_concurrent_safe
  → build SDK kwargs dict from params directly
  → native_toolset.add_function(fn, **kwargs)   ← SDK wired here
  → native_index[name] = ToolInfo(...)           ← ToolInfo built after
```

One field, `retries`, bypasses `ToolInfo` entirely — it is passed to `add_function` but never stored in `ToolInfo`. This means `retries` is invisible to anything that inspects tool metadata from the index (dispatch logic, tests, future policy layers).

Peer research (gemini-cli `DiscoveredTool`, opencode `Tool.Info`, hermes-agent `ToolEntry`) confirms the converged practice is metadata-first: build the descriptor from registration params, then derive SDK kwargs from it.

No related exec-plan exists. `docs/specs/tools.md` was updated in v0.7.146 to describe registration axes but does not enumerate `retries` — no stale doc to fix here; sync-doc will handle it post-delivery.

---

## Problem & Outcome

**Problem:** `_register_tool()` wires the SDK before `ToolInfo` exists. `retries` is set on the SDK but absent from `ToolInfo`, so tool metadata is incomplete.

**Failure cost:** Any consumer of `ToolInfo` (dispatch logic, tests, future scheduler) that wants to know a tool's retry policy must read the SDK's internal state rather than the metadata index. A new tool that sets `retries` at registration has no way to surface that value through the standard metadata path. `native_index["web_search"].retries` raises `AttributeError`.

**Outcome:** `ToolInfo` gains a `retries` field. `_register_tool()` builds `ToolInfo` first, then derives all SDK kwargs from it. `ToolInfo` is the single source of truth for every registration-time parameter.

---

## Scope

**In scope:**
- Add `retries: int | None = None` to `ToolInfo`
- Reorder `_register_tool()`: build `ToolInfo` → derive SDK kwargs from `tool_info.*` → call `add_function`
- Update tests to assert `retries` is correctly stored in `ToolInfo` for representative tools

**Out of scope:**
- Changing `_mcp.py` — MCP `ToolInfo` construction gets `retries=None` via field default; no change needed
- Changing any SDK retry behavior — the values registered with the SDK are identical before and after
- Adding `retries` to any display, config, or runtime path — field is metadata only for now

---

## Behavioral Constraints

- All existing SDK kwargs (`requires_approval`, `sequential`, `defer_loading`, `retries`) must be byte-for-byte identical after the reorder.
- `native_index["write_file"].retries == 1` (previously wired only to SDK)
- `native_index["web_search"].retries == 3`
- `native_index["web_fetch"].retries == 3`
- `native_index["patch"].retries == 1`
- `native_index["check_capabilities"].retries is None` (no retries registered)
- All existing `tests/test_tool_registry.py` tests pass without modification.
- MCP tools receive `retries=None` via field default — no regression in `_mcp.py`.

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
    is_read_only: bool = False
    is_concurrent_safe: bool = False
    retries: int | None = None   # ← new: mirrors SDK retries kwarg; None = SDK default
```

Default `None` is correct: it matches the SDK's "omit retries, use framework default" behavior for the majority of tools.

### Reordered `_register_tool()` flow

```
params arrive
  → assert invariant (is_read_only → is_concurrent_safe)
  → build ToolInfo from all params (including retries)
  → derive SDK kwargs from tool_info.*:
        requires_approval  ← tool_info.approval
        sequential         ← not tool_info.is_concurrent_safe
        defer_loading      ← tool_info.visibility == DEFERRED
        retries            ← tool_info.retries (conditional)
  → native_toolset.add_function(fn, **kwargs)
  → native_index[name] = tool_info
```

`ToolInfo` is constructed once and reused — no duplication of field access.

### MCP tools

`_mcp.py:87-94` constructs `ToolInfo(name=..., description=..., approval=..., source=MCP, visibility=DEFERRED, integration=...)` without `retries`. Field default `None` applies automatically. No change required.

---

## Implementation Plan

### ✓ DONE — TASK-1 — Add `retries` field to `ToolInfo`

```
files:
  - co_cli/deps.py

done_when: >
  python -c "from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum;
  t = ToolInfo(name='x', description='x', approval=False,
  source=ToolSourceEnum.NATIVE, visibility=VisibilityPolicyEnum.ALWAYS);
  assert hasattr(t, 'retries'); assert t.retries is None; print('ok')" exits 0.
  AND uv run pytest tests/test_tool_registry.py -x passes (all existing constructions
  remain valid via default).

success_signal: N/A

prerequisites: []
```

**Implementation notes:**
- Append `retries: int | None = None` after `is_concurrent_safe` in the `ToolInfo` dataclass.
- Default must be `None` — matches SDK "omit = use framework default" semantics.
- No changes to `_mcp.py` — existing `ToolInfo(...)` calls receive `retries=None` via default.
- No changes to any test file for this task — all existing constructions remain valid.

---

### ✓ DONE — TASK-2 — Reorder `_register_tool()` to ToolInfo-first; update tests

```
files:
  - co_cli/agent/_native_toolset.py
  - tests/test_tool_registry.py

done_when: >
  uv run pytest tests/test_tool_registry.py -x passes, including:
  - new test_toolinfo_retries: native_index["web_search"].retries == 3
    AND native_index["web_fetch"].retries == 3
    AND native_index["write_file"].retries == 1
    AND native_index["patch"].retries == 1
    AND native_index["save_article"].retries == 1
    AND native_index["check_capabilities"].retries is None
  - all existing tests pass without modification

success_signal: N/A

prerequisites: [TASK-1]
```

**Implementation notes:**

1. In `_register_tool()`:
   - Build `ToolInfo` immediately after the invariant assertion (before any SDK kwargs).
   - Replace the separate `sequential = not is_concurrent_safe` local var and the kwargs dict construction with reads from `tool_info.*`:
     ```python
     tool_info = ToolInfo(
         name=name, description=description,
         approval=approval, source=ToolSourceEnum.NATIVE,
         visibility=visibility, integration=integration,
         max_result_size=max_result_size,
         is_read_only=is_read_only, is_concurrent_safe=is_concurrent_safe,
         retries=retries,
     )
     kwargs: dict[str, Any] = {
         "requires_approval": tool_info.approval,
         "sequential": not tool_info.is_concurrent_safe,
         "defer_loading": tool_info.visibility == VisibilityPolicyEnum.DEFERRED,
     }
     if tool_info.retries is not None:
         kwargs["retries"] = tool_info.retries
     native_toolset.add_function(fn, **kwargs)
     native_index[name] = tool_info
     ```
   - Remove the now-redundant `sequential` local variable (it is inlined into the kwargs dict).

2. In `tests/test_tool_registry.py`:
   - Add `test_toolinfo_retries`: call `_build_native_toolset(_CONFIG)`, capture `native_index`, assert:
     - `native_index["web_search"].retries == 3`
     - `native_index["web_fetch"].retries == 3`
     - `native_index["write_file"].retries == 1`
     - `native_index["patch"].retries == 1`
     - `native_index["save_article"].retries == 1`
     - `native_index["check_capabilities"].retries is None`

---

## Testing

No new test files. All coverage lives in `tests/test_tool_registry.py`.

Regression surface:
- `test_write_tools_are_sequential` — verifies `sequential=True` for write_file/patch; must pass after reorder.
- `test_excluded_tools_are_not_sequential` — must pass.
- `test_sequential_tool_count` — exactly 2 sequential tools; must pass.
- `test_toolinfo_retries` (new) — verifies `retries` is correctly stored in `ToolInfo`.

Full suite gate: `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log`.

---

## Open Questions

None — all design decisions are resolvable from existing source.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev toolinfo-source-of-truth`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/deps.py` | `retries: int | None = None` placed correctly after `is_concurrent_safe`, default `None` mirrors "omit = framework default". Field order and typing match spec exactly. | clean | TASK-1 |
| `co_cli/agent/_native_toolset.py` | Reorder is correct: `ToolInfo` built first, SDK kwargs derived from `tool_info.*`, `add_function` called last. All three derived values byte-for-byte equivalent. `retries` conditionally added only when non-`None`. No dead code, no stale imports. | clean | TASK-2 |
| `tests/test_tool_registry.py` | `test_toolinfo_retries` uses real `_build_native_toolset` — no mocks. Assertions use exact equality. Covers both non-`None` values and the `None` case. Deletion would leave retries-mapping silent regression. | clean | TASK-2 |

**Overall: clean**

## Delivery Summary — 2026-04-15

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | python assertion exits 0 AND pytest tests/test_tool_registry.py -x passes | ✓ pass |
| TASK-2 | pytest tests/test_tool_registry.py -x passes including test_toolinfo_retries | ✓ pass |

**Tests:** full suite — 471 passed, 0 failed
**Independent Review:** clean
**Doc Sync:** clean (retries axis already documented in tools.md)

**Overall: DELIVERED**
`ToolInfo` gains a `retries` field (default `None`); `_register_tool()` now builds `ToolInfo` first and derives all SDK kwargs from it, making `ToolInfo` the single source of truth for every registration-time parameter.

## Implementation Review — 2026-04-15

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | python assertion exits 0 AND pytest passes | ✓ pass | `deps.py:88` — `retries: int | None = None` appended after `is_concurrent_safe`; `_mcp.py:87` omits field — default `None` applies |
| TASK-2 | pytest tests/test_tool_registry.py -x passes including test_toolinfo_retries | ✓ pass | `_native_toolset.py:87-107` — `ToolInfo` built at :87, kwargs derived from `tool_info.*` at :99-105, `add_function` at :106, `native_index[name] = tool_info` at :107; `sequential` local var removed |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -v`
- Result: 471 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: narrow — all tasks confined to `deps.py` (field addition) and `_native_toolset.py` (internal reorder); no public API renamed
- Result: clean — `tools.md` already described `retries` as a registration axis

### Behavioral Verification
- `uv run co config`: ✓ all components healthy (LLM, Shell, Google, Web Search, MCP)
- No user-facing changes — behavioral verification limited to system start

### Overall: PASS
Pure internal refactor: `ToolInfo` is now the canonical source of truth for all registration-time parameters including `retries`, with no behavior change at the SDK layer.
