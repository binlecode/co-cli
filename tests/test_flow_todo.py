"""Behavioral tests for todo_write and todo_read.

Exercises: fresh-write validation, merge-mode semantics, all-or-nothing
rejection, metadata contract, and the id-charset rule.
No LLM — pure function over real CoDeps + CoSessionState.
"""

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.todo.rw import todo_read, todo_write


def _make_deps(tmp_path: Path) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        knowledge_dir=tmp_path / "knowledge",
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


# ---------------------------------------------------------------------------
# 1. Fresh write — happy path
# ---------------------------------------------------------------------------


def test_fresh_write_accepts_well_formed_items_and_replaces_state(tmp_path: Path) -> None:
    """todo_write with valid items replaces session_todos in full.

    Regression guard: if validation silently drops items or fails to assign state,
    todo_read returns stale or empty data and the model loses track of its plan.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = todo_write(
        ctx,
        [
            {"id": "1", "content": "Step one", "status": "pending", "priority": "high"},
            {"id": "2", "content": "Step two", "status": "in_progress"},
        ],
    )

    assert result.metadata is not None
    assert result.metadata.get("count") == 2
    assert len(deps.session.session_todos) == 2
    assert deps.session.session_todos[0]["id"] == "1"
    assert deps.session.session_todos[1]["id"] == "2"


# ---------------------------------------------------------------------------
# 2. Fresh write — missing id
# ---------------------------------------------------------------------------


def test_fresh_write_rejects_missing_id_leaves_state_unchanged(tmp_path: Path) -> None:
    """todo_write rejects any item without an id and leaves session_todos unchanged.

    Regression guard: if missing id is silently accepted, the compaction snapshot
    and resume rehydration both break (id is the merge key).
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    # plain dict literal simulates pre-existing state; structurally matches TodoItem
    deps.session.session_todos = [
        {"id": "existing", "content": "keep me", "status": "pending", "priority": "medium"}
    ]  # type: ignore[list-item]

    result = todo_write(ctx, [{"content": "no id item", "status": "pending"}])

    assert result.metadata is not None
    assert result.metadata.get("errors")
    assert len(deps.session.session_todos) == 1
    assert deps.session.session_todos[0]["id"] == "existing"


# ---------------------------------------------------------------------------
# 3. Fresh write — duplicate id in payload
# ---------------------------------------------------------------------------


def test_fresh_write_rejects_duplicate_id_in_payload(tmp_path: Path) -> None:
    """todo_write rejects a payload with two items sharing the same id.

    Regression guard: duplicate ids make merge mode non-deterministic and
    corrupt the id→item mapping used by resume rehydration.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = todo_write(
        ctx,
        [
            {"id": "dup", "content": "first", "status": "pending"},
            {"id": "dup", "content": "second", "status": "pending"},
        ],
    )

    assert result.metadata is not None
    assert result.metadata.get("errors")
    assert deps.session.session_todos == []


# ---------------------------------------------------------------------------
# 4. Fresh write — invalid status / priority
# ---------------------------------------------------------------------------


def test_fresh_write_rejects_invalid_status(tmp_path: Path) -> None:
    """todo_write rejects items with an invalid status or priority value, leaving state unchanged."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = todo_write(ctx, [{"id": "x", "content": "task", "status": "DONE"}])

    assert result.metadata is not None
    assert result.metadata.get("errors")
    assert deps.session.session_todos == []


# ---------------------------------------------------------------------------
# 5. Fresh write — empty content
# ---------------------------------------------------------------------------


def test_fresh_write_rejects_empty_content(tmp_path: Path) -> None:
    """todo_write rejects items whose content is empty or whitespace-only."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = todo_write(ctx, [{"id": "x", "content": "   ", "status": "pending"}])

    assert result.metadata is not None
    assert result.metadata.get("errors")
    assert deps.session.session_todos == []


# ---------------------------------------------------------------------------
# 6. Merge — single field update, no other fields touched
# ---------------------------------------------------------------------------


def test_merge_updates_single_field_without_touching_others(tmp_path: Path) -> None:
    """merge=True with only status in the payload updates status; content and priority unchanged.

    Regression guard: if merge overwrites non-present fields with defaults, the
    model's carefully-set priorities and descriptions are silently discarded.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    todo_write(
        ctx,
        [{"id": "a", "content": "Important task", "status": "pending", "priority": "high"}],
    )
    original_content = deps.session.session_todos[0]["content"]
    original_priority = deps.session.session_todos[0]["priority"]

    todo_write(ctx, [{"id": "a", "status": "completed"}], merge=True)

    assert deps.session.session_todos[0]["status"] == "completed"
    assert deps.session.session_todos[0]["content"] == original_content
    assert deps.session.session_todos[0]["priority"] == original_priority


