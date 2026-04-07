# TODO: Rename ToolConfig → ToolInfo + Per-Tool Result Sizing

Task type: refactor

## Context

`ToolConfig` in `co_cli/deps.py:271-290` is a frozen dataclass with 8 fields describing
intrinsic tool properties (name, description, approval, source, integration, loading policy,
search hint). It is constructed once during `_build_filtered_toolset()` in `agent.py:108-133`
and stored in `CoDeps.tool_index: dict[str, ToolConfig]`. Four consumer files import it:
`agent.py`, `tools/tool_search.py`, `context/_deferred_tool_prompt.py`, and one test file
`tests/test_agent.py:112`.

Tool result sizing uses a single global constant `TOOL_RESULT_MAX_SIZE = 50_000` in
`co_cli/tools/tool_result_storage.py:17`, checked by `tool_output()` in
`co_cli/tools/tool_output.py:31`. All tools share the same threshold regardless of
their output characteristics.

Research in `docs/reference/RESEARCH-tool-lifecycle-gaps.md` §3.7 shows cc's BashTool uses
30,000 chars while co-cli uses 50,000 for all tools.

File path corrections from the original draft:
- `co_cli/tools/tool_result_storage.py` (not `co_cli/context/_tool_result_storage.py`)
- `co_cli/context/orchestrate.py` (not `co_cli/context/_orchestrate.py`)

## Problem & Outcome

**Problem:** The class name `ToolConfig` implies tunability and bootstrap configuration, but
the type is a frozen descriptor set once at registration. The `*Config` suffix violates
CLAUDE.md naming conventions (`*Config` = bootstrap configuration, `*Info` = read-only
descriptor). The global `TOOL_RESULT_MAX_SIZE` prevents tuning result sizing per tool —
`read_file` and `run_shell_command` have fundamentally different output characteristics but
share the same 50KB threshold.

**Failure cost:** No user-facing failure today. The naming mismatch creates confusion during
code review and planning. The global result size threshold means shell output is
over-budgeted (cc uses 30KB for shell — shell output is compact and rarely needs 50KB)
while file reads are under-budgeted (source files routinely exceed 50KB, causing unnecessary
persistence and preview truncation).

**Outcome:** `ToolConfig` renamed to `ToolInfo` with a `max_result_size` field; `tool_output()`
uses per-tool `max_result_size` from `ToolInfo` instead of the global constant.

## Scope

**In scope:**
- Rename `ToolConfig` → `ToolInfo` across all source, test, and comment references
- Keep `frozen=True` (enforces immutability at the language level; CLAUDE.md permits it for `*Info`)
- Add `max_result_size: int = 50_000` field to `ToolInfo`
- Wire `max_result_size` into `tool_output()` via `ctx.deps.tool_index` lookup
- Tag `run_shell_command` (30,000) and `read_file` (80,000) with per-tool overrides
- Update tests

**Out of scope:**
- `is_read_only` / `is_destructive` capability fields — no consumer exists in this delivery
  or on the near-term roadmap (pydantic-ai lacks dispatch hooks for concurrency partitioning).
  Add these in the delivery that introduces their consumer, so they can be validated end-to-end.
- Input-dependent concurrency, semantic input validation, pre/post tool hooks, permission
  rule system expansion — separate features
- DESIGN doc updates — handled automatically by sync-doc post-delivery

## Behavioral Constraints

- `ToolInfo.__post_init__` must enforce the same `always_load != should_defer` invariant
  currently on `ToolConfig`
- `max_result_size` default must equal the current `TOOL_RESULT_MAX_SIZE` (50,000) so that
  tools without an explicit override behave identically to today
- `tool_output()` must fall back to `TOOL_RESULT_MAX_SIZE` when `ctx` is `None` (some
  callers pass `ctx=None` — the global constant remains the fallback)
- `tool_output()` must also fall back to `TOOL_RESULT_MAX_SIZE` when `ctx.tool_name` is not
  in `tool_index` (defensive — evals and tests may construct ctx without populating tool_index)
- No behavioral change for any tool's approval, visibility, or result handling beyond the
  per-tool result sizing for `run_shell_command` and `read_file`
