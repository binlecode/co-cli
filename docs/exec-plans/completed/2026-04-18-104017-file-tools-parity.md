# Plan: File Tools Effectiveness Parity

**Task type: code-feature**

## Context

The `co-cli` toolset currently has an implementation gap regarding file operations (`read_file`, `patch`). While the core functionality exists, the API shape and error feedback loops limit the LLM's reasoning and recovery capabilities, leading to tool paralysis or destructive multi-step workarounds.

**Current state validation (2026-04-18):**
- `co_cli/tools/files.py` examined; none of the proposed enhancements are implemented — all four gaps are unshipped.
- No `tests/test_files.py` exists; TASK-1 will create it.
- No reference research doc found (`docs/reference/RESEARCH-*file*` → not found).
- Original plan had a factual error: pagination hint parameter name written as `offset` but the `read_file` API uses `start_line`. Corrected in this revision.
- Original plan omitted the exact-match ambiguous path (line 586 in `patch`) from the error-message update scope. Both paths must be updated.

Four gaps addressed:
1. **Ambiguous Match Feedback Loop (Critical):** Both the exact-match path (`patch` line 586) and `_fuzzy_apply` return "use replace_all=True to replace all" without guiding context expansion. Both code paths must be updated.
2. **Post-Edit Verification (Diff, opt-in):** `patch` has no mechanism for the model to verify what changed without re-reading the file, risking re-application of an already-succeeded patch.
3. **No Auto-Linting:** Syntax errors introduced during a patch are invisible until the next turn.
4. **Missing Recovery Hints on `read_file`:** (a) Missing file returns a generic error with no filename suggestions; (b) partial read returns no continuation hint.

Note: V4A multi-file patch mode is out of scope — single-file parity is the MVP target.

## Problem & Outcome

**Problem:** The `co-cli` LLM agent struggles to perform targeted code edits efficiently, often corrupting files or wasting turns due to poor error messages, missing continuation hints, and no post-edit verification signal.
**Failure cost:** Agents waste context tokens, enter infinite retry loops applying patches, silently introduce syntax errors, issue unnecessary `glob` calls for near-typo paths, and re-apply already-succeeded patches because they cannot confirm the edit landed. Misguided `replace_all=True` on ambiguous matches can cause widespread file corruption.

**Outcome:** The `co-cli` file tools (`read_file`, `patch`) provide the model with explicit error-recovery hints, opt-in post-edit diffs, fuzzy filename suggestions, pagination continuations, and non-fatal lint feedback.

## Scope

- Enhance `co_cli/tools/files.py:read_file` to include fuzzy filename suggestions for missing files.
- Enhance `co_cli/tools/files.py:read_file` to include explicit pagination hints when a partial read does not reach EOF.
- Enhance `co_cli/tools/files.py:patch` (exact-match and `_fuzzy_apply` paths) to return context-expansion guidance on ambiguous matches.
- Add `show_diff: bool = False` parameter to `patch`; when `True`, return a unified diff in the display after a successful edit.
- Enhance `co_cli/tools/files.py:patch` to run `ruff check` on Python files after a successful edit, appending warnings non-fatally and outside the resource lock.
- Refactor `read_file` and `patch` docstrings for prompt parity with hermes-agent: uniqueness requirement, not-found recovery hint, `start_line`/`end_line` guidance, and `show_diff:` Args entry.

## Behavioral Constraints

- Must maintain existing `RunContext[CoDeps]` and `async` conventions throughout.
- Must maintain workspace boundary enforcement, resource lock, and staleness check preconditions on all paths.
- Fuzzy filename suggestions must silently skip (no crash, no extra error) if the parent directory does not exist.
- `ruff check` must execute only on `.py` files; must be wrapped in `asyncio.timeout(5)`; must treat `FileNotFoundError` (ruff/uv not on PATH) as a silent no-op; must never cause the tool call to return a `tool_error`; must run after the resource lock is released.
- Diff generation is opt-in (`show_diff=False` by default); when `show_diff=False`, no diff block is added to display.
- Pagination hint must use `start_line={hi + 1}` — matching the `read_file` API — never `offset`.
- Diff and lint output is appended to the display string only; structured `ToolReturn` fields (`replacements`, `strategy`, `path`) remain unchanged.

## High-Level Design

### `read_file` Enhancements

**Fuzzy Suggestions:** When `resolved.exists()` is False and `resolved.parent.exists()` is True:
```
names = [p.name for p in resolved.parent.iterdir()]
matches = difflib.get_close_matches(resolved.name, names, n=3, cutoff=0.6)
if matches:
    error_msg += f"\nSimilar files: {matches}"
```
Return `tool_error(error_msg, ctx=ctx)`.

**Pagination Hints:** `hi` is defined as `end_line if end_line is not None else total_line_count`. A hint is emitted only when `end_line` was explicitly provided and `total_line_count > hi`. In that case, append to `display`:
```
\n[{total_line_count - hi} more lines — use start_line={hi + 1} to continue reading]
```
No hint when reading the full file (end_line is None → hi == total_line_count).