# ---------------------------------------------------------------------------
# 7. Merge — preserves unmentioned items in original order
# ---------------------------------------------------------------------------


def test_merge_preserves_unmentioned_items_in_order(tmp_path: Path) -> None:
    """merge=True only modifies items in the payload; all others are preserved in original order.

    Regression guard: if merge rebuilds the list without preserving unmentioned
    items, the model loses visibility into other in-flight tasks.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    todo_write(
        ctx,
        [
            {"id": "1", "content": "Alpha", "status": "pending"},
            {"id": "2", "content": "Beta", "status": "pending"},
            {"id": "3", "content": "Gamma", "status": "pending"},
        ],
    )

    todo_write(ctx, [{"id": "2", "status": "completed"}], merge=True)

    todos = deps.session.session_todos
    assert len(todos) == 3
    assert todos[0]["id"] == "1"
    assert todos[0]["status"] == "pending"
    assert todos[1]["id"] == "2"
    assert todos[1]["status"] == "completed"
    assert todos[2]["id"] == "3"
    assert todos[2]["status"] == "pending"


# ---------------------------------------------------------------------------
# 8. Merge — unknown id appended as new item (after existing)
# ---------------------------------------------------------------------------


def test_merge_appends_unknown_id_as_new_item(tmp_path: Path) -> None:
    """merge=True with an unknown id treats it as a new item appended after existing ones.

    Regression guard: if unknown ids are silently dropped or rejected, the model
    cannot add new tasks mid-plan via merge — forcing a costly full rewrite.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    todo_write(ctx, [{"id": "a", "content": "First task", "status": "pending"}])

    todo_write(ctx, [{"id": "b", "content": "New task", "status": "pending"}], merge=True)

    todos = deps.session.session_todos
    assert len(todos) == 2
    assert todos[0]["id"] == "a"
    assert todos[1]["id"] == "b"
    assert todos[1]["content"] == "New task"


# ---------------------------------------------------------------------------
# 9. Merge — missing id → all-or-nothing reject
# ---------------------------------------------------------------------------


def test_merge_rejects_missing_id_leaves_state_unchanged(tmp_path: Path) -> None:
    """merge=True with a missing id rejects the whole payload; state unchanged."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    todo_write(ctx, [{"id": "x", "content": "Keep me", "status": "pending"}])
    original = list(deps.session.session_todos)

    result = todo_write(ctx, [{"content": "no id", "status": "completed"}], merge=True)

    assert result.metadata is not None
    assert result.metadata.get("errors")
    assert deps.session.session_todos == original


# ---------------------------------------------------------------------------
# 10. Merge — invalid field on existing id → all-or-nothing reject
# ---------------------------------------------------------------------------


def test_merge_rejects_invalid_field_on_existing_id_leaves_state_unchanged(tmp_path: Path) -> None:
    """merge=True with an invalid status on a known id rejects the payload; state unchanged."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    todo_write(ctx, [{"id": "a", "content": "Task", "status": "pending"}])
    original = list(deps.session.session_todos)

    result = todo_write(ctx, [{"id": "a", "status": "INVALID_STATUS"}], merge=True)

    assert result.metadata is not None
    assert result.metadata.get("errors")
    assert deps.session.session_todos == original


# ---------------------------------------------------------------------------
# 11. todo_read returns id in each item dict
# ---------------------------------------------------------------------------


