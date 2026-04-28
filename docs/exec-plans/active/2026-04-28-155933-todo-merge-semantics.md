# Plan: Todo Merge Semantics

Task type: `code-feature`

## Context

An existing Gate-1-approved plan covers the same scope:
`docs/exec-plans/active/2026-04-27-000000-parity-backfill-todo-tool-for-co-from-hermes.md`

That plan was not shipped (tasks show no `✓ DONE` marks). This plan supersedes it with
corrected `done_when` checks, current-state validation, and explicit RGR structure.
The prior plan should be archived or left as-is — it will not be executed.

**Current-state validation:**

- `co_cli/tools/todo.py`: `todo_write(todos)` + `todo_read()` — full-rewrite model;
  items are `{content, status, priority}` only; no `id` field; no `merge` param.
- `co_cli/context/_compaction_markers.py:143,157`: active todo lines formatted as
  `- [status] content` (no id); both `_gather_session_todos` and `build_todo_snapshot`
  have this same format.
- `tests/tools/test_todo.py`: 9 passing tests; covers write/read/validation; no merge tests.
- `tests/context/test_context_compaction.py`: uses `session_todos` dicts without `id`
  field (lines 894, 1264) — these will be updated to include `id` as part of this delivery.

No doc/source inaccuracies blocking planning. No stale TODO hygiene issues.

## Problem & Outcome

**Problem:** `todo_write` requires the model to rewrite the entire task list to update a
single item's status. For a 10-item plan, marking one step done sends all 10 items again.

**Failure cost:** Model drops tasks mid-plan (forgot to re-include them), sends a stale
snapshot after earlier items changed, or over-spends tokens on list management instead of work.

**Outcome:** `todo_write(todos, merge=True)` updates named items by `id` without touching
the rest. `_compaction_markers` shows IDs in active-task lines so the model can reference
them after context compression. Existing full-rewrite behavior (`merge=False`) is unchanged.

## Scope

- Add optional `id` field to todo items (auto-assigned on fresh write if absent).
- Add `merge: bool = False` param to `todo_write`.
- Update `_gather_session_todos` and `build_todo_snapshot` to include `id` in active lines.
- Tests for merge (update, append, preserve) and compaction id-display.

**Out of scope:**
- Cross-session persistence (co-cli is deliberately session-scoped).
- Collapsing `todo_write` + `todo_read` into one tool (hermes pattern; not worth the API break).
- `priority` field removed or changed (kept; co-cli advantage over hermes).

## Behavioral Constraints

- `merge=False` (default): full replace; `id` auto-assigned as `str(index+1)` if absent.
  Model-provided ids are preserved as-is.
- `merge=True`: match existing items by `id`; update only fields explicitly present in
  the payload; preserve all other fields. Items with unknown ids are appended. Items not
  mentioned in the payload are preserved unchanged and in original order.
- `id` in merge mode: items without `id` in the payload produce a per-item validation
  error (returned in the `errors` list); they do not corrupt state via silent ghost-append.
  The call still processes other valid items in the payload.
- Status and priority re-validation applies in both modes (merge updates go through the
  same `_VALID_STATUS` / `_VALID_PRIORITY` gates as fresh writes). Invalid field values
  in a merge payload produce a per-item error but do not discard other items or other fields.

## High-Level Design

**Item schema change:**
```
# before: {content: str, status: str, priority: str}
# after:  {id: str, content: str, status: str, priority: str}
```

**Auto-id on fresh write** (merge=False):
```python
for i, item in enumerate(validated):
    if not item.get("id"):
        item["id"] = str(i + 1)
```

**Merge logic** (merge=True):
```python
existing = {item["id"]: item for item in session_todos}
errors = []
for incoming in todos:
    id_ = str(incoming.get("id", "")).strip()
    if not id_:
        errors.append("merge item missing 'id' — skipped")
        continue
    if id_ in existing:
        for field in ("content", "status", "priority"):
            if field not in incoming:
                continue
            if field == "status" and incoming[field] not in _VALID_STATUS:
                errors.append(f"item {id_!r}: invalid status {incoming[field]!r}")
                continue
            if field == "priority" and incoming[field] not in _VALID_PRIORITY:
                errors.append(f"item {id_!r}: invalid priority {incoming[field]!r}")
                continue
            existing[id_][field] = incoming[field]
    else:
        validated = _validate_item(incoming)  # full validation for new items
        validated["id"] = id_               # ensure id is embedded
        existing[id_] = validated
session_todos = list(existing.values())
```

**Partial validation contract:** `_validate_item` performs full validation (content,
status, priority required; id optional — auto-assigned by caller). In merge mode, the
update path does field-by-field validation inline (above); only new items go through
`_validate_item`. An invalid field update produces an error entry for that item but does
not discard the item or other fields.