### `patch` Enhancements

**Error Messaging:** Both the exact-match ambiguous path (currently at `patch` line 586) and `_fuzzy_apply` return:
```
Found {count} occurrences — provide more surrounding context to make old_string unique, or use replace_all=True to replace all occurrences.
```

**Diff Generation (opt-in):** After any successful `content → updated` replacement, if `show_diff=True`:
```python
diff_lines = list(difflib.unified_diff(
    content.splitlines(keepends=True),
    updated.splitlines(keepends=True),
    fromfile=f"a/{path}",
    tofile=f"b/{path}",
    lineterm="",
))
diff_str = "".join(diff_lines) if diff_lines else "(no diff)"
display = f"[Diff]\n{diff_str}\n\n{display}"
```
When `show_diff=False` (default), skip this block entirely.

**Auto-Linting (outside resource lock):** After writing `.py` files, the resource lock must be released first. Structure:
```
with resource_lock:
    # ... write file, build display string ...
    local_display = display
    local_resolved = resolved
    local_suffix = resolved.suffix
# lock released — now run lint
if local_suffix == ".py":
    try:
        async with asyncio.timeout(5):
            proc = await asyncio.create_subprocess_exec(
                "uv", "run", "ruff", "check", str(local_resolved),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                local_display += f"\n\n[Auto-Lint Warnings]\n{stdout.decode()}"
    except (asyncio.TimeoutError, FileNotFoundError):
        pass
return tool_output(local_display, ...)
```

## Implementation Plan

### ✓ DONE — TASK-1: read_file — Fuzzy Suggestions and Pagination Hints
- **files:** `co_cli/tools/files.py`, `tests/test_files.py`
- **done_when:** `uv run pytest tests/test_files.py -k "read_file"` passes; tests cover: (a) missing file with similar name returns suggestion list in error message, (b) missing file with no similar names returns clean "File not found" error, (c) missing parent directory returns clean error with no crash, (d) partial read with lines remaining returns `start_line=N` hint in display, (e) partial read at EOF returns no hint, (f) full read returns no hint. Also: `grep "^import difflib" co_cli/tools/files.py` exits 0.
- **success_signal:** When reading a non-existent file with a near-typo name, the agent sees similar filenames inline and self-corrects without needing to call `glob`.

### ✓ DONE — TASK-2: patch — Context-Expansion Error and Opt-In Unified Diff
- **files:** `co_cli/tools/files.py`, `tests/test_files.py`
- **done_when:** `uv run pytest tests/test_files.py -k "patch"` passes; tests cover: (a) ambiguous exact match error contains "provide more surrounding context", (b) ambiguous fuzzy match error contains "provide more surrounding context", (c) `patch(..., show_diff=True)` on a successful exact-match returns display containing `[Diff]` block with `+`/`-` lines, (d) `patch(..., show_diff=True)` on a successful fuzzy-match returns `[Diff]` block, (e) `patch(...)` without `show_diff` (default False) returns no `[Diff]` block, (f) `patch(..., show_diff=True)` where `old_string == new_string` produces `(no diff)` in display without raising. Also: `grep "^import difflib" co_cli/tools/files.py` exits 0; `patch` docstring `Args:` section includes a `show_diff:` entry describing when to pass `True` (prompt parity — the LLM must know the param exists and why to use it).
- **success_signal:** When patching ambiguously, the model sees "provide more surrounding context" guidance. When `show_diff=True` is passed, successful patches include a `+`/`-` diff block.

### ✓ DONE — TASK-3: patch — Auto-Linting
- **files:** `co_cli/tools/files.py`, `tests/test_files.py`
- **prerequisites:** [TASK-2]
- **done_when:** `uv run pytest tests/test_files.py -k "lint"` passes; tests cover: (a) patching a `.py` file that introduces invalid syntax produces `[Auto-Lint Warnings]` in display, (b) patching a `.py` file with clean result produces no lint block, (c) patching a non-`.py` file produces no lint output. Also: `grep "^import asyncio" co_cli/tools/files.py` exits 0.
- **success_signal:** If the LLM introduces a syntax error during a patch, it immediately receives linter warnings in the tool response without the tool call failing.

### ✓ DONE — TASK-4: Docstring Prompt Parity — read_file and patch
- **files:** `co_cli/tools/files.py`
- **prerequisites:** [TASK-1, TASK-2]
- **done_when:** Docstrings for `read_file` and `patch` contain all of the following (grep-verifiable): (a) `read_file` body mentions `start_line`/`end_line` for large files and that missing-file errors include similarity suggestions; (b) `patch` description states old_string must be unique in the file; (c) `patch` description includes a recovery hint when old_string is not found ("re-read the file to confirm the text before retrying"); (d) `patch` `Args:` includes `show_diff:` entry explaining when to pass `True`.
- **success_signal:** A model reading only the tool schema can correctly recover from a failed patch, know to use `start_line`/`end_line` for large files, and know when to request a diff — without relying on system-prompt guidance.