def test_todo_read_returns_id_in_each_item_dict(tmp_path: Path) -> None:
    """todo_read's todos metadata includes id in every item dict.

    Regression guard: if id is absent from todo_read output, the model cannot
    reference items by id for merge updates.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    todo_write(
        ctx,
        [
            {"id": "task-1", "content": "Alpha", "status": "pending"},
            {"id": "task-2", "content": "Beta", "status": "in_progress"},
        ],
    )

    result = todo_read(ctx)

    assert result.metadata is not None
    todos = result.metadata.get("todos", [])
    assert len(todos) == 2
    for item in todos:
        assert "id" in item
    assert todos[0]["id"] == "task-1"
    assert todos[1]["id"] == "task-2"


# ---------------------------------------------------------------------------
# 12. todo_write metadata todos — full post-state with all keys
# ---------------------------------------------------------------------------


def test_todo_write_metadata_todos_has_all_keys_on_success(tmp_path: Path) -> None:
    """todo_write success response carries metadata.todos with id/content/status/priority.

    Regression guard: if metadata.todos is absent or missing keys, resume
    rehydration cannot reconstruct the session state from the ToolReturnPart.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = todo_write(
        ctx,
        [
            {"id": "r1", "content": "Research", "status": "pending", "priority": "high"},
            {"id": "r2", "content": "Write", "status": "pending"},
        ],
    )

    assert result.metadata is not None
    todos = result.metadata.get("todos")
    assert isinstance(todos, list)
    assert len(todos) == 2
    required_keys = {"id", "content", "status", "priority"}
    for item in todos:
        assert required_keys.issubset(item.keys()), f"Missing keys: {required_keys - item.keys()}"


# ---------------------------------------------------------------------------
# 18. Fresh write — id charset validation (period or whitespace)
# ---------------------------------------------------------------------------


def test_fresh_write_rejects_id_with_period(tmp_path: Path) -> None:
    """todo_write rejects ids containing a period — the snapshot parser uses '. ' as separator."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = todo_write(ctx, [{"id": "task.1", "content": "Bad id", "status": "pending"}])

    assert result.metadata is not None
    assert result.metadata.get("errors")
    assert deps.session.session_todos == []


# ---------------------------------------------------------------------------
# One-in-progress invariant (aggregate constraint)
# ---------------------------------------------------------------------------


def test_fresh_write_two_in_progress_rejects_and_names_both_ids(tmp_path: Path) -> None:
    """Fresh write with 2 in_progress items is rejected all-or-nothing; error names both ids.

    Regression guard: if the aggregate check is missing, the model can claim parallel
    work in progress, corrupting the TUI status and compaction snapshot.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    deps.session.session_todos = [  # type: ignore[list-item]
        {"id": "pre", "content": "pre-existing", "status": "pending", "priority": "medium"}
    ]

    result = todo_write(
        ctx,
        [
            {"id": "x", "content": "Task X", "status": "in_progress"},
            {"id": "y", "content": "Task Y", "status": "in_progress"},
        ],
    )

    assert result.metadata is not None
    errors = result.metadata.get("errors") or []
    assert errors, "expected rejection"
    error_text = " ".join(errors)
    assert "x" in error_text
    assert "y" in error_text
    # state unchanged — still the pre-existing item
    assert len(deps.session.session_todos) == 1
    assert deps.session.session_todos[0]["id"] == "pre"


def test_merge_existing_one_in_progress_unrelated_update_accepts(tmp_path: Path) -> None:
    """Merge that doesn't touch status of the in_progress item → accepted (count stays 1)."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    todo_write(
        ctx,
        [
            {"id": "a", "content": "Active task", "status": "in_progress"},
            {"id": "b", "content": "Other task", "status": "pending"},
        ],
    )

    result = todo_write(ctx, [{"id": "b", "priority": "high"}], merge=True)

    assert result.metadata is not None
    assert not result.metadata.get("errors")
    assert deps.session.session_todos[0]["status"] == "in_progress"
    assert deps.session.session_todos[1]["priority"] == "high"


def test_merge_adds_second_in_progress_rejects(tmp_path: Path) -> None:
    """Merge: existing has A=in_progress; payload sets B=in_progress → rejected; state unchanged.

    Regression guard: without the final-state aggregate check in merge mode, the
    model can silently accumulate multiple in_progress items across calls.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    todo_write(
        ctx,
        [
            {"id": "a", "content": "Active", "status": "in_progress"},
            {"id": "b", "content": "Next", "status": "pending"},
        ],
    )
    original = list(deps.session.session_todos)

    result = todo_write(ctx, [{"id": "b", "status": "in_progress"}], merge=True)

    assert result.metadata is not None
    assert result.metadata.get("errors")
    assert deps.session.session_todos == original


