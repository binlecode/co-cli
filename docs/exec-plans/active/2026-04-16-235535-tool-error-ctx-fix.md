# Plan: Fix ctx-Bypass in Tool Result Constructors — Structural DRY Fix

**Task type: refactor**

---

## Context

An audit of `co_cli/tools/` surfaced three related ctx-bypass bugs on the tool-result
construction path. All three share the same root cause: a ctx-aware helper has `ctx`
as an **optional** parameter, letting callers silently route through the raw
(no-sizing, no-tracing) fallback path.

### Bug 1 — `tool_error(..., ctx=None)` on error branches

```python
def tool_error(message, *, ctx=None):
    if ctx is not None:
        return tool_output(message, ctx=ctx, error=True)   # size checking + tracing
    return tool_output_raw(message, error=True)             # no size checking, no ctx
```

24 live call sites inside ctx-aware tool functions omit `ctx=ctx`:
- `co_cli/tools/files.py` — 21 sites (glob, read_file, grep, write_file, patch)
- `co_cli/tools/web.py` — 1 site (`web_fetch` unsupported content-type at ~585)
- `co_cli/tools/knowledge.py` — 2 sites (`ResourceBusyError` branches at ~1015, ~1107)

### Bug 2 — `tool_output_raw()` misused on ctx-aware success branches in `knowledge.py`

`save_knowledge` and `_consolidate_and_reindex` have `ctx: RunContext[CoDeps]` available
but four success-path returns use `tool_output_raw()`, bypassing per-tool sizing:

- `knowledge.py:340` — `save_knowledge` dedup "skipped" branch
- `knowledge.py:354` — `save_knowledge` merge/append branch
- `knowledge.py:395` — `save_knowledge` final save branch
- `knowledge.py:954` — `_consolidate_and_reindex` return (ctx in scope)

### Bug 3 — `handle_google_api_error(..., ctx=None)` on Google tool error branches

```python
def handle_google_api_error(label, e, *, ctx: "RunContext[CoDeps] | None" = None) -> ToolReturn:
    if status == 401:
        return tool_error(f"{label}: ...", ctx=ctx)   # forwards ctx into tool_error
    raise ModelRetry(...)
```

Callers that omit `ctx` propagate the same bypass downstream through `tool_error`. Five
live sites omit `ctx=ctx`:
- `co_cli/tools/google/calendar.py` — 2 sites (141, 208)
- `co_cli/tools/google/gmail.py` — 3 sites (78, 121, 155)
- `co_cli/tools/google/drive.py` — 0 (both its sites already pass `ctx=ctx`)

### Root cause

All three surfaces have an optional `ctx` parameter, which is a footgun. The structural
fix is to make `ctx` **required** on `tool_error()` and `handle_google_api_error()`,
and to correct the ctx-aware `tool_output_raw` misuse in `knowledge.py`. Callers that
genuinely have no `RunContext` (e.g., `_http_get_with_retries`) call `tool_output_raw(..., error=True)`
directly — the semantically correct helper for ctx-less error returns.

### Validation against current tree

- `co_cli/tools/files.py` — 21 `tool_error(` sites inside ctx-aware tools, all omit `ctx=ctx`
- `co_cli/tools/web.py` — 4 sites; 3 in `_http_get_with_retries` (legitimately ctx-less), 1 bug in `web_fetch`
- `co_cli/tools/knowledge.py` — 2 `tool_error(` sites plus 4 `tool_output_raw(` sites on ctx-aware paths
- `co_cli/tools/memory.py` — 0 sites
- `co_cli/tools/google/{calendar,gmail}.py` — 5 `handle_google_api_error(` sites without `ctx=ctx`
- `co_cli/tools/google/drive.py` — 2 correct sites (already pass `ctx=ctx`); used only for validation

Net: 24 + 4 + 5 = **33 mechanical call-site fixes**, plus 2 signature tightenings and
3 helper migrations.

---

## Problem & Outcome

**Problem:** Three tool-result helpers accept `ctx` as optional. Callers can silently
bypass per-tool size checking and tracing tool-name metadata. 33 call sites currently
exhibit the bug, and nothing structurally prevents future call sites from reintroducing it.