- Zero stale `ToolConfig` references must remain in source or tests after completion
  (grep scope: `co_cli/` and `tests/`; `docs/` handled by sync-doc, `evals/` verified to
  not import `ToolConfig`)
- Per-tool sizing rationale:
  - `run_shell_command: 30_000` — shell output is compact command results; 50KB is wasteful
    context budget. Matches cc's BashTool (30K). Reduces context consumption for typical
    shell output without affecting functionality.
  - `read_file: 80_000` — source files and config files routinely exceed 50KB; the current
    threshold causes unnecessary disk persistence and preview truncation for normal file
    reads. 80KB accommodates large source files while still capping truly huge outputs.

## High-Level Design

1. **Rename in deps.py:** Rename class `ToolConfig` → `ToolInfo`, keep `frozen=True`, update
   `__post_init__` error string. Add `max_result_size: int = 50_000` field.

2. **Update registration in agent.py:** Add `max_result_size` parameter to `_reg()`. Tag
   `run_shell_command` (30,000) and `read_file` (80,000). All others use the default.

3. **Wire per-tool result sizing:** In `tool_output()`, when `ctx` is not None, look up
   `ctx.deps.tool_index.get(ctx.tool_name)` — if found, use its `max_result_size`; otherwise
   fall back to `TOOL_RESULT_MAX_SIZE`. Pass resolved threshold to `persist_if_oversized()`
   as a new `max_size` parameter. In `persist_if_oversized()`, replace the internal
   `TOOL_RESULT_MAX_SIZE` comparison on line 41 with the `max_size` parameter.

4. **Update all imports:** Four source files + one test file import `ToolConfig` — rename each.
   Grep repo-wide for stale references in comments and docstrings.

5. **Update tests:** Fix `ToolConfig` references in `tests/test_agent.py`. Add per-tool
   result sizing test. Verify existing `tests/test_tool_result_storage.py` still passes
   (fallback path regression coverage).

## Implementation Plan

### ✓ DONE — TASK-1: Rename ToolConfig → ToolInfo + add max_result_size field
files: co_cli/deps.py, co_cli/agent.py, co_cli/tools/tool_search.py, co_cli/context/_deferred_tool_prompt.py
done_when: `grep -r "ToolConfig" co_cli/ tests/` returns zero matches; `uv run python -c "from co_cli.deps import ToolInfo; t = ToolInfo(name='x', description='x', approval=False, source='native', always_load=True, max_result_size=30000); assert t.max_result_size == 30000"` succeeds
success_signal: N/A (refactor — no user-visible change)

Steps:
- In `deps.py`: rename `ToolConfig` → `ToolInfo`, keep `frozen=True`, update error string in
  `__post_init__` from `ToolConfig(...)` to `ToolInfo(...)`
- Add field to `ToolInfo`:
  ```
  max_result_size: int = 50_000
  ```
- Update `CoDeps.tool_index` type annotation: `dict[str, "ToolInfo"]`
- In `agent.py`: update `_reg()` signature to accept `max_result_size` kwarg, pass through
  to `ToolInfo()`. Update return type annotation and `native_index` type.
- Tag tools in `_reg()` calls:
  - `max_result_size=30_000` on: `run_shell_command`
  - `max_result_size=80_000` on: `read_file`
  - All others: default (50,000)
- Update imports in `tools/tool_search.py` and `context/_deferred_tool_prompt.py`:
  `ToolConfig` → `ToolInfo`
- Grep repo-wide for remaining `ToolConfig` references in comments, docstrings, type hints

### ✓ DONE — TASK-2: Wire per-tool max_result_size into tool_output()
files: co_cli/tools/tool_output.py, co_cli/tools/tool_result_storage.py
prerequisites: [TASK-1]
done_when: `uv run pytest tests/test_tool_output_sizing.py tests/test_tool_result_storage.py -x` passes (new test in TASK-3; existing tests for regression)
success_signal: `run_shell_command` results persist at 30KB instead of 50KB; `read_file` results persist at 80KB instead of 50KB

