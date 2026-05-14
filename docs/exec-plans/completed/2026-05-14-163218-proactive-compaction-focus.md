# Plan: Proactive compaction focus inference

Task type: `code-feature`

## Context

`proactive_window_processor` (`co_cli/context/compaction.py:556`) calls `compact_messages(ctx, messages, bounds, focus=None)`. The summarizer accepts a `focus` parameter — when set, its `FOCUS TOPIC` block (`co_cli/context/summarization.py:244-250`) reserves ~60-70% of the summary for focus-related content. The proactive path never provides one, so the summarizer weights every turn equally and drops on-task signal arbitrarily. The `/compact` command path already accepts user-supplied focus; auto-compaction is the only path that doesn't.

`compact_messages` already plumbs `focus` all the way down to `summarize_messages` — no signature change needed in those functions.

`CoDeps.session.session_todos` is a `list[TodoItem]` where `TodoItem` has `content: str` and `status: Literal["pending", "in_progress", "completed", "cancelled"]`. Messages contain `UserPromptPart` (pydantic_ai) with `content: str`.

Existing compaction fallbacks (circuit breaker, empty rejection, static marker) are unaffected — this only changes what string is passed to the summarizer when it runs.

## Problem & Outcome

**Problem:** Every proactive compaction fires with `focus=None`, so the summarizer operates without knowing what the user is currently working on. High-signal turns related to the active task are compressed equally with low-signal turns.

**Failure cost:** Without focus inference, the post-compaction summary loses active-task context at exactly the moment the user needs it — mid-session, during the longest runs where compaction matters most.

**Outcome:** After delivery, every proactive compaction call passes a non-empty `focus` string when one can be cheaply derived. The summarizer's existing `FOCUS TOPIC` block preserves ~60-70% of the summary for focus-related content, so on-task signal survives compaction. When no focus is derivable, `focus=None` falls through to today's behavior.

## Scope

**In:**
- `_resolve_proactive_focus(ctx, messages) -> str | None` — pure focus resolver, lives in `co_cli/context/compaction.py` near its only caller.
- Wire `_resolve_proactive_focus` into `proactive_window_processor` at the `compact_messages` call site.
- Unit tests covering the three resolution branches (in-progress todo / user-message / none).

**Out:**
- Changing the summarizer template — it's already richer than peer projects.
- Adding a preemptive router (fits/truncate/compact).
- Pre-compaction snapshot / session rotation.
- Touching the `/compact` user-driven path — it already accepts focus.
- LLM-based focus inference.
- Exporting `_resolve_proactive_focus` from `__all__` — it's an implementation detail.

## Behavioral Constraints

1. When `session_todos` contains exactly one item with `status == "in_progress"`, `_resolve_proactive_focus` must return that item's `content`, head-capped at 200 characters (`[:200]`).
2. When no in-progress todo exists and messages contain at least one `UserPromptPart`, `_resolve_proactive_focus` must return the most recent `UserPromptPart.content` (scanning newest-to-oldest), tail-capped at 200 characters.
3. When neither condition applies, `_resolve_proactive_focus` must return `None`.
4. The function must make no LLM call, no I/O, and no mutations.
5. A `None` return must produce identical proactive compaction behavior to today (the `focus=None` fallthrough is already tested end-to-end).

## High-Level Design

**Focus resolution (hybrid, in priority order):**

1. If `ctx.deps.session.session_todos` contains an item with `status == "in_progress"`, return its `content` tail-capped at 200 chars.
2. Else, scan `messages` from newest backward; return the first `UserPromptPart.content` tail-capped at 200 chars.
3. Else, return `None`.

Pure function, no LLM call. Lives in `co_cli/context/compaction.py` near `proactive_window_processor` (its only caller). The 200-char cap prevents oversized focus strings from skewing the summarizer prompt.

**Wiring:**

In `proactive_window_processor` before the `compact_messages` call (`compaction.py:556`):

```python
focus = _resolve_proactive_focus(ctx, messages)
result, summary_text = await compact_messages(ctx, messages, bounds, focus=focus)
```

`compact_messages` already accepts `focus` and passes it down — no further changes.

**Import:** `UserPromptPart` must be added to the `pydantic_ai.messages` import in `compaction.py` (currently absent from that import block).

## Tasks

### ✓ DONE TASK-1 — Add `_resolve_proactive_focus` to `compaction.py`

**files:** `co_cli/context/compaction.py`

Add `UserPromptPart` to the existing `pydantic_ai.messages` import block.

Add the private function immediately before `proactive_window_processor`:

```python
def _resolve_proactive_focus(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> str | None:
    for todo in ctx.deps.session.session_todos:
        if todo["status"] == "in_progress":
            return todo["content"][:200]
    for msg in reversed(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    return part.content[-200:]
    return None
```

**done_when:** `grep -n "_resolve_proactive_focus" co_cli/context/compaction.py` returns the function definition and `grep -n "UserPromptPart" co_cli/context/compaction.py | grep "from pydantic_ai"` returns a hit confirming it is in the import statement.

**success_signal:** N/A (no user-facing change on its own)

**prerequisites:** none

---

### ✓ DONE TASK-2 — Wire focus into `proactive_window_processor`

**files:** `co_cli/context/compaction.py`

Replace line 556:
```python
result, summary_text = await compact_messages(ctx, messages, bounds, focus=None)
```
with:
```python
focus = _resolve_proactive_focus(ctx, messages)
result, summary_text = await compact_messages(ctx, messages, bounds, focus=focus)
```

