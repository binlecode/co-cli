# Plan: Enforce one-in-progress invariant in todo_write

Task type: `code-feature`

## Context

The `todo-continuity` plan (shipped 2026-05-14) established `todo_write`'s validation contract and locked the planning-surface framing. The `todo_write` docstring states the one-in-progress rule:

> "Create the list before starting work, update status as each sub-goal completes, and **keep at most ONE item 'in_progress' at a time.**"
> — `co_cli/tools/todo/rw.py:189`

The rule is **not enforced** in code. Neither `_run_fresh` (`co_cli/tools/todo/rw.py:106-123`) nor `_run_merge` (`co_cli/tools/todo/rw.py:126-176`) counts `in_progress` items in the final state. A `todo_write` payload that produces 2+ `in_progress` items is silently accepted.

This gap was surfaced during the `todo-planning-spec` grill (2026-05-14, decision D7) when locking which behavioral invariants the new `docs/specs/self-planning.md` spec would formalize as rules. Per the spec-scope rule (separate plan for any functional change), enforcement is split into this plan.

**Supersedes:** none.

**Current-state validation (2026-05-14):**

- Docstring rule — `co_cli/tools/todo/rw.py:189`, planning discipline phrasing inside the `todo_write` docstring. Not enforced anywhere in the file.
- Fresh validation path — `_validate_fresh` (rw.py:29-67) and `_run_fresh` (rw.py:106-123) check per-item fields; no aggregate check across items.
- Merge validation path — `_validate_merge_update` (rw.py:70-103) and `_run_merge` (rw.py:126-176) check per-item fields and id semantics; no aggregate check across the *final merged state*.
- Test coverage — `tests/test_flow_todo.py` covers single-item status validation (row 4 of the todo-continuity testing table), but no test asserts the one-in-progress aggregate rule.
- Existing test file pattern — flat `tests/test_flow_<scope>.py`, no mocks, real `CoDeps` + real `CoSessionState`.

No source/spec inaccuracies block this delivery. `/sync-doc` not required pre-delivery.

## Problem & Outcome

**Problem:**
- Docstring sets a planning-discipline rule ("at most ONE in_progress at a time") that the validation pipeline does not enforce.
- The model can write a list with multiple `in_progress` items (fresh) or merge-update a 2nd item to `in_progress` while the existing one is still `in_progress` (merge), and the write succeeds.

**Failure cost:**
- Planning discipline degrades: the agent can claim parallel work in progress when the implicit contract is one-at-a-time, undermining the "what's the agent doing right now" signal that downstream surfaces (TUI status, compaction snapshot) rely on.
- Spec/code divergence: `docs/specs/self-planning.md` (in flight via `todo-planning-spec`) will codify the one-in-progress rule as a hard rule (D7-a). Without enforcement, the spec contradicts shipped behavior.

**Outcome:**
- After this delivery, any `todo_write` call (fresh or merge) whose **final state** contains >1 `in_progress` item is rejected all-or-nothing with a single clear error message. `session.session_todos` is preserved unchanged.
- A merge that swaps `in_progress` from one item to another in the same call (e.g., set A=completed AND B=in_progress when A was previously in_progress) succeeds because the final state has exactly one `in_progress`.

## Scope

**In:**
- Add an aggregate check in `_run_fresh` and `_run_merge` that counts `in_progress` items in the final validated/merged list. If count > 1, return errors and apply nothing.
- Error message format consistent with existing per-item errors: a single line listing the offending ids in payload order.
- Tests covering the new behavior per `## Testing`.

**Out:**
- Enforcing a *minimum* of one in_progress (i.e., "always have one in flight") — not part of the docstring rule.
- Auto-demoting an existing in_progress when a new one is set (silent fix-up) — rejected; the model must be explicit.
- Auto-promoting the first pending to in_progress on fresh write — not part of this plan.
- Status transition order enforcement (D7-d in the spec grill is a convention, not a rule).
- Backfill / cleanup of pre-delivery sessions whose `session_todos` already contains 2+ in_progress — the next `todo_write` will reject; until then state stays as-is. Acceptable: existing state was already accepted under the old contract.
- Spec updates — `docs/specs/self-planning.md` (created by `todo-planning-spec`) already documents the rule. Post-delivery `/sync-doc` only if the docstring needs rewording.

## Behavioral Constraints

