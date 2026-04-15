# Plan: tools/ Consolidation

**Task type: refactor** — code reorganization without behavior change. No new tools, no new behavior, no config additions.

---

## Context

The `co_cli/tools/` directory has 28 Python files. Three groups have fragmented across multiple files without a clear structural reason:

1. **Memory tools** — `memory.py` (518 lines), `memory_edit.py` (216 lines), `memory_write.py` (97 lines). During this read, one additional defect was found:
   - `memory.py` contains inferior duplicate implementations of `update_memory` and `append_memory` (lines 366–518). The authoritative implementations live in `memory_edit.py`: they use `render_memory_file`, atomic `os.replace` via `tempfile.NamedTemporaryFile`, and knowledge store DB re-indexing after every write. The `memory.py` versions use direct `write_text` and do NOT re-index the DB — they are live but inferior. Tests (`tests/test_memory.py` lines 471/498) import specifically from `memory_edit.py` to exercise the DB re-indexing path. `memory_edit.py` is the authoritative source for these operations.

2. **Google integration tools** — `_google_auth.py` (87 lines), `google_calendar.py` (221 lines), `google_drive.py` (181 lines), `google_gmail.py` (168 lines). Each file defines its own `_get_*_service()` factory with identical auth delegation. All three service files repeat the same NOT_CONFIGURED error pattern.

3. **Background tasks** — `background.py` is the implementation layer for `task_control.py`. Renaming to `_background.py` was initially proposed but is **invalid** per CLAUDE.md: leading-underscore modules are package-private, but `background.py` is imported by `co_cli/deps.py`, `co_cli/main.py`, and `co_cli/commands/_commands.py` (all outside `co_cli/tools/`). No rename needed.

**Workflow artifact hygiene:** No stale exec-plans for this scope.

---

## Problem & Outcome

**Problem:** Memory tooling is split across three files with a stale dead-code block (`update_memory`/`append_memory` in `memory.py`) and shared helpers replicated inline. Google tool files repeat the same service-factory pattern three times with no shared abstraction.

**Failure cost:** The inferior `update_memory`/`append_memory` in `memory.py` is a trap — a developer reading the file will find these functions and assume they are the canonical version, missing that `memory_edit.py` has the atomic-write + re-index implementation. Any caller that accidentally uses the `memory.py` version would silently skip DB re-indexing, breaking memory search. The duplicated `_get_*_service()` factories in Google files make auth logic change risky (three places to update instead of one).

**Outcome:**
- Memory operations consolidated into one `memory.py`; `memory_write.py` and `memory_edit.py` deleted.
- Google tools in a `co_cli/tools/google/` subpackage with a single shared `_get_google_service()` factory; old flat files deleted.
- All callers updated; zero stale imports.

---

## Scope

**In scope:**
- Remove dead `update_memory`/`append_memory` from `memory.py`
- Merge `memory_write.py` and `memory_edit.py` into `memory.py`
- Move Google tools into `co_cli/tools/google/` subpackage with shared auth factory
- Update all callers (production + tests + evals) for both consolidations

**Out of scope:**
- Registering `save_memory`, `update_memory`, `append_memory` as agent tools (currently intentionally unregistered — separate decision)
- `background.py` rename (violates CLAUDE.md package-private rule)
- Any behavior change to tool logic

---

## Behavioral Constraints

- All public tool function signatures must be identical before and after consolidation.
- `_native_toolset.py` must register the same tools with the same metadata (approval, visibility, retries, max_result_size).
- `co_cli/memory/_extractor.py` must import `save_memory` from its new location and call it with the same arguments.
- `_recall_for_context` must remain importable from `co_cli.tools.memory` (called by `co_cli/context/_history.py:661`).
- `BackgroundTaskState` and related symbols remain in `co_cli/tools/background` (no rename, not in scope).
- No test may be deleted; only import paths updated.
- Full test suite (`uv run pytest`) must pass after each task.

---

## High-Level Design

### Memory consolidation (`memory.py`)

Merge `memory_write.py` (save_memory) and `memory_edit.py` (update_memory, append_memory) into the existing `memory.py`. Remove the stale dead code first. The merged file keeps:
- Module-level helpers: `_TRACER`, `_LINE_PREFIX_RE`, `_LINE_NUM_RE`, `grep_recall`, `filter_memories`
- Private: `_recall_for_context` (used by `_history.py`)
- Public tools: `search_memories`, `list_memories`, `save_memory`, `update_memory`, `append_memory`

`_find_by_slug()` from `memory_edit.py` becomes a module-level helper in `memory.py`.