**Failure cost:**
- Oversized error messages (e.g., a boundary-violation error on a very long path, a
  Google-API error body dumped into a 401 terminal message) bypass the persistence
  threshold and reach the model raw, consuming context budget.
- `save_knowledge` success payloads bypass per-tool sizing — long dedup-diff or final-save
  messages are sent raw.
- Tracing metadata lacks the tool name on these paths — observability gaps in spans.
- The inconsistency is invisible to tests that only assert `error=True` or `action=...`.

**Outcome:**
- `tool_error(message, *, ctx)` and `handle_google_api_error(label, e, *, ctx)` both
  require `ctx`.
- Callers without `RunContext` (only `_http_get_with_retries`) call
  `tool_output_raw(message, error=True)` directly.
- All four ctx-aware `tool_output_raw` sites in `knowledge.py` switch to
  `tool_output(ctx=ctx, ...)`.
- Bug is structurally impossible going forward — type checker enforces it; human review
  sees the explicit `ctx=ctx` on every call.

---

## Scope

**In:**
- `co_cli/tools/tool_io.py` — make `ctx` required in `tool_error()` and
  `handle_google_api_error()`; drop the `ctx=None` fallbacks. Update the `tool_error`
  docstring to point ctx-less callers at `tool_output_raw(..., error=True)`.
- `co_cli/tools/web.py` — migrate 3 `_http_get_with_retries` call sites (~398, ~401, ~413)
  from `tool_error(...)` to `tool_output_raw(..., error=True)`. Add `ctx=ctx` to the 1
  `web_fetch` site (~585).
- `co_cli/tools/files.py` — add `ctx=ctx` to 21 sites.
- `co_cli/tools/knowledge.py`:
  - add `ctx=ctx` to 2 `tool_error` sites in the `ResourceBusyError` branches
  - migrate 4 ctx-aware `tool_output_raw` sites to `tool_output(ctx=ctx, ...)` (preserving metadata kwargs)
- `co_cli/tools/google/calendar.py` — add `ctx=ctx` to 2 sites.
- `co_cli/tools/google/gmail.py` — add `ctx=ctx` to 3 sites.
- Regression tests in `tests/test_tools_files.py` (×5: glob, read_file, grep, write_file, patch)
  and `tests/test_memory.py` (×3: append_knowledge busy, update_knowledge busy,
  save_knowledge success-path oversize).

**Out:**
- No changes to `co_cli/tools/memory.py` — 0 affected sites.
- No changes to `co_cli/tools/google/drive.py` — both sites already pass `ctx=ctx` (validation only).
- No changes to `co_cli/tools/shell.py` or `co_cli/tools/execute_code.py` — both already
  pass `ctx=ctx` on all `tool_error` calls (validation only).
- No changes to tool registry, agent, or approval logic.
- No refactor of the `_http_get_with_retries` call chain — the helper continues to lack
  `ctx` by design; only its error constructor changes.
- No new tests for `web_fetch` or Google tools — these require real HTTP / real
  credentials with no mock-free alternative. Existing test suites cover their integration boundaries.

---

## Behavioral Constraints

1. **No behavior change on success paths for non-knowledge tools** — success returns
   outside `knowledge.py` already use `tool_output(ctx=ctx, ...)` and must remain unchanged.
2. **Knowledge success paths change observably**: `save_knowledge` and
   `_consolidate_and_reindex` returns now go through `tool_output()` sizing. For short
   messages (the common case) this is a no-op; for any message exceeding the per-tool
   `max_result_size` the output becomes `[persisted to file …]`. This is the intended
   consistency improvement, not a regression.
3. **`tool_error()` and `handle_google_api_error()` signatures tighten** — `ctx` becomes
   required. Both are package-private; there are no external consumers. Any caller
   failing to pass `ctx` is a type error; this is the intended enforcement mechanism.
4. **Helper parity** — `_http_get_with_retries` returns `ToolReturn` via
   `tool_output_raw(..., error=True)`. The returned shape
   (`return_value=message, metadata={"error": True}`) is byte-identical to the old
   `tool_error(message)` without ctx, so caller behavior is unchanged.