**Rule (final-state, not payload):**
- After per-item validation passes, count items with `status == "in_progress"` in the **final list that would replace `session.session_todos`**.
- If that count is > 1, return a single error and apply nothing. `session.session_todos` is preserved unchanged.
- If count is 0 or 1, accept.

**Why final-state, not payload:**
- Fresh mode: payload IS the final state, so they're the same.
- Merge mode: the offending in_progress might be an existing item the payload didn't touch, OR a payload update, OR an interaction between the two. Only the merged state is the truth.

**Error message:**
- Single line, listing offending ids in their order in the final list. Example: `"Multiple items marked 'in_progress' — only ONE allowed at a time. Offending ids: 2, 5. Resolve by setting all but one to 'pending', 'completed', or 'cancelled'."`
- Categorize the failure under the existing `"Todo list NOT updated — validation errors:\n"` rejection block so the rejection path is unified with per-item validation failures.

**All-or-nothing preserved:**
- The aggregate check runs AFTER per-item validation. If any per-item error exists, the aggregate check is skipped (already rejected).
- If per-item validation passes but the aggregate check fails, the merged list is discarded and `session.session_todos` is preserved.

**Fresh mode interaction:**
- Empty list: 0 in_progress → accept (no change to one-in-progress semantics).
- Single item with `status="in_progress"`: accept.
- Multiple items with `status="in_progress"`: reject.

**Merge mode interaction (most subtle):**
- Existing has item A=in_progress. Payload sets B=in_progress (where B is a new id or an existing pending). Merged final has A=in_progress AND B=in_progress → reject.
- Existing has item A=in_progress. Payload sets A=completed AND B=in_progress in the same call. Merged final has A=completed AND B=in_progress → accept (final count = 1).
- Existing has item A=in_progress. Payload only touches priority/content of unrelated items. Merged final has A=in_progress unchanged → accept (final count = 1).
- Existing has item A=in_progress AND item B=in_progress (pre-delivery state; was accepted under old contract). Payload doesn't touch either. Merged final has both → **reject**. This is the only "ugly" case: the model has to clean up legacy state before the next mutation will succeed. Acceptable: the rejection is informative and the cleanup path (set one to pending/completed) is obvious.

## High-Level Design

**Single helper, reused by both paths:**

```python
# co_cli/tools/todo/rw.py

def _check_one_in_progress(items: list[TodoItem]) -> list[str]:
    """Return error list if more than one item is in_progress; empty list otherwise."""
    in_progress_ids = [t["id"] for t in items if t.get("status") == "in_progress"]
    if len(in_progress_ids) > 1:
        return [
            f"Multiple items marked 'in_progress' — only ONE allowed at a time. "
            f"Offending ids: {', '.join(in_progress_ids)}. "
            f"Resolve by setting all but one to 'pending', 'completed', or 'cancelled'."
        ]
    return []
```

**Wired into `_run_fresh`:**

```python
def _run_fresh(todos: list[dict[str, Any]]) -> tuple[list[TodoItem] | None, list[str]]:
    errors: list[str] = []
    # ... existing per-item validation ...

    if errors:
        return None, errors

    aggregate_errors = _check_one_in_progress(validated)
    if aggregate_errors:
        return None, aggregate_errors

    return validated, []
```

**Wired into `_run_merge`:**

```python
def _run_merge(
    todos: list[dict[str, Any]], existing: list[TodoItem]
) -> tuple[list[TodoItem] | None, list[str]]:
    # ... existing per-item validation + merge construction ...

    if errors:
        return None, errors

    # ... merged: list[TodoItem] = ... ...

    aggregate_errors = _check_one_in_progress(merged)
    if aggregate_errors:
        return None, aggregate_errors

    return merged, []
```

