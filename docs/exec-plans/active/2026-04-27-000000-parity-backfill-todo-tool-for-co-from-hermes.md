# Plan: Parity Backfill Todo Tool for Co CLI from Hermes

Task type: `code-feature`

## Context
Hermes has a robust task management system (`TodoStore` + `todo_tool`) that serves two crucial functions:
1. It forces the LLM to structure complex, multi-step work, acting as an external memory and focusing mechanism.
2. It persists through context compression and session boundaries by serializing/hydrating the task list.

Currently, `co-cli` has a basic `todo_write` and `todo_read` implementation in `co_cli/tools/todo.py`. It writes a list of dicts to `ctx.deps.session.session_todos`.
However, the `co-cli` implementation lacks the "update by id" (`merge=True`) functionality that makes the Hermes `todo` tool so effective. In Hermes, the LLM provides an `id` for each task and updates specific tasks by ID, allowing for a single tool call (`todo(todos=[{"id": "db", "status": "completed"}], merge=True)`) rather than rewriting the entire list every time it wants to check off an item.

## Problem & Outcome
**Problem:** The `co-cli` `todo_write` tool requires the model to rewrite the *entire* task list just to update the status of a single item. This is error-prone, consumes more tokens, and frequently results in dropped tasks or context drift during long sessions.
**Failure cost:** The LLM forgets tasks, drops steps from its plan, or wastes generation tokens rewriting 10-item lists just to change one word.

**Outcome:** Bring `co-cli`'s `todo` tool to feature-parity with Hermes by introducing the `merge=True` behavior, requiring an `id` field for tasks, and updating the tool descriptions to match the proven behavioral prompting from Hermes.

## Scope
- Refactor `co_cli/tools/todo.py` to support `id` tracking and `merge` logic.
- Update `CoDeps.session.session_todos` structure if necessary (it is already a `list[dict]`, but now those dicts will enforce `id`).
- Update `co_cli/context/_compaction_markers.py` to ensure the new task shape (with IDs) is serialized properly during compaction.
- Add unit tests for the merge behavior in `tests/tools/test_todo.py`.

## Behavioral Constraints
- Must never overwrite existing task content or priority when `merge=True` if only `status` is passed.
- Must ensure that any task without an ID is assigned a fallback ID to prevent crashes (e.g. `?` or a generated UUID), but the tool schema must strictly require the `id` field so the model owns and predictably assigns its own IDs.
- Order must be preserved during merge updates.

## High-Level Design
1.  **State Shape (`session_todos`)**:
    Change the expected shape of items in `session_todos` from:
    `{"content": str, "status": str, "priority": str}`
    To:
    `{"id": str, "content": str, "status": str, "priority": str}`

2.  **Tool Signature (`todo_write`)**:
    Add `merge: bool = False` to `todo_write`.
    - If `merge=False` (Replace mode): validate the input, require `id`s for every item (assign "?" if missing but fail validation if completely missing from prompt), and replace `session_todos`.
    - If `merge=True` (Update mode): Iterate over provided items. If `id` exists in current `session_todos`, update `content`, `status`, and/or `priority` if provided. If `id` does not exist, append as a new task.

3.  **Prompt Engineering (Tool Description)**:
    Adopt the behavioral guidance from Hermes's `TODO_SCHEMA` directly into the docstring of `todo_write`. Specifically:
    - *"List order is priority. Only ONE item in_progress at a time."*
    - *"Mark items completed immediately when done. If something fails, cancel it and add a revised item."*

4.  **Compaction Snapshot**:
    Update `_gather_session_todos` and `build_todo_snapshot` in `co_cli/context/_compaction_markers.py` to display the ID (e.g., `- [ ] id1. Task content`).

## Implementation Plan

- **TASK-1**: Implement `merge` logic and `id` field in `co_cli/tools/todo.py`.
  - `files:` `co_cli/tools/todo.py`
  - `done_when:` `python -c "from co_cli.tools.todo import todo_write; print('merge' in todo_write.__code__.co_varnames)"` prints True
  - `success_signal:` Model can update a task status without rewriting the entire plan.
  - `prerequisites:` []

- **TASK-2**: Update compaction formatting to include task IDs.
  - `files:` `co_cli/context/_compaction_markers.py`
  - `done_when:` `python -c "from co_cli.context._compaction_markers import _gather_session_todos; assert 'id1.' in _gather_session_todos([{'id': 'id1', 'content': 'test', 'status': 'pending'}])"` passes without error.
  - `success_signal:` N/A (Internal formatting)
  - `prerequisites:` [TASK-1]

- **TASK-3**: Write unit tests for replace and merge modes.
  - `files:` `tests/tools/test_todo.py`
  - `done_when:` `uv run pytest tests/tools/test_todo.py` passes successfully.
  - `success_signal:` N/A
  - `prerequisites:` [TASK-1]

## Testing
- **Red-Green-Refactor**: `tests/tools/test_todo.py` will test the validation logic (requiring `id`), the replace mode, and the merge mode.
- **Integration Check**: `tests/tools/test_todo.py` will contain an automated integration test that creates a plan with `merge=False` and then updates a single item with `merge=True`.

## Open Questions
- **Question**: Should `priority` be retained from the current `co-cli` implementation?
  - **Answer**: Yes. Hermes does not have `priority`, but since `co-cli` already supports it, we should keep it to avoid breaking existing agent expectations. We will just add `id` and `merge` on top.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev 2026-04-27-000000-parity-backfill-todo-tool-for-co-from-hermes`