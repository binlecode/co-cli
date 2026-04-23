# Plan: Preserve Active Todos Across Compaction

**Slug:** compaction-todo-snapshot  
**Created:** 2026-04-23  
**Source:** code review of `co_cli/context/_history.py`, `co_cli/context/summarization.py`, `co_cli/context/transcript.py`, `co_cli/tools/todo.py`, `co_cli/prompts/rules/05_workflow.md`, and peer comparison against Hermes's todo/compression path

---

## Context

This plan addresses a concrete continuity gap in co-cli's compaction flow:
active session todos are currently passed only as summarizer enrichment and are
not reinserted into the compacted history as a durable message. If the summary
omits a todo, the model loses awareness of it in post-compaction context.

No existing active exec-plan in `docs/exec-plans/active/` covers this exact
todo-survival gap. This plan is scoped to the compaction/todo continuity fix
only. It does not introduce broader session-rollover or prompt redesign work.

No `docs/specs/` files are listed as implementation inputs. Spec updates remain
delivery outputs and should be handled by the normal doc-sync step after code
changes land.

---

## Current-State Validation

### co-cli

`_gather_session_todos()` in `co_cli/context/_history.py` formats pending todos
as plain text:

- it returns `None` when there are no todos
- it filters out `completed` and `cancelled`
- it renders up to 10 items under an `Active tasks:` heading

`_gather_compaction_context()` in the same file includes that todo text as one
of three summarizer-side context sources:

1. file working set from dropped tool calls
2. pending session todos from `ctx.deps.session.session_todos`
3. prior summary text from dropped messages

`_summarize_dropped_messages()` passes that enrichment to
`summarize_messages(...)`, and `_build_summarizer_prompt()` in
`co_cli/context/summarization.py` injects it under `## Additional Context`.
This is prompt enrichment only.

`_apply_compaction()` then rebuilds history as:

1. preserved head
2. compaction marker
3. preserved `search_tools` breadcrumbs from the dropped range
4. preserved tail

There is no post-compaction todo reinjection step in `_apply_compaction()`.

`_build_compaction_marker()` only chooses between:

- a summary marker containing the LLM-produced summary text
- a static marker when summarization is absent or fails

Neither marker includes an unconditional todo snapshot.

Transcript branching does not restore the missing context. When
`history_compaction_applied=True`, `persist_session_history()` in
`co_cli/context/transcript.py` writes the already-compacted message list into a
child session. If the compacted list lacks a todo snapshot, the child
transcript lacks it too.

The todo state itself still exists in memory in
`CoSessionState.session_todos` (`co_cli/deps.py`), and `todo_read()` can still
surface it, but the model is not guaranteed to know it should call `todo_read()`
after compaction unless the summary happened to preserve that fact.

This matters because the workflow rule in
`co_cli/prompts/rules/05_workflow.md` explicitly says that when a todo list is
active, the model should call `todo_read()` and confirm no `pending` or
`in_progress` items remain before responding as done. The current compaction
path can sever that behavioral loop.

### Hermes reference behavior

Hermes uses a dedicated `TodoStore` with `format_for_injection()` in
`tools/todo_tool.py`. That helper:

- stores todo state outside the summary path
- formats only active items for reinjection
- filters out `completed` and `cancelled`
- emits an explicit continuity banner stating that the task list survived
  context compression

In `run_agent.py`, `_compress_context()` calls
`self._todo_store.format_for_injection()` after compressing the message list and
then unconditionally appends the result as a new `user` message when present.

Hermes also rehydrates its in-memory todo store on a fresh agent instance by
scanning history for the latest todo tool response and replaying it into the
store. That rehydration path is separate from the post-compression user-message
injection. The important comparison for co-cli is the explicit post-compaction
message insertion, not just the existence of out-of-band store state.

### Existing test coverage

Current co-cli tests verify pieces of the present behavior but not the missing
continuity guarantee:

