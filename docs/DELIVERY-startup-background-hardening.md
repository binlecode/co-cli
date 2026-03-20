# Delivery: Startup And Background Task Hardening
Date: 2026-03-19

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1 | clean `co chat` startup failure with no traceback | ✓ pass | Already present in the worktree baseline via `chat()` startup error handling; regression passes |
| TASK-2 | same-second background tasks never reuse `task_id` | ✓ pass | Already present in the worktree baseline via UUID-suffixed task IDs; regression passes |
| TASK-3 | `hybrid` bootstrap degrades to supported backend with explicit status | ✓ pass | Added real bootstrap resolver, status surfacing, and deterministic regression coverage |

## Files Changed
- `co_cli/bootstrap/_bootstrap.py` — added `resolve_knowledge_backend()` and startup degradation status collection
- `co_cli/deps.py` — added `CoRuntimeState.startup_statuses`
- `co_cli/main.py` — renders bootstrap degradation statuses before the welcome banner
- `co_cli/knowledge/_index_store.py` — closes the SQLite connection before re-raising hybrid init failure
- `tests/test_bootstrap.py` — added deterministic hybrid-to-fts5 degradation regression
- `docs/DESIGN-system-bootstrap.md` — synced startup pseudocode and degradation behavior
- `docs/TODO-startup-background-hardening.md` — marked shipped tasks and appended TL/dev audit trail

## Tests
- `uv run pytest tests/test_startup_failures.py tests/test_bootstrap.py`
- Result: pass

## Overall
All three TODO tasks now satisfy their `done_when` criteria. Startup and task-ID hardening were already implemented in the active worktree; the remaining delivery work was the hybrid knowledge backend degradation path and its regression/doc sync.