### Google subpackage (`co_cli/tools/google/`)

```
co_cli/tools/google/
    __init__.py     # docstring-only (CLAUDE.md: __init__.py must be docstring-only)
    _auth.py        # package-private: get_cached_google_creds, ensure_google_credentials,
                    #   ALL_GOOGLE_SCOPES, _get_google_service(); only imported within subpackage
    calendar.py     # public: list_calendar_events, search_calendar_events
    drive.py        # public: search_drive_files, read_drive_file
    gmail.py        # public: list_gmail_emails, search_gmail_emails, create_gmail_draft
```

Module naming follows CLAUDE.md package-private rule: `calendar.py`, `drive.py`, `gmail.py` have no underscore because they are imported from outside the subpackage by `co_cli/agent/_native_toolset.py`. `_auth.py` keeps the underscore because it is only imported from within `co_cli/tools/google/`.

`_get_google_service(ctx, service_name, version, not_configured_msg)` in `_auth.py` replaces the three `_get_*_service()` functions. Each service module imports it from `co_cli.tools.google._auth`.

`_native_toolset.py` imports change from `co_cli.tools.google_calendar` etc. to `co_cli.tools.google.calendar`, `co_cli.tools.google.drive`, `co_cli.tools.google.gmail`.

---

## Implementation Plan

### ✓ DONE — TASK-1 — Remove dead code from memory.py

**Priority: 1 (do first — prerequisite for TASK-2)**

Remove `update_memory` (lines 366–461) and `append_memory` (lines 464–518) from `co_cli/tools/memory.py`. These are the inferior duplicates: they use direct `write_text` with no atomic write and no knowledge store DB re-index. The authoritative implementations are in `memory_edit.py` (atomic `os.replace` + DB re-index) and will be merged into `memory.py` in TASK-2. Removing the inferior copies first prevents any risk of accidentally using them during TASK-2.

```
files:
  - co_cli/tools/memory.py
done_when: |
  grep -n "^async def update_memory\|^async def append_memory" co_cli/tools/memory.py
  returns zero results AND
  uv run pytest tests/test_memory.py -x passes
success_signal: N/A (no user-visible behavior change)
prerequisites: none
```

### ✓ DONE — TASK-2 — Consolidate memory tools into memory.py

**Priority: 2**

Merge `memory_write.py` and `memory_edit.py` into `memory.py`, then delete the source files. Update all callers.

Steps:
1. Merge `memory_write.py` into `memory.py`: add `save_memory` and its `_slugify` helper. Add comment: `# _slugify is intentionally duplicated from articles.py; consolidation deferred`. Add missing imports: `from uuid import uuid4`, `import hashlib`; extend `co_cli.knowledge._frontmatter` import to include `MemoryTypeEnum`; extend `co_cli.tools.tool_output` import to include `tool_output_raw`.
2. Merge `memory_edit.py` (authoritative source) into `memory.py`: add `_find_by_slug`, then the authoritative `update_memory` and `append_memory` (using `render_memory_file`, atomic `os.replace` + `tempfile.NamedTemporaryFile`, and knowledge store re-indexing). Remove the duplicate `_LINE_PREFIX_RE` / `_LINE_NUM_RE` definitions from `memory_edit.py` content since they already exist at the top of `memory.py`. Add required imports not yet in `memory.py` after step 1: `import os`, `import tempfile`, and extend the `co_cli.knowledge._frontmatter` import to include `render_memory_file`. (`hashlib` was already added in step 1 — do not duplicate.)
3. Delete `co_cli/tools/memory_write.py` and `co_cli/tools/memory_edit.py`.
4. Update all `from co_cli.tools.memory_edit import ...` and `from co_cli.tools.memory_write import ...` occurrences across all files:
   - `co_cli/memory/_extractor.py`: `from co_cli.tools.memory_write import save_memory` → `from co_cli.tools.memory import save_memory`
   - `tests/test_memory.py`: all `from co_cli.tools.memory_edit import ...` occurrences (module-level line 23, inline lines 471, 498) → `from co_cli.tools.memory import ...`
   - `evals/eval_memory_edit_recall.py`: both import lines → `from co_cli.tools.memory import ...`