Steps:
- In `tool_output()`: when `ctx is not None`, look up
  `ctx.deps.tool_index.get(ctx.tool_name)` — if found, use its `max_result_size`;
  otherwise fall back to `TOOL_RESULT_MAX_SIZE`
- The comparison `len(display) > TOOL_RESULT_MAX_SIZE` becomes
  `len(display) > threshold` where `threshold` is resolved per above
- Pass `threshold` to `persist_if_oversized()` as a new parameter
- Update `persist_if_oversized()` signature: add `max_size: int = TOOL_RESULT_MAX_SIZE`
  parameter. Replace the `TOOL_RESULT_MAX_SIZE` comparison on line 41 with `max_size`.
  The default preserves backward compatibility for direct callers (evals, existing tests).
- Keep `TOOL_RESULT_MAX_SIZE` as a module-level constant (still used as default value
  and by `check_tool_results_size()`)
- Verify `evals/eval_compaction_quality.py` callers are compatible (they use positional args
  without `max_size`, so they get the default — no change needed, but confirm)

### ✓ DONE — TASK-3: Update tests
files: tests/test_agent.py, tests/test_tool_result_storage.py, tests/test_tool_output_sizing.py (new)
prerequisites: [TASK-1, TASK-2]
done_when: `uv run pytest tests/test_agent.py tests/test_tool_result_storage.py tests/test_tool_output_sizing.py -x` passes
success_signal: N/A (test infrastructure)

Steps:
- In `tests/test_agent.py`: rename all `ToolConfig` references to `ToolInfo`
- Add assertions in `test_tool_index_loading_policy_metadata` (or new test) verifying:
  - `run_shell_command` has `max_result_size == 30_000`
  - `read_file` has `max_result_size == 80_000`
  - All other tools have `max_result_size == 50_000` (default)
- Verify `tests/test_tool_result_storage.py` passes unchanged (existing tests exercise the
  fallback path — ctx without tool_index populated → falls back to global constant)
- New file `tests/test_tool_output_sizing.py`: test that `tool_output()` respects per-tool
  `max_result_size` — construct real `CoDeps` and `RunContext` with a `ToolInfo` entry
  having `max_result_size=100`, call `tool_output()` with content > 100 chars, assert
  the persisted-output placeholder is returned. Also test the `ctx=None` fallback path.
  Also test `persist_if_oversized()` with an explicit `max_size` argument.

## Testing

- Scoped runs during implementation: `uv run pytest tests/test_agent.py tests/test_tool_result_storage.py tests/test_tool_output_sizing.py -x`
- Full suite before shipping: `uv run pytest -x`
- Grep validation: `grep -r "ToolConfig" co_cli/ tests/` must return zero matches

## Open Questions

None — all questions resolved by codebase inspection.

## Delivery Summary

All 3 tasks shipped. Full test suite: 345 passed, 0 failed (107s).

**Files changed:**
- `co_cli/deps.py` — renamed `ToolConfig` → `ToolInfo`, added `max_result_size: int = 50_000` field
- `co_cli/agent.py` — updated `_reg()` with `max_result_size` kwarg, tagged `run_shell_command` (30K) and `read_file` (80K), renamed all `ToolConfig` → `ToolInfo` references
- `co_cli/tools/tool_search.py` — import rename
- `co_cli/context/_deferred_tool_prompt.py` — import + type annotation rename
- `co_cli/tools/tool_output.py` — per-tool threshold lookup via `ctx.deps.tool_index`
- `co_cli/tools/tool_result_storage.py` — `persist_if_oversized()` now accepts `max_size` kwarg
- `tests/test_agent.py` — renamed reference, added `max_result_size` assertions for all tools
- `tests/test_tool_output_sizing.py` (new) — 6 tests covering per-tool sizing, fallback, and `persist_if_oversized` with explicit `max_size`

**Grep validation:** `grep -r "ToolConfig" co_cli/ tests/` returns zero source matches.

> Ready for `/review-impl toolconfig-to-toolinfo`