## Testing

All tests use `tmp_path` (real filesystem writes) and real subprocess invocations. No `unittest.mock`, `monkeypatch`, or `pytest-mock`. Tests are async (`pytest-asyncio`). `tests/test_files.py` is created by TASK-1 — TASK-2 and TASK-3 append to the same file. `ruff` is available in dev deps.

## Open Questions

- None.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev 2026-04-18-104017-file-tools-parity`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/tools/files.py` | `_make_diff_block` used `splitlines(keepends=True)` + `"".join()` — diff headers merged onto one line in output | blocking | TASK-2 |
| `co_cli/tools/files.py` | `Similar files` error used Python list repr instead of human-readable string | minor | TASK-1 |

Both findings fixed before delivery. Stronger assertions added to `test_patch_show_diff_exact_match_contains_diff_block` to catch the malformed-header regression.

**Overall: 2 fixed (1 was blocking, 1 minor)**

## Delivery Summary — 2026-04-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/test_files.py -k "read_file"` passes; `import difflib` present | ✓ pass |
| TASK-2 | `uv run pytest tests/test_files.py -k "patch"` passes; `show_diff:` docstring entry present | ✓ pass |
| TASK-3 | `uv run pytest tests/test_files.py -k "lint"` passes; `import asyncio` present | ✓ pass |
| TASK-4 | Docstrings contain all required elements (grep-verified) | ✓ pass |

**Tests:** full suite — 634 passed, 0 failed
**Independent Review:** 1 blocking fixed, 1 minor fixed
**Doc Sync:** fixed (`read_file` description, `patch` signature + description in `docs/specs/tools.md`)

**Overall: DELIVERED**
All four tasks shipped. `read_file` now emits fuzzy filename suggestions and pagination continuation hints. `patch` now surfaces context-expansion guidance on ambiguous matches, supports `show_diff=True` for post-edit verification, and auto-runs `ruff` on `.py` files after successful edits. Docstrings updated for full prompt parity.

## Implementation Review — 2026-04-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `pytest -k "read_file"` passes; `import difflib` | ✓ pass | `files.py:173-180` — fuzzy suggestion on missing file; `files.py:213-216` — pagination hint guarded by `end_line is not None and total_line_count > hi` |
| TASK-2 | `pytest -k "patch"` passes; `show_diff:` docstring | ✓ pass | `files.py:542-545` — exact-match ambiguous error; `files.py:643-646` — fuzzy ambiguous error; `files.py:509-521` — `_make_diff_block` with `splitlines()` + `"\n".join()`; `files.py:662` — `show_diff: bool = False` |
| TASK-3 | `pytest -k "lint"` passes; `import asyncio` | ✓ pass | `files.py:569-589` — `_run_lint_if_python`; `files.py:725` — called after `try/except` block (lock released); `files.py:571` — `.py`-only guard; `files.py:587` — `(TimeoutError, FileNotFoundError)` silent |
| TASK-4 | All four docstring requirements grep-verified | ✓ pass | `files.py:152-155` — `start_line/end_line` + similarity hint; `files.py:668-669` — uniqueness + re-read recovery; `files.py:686-687` — `show_diff:` Args entry |

### Issues Found & Fixed
No issues found during review. All spec requirements implemented as specified. The two findings from the delivery-phase independent review (malformed diff headers, list repr) were already fixed before this review ran.

### Tests
- Command: `uv run pytest -v`
- Result: 634 passed, 0 failed
- Log: `.pytest-logs/20260418-212134-review-impl.log`

### Doc Sync
- Scope: narrow — all tasks confined to `co_cli/tools/files.py`; no public API renames, no schema changes
- Result: clean — `docs/specs/tools.md` was updated during delivery with correct `read_file` and `patch` descriptions; no further changes needed

### Behavioral Verification
- `uv run co config`: ✓ healthy (LLM online, shell active, MCP ready)
- Success signals verified via direct tool invocation:
  - TASK-1: `read_file("confg.yaml")` → `"Similar files: config.yaml"` — agent self-corrects without `glob`
  - TASK-1: partial read of 29-line file → `"[19 more lines — use start_line=11 to continue reading]"` — pagination hint present
  - TASK-2: ambiguous `patch` → `"provide more surrounding context to make old_string unique"` — guidance correct
  - TASK-2: `patch(..., show_diff=True)` → `['-x = 1', '+x = 99']` — proper unified diff lines
  - TASK-3: `patch` introducing unused imports → `[Auto-Lint Warnings]` in display, tool call succeeds (not error)
  - TASK-4: docstring schema verified — all four prompt-parity requirements confirmed in tool description visible to model

### Overall: PASS
All four tasks fully implemented and verified. 634 tests green. No issues found or fixed. Ship directly.