```
files:
  - co_cli/tools/memory.py
  - co_cli/tools/memory_write.py   (delete)
  - co_cli/tools/memory_edit.py    (delete)
  - co_cli/memory/_extractor.py
  - tests/test_memory.py
  - evals/eval_memory_edit_recall.py
done_when: |
  ls co_cli/tools/memory_write.py 2>/dev/null returns "no such file" AND
  ls co_cli/tools/memory_edit.py 2>/dev/null returns "no such file" AND
  grep -rn "from co_cli.tools.memory_write\|from co_cli.tools.memory_edit" --include="*.py" returns zero results AND
  uv run pytest tests/test_memory.py -x passes
success_signal: N/A (no user-visible behavior change)
prerequisites: [TASK-1]
```

### ✓ DONE — TASK-3 — Google tools subpackage

**Priority: 3**

Create `co_cli/tools/google/` subpackage and move the four Google files into it with a shared service factory.

Steps:
1. Create `co_cli/tools/google/_auth.py`:
   - Copy `ensure_google_credentials`, `get_cached_google_creds`, `ALL_GOOGLE_SCOPES` from `_google_auth.py`
   - Add `_get_google_service(ctx, service_name, version, not_configured_msg)` factory
2. Create `co_cli/tools/google/calendar.py`: copy from `google_calendar.py`, replace `_get_calendar_service(ctx)` call with `_get_google_service(ctx, "calendar", "v3", _CALENDAR_NOT_CONFIGURED)` imported from `co_cli.tools.google._auth`. Move the NOT_CONFIGURED constant inline.
3. Create `co_cli/tools/google/drive.py`: same pattern for Drive, importing `_get_google_service` from `._auth`.
4. Create `co_cli/tools/google/gmail.py`: same pattern for Gmail.
5. Create `co_cli/tools/google/__init__.py`: docstring-only (`"""Google integration tools subpackage."""`). No imports, no re-exports.
6. Delete `co_cli/tools/_google_auth.py`, `co_cli/tools/google_calendar.py`, `co_cli/tools/google_drive.py`, `co_cli/tools/google_gmail.py`.
7. Update callers to use direct submodule imports:
   - `co_cli/agent/_native_toolset.py`:
     - `from co_cli.tools.google_calendar import ...` → `from co_cli.tools.google.calendar import ...`
     - `from co_cli.tools.google_drive import ...` → `from co_cli.tools.google.drive import ...`
     - `from co_cli.tools.google_gmail import ...` → `from co_cli.tools.google.gmail import ...`
   - `co_cli/tools/agents.py`: `from co_cli.tools.google_drive import search_drive_files` → `from co_cli.tools.google.drive import search_drive_files`

Guard conditions for shared factory: if `creds` is None, return `(None, tool_error(not_configured_msg))`. Identical behavior to all three existing `_get_*_service()` functions — no divergence.

```
files:
  - co_cli/tools/google/__init__.py   (create — docstring-only)
  - co_cli/tools/google/_auth.py      (create)
  - co_cli/tools/google/calendar.py   (create)
  - co_cli/tools/google/drive.py      (create)
  - co_cli/tools/google/gmail.py      (create)
  - co_cli/tools/_google_auth.py      (delete)
  - co_cli/tools/google_calendar.py   (delete)
  - co_cli/tools/google_drive.py      (delete)
  - co_cli/tools/google_gmail.py      (delete)
  - co_cli/agent/_native_toolset.py
  - co_cli/tools/agents.py
done_when: |
  ls co_cli/tools/google_calendar.py 2>/dev/null returns "no such file" AND
  grep -rn "from co_cli.tools.google_calendar\|from co_cli.tools.google_drive\|from co_cli.tools.google_gmail\|from co_cli.tools._google_auth" --include="*.py" returns zero results AND
  uv run pytest -x passes (full suite)
success_signal: N/A (no user-visible behavior change)
prerequisites: none
```

### ✓ DONE — TASK-4 — Consolidate tool infrastructure files

**Priority: 4**

Twelve infrastructure/helper files support the tools layer. Several are single-consumer files that should be inlined; others form a logical group that should be one file. Three groups:

**Group A — Tool I/O (3 files → 1)**

`tool_output.py` (64 L), `tool_errors.py` (73 L), `tool_result_storage.py` (97 L) form a single concern: how tool results are constructed, typed, and sized. Merge into `tool_io.py`. Both `ToolResultPayload` and `tool_output_raw` are imported from outside `co_cli/tools/` (`co_cli/context/tool_display.py`, `co_cli/display/_core.py`) — no underscore.

Callers to update (production): every file in `co_cli/tools/` that imports from these three, plus `co_cli/context/tool_display.py`, `co_cli/display/_core.py`. Tests importing `tool_output` and `tool_result_storage` update import paths accordingly.

**Group B — Web helpers (2 files → inline)**