**Compaction format change:**
```
# before: - [in_progress] Write tests
# after:  - [in_progress] 2. Write tests
```

Both `_gather_session_todos` (display) and `build_todo_snapshot` (model injection) updated:
```python
f"- [{t['status']}] {t['id']}. {t['content']}"
```

## Implementation Plan

### TASK-1 — Write failing tests for merge semantics (Red)

Add tests for:
- `todo_write` auto-assigns sequential ids on fresh write
- `merge=True` updates matching item status, leaves others untouched
- `merge=True` appends unknown id as new item
- `merge=True` on empty list behaves like fresh write
- `merge=True` preserves list order
- `todo_read` returns `id` in each todo dict
- Compaction display includes id: `_gather_session_todos` output contains `"1."` when item has `id="1"`
- `build_todo_snapshot` output also includes id

`files:` `tests/tools/test_todo.py`, `tests/context/test_context_compaction.py`
`done_when:` Run `uv run pytest tests/tools/test_todo.py tests/context/test_context_compaction.py 2>&1 | tee /tmp/todo_red.log` then verify two things: (1) `grep -E "FAILED.*(test_write_assigns_ids|test_merge_|test_compaction_includes_id|test_todo_snapshot_includes_id|test_read_returns_id|test_merge_rejects_missing_id)" /tmp/todo_red.log` exits 0 (new tests are red); (2) `grep -E "FAILED.*(test_write_todos_|test_read_todos_)" /tmp/todo_red.log` exits 1 (existing 9 tests still pass).
`success_signal:` N/A
`prerequisites:` []

### TASK-2 — Implement id field and merge flag in todo_write (Green)

- Add `id` field to validated item dict; auto-assign `str(i+1)` on fresh write if absent.
- Add `merge: bool = False` param; implement merge logic per High-Level Design.
- Update `todo_read` to return `id` in each item dict (already stored; just verify it is passed through in the `todos` metadata list).
- Update docstring with merge guidance: `merge=True` + `id`-based targeting; "Only ONE item in_progress at a time."

`files:` `co_cli/tools/todo.py`
`done_when:` `uv run pytest tests/tools/test_todo.py -x` passes (all tests green, including TASK-1 merge tests).
`success_signal:` Model calls `todo_write([{"id": "3", "status": "completed"}], merge=True)` and only item 3 changes; all other items remain.
`prerequisites:` [TASK-1]

### TASK-3 — Update compaction markers to display id (Green)

- `_gather_session_todos`: change format to `f"- [{t['status']}] {t['id']}. {t['content']}"`.
- `build_todo_snapshot`: same format change (lines 157–158).
- Update bare-dict fixtures at `test_context_compaction.py:894,1264` to include `"id"` field.

`files:` `co_cli/context/_compaction_markers.py`, `tests/context/test_context_compaction.py`
`done_when:` `uv run pytest tests/context/test_context_compaction.py -x` passes (all compaction tests green).
`success_signal:` N/A (internal; model sees `- [pending] 2. Write tests` in post-compression injection).
`prerequisites:` [TASK-1]

## Testing

**Red-Green-Refactor:** TASK-1 writes failing tests first. TASK-2 and TASK-3 are the green
phase — each task's `done_when` is the pytest run that confirms its tests pass.

**Test matrix for TASK-1:**

| Test | File | Verifies |
|---|---|---|
| `test_write_assigns_ids` | test_todo.py | Fresh write: items get sequential string ids |
| `test_merge_updates_status` | test_todo.py | merge=True on known id: only status changes |
| `test_merge_preserves_unmentioned` | test_todo.py | Items not in merge payload unchanged |
| `test_merge_appends_unknown_id` | test_todo.py | Unknown id in payload → appended |
| `test_merge_empty_list` | test_todo.py | merge=True on empty session → behaves like fresh write |
| `test_merge_preserves_order` | test_todo.py | Order of unmentioned items unchanged |
| `test_read_returns_id` | test_todo.py | todo_read metadata includes id per item |
| `test_merge_rejects_missing_id` | test_todo.py | merge item with no id returns error, list unchanged |
| `test_compaction_includes_id` | test_context_compaction.py | `_gather_session_todos` output has `"1."` |
| `test_todo_snapshot_includes_id` | test_context_compaction.py | `build_todo_snapshot` content has `"1."` |

## Open Questions

None — all answerable by inspection:
- **Should `priority` be kept?** Yes. Hermes lacks it; we keep it.
- **Should merge items without `id` fail silently?** No — per-item validation error, not ghost-append.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev 2026-04-28-155933-todo-merge-semantics`