5. **Test assertions must exercise the ctx path** — new tests use a local `_make_ctx_sized`
   helper that registers the tool in `tool_index` with a small `max_result_size`, trigger
   an error (or a long success payload for `save_knowledge`) longer than the threshold,
   and assert `PERSISTED_OUTPUT_TAG in result.return_value`. Checking only `error=True`
   or `action=…` is insufficient.

---

## High-Level Design

### Step 1 — Tighten signatures in `tool_io.py`

```python
def tool_error(message: str, *, ctx: "RunContext[CoDeps]") -> ToolReturn:
    """Return a ToolReturn for terminal (non-retryable) tool failures.

    Unlike ModelRetry, this stops the retry loop immediately — the model
    sees the error in the tool result and can pick a different tool.

    Tool functions always have RunContext; use this helper. For ctx-less
    helpers (e.g. _http_get_with_retries), call tool_output_raw(..., error=True)
    directly.
    """
    return tool_output(message, ctx=ctx, error=True)


def handle_google_api_error(
    label: str,
    e: Exception,
    *,
    ctx: "RunContext[CoDeps]",
) -> ToolReturn:
    """Route Google API errors to tool_error or ModelRetry.

    401 → terminal (auth failure, user must fix credentials)
    403/404/429/5xx → ModelRetry
    """
    ...  # body unchanged; ctx is now required and forwarded into tool_error()
```

### Step 2 — Migrate `_http_get_with_retries` to `tool_output_raw`

Three sites at `co_cli/tools/web.py:~398, ~401, ~413`:
```python
return tool_error(decision.message)
```
become:
```python
return tool_output_raw(decision.message, error=True)
```

Import at the top of `web.py` gains `tool_output_raw`.

### Step 3 — Migrate knowledge ctx-aware `tool_output_raw` sites to `tool_output(ctx=ctx, ...)`

Four sites: `knowledge.py:340, 354, 395, 954`. Example:
```python
return tool_output_raw(
    f"Skipped (near-identical to {best_artifact.path.name})",
    action="skipped",
    path=str(best_artifact.path),
    artifact_id=best_artifact.id,
)
```
becomes:
```python
return tool_output(
    f"Skipped (near-identical to {best_artifact.path.name})",
    ctx=ctx,
    action="skipped",
    path=str(best_artifact.path),
    artifact_id=best_artifact.id,
)
```

`_consolidate_and_reindex(ctx, path, ...)` already takes `ctx` — it's in scope at line 954.

### Step 4 — Add `ctx=ctx` to all caller sites

29 sites across `files.py` (21), `web.py` (1), `knowledge.py` (2), `calendar.py` (2), `gmail.py` (3):

```python
return tool_error(f"File not found: {path}", ctx=ctx)                  # files.py, web.py, knowledge.py
return handle_google_api_error("Calendar", e, ctx=ctx)                 # calendar.py, gmail.py
```

### Test approach

`test_tool_output_sizing.py:40-65` already has `_make_ctx_with_index()` — same pattern
replicated locally in each test file (no cross-file imports).

`tests/test_tools_files.py` helper needs `workspace_root` + `tool_index`; `tests/test_memory.py`
helper needs `knowledge_dir` + `tool_results_dir` + `tool_index`. `tool_name=tool_name`
on `RunContext(...)` is mandatory so `ctx.tool_name` resolves to the registered entry.

New tests use `max_result_size=10` and trigger outputs > 10 chars:
- `glob`: non-existent path — `"a" * 50` → `"Path not found: " + "a"*50` is 66 chars > 10
- `read_file`: non-existent file with a long name — same pattern
- `grep`: invalid regex (error message > 10 chars)
- `write_file`: staleness guard — write a file, record mtime, mutate on disk, call
  `write_file` again; error contains the path and exceeds 10 chars
- `patch`: no-match — provide an `old_string` that doesn't exist; error contains the string and path
- `append_knowledge`: hold `ctx.deps.resource_locks.try_acquire(slug)` in one task, then
  call `append_knowledge`; busy-message is far over 10 chars
- `update_knowledge`: same real lock-contention pattern, targeting `update_knowledge`
- `save_knowledge` success: call `save_knowledge` with content whose final
  display message (`"✓ Saved knowledge: <filename>"`) exceeds `max_result_size=10`.
  This exercises the **success-path** migration (Bug 2) — assert
  `PERSISTED_OUTPUT_TAG in result.return_value`. Prior to the fix this assertion fails
  because `tool_output_raw` skips sizing.