**Why a separate function:**
- DRY across fresh and merge paths.
- Single test target for the rule logic; integration tests cover the wiring.
- Future aggregate rules (if any are spec'd later) follow the same pattern.

**Docstring tweak:**
The current docstring already states the rule. After this delivery, it's promoted from advisory to enforced. Update wording one line:

> Before: "and keep at most ONE item 'in_progress' at a time."
> After: "and only ONE item may be 'in_progress' at a time — writes that produce more than one are rejected."

## Tasks

### ✓ DONE TASK-1 — Add aggregate check helper and wire into both paths

Add `_check_one_in_progress(items)` helper in `co_cli/tools/todo/rw.py`. Wire into `_run_fresh` and `_run_merge` after per-item validation passes, before returning the validated/merged list.

Update the `todo_write` docstring (rw.py:189 area) to reflect enforcement.

`files:` `co_cli/tools/todo/rw.py`
`done_when:` `_check_one_in_progress` is called at the end of both `_run_fresh` and `_run_merge`; docstring wording updated.
`success_signal:` Manual check: model calls `todo_write([{id: "1", content: "A", status: "in_progress"}, {id: "2", content: "B", status: "in_progress"}])`; tool returns a rejection message naming both ids; `todo_read` shows the pre-call state unchanged.
`prerequisites:` []

### ✓ DONE TASK-2 — Tests for the one-in-progress rule

Add tests in `tests/test_flow_todo.py` covering each row of `## Testing`. Tests must use real `CoDeps` + real `CoSessionState` (no mocks) per `agent_docs/testing.md`.

`files:` `tests/test_flow_todo.py`
`done_when:` `uv run pytest tests/test_flow_todo.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-one-in-progress.log` exits 0.
`success_signal:` All 8 new test cases pass; existing tests in `test_flow_todo.py` continue to pass.
`prerequisites:` [TASK-1]

## Testing

**Critical behavioral contracts** (no count target — add tests if a contract is uncovered; remove only if subsumed):

| # | Behavior | File |
|---|---|---|
| 1 | Fresh write with 0 in_progress → accept | `tests/test_flow_todo.py` |
| 2 | Fresh write with 1 in_progress → accept | `tests/test_flow_todo.py` |
| 3 | Fresh write with 2 in_progress → reject all-or-nothing; state unchanged; error names both offending ids | `tests/test_flow_todo.py` |
| 4 | Merge: existing has 1 in_progress, payload doesn't touch status → accept (count stays 1) | `tests/test_flow_todo.py` |
| 5 | Merge: existing has 1 in_progress (id=A), payload sets B=in_progress → reject; final would have 2; state unchanged | `tests/test_flow_todo.py` |
| 6 | Merge: existing has 1 in_progress (id=A), payload sets A=completed AND B=in_progress in same call → accept (final count = 1) | `tests/test_flow_todo.py` |
| 7 | Merge: existing has 2 in_progress (legacy state), payload doesn't touch either → reject; final count = 2; state unchanged | `tests/test_flow_todo.py` |
| 8 | Aggregate rejection preserves all-or-nothing: error message starts with the standard rejection prefix; no partial application; `session.session_todos` is the same object/contents as before the call | `tests/test_flow_todo.py` |

Tests must not use mocks — construct real `CoDeps` with a real `CoSessionState` per repo policy (`agent_docs/testing.md`).

## Open Questions

None. All resolved during the `todo-planning-spec` grill on 2026-05-14 (D7) and inline during this plan's drafting.

## Notes

This plan is the **enforcement follow-up** to `docs/exec-plans/active/2026-05-14-091450-todo-planning-spec.md` (D7-a). Land in either order; the spec plan documents the rule, this plan enforces it. If both ship close in time, no `/sync-doc` is needed because the spec plan already states the rule as a hard rule (not a convention).

## Implementation Review — 2026-05-14

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `_check_one_in_progress` called at end of both `_run_fresh` and `_run_merge`; docstring updated | ✓ pass | `rw.py:106` — helper defined with `t["status"]` direct access; `rw.py:136` — wired into `_run_fresh` after per-item guard; `rw.py:192` — wired into `_run_merge` after merge construction; `rw.py:209` — docstring states enforcement |
| TASK-2 | `pytest tests/test_flow_todo.py -x` exits 0 | ✓ pass | 23 tests pass (8 new + 15 prior); all 8 behavioral contracts covered; no mocks; real `CoDeps` + `CoSessionState` throughout |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `_run_merge` list construction used for+if loop; adding aggregate-check branch pushed McCabe complexity from 12 to 13 (ruff C901 limit) | `rw.py:168–175` (pre-fix) | blocking | Converted to list comprehension — complexity drops to 12, lint passes |

### Tests
- Command: `uv run pytest -v`
- Result: 479 passed, 0 failed
- Log: `.pytest-logs/*-review-impl.log`

### Behavioral Verification
No user-facing CLI surface changed (no new commands, no output format changes). The enforcement is a tool-level validation rule. Skipped with justification.

### Overall: PASS
One lint-blocking complexity violation found and auto-fixed (for+if → comprehension in `_run_merge`); all spec requirements confirmed with file:line evidence; full suite green at 479/479.