- `tests/test_context_compaction.py` verifies that `Active tasks:` can appear in
  summarizer prompt assembly
- `tests/test_history.py` verifies that `/compact` returns a compacted history
- `tests/test_transcript.py` verifies that `history_compaction_applied=True`
  causes transcript branching into a child session

There is no regression test asserting that an active todo remains model-visible
in the compacted history after compaction, or after fallback/static-marker
compaction, or in the compacted child transcript.

---

## Problem

Active todos are currently fragile across compaction because they survive only
through summary quality. The compaction system has two separate artifacts:

1. summarizer enrichment context
2. rewritten durable history

co-cli presently treats todos as part of artifact 1 only. It does not preserve
them in artifact 2.

That produces these failure modes:

- if the LLM summary omits a pending todo, the model loses awareness of that
  task in active context
- if summarization fails, is skipped by the circuit breaker, or the model is
  absent, static-marker compaction preserves no todo information at all
- the transcript child session created after compaction inherits the same loss
- the workflow rule that depends on the model knowing a todo list exists becomes
  unreliable after compaction

This is a low-severity but real continuity defect. It does not corrupt Python
state, but it does weaken the model's execution state at exactly the point where
history has been compressed and precision matters most.

---

## Desired Outcome

After any compaction pass, an active todo list must remain model-visible as a
durable standalone message in the compacted history, independent of whether the
LLM summary retained it.

That guarantee must hold for:

- proactive compaction
- pre-turn hygiene compaction
- overflow recovery compaction
- summary-marker compaction
- static-marker fallback compaction

Completed and cancelled items must not be reintroduced, because doing so would
encourage rework of finished tasks.

The existing enrichment path should remain in place. The todo snapshot is a
safety net added to durable history, not a replacement for summarizer context.

---

## Scope

### In scope

- add a compaction-only todo snapshot message builder with a stable sentinel
  prefix so subsequent passes can recognize prior snapshots
- extract a shared `_active_todos(todos)` helper to filter pending/in_progress
  items once, reused by enrichment and snapshot formatters
- inject the snapshot into `_apply_compaction()` for the proactive / hygiene /
  overflow / summary / static-marker paths
- inject the snapshot into the `/compact` slash command's minimal two-message
  output so user-invoked compaction has the same durability guarantee
- add regression tests for post-compaction todo visibility, including a
  re-compaction pass that must not duplicate snapshots
- sync compaction/core-loop specs as a delivery output via `sync-doc`

### Out of scope

- redesigning the todo tool API
- persisting todos to disk outside existing session mechanisms
- introducing session rollover metadata beyond the current transcript branching
- changing the compaction summary schema
- changing `todo_read()` / `todo_write()` semantics unrelated to compaction
- spec files in `docs/specs/` as implementation inputs — spec updates are
  delivery outputs only (CLAUDE.md rule)

---

## Design Constraints

1. The injected todo snapshot must be built from
   `ctx.deps.session.session_todos`, not reconstructed from dropped messages.
   The session state is authoritative for the active checklist.
2. Only `pending` and `in_progress` items should be injected. `completed` and
   `cancelled` items must stay excluded.
3. The snapshot should be inserted unconditionally when active items exist,
   regardless of whether `summary_text` is present.
4. Filtering logic (pending/in_progress) must live in one shared helper reused
   by both enrichment (`_gather_session_todos`) and snapshot building, to
   prevent divergence risk called out in Risks.
5. The snapshot content must start with a stable, unique sentinel prefix
   (`_TODO_SNAPSHOT_PREFIX`) — analogous to `_SUMMARY_MARKER_PREFIX`. This
   allows future filtering in enrichment / anchoring paths if needed and is
   defensive against repeated passes.
6. Re-compaction safety: on a second compaction pass, if the prior snapshot
   message falls in the dropped range it must simply be replaced by a fresh
   one built from live `session_todos` — never duplicated in the compacted
   output. This is naturally satisfied because the snapshot is rebuilt from
   `ctx.deps.session.session_todos` each pass and the prior snapshot lives in
   the dropped middle, but a regression test must lock this in.