No new tests for `web_fetch` or Google tools — they require real HTTP / real
credentials with no mock-free alternative. Existing suites cover them.

---

## Implementation Plan

### TASK-1 — Tighten error-constructor signatures; migrate ctx-less helper ✓ DONE

```yaml
id: TASK-1
files:
  - co_cli/tools/tool_io.py
  - co_cli/tools/web.py
done_when: >
  (1) co_cli/tools/tool_io.py: tool_error() signature is
      `def tool_error(message: str, *, ctx: "RunContext[CoDeps]") -> ToolReturn`
      (no default), body is a single return through tool_output(..., error=True).
  (2) co_cli/tools/tool_io.py: handle_google_api_error() signature is
      `def handle_google_api_error(label, e, *, ctx: "RunContext[CoDeps]") -> ToolReturn`
      (no default), body is otherwise unchanged.
  (3) grep -n "tool_error(" co_cli/tools/web.py returns exactly 1 site (web_fetch at ~585).
      The 3 former _http_get_with_retries sites now call tool_output_raw(..., error=True).
  (4) uv run pytest tests/test_web.py -x passes (existing web tests must still pass;
      byte-identical ToolReturn shape from tool_output_raw).
success_signal: N/A — internal API tightening.
```

### TASK-2 — Add `ctx=ctx` to all call sites ✓ DONE

```yaml
id: TASK-2
files:
  - co_cli/tools/files.py
  - co_cli/tools/web.py
  - co_cli/tools/knowledge.py
  - co_cli/tools/google/calendar.py
  - co_cli/tools/google/gmail.py
done_when: >
  (1) grep -n "tool_error(" co_cli/tools/files.py | grep -v "ctx=ctx" returns zero lines.
  (2) grep -n "tool_error(" co_cli/tools/web.py | grep -v "ctx=ctx" returns zero lines
      (the 3 helper sites now use tool_output_raw).
  (3) grep -n "tool_error(" co_cli/tools/knowledge.py | grep -v "ctx=ctx" returns zero lines.
  (4) grep -n "tool_output_raw(" co_cli/tools/knowledge.py returns zero lines — all four
      ctx-aware sites (340, 354, 395, 954) now call tool_output(ctx=ctx, ...).
      The `from co_cli.tools.tool_io import ...` line no longer imports tool_output_raw.
  (5) grep -n "handle_google_api_error(" co_cli/tools/google/{calendar,gmail}.py | grep -v "ctx=ctx"
      returns zero lines.
  (6) uv run pytest tests/test_tools_files.py tests/test_web.py tests/test_memory.py \
                   tests/test_resource_lock.py tests/test_tool_output_sizing.py -x passes.
success_signal: N/A — internal tracing change.
prerequisites: [TASK-1]
```

### TASK-3 — Regression tests covering ctx-path on error and success branches ✓ DONE

```yaml
id: TASK-3
files:
  - tests/test_tools_files.py
  - tests/test_memory.py
done_when: >
  uv run pytest tests/test_tools_files.py::test_glob_error_uses_ctx_path
               tests/test_tools_files.py::test_read_file_error_uses_ctx_path
               tests/test_tools_files.py::test_grep_error_uses_ctx_path
               tests/test_tools_files.py::test_write_file_error_uses_ctx_path
               tests/test_tools_files.py::test_patch_error_uses_ctx_path
               tests/test_memory.py::test_append_knowledge_busy_error_uses_ctx_path
               tests/test_memory.py::test_update_knowledge_busy_error_uses_ctx_path
               tests/test_memory.py::test_save_knowledge_success_uses_ctx_path -x passes.
  Each test must assert PERSISTED_OUTPUT_TAG in result.return_value (not only error=True
  or action=…).
success_signal: N/A — regression guard only.
prerequisites: [TASK-2]
```

---

## Testing

Full run after TASK-3:

```
uv run pytest tests/test_tools_files.py tests/test_web.py tests/test_memory.py \
              tests/test_resource_lock.py tests/test_tool_output_sizing.py -x
```

Eight new tests total:
- Five in `tests/test_tools_files.py` — one per affected file tool function (glob, read_file,
  grep, write_file, patch)