`_http_retry.py` (179 L) and `_url_safety.py` (63 L) are only imported by `web.py`. Inline both into `web.py` as private module-level helpers. Delete the source files.

**Group C — Shell infra (no changes needed)**

Under the updated three-tier visibility rule (CLAUDE.md), test imports do not determine tier — only production code import locations count.

- `_shell_policy.py`: only `shell.py` (production) imports it within `co_cli/tools/` → correctly package-private, underscore stays. `tests/test_shell.py` does white-box access — that is permitted and does not trigger a rename.
- `_shell_env.py`: only `shell.py` and `background.py` (both in `co_cli/tools/`) import it → correctly package-private. No change.
- `shell_backend.py`: imported by `co_cli/deps.py`, `co_cli/bootstrap/core.py`, and many tests → stable internal API tier, no underscore is correct. No change.

```
files:
  # Group A — Tool I/O merge
  - co_cli/tools/tool_io.py              (create — merged from tool_output.py + tool_errors.py + tool_result_storage.py)
  - co_cli/tools/tool_output.py          (delete)
  - co_cli/tools/tool_errors.py          (delete)
  - co_cli/tools/tool_result_storage.py  (delete)
  # Group B — Web helpers inline
  - co_cli/tools/web.py                  (inline _http_retry.py + _url_safety.py; update tool_output/tool_errors → tool_io)
  - co_cli/tools/_http_retry.py          (delete)
  - co_cli/tools/_url_safety.py          (delete)
  # Import updates — tools/ internal callers
  - co_cli/tools/agents.py
  - co_cli/tools/articles.py
  - co_cli/tools/capabilities.py
  - co_cli/tools/files.py
  - co_cli/tools/memory.py               (also has inline tool_error imports added by TASK-2 merge)
  - co_cli/tools/obsidian.py
  - co_cli/tools/session_search.py
  - co_cli/tools/shell.py
  - co_cli/tools/task_control.py
  - co_cli/tools/todo.py
  # Import updates — google subpackage (created by TASK-3, import from tool_io)
  - co_cli/tools/google/calendar.py
  - co_cli/tools/google/drive.py
  - co_cli/tools/google/gmail.py
  # Import updates — outside tools/
  - co_cli/agent/_native_toolset.py
  - co_cli/context/tool_display.py
  - co_cli/display/_core.py
  # Import updates — tests
  - tests/test_tool_output_sizing.py
  # Import updates — evals
  - evals/eval_compaction_quality.py
done_when: |
  grep -rn "from co_cli.tools.tool_output\|from co_cli.tools.tool_errors\|from co_cli.tools.tool_result_storage\|from co_cli.tools._http_retry\|from co_cli.tools._url_safety" --include="*.py" returns zero results AND
  uv run pytest -x passes (full suite)
success_signal: N/A (no user-visible behavior change)
prerequisites: [TASK-2, TASK-3]
```

---

## Testing

All tasks are pure refactors; no new test files needed. Verification relies on the existing test suite:

- `tests/test_memory.py` — covers `save_memory`, `update_memory`, `append_memory`, `list_memories`, `search_memories`, `_recall_for_context`
- `tests/test_tool_registry.py` — verifies tool registration in native toolset
- `tests/test_tool_calling_functional.py` — end-to-end tool calling
- `tests/test_background.py` — background task layer (not affected, confirmed)

Full suite gate: `uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tools-consolidation.log`

---

## Open Questions

None — all questions answered by source inspection before drafting.

## Final — Team Lead