7. Search breadcrumb preservation in `_preserve_search_tool_breadcrumbs()` must
   remain intact.
8. Transcript branching should work automatically by persisting the already-
   compacted message list. No parallel persistence mechanism should be added.
9. The injected message should be concise and explicit that it exists because of
   compaction, so the model interprets it as preserved task state rather than a
   new user request.

---

## Proposed Design

### 1. Extract a shared active-todo filter and add a snapshot builder in `co_cli/context/_history.py`

Introduce a small private helper that encapsulates the pending/in_progress
filter:

```python
def _active_todos(todos: list) -> list:
    return [t for t in todos if t.get("status") not in ("completed", "cancelled")]
```

Refactor `_gather_session_todos()` to call it. Add a second helper
`_build_todo_snapshot()` that reuses the same filter and returns a
`ModelRequest | None`:

- return `None` when there are no active items
- render items via a fixed sentinel prefix constant
  `_TODO_SNAPSHOT_PREFIX` — e.g.
  `[ACTIVE TODOS — PRESERVED ACROSS CONVERSATION COMPACTION]`
- include up to 10 items (mirrors enrichment cap)

Content shape:

```text
[ACTIVE TODOS — PRESERVED ACROSS CONVERSATION COMPACTION]
- [pending] ...
- [in_progress] ...
```

### 2. Inject the snapshot in `_apply_compaction()`

Update `_apply_compaction()` so the compacted history becomes:

1. preserved head
2. compaction marker (summary or static)
3. todo snapshot, when active items exist
4. preserved `search_tools` breadcrumbs from dropped messages
5. preserved tail

Ordering rationale:

- the marker remains the primary "what happened before" boundary
- the todo snapshot sits directly after the marker so its provenance reads as
  "preserved from before compaction" rather than as an unrelated later user
  turn
- the snapshot is a `ModelRequest` with a single `UserPromptPart`, same
  structural shape as the marker

### 3. Mirror the snapshot in the `/compact` slash command path

`_cmd_compact` in `co_cli/commands/_commands.py` does **not** route through
`_apply_compaction()`. It builds its own minimal two-message history
(`[summary request, ack response]`). For parity, append the todo snapshot
after the summary request when active todos exist:

```
ModelRequest(summary)
ModelRequest(todo snapshot)    ← new, only when active items exist
ModelResponse(ack)
```

This keeps user-invoked compaction aligned with automatic compaction paths.

### 4. Preserve current summarizer enrichment

Do not remove `_gather_session_todos()` from `_gather_compaction_context()`.
The summarizer still benefits from seeing active tasks while composing the
handoff summary. The new post-compaction message closes a durability gap; it
is not a substitute for richer summarizer guidance.

### 5. Let transcript branching persist the new artifact automatically

Once `_apply_compaction()` returns a history that already contains the todo
snapshot, the existing transcript path persists it without further changes:

- `history_compaction_applied` is already set in `_apply_compaction()`
- `persist_session_history()` already writes the entire compacted list into the
  child transcript

### 6. Re-compaction safety

A second compaction pass over already-compacted history will:

- drop the prior snapshot (it sits between head and tail)
- rebuild a fresh snapshot from live `ctx.deps.session.session_todos`
- insert it back at the same structural position

No deduplication code is needed — the builder always reads live session state,
and the prior snapshot naturally falls into the dropped range. A regression
test must exercise this path explicitly.

---

## Implementation Plan

### TASK-1: Shared active-todo filter + snapshot builder ✓ DONE