def test_merge_swaps_in_progress_in_same_call_accepts(tmp_path: Path) -> None:
    """Merge: A=completed AND B=in_progress in one call when A was in_progress → accepted (final count=1).

    Regression guard: if the check runs on the payload rather than the final merged
    state, this valid swap would be rejected because both statuses appear in the diff.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    todo_write(
        ctx,
        [
            {"id": "a", "content": "Done now", "status": "in_progress"},
            {"id": "b", "content": "Up next", "status": "pending"},
        ],
    )

    result = todo_write(
        ctx,
        [{"id": "a", "status": "completed"}, {"id": "b", "status": "in_progress"}],
        merge=True,
    )

    assert result.metadata is not None
    assert not result.metadata.get("errors")
    todos = deps.session.session_todos
    assert todos[0]["status"] == "completed"
    assert todos[1]["status"] == "in_progress"


def test_merge_legacy_two_in_progress_rejects(tmp_path: Path) -> None:
    """Merge on legacy state with 2 in_progress items (accepted under old contract) → rejected.

    The model must clean up legacy state before any further mutation will succeed.
    This is the expected recovery path: rejection message names the offending ids.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    # simulate pre-enforcement state accepted under the old contract
    deps.session.session_todos = [  # type: ignore[list-item]
        {"id": "a", "content": "Task A", "status": "in_progress", "priority": "medium"},
        {"id": "b", "content": "Task B", "status": "in_progress", "priority": "medium"},
    ]

    result = todo_write(ctx, [{"id": "a", "priority": "high"}], merge=True)

    assert result.metadata is not None
    errors = result.metadata.get("errors") or []
    assert errors, "expected rejection on legacy 2-in-progress state"
    error_text = " ".join(errors)
    assert "a" in error_text
    assert "b" in error_text
    # state preserved
    assert len(deps.session.session_todos) == 2
    assert all(t["status"] == "in_progress" for t in deps.session.session_todos)


def test_per_item_error_short_circuits_aggregate_check(tmp_path: Path) -> None:
    """Per-item validation failure suppresses the aggregate check entirely.

    Regression guard: if the aggregate check ran before the per-item guard,
    a payload with both an invalid field and multiple in_progress items would
    return two error categories instead of one, confusing the model about which
    to fix first. The spec-stated ordering (per-item → aggregate) must hold.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = todo_write(
        ctx,
        [
            {"id": "a", "content": "Task A", "status": "in_progress"},
            {"id": "b", "content": "Task B", "status": "in_progress"},
            {"id": "c", "content": "Task C", "status": "running"},  # invalid status
        ],
    )

    assert result.metadata is not None
    errors = result.metadata.get("errors") or []
    assert errors, "expected per-item error"
    # only per-item error — no aggregate error about multiple in_progress
    assert not any("Multiple items marked" in e for e in errors)
    assert any("running" in e for e in errors)
    # state unchanged
    assert deps.session.session_todos == []


def test_aggregate_rejection_is_all_or_nothing(tmp_path: Path) -> None:
    """Aggregate rejection on fresh write: errors reported; session_todos unchanged in full.

    Verifies no partial application — the entire write is discarded, not just the
    offending items.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    deps.session.session_todos = [  # type: ignore[list-item]
        {"id": "keep", "content": "Keep me", "status": "pending", "priority": "medium"}
    ]
    before = list(deps.session.session_todos)

    result = todo_write(
        ctx,
        [
            {"id": "p", "content": "Passes validation", "status": "in_progress"},
            {"id": "q", "content": "Also in_progress", "status": "in_progress"},
            {"id": "r", "content": "Pending one", "status": "pending"},
        ],
    )

    assert result.metadata is not None
    assert result.metadata.get("errors"), "expected aggregate error"
    # none of the payload items were applied
    assert deps.session.session_todos == before