- Three in `tests/test_memory.py` — `append_knowledge` busy, `update_knowledge` busy,
  `save_knowledge` success oversize (new coverage for the Bug 2 migration)

Each uses a sized `RunContext` helper with `max_result_size=10` and asserts
`PERSISTED_OUTPUT_TAG in result.return_value`.

**Dev note (patch test setup):** `patch` checks `file_read_mtimes` before applying the
diff — if absent, it raises `ModelRetry` (stale-read guard) before reaching the
no-match `tool_error` path. The test must call `read_file` first or directly populate
`ctx.deps.file_read_mtimes[path]`. Pattern is established in
`tests/test_resource_lock.py:91` (`test_patch_same_path_contention`).

**Dev note (knowledge busy-path tests):** use the real lock-contention structure at
`tests/test_resource_lock.py:85`: create a task that holds
`ctx.deps.resource_locks.try_acquire(slug)`, wait until acquired, call the knowledge
tool, assert `PERSISTED_OUTPUT_TAG` in the returned display, release the lock, await
the holder task. No mocks required.

**Dev note (save_knowledge success-path test):** the final display is
`"✓ Saved knowledge: <filename>"` which is short by default. With `max_result_size=10`,
any realistic filename exceeds the threshold. Set `consolidation_enabled=False` in the
test config so the dedup branch does not run; this isolates the final-save return (line 395).

---

## Open Questions

None. All resolved by inspection:

- Q: Why not keep `ctx` optional and just add `ctx=ctx` at every site?
  A: The optional signature is the root cause. A sprinkle fixes today's bug but leaves
  the same footgun for every future call site. Required `ctx` shifts the footgun into
  an IDE/dev-time type error (caught by any type-checker run) and makes the contract
  visible at call sites via the explicit `ctx=ctx`. The CI quality gate (ruff + pytest)
  catches the omission indirectly through regression tests; the structural argument is
  about human review and dev-time enforcement, not CI.
- Q: Should `_http_get_with_retries` be refactored to accept `ctx`?
  A: No. The helper is genuinely ctx-less — its callers already return correctly-ctx'd
  success paths. Swapping its error constructor from `tool_error(msg)` to
  `tool_output_raw(msg, error=True)` is a one-line change per site with byte-identical output.
- Q: Are the knowledge `tool_output_raw` success-path calls really bugs?
  A: Yes. `save_knowledge` and `_consolidate_and_reindex` both have `ctx` in scope and
  are the only success returns that skip per-tool sizing. The calls pre-date the
  per-tool sizing feature and were never updated. Consistency with every other tool's
  success path is the correct end state.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev tool-error-ctx-fix`

---

## Delivery Summary

**Delivered by:** `/deliver tool-error-ctx-fix`
**Version:** v0.7.193 (bugfix)
**Full suite:** 609 passed

### What changed

- `co_cli/tools/tool_io.py`: `tool_error()` and `handle_google_api_error()` signatures tightened — `ctx` is now required (no default). `tool_error` docstring updated to direct ctx-less callers to `tool_output_raw(..., error=True)`.
- `co_cli/tools/web.py`: 3 `_http_get_with_retries` sites migrated to `tool_output_raw(..., error=True)`; 1 `web_fetch` site now passes `ctx=ctx`.
- `co_cli/tools/files.py`: `ctx=ctx` added to all 21 `tool_error` call sites across `glob`, `read_file`, `grep`, `write_file`, `patch`.
- `co_cli/tools/knowledge.py`: `ctx=ctx` added to 2 `ResourceBusyError` branches; 4 `tool_output_raw` ctx-aware success returns migrated to `tool_output(ctx=ctx, ...)`; `tool_output_raw` import removed.
- `co_cli/tools/google/calendar.py`: `ctx=ctx` added to 2 `handle_google_api_error` sites.
- `co_cli/tools/google/gmail.py`: `ctx=ctx` added to 3 `handle_google_api_error` sites.
- `tests/test_tools_files.py`: 5 new regression tests asserting `PERSISTED_OUTPUT_TAG in result.return_value`.
- `tests/test_memory.py`: 3 new regression tests (append/update busy path, save_knowledge success path).