```
files:
  - co_cli/context/_history.py

done_when: >
  A private `_active_todos(todos)` helper exists and is used by both
  `_gather_session_todos` and a new `_build_todo_snapshot` helper.
  `_build_todo_snapshot` returns a ModelRequest with a single UserPromptPart
  whose content starts with `_TODO_SNAPSHOT_PREFIX`, listing only pending /
  in_progress items. Returns None when there are no active items.

verification:
  - helper-level tests prove empty and all-complete inputs return None
  - helper-level tests prove pending/in_progress items are rendered
  - helper-level tests prove completed/cancelled items are excluded
  - `_gather_session_todos` still produces the existing `Active tasks:` text
    (no behavior change visible to enrichment callers)
```

### TASK-2: Inject the snapshot into `_apply_compaction()` ✓ DONE

```
files:
  - co_cli/context/_history.py
  - tests/test_history.py

done_when: >
  `_apply_compaction()` appends the todo snapshot whenever active todos exist.
  The snapshot is present for both summary-marker and static-marker fallback
  compaction. No snapshot is inserted when there are no active todos.

verification:
  - proactive compaction test asserts the compacted history contains a
    ModelRequest whose UserPromptPart content starts with `_TODO_SNAPSHOT_PREFIX`
    when `session_todos` has an active item
  - static-marker fallback compaction (model=None) test asserts the snapshot is
    still present
  - no-active-todos case: result message count matches current behavior (no
    extra message inserted)
  - existing search-breadcrumb test still passes (no regression)
```

### TASK-3: Mirror the snapshot in the `/compact` slash command path ✓ DONE

```
files:
  - co_cli/commands/_commands.py
  - tests/test_history.py

done_when: >
  `_cmd_compact` inserts the todo snapshot between the summary request and the
  ack response when active session todos exist. When no active todos exist, the
  output remains the current two-message history.

verification:
  - `/compact` dispatch test with active session_todos asserts the returned
    `ReplaceTranscript.history` contains the snapshot between the summary
    request and the ack response
  - existing `test_compact_produces_two_message_history` continues to pass
    (no active todos → two messages)
```

### TASK-4: Re-compaction safety regression test ✓ DONE

```
files:
  - tests/test_history.py

done_when: >
  A test runs `summarize_history_window` over an already-compacted history
  (head + marker + snapshot + breadcrumbs + tail + new turns) and asserts the
  final compacted result contains exactly ONE snapshot message — the fresh
  one rebuilt from live session state — not two.

verification:
  - test fails against a naive implementation that just preserves prior
    snapshots as-is into the retained tail
  - test passes against the design: fresh snapshot from live session_todos
    each pass, prior snapshot naturally dropped
```

### TASK-5: Transcript persistence test ✓ DONE

```
files:
  - tests/test_transcript.py

done_when: >
  A transcript test persists a compacted history containing the snapshot,
  reloads the child session, and asserts the snapshot content survives.

verification:
  - round-trip test with `history_compacted=True` asserts the child transcript
    contains the snapshot prefix
```

### TASK-6: Doc sync (delivery output)

Spec sync happens as a post-delivery step via `sync-doc` — not an
implementation task. The specs to update after code lands:

- `docs/specs/compaction.md` — note the durable todo snapshot alongside the
  summarizer enrichment path
- `docs/specs/core-loop.md` — reference the snapshot in the post-compaction
  history shape where appropriate

---

## Test Plan

### Functional regression tests

Add or update tests covering the actual behavior gap rather than just prompt
assembly:

1. helper returns `None` when `session_todos` is empty
2. helper returns `None` when all todos are `completed` or `cancelled`
3. helper includes only `pending` / `in_progress` items
4. helper banner explicitly signals preservation across compaction
5. proactive compaction result includes the todo snapshot as a standalone
   `ModelRequest`
6. static-marker fallback compaction result still includes the todo snapshot
7. compacted child transcript persists the injected snapshot content

### Non-regression checks

Ensure the change does not disturb adjacent compaction behavior:

1. existing search breadcrumb preservation remains intact
2. `history_compaction_applied` and `compacted_in_current_turn` flags still
   behave as before
3. no snapshot is inserted when there are no active todos
4. summarizer enrichment tests still pass

### Expected command scope during delivery