Plan approved. TASKS 1–3 cleared two automated review cycles (C1 + C2, all blocking issues resolved). TASK-4 added post-C2 based on user scope expansion; follows the same refactor pattern and was not separately cycled — human Gate 1 review covers TASK-4 scope.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev tools-consolidation`

---

## Delivery Summary — 2026-04-15

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | inferior update_memory/append_memory removed; pytest tests/test_memory.py passes | ✓ pass (implemented atomically with TASK-2) |
| TASK-2 | memory_write.py/memory_edit.py deleted; no stale imports; pytest tests/test_memory.py passes | ✓ pass |
| TASK-3 | google_calendar.py/drive.py/gmail.py deleted; no stale imports; pytest -x passes | ✓ pass |
| TASK-4 | no stale imports from tool_output/tool_errors/tool_result_storage/_http_retry/_url_safety; pytest -x passes | ✓ pass |

**Tests:** full suite — 468 passed, 0 failed
**Independent Review:** clean / 0 blocking / 2 minor (pre-existing `tool_error` without ctx in gmail.py, calendar.py, memory.py — not regressions from this refactor)
**Doc Sync:** fixed (tools.md: Core Infrastructure + Domain Tools stale paths; memory.md: section 2.4 + Files table)

**Overall: DELIVERED**
Pure refactor: 11 files deleted (memory_write.py, memory_edit.py, tool_output.py, tool_errors.py, tool_result_storage.py, _http_retry.py, _url_safety.py, _google_auth.py, google_calendar.py, google_drive.py, google_gmail.py), 3 files created (tool_io.py, google/__init__.py, google/_auth.py, google/calendar.py, google/drive.py, google/gmail.py), all callers updated, zero stale imports.

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/tools/memory.py:217` | `tool_error()` without `ctx` in `search_memories()` — ctx available but not passed. Pre-existing pattern. | minor | TASK-1+2 |
| `co_cli/tools/memory.py:560,631` | `tool_error()` without `ctx` in `update_memory`/`append_memory` error paths. Pre-existing pattern. | minor | TASK-1+2 |
| All refactored modules | No stale imports from any deleted module. Comprehensive grep confirms zero results. | clean | all |
| `tool_io.py` | Correctly merges all symbols from tool_output.py + tool_errors.py + tool_result_storage.py. | clean | TASK-4 |
| `web.py` | SSRF protection and HTTP retry helpers correctly inlined as private module-level helpers. | clean | TASK-4 |
| `google/__init__.py` | Docstring-only per CLAUDE.md. | clean | TASK-3 |
| `google/_auth.py` | Package-private underscore prefix correct. `_get_google_service` factory properly replaces three per-service factories. | clean | TASK-3 |
| `co_cli/agent/_native_toolset.py` | Google imports updated to `co_cli.tools.google.{calendar,drive,gmail}`. | clean | TASK-3 |
| `tests/test_memory.py` | Real RunContext + real CoDeps. No mocks. 19/19 pass. | clean | TASK-1+2 |

**Overall: clean / 0 blocking / 2 minor**

---

## Implementation Review — 2026-04-15

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1+2 | inferior versions removed; memory_write.py/memory_edit.py deleted; no stale imports; tests pass | ✓ pass | memory.py:453,565 — `update_memory`/`append_memory` present with atomic `os.replace` (memory.py:532-536, 603-607) + DB re-index (memory.py:538-552, 609-622); memory_write.py/memory_edit.py confirmed absent; `_extractor.py:29` imports `save_memory` from `co_cli.tools.memory` |
| TASK-3 | old google files deleted; no stale imports; full suite passes | ✓ pass | google/__init__.py:1 docstring-only; google/_auth.py:91 `_get_google_service` shared factory replacing 3 per-service factories; calendar.py:9, drive.py:9, gmail.py:9 all import from `._auth`; _native_toolset.py:20-22 updated to `co_cli.tools.google.{calendar,drive,gmail}`; agents.py:402 lazy import from `co_cli.tools.google.drive`; all 4 old files confirmed absent |
| TASK-4 | no stale imports; full suite passes | ✓ pass | tool_io.py: merged `tool_output`, `tool_output_raw`, `tool_error`, `ToolResultPayload`, `persist_if_oversized`, `check_tool_results_size`, `http_status_code`, `handle_google_api_error`; web.py:26-257 SSRF protection and HTTP retry inlined as private helpers; grep of all deleted module names returns zero results |

### Issues Found & Fixed

No issues found. Pre-existing minors from delivery review re-confirmed and accepted:
- `memory.py:217` — `tool_error()` without `ctx` in `search_memories` (ctx available but not passed; pre-existing pattern, no functional impact)
- `memory.py:560,631` — same pattern in `update_memory`/`append_memory` ResourceBusyError paths

### Tests
- Command: `uv run pytest -v`
- Result: 468 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: full — shared modules renamed, new google/ subpackage
- Result: clean — `docs/specs/tools.md` and `docs/specs/memory.md` contain no stale file references; `tool_io.py`, `google/_auth.py`, `google/calendar.py`, `google/drive.py`, `google/gmail.py` all present in Files tables

### Behavioral Verification
- `uv run co config`: ✓ healthy — all components (LLM, Shell, Google, Web Search, MCP, Database) show expected status; Google integration correctly configured via `co_cli.tools.google._auth`
- No user-facing behavior changed (pure refactor) — success_signal: N/A for all tasks

### Overall: PASS
Pure refactor delivered cleanly: 11 files deleted, 6 files created, all callers updated, zero stale imports, 468 tests green, lint clean, docs current.