**done_when:** `grep -n "focus=None" co_cli/context/compaction.py` returns no match at the `proactive_window_processor` call site (line ~556); `grep -n "_resolve_proactive_focus" co_cli/context/compaction.py` returns a call site inside `proactive_window_processor`.

**success_signal:** `uv run pytest tests/test_flow_compaction_proactive.py -x` passes.

**prerequisites:** TASK-1

---

### ✓ DONE TASK-3 — Unit tests for `_resolve_proactive_focus`

**files:** `tests/test_flow_compaction_proactive.py`

Add three tests (no LLM, no async) directly below the existing import block, importing `_resolve_proactive_focus` from `co_cli.context.compaction`:

1. **`test_focus_from_in_progress_todo`** — `session_todos` contains one `in_progress` item; assert the returned string equals `todo["content"][:200]`.
2. **`test_focus_from_last_user_message`** — no todos, messages list has two `ModelRequest` objects each wrapping a `UserPromptPart`; assert the returned string is the `content` of the *most recent* `UserPromptPart` (tail-capped at 200). The fixture must wrap parts in `ModelRequest` — bare `UserPromptPart` objects are not scanned by the implementation.
3. **`test_focus_none_when_no_todo_and_no_messages`** — empty todos, empty messages; assert return is `None`.

No LLM call, no `asyncio.timeout`, no `ensure_ollama_warm` — these are pure-function tests.

**done_when:** `uv run pytest tests/test_flow_compaction_proactive.py -x` passes with the three new tests included and no existing test regressions.

**success_signal:** `uv run pytest tests/test_flow_compaction_proactive.py -x` green.

**prerequisites:** TASK-1

## Testing

Full compaction proactive suite: `uv run pytest tests/test_flow_compaction_proactive.py -x`.

No new LLM-touching tests added — the resolver is a pure function. Existing LLM-exercising tests in the file cover the end-to-end path where `focus` is now non-None when conditions are met.

## Open Questions

None — all decisions resolved via `/grill-me` on 2026-05-14. See resolved decisions below.

## Resolved Decisions

### D1. Borrow opencode's structured compaction template?
Chosen: Drop. co-cli's template (13 sections + verbatim anchors + prior-summary integration) is already richer.

### D2. Real weak link in co-cli compaction?
Chosen: Focus inference on the proactive path (`focus=None` at `compaction.py:556`).

### D3. Focus source?
Chosen: Hybrid — `in_progress` todo content if present, else recent user-message tail (~200 chars), else `None`.

### D4. Add openclaw's preemptive router (fits/truncate/compact)?
Chosen: Don't add. `evict_old_tool_results` covers the truncate case; focus inference yields more.

### D5. Adopt hermes session_id rotation / pre-compaction snapshot?
Chosen: Drop. Existing fallbacks gate load-bearing failure modes; marginal gain doesn't justify retention bookkeeping.

## Delivery Summary — 2026-05-14

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_resolve_proactive_focus` defined; `UserPromptPart` in import block | ✓ pass |
| TASK-2 | `focus=None` removed at proactive call site; resolver called inside `proactive_window_processor` | ✓ pass |
| TASK-3 | `uv run pytest tests/test_flow_compaction_proactive.py -x` passes with three new tests | ✓ pass |

**Tests:** scoped — 18 passed, 0 failed
**Doc Sync:** skipped — no spec-visible behavior change (internal implementation detail only)

**Overall: DELIVERED**
All three tasks passed; lint clean; 18 scoped tests green including three new pure-function tests for the resolver.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-m-1   | adopt    | Tighter grep prevents false pass on comment hits | TASK-1 `done_when`: grep piped through `grep "from pydantic_ai"` |
| CD-m-2   | adopt    | Without the clarification the fixture could silently return None | TASK-3 test 2: description now specifies `ModelRequest` wrapping with explicit warning |
| PO-m-1   | adopt    | Direction ambiguity is a real footgun between `[:200]` and `[-200:]` | Behavioral Constraints 1: changed "capped" to "head-capped at 200 characters (`[:200]`)" |

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev proactive-compaction-focus`

## Implementation Review — 2026-05-14

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `_resolve_proactive_focus` defined; `UserPromptPart` in import block | ✓ pass | compaction.py:399 — function defined; compaction.py:22-28 — `UserPromptPart` in multi-line import from `pydantic_ai.messages`; slice directions confirmed: todo `[:200]` (line 405), user message `[-200:]` (line 410) |
| TASK-2 | `focus=None` removed at proactive call site; resolver called inside `proactive_window_processor` | ✓ pass | compaction.py:572-573 — resolver called, result passed as `focus=focus`; line 393 (only `focus=None`) is inside `recover_overflow_history`, not proactive path |
| TASK-3 | `uv run pytest tests/test_flow_compaction_proactive.py -x` passes with three new tests | ✓ pass | test_flow_compaction_proactive.py:424,448,472 — three sync pure-function tests; no mocks; ModelRequest wrapping confirmed; tail-cap direction `[-200:]` confirmed at line 469 |

### Issues Found & Fixed

No issues found.

### Tests
- Command: `uv run pytest -v`
- Result: 487 passed, 0 failed
- Log: `.pytest-logs/20260514-192941-review-impl.log`

### Behavioral Verification
No user-facing surface changed (`_resolve_proactive_focus` is a private pure function; the only externally observable effect is richer compaction summaries mid-session). `co status` does not exist in this system. Behavioral verification skipped — no CLI, tool, or output-format changes.

### Overall: PASS
All three tasks implemented correctly: focus resolver added with correct slice directions, wired into the proactive path replacing hardcoded `None`, and covered by three clean pure-function tests. Full suite green at 487 passed.