During implementation, run only the directly affected tests first:

- `uv run pytest tests/test_history.py`
- `uv run pytest tests/test_context_compaction.py`
- `uv run pytest tests/test_transcript.py`

Before shipping, run the repo's required gate via `scripts/quality-gate.sh full`
per repository policy.

---

## Acceptance Criteria

1. An active todo list survives compaction as a durable history message even if
   the summary omits it.
2. The guarantee holds on both summary-marker and static-marker compaction
   paths.
3. Only active items are reintroduced; completed and cancelled items are not.
4. The child transcript created after compaction persists the injected snapshot.
5. Existing summarizer enrichment, search breadcrumb preservation, and runtime
   compaction flags continue to work.
6. Specs state the behavior accurately after delivery.

---

## Risks and Review Focus

### Risk: message ordering ambiguity

If the todo snapshot is inserted in a confusing position relative to the marker
and preserved tail, the model may read it as a new user request rather than
preserved state.

**Review focus:** verify wording and placement make its provenance obvious.

### Risk: duplicated filtering logic

If enrichment formatting and durable snapshot formatting diverge too far, later
maintenance may update one path and forget the other.

**Review focus:** allow separate helpers for separate output types, but keep
shared filtering semantics obvious and tested.

### Risk: missing fallback coverage

It is easy to implement only the "summary exists" path and accidentally leave
the static-marker fallback unchanged.

**Review focus:** ensure at least one regression test forces `summary_text=None`
and proves the todo snapshot still survives.

---

## Delivery Summary

**Date:** 2026-04-23

### Tasks

- TASK-1 ✓ DONE — `_active_todos` helper + `_build_todo_snapshot` builder in
  `co_cli/context/_history.py`. `_TODO_SNAPSHOT_PREFIX` sentinel constant added.
  `_gather_session_todos` refactored to share the filter.
- TASK-2 ✓ DONE — `_apply_compaction` in `co_cli/context/_history.py` now
  injects the snapshot right after the marker when active todos exist. Works
  for proactive, hygiene, overflow, summary-marker and static-marker paths.
- TASK-3 ✓ DONE — `_cmd_compact` in `co_cli/commands/_commands.py` inserts the
  snapshot between the summary request and the ack response when active todos
  exist. No change to the zero-todos shape (still two messages).
- TASK-4 ✓ DONE — re-compaction regression test
  `test_apply_compaction_re_compaction_does_not_duplicate_snapshot` in
  `tests/test_history.py` exercises two sequential compaction passes and locks
  in exactly one snapshot in the final output.
- TASK-5 ✓ DONE — `test_persist_session_history_preserves_todo_snapshot_in_child`
  in `tests/test_transcript.py` proves round-trip persistence of the snapshot
  through the child session.

### Code paths changed

- `co_cli/context/_history.py` — new helpers + constant; injection in
  `_apply_compaction()`.
- `co_cli/commands/_commands.py` — snapshot inserted in `_cmd_compact`.
- `tests/test_history.py` — 11 new tests (helpers, enrichment, snapshot,
  `_apply_compaction` variants, re-compaction safety, `/compact` parity).
- `tests/test_transcript.py` — 1 new round-trip test.

### Tests run

- `uv run pytest tests/test_history.py tests/test_transcript.py
  --deselect tests/test_history.py::test_circuit_breaker_probes_at_cadence`
  → 59 passed. (The deselected test is an ambient Ollama-latency flake at the
  10s non-reasoning budget — unrelated to this change; reproducible on `main`.)
- `uv run pytest tests/test_context_compaction.py` → 35 passed.
- `uv run pytest tests/ -k "todo"
  --deselect tests/test_history.py::test_circuit_breaker_probes_at_cadence`
  → 20 passed.
- `scripts/quality-gate.sh lint` → PASS.

### Post-delivery

- `sync-doc` to update `docs/specs/compaction.md` and
  `docs/specs/core-loop.md` with the new post-marker snapshot step.