## Implementation Review — 2026-04-07

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `grep -r "ToolConfig" co_cli/ tests/` zero matches | ✓ pass | `grep --include='*.py'` returns exit 1 (no matches); `.pyc` bytecache matches are stale, not source |
| TASK-1 | `ToolInfo` construction with `max_result_size` | ✓ pass | `deps.py:271-284` — `@dataclass(frozen=True) class ToolInfo` with `max_result_size: int = 50_000` |
| TASK-1 | `__post_init__` error string updated | ✓ pass | `deps.py:288-289` — `f"ToolInfo({self.name!r}): ..."` |
| TASK-1 | `CoDeps.tool_index` type annotation | ✓ pass | `deps.py:386` — `dict[str, "ToolInfo"]` |
| TASK-1 | `_reg()` accepts `max_result_size` | ✓ pass | `agent.py:117` — `max_result_size: int = 50_000` param; `agent.py:134` — passed to `ToolInfo()` |
| TASK-1 | `run_shell_command` tagged 30K | ✓ pass | `agent.py:164` — `max_result_size=30_000` |
| TASK-1 | `read_file` tagged 80K | ✓ pass | `agent.py:156` — `max_result_size=80_000` |
| TASK-1 | imports updated in 4 files | ✓ pass | `agent.py:15`, `tool_search.py:5`, `_deferred_tool_prompt.py:3` — all `ToolInfo` |
| TASK-2 | `tool_output()` per-tool lookup | ✓ pass | `tool_output.py:32-33` — `info = ctx.deps.tool_index.get(ctx.tool_name)`, `threshold = info.max_result_size if info else TOOL_RESULT_MAX_SIZE` |
| TASK-2 | `persist_if_oversized()` `max_size` param | ✓ pass | `tool_result_storage.py:27` — `max_size: int = TOOL_RESULT_MAX_SIZE`; `line 44` — `if len(content) <= max_size` |
| TASK-2 | `tool_output()` passes threshold | ✓ pass | `tool_output.py:37` — `max_size=threshold` |
| TASK-2 | Eval callers compatible | ✓ pass | `evals/eval_compaction_quality.py` uses 3 positional args — default `max_size` applies |
| TASK-3 | `test_agent.py` ToolConfig ref renamed | ✓ pass | `test_agent.py:112` — `ToolInfo.name` |
| TASK-3 | `max_result_size` assertions added | ✓ pass | `test_agent.py:134-142` — shell=30K, read_file=80K, all others=50K |
| TASK-3 | New `test_tool_output_sizing.py` | ✓ pass | 6 tests: per-tool threshold, under threshold, no-ctx, not-in-index fallback, explicit max_size, default max_size |
| TASK-3 | Existing `test_tool_result_storage.py` passes | ✓ pass | 4 existing tests pass — fallback path regression confirmed |

### Call Path Verification
| Chain | Evidence |
|-------|---------|
| `_reg(max_result_size)` → `ToolInfo(max_result_size)` → `CoDeps.tool_index` | `agent.py:117,134` → `deps.py:284` → `deps.py:386` |
| `tool_output(ctx)` → `ctx.deps.tool_index.get()` → `.max_result_size` → `persist_if_oversized(max_size=)` | `tool_output.py:32-37` → `tool_result_storage.py:27,44` |
| `discover_mcp_tools()` → `ToolInfo(should_defer=True)` (gets default 50K) | `agent.py:381-387` |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -x`
- Result: 345 passed, 0 failed
- Log: `.pytest-logs/20260407-review-impl.log`

### Doc Sync
- Scope: full — public API renamed (`ToolConfig` → `ToolInfo`)
- Result: fixed — 3 DESIGN docs updated (DESIGN-bootstrap.md, DESIGN-tools.md, DESIGN-system.md)

### Behavioral Verification
No user-facing CLI surface changed — all tasks are N/A (refactor) or internal runtime behavior. Per-tool sizing wiring confirmed via integration tests (`test_tool_index_loading_policy_metadata`, `test_tool_output_uses_per_tool_threshold`).

### Overall: PASS
Clean rename with per-tool result sizing. All spec requirements confirmed with file:line evidence, all `done_when` re-executed and passing, full suite green, no blocking or minor findings.

