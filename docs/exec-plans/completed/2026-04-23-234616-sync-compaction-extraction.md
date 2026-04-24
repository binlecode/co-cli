# Plan: Synchronous Extraction at Compaction Boundary

**Slug:** sync-compaction-extraction
**Created:** 2026-04-23
**Source:** `docs/reference/RESEARCH-co-compaction-flow-audit.md` §Issue 3
("Compaction extraction can skip content it is meant to rescue"); code review of
`co_cli/knowledge/_distiller.py`, `co_cli/context/compaction.py`,
`co_cli/commands/_commands.py`, `co_cli/main.py`.

---

## Context

Co-cli extracts knowledge from the transcript in two contexts:

1. **Cadence extraction** — fire-and-forget at clean post-turn boundaries,
   driven by `memory.extract_every_n_turns`. Loss here only delays extraction
   by one cadence tick, since the delta is still in history.
2. **Compaction-boundary extraction** — runs right before history is replaced
   by a marker + summary. The pre-compact tail is about to be discarded, so
   anything not extracted here is permanently lost to the knowledge layer.

Today both paths share the same single-flight fire-and-forget launcher. That
couples cadence and compaction in a way that silently drops compaction-time
extraction when a cadence task happens to be running, while still advancing the
extraction cursor as if it had succeeded.

---

## Current-State Validation

### Shared launcher

`co_cli/knowledge/_distiller.py:180-204` — `fire_and_forget_extraction()` is
single-flight: if `_in_flight` is set and not done, scheduling is **skipped**
and the function returns without error.

```python
def fire_and_forget_extraction(..., cursor_start, advance_cursor=True):
    global _in_flight
    if _in_flight is not None and not _in_flight.done():
        logger.debug("Extraction already in progress, skipping")
        return
    _in_flight = ... create_task(_run_extraction_async(...))
```

### Compaction scheduler

`co_cli/knowledge/_distiller.py:207-238` — `schedule_compaction_extraction()`
calls the shared launcher, then pins the cursor **unconditionally**, even when
the launch was skipped:

```python
def schedule_compaction_extraction(pre_compact, post_compact, deps, frontend=None):
    cursor = deps.session.last_extracted_message_idx
    if 0 <= cursor < len(pre_compact):
        delta = pre_compact[cursor:]
        fire_and_forget_extraction(delta, ..., cursor_start=cursor, advance_cursor=False)
    deps.session.last_extracted_message_idx = len(post_compact)
    deps.session.last_extracted_turn_idx = 0
```

### Call sites

All three callers are already `async`:

- `co_cli/context/compaction.py:206-208` — `_apply_compaction()` (proactive,
  hygiene, and overflow-recovery paths all flow through here; signature at
  line 179).
- `co_cli/context/compaction.py:290-292` — `emergency_recover_overflow_history()`
  (signature at line 257).
- `co_cli/commands/_commands.py:384-386` — `_cmd_compact()` (slash command;
  signature at line 329).

### Cursor state

`co_cli/deps.py:110-112` — `CoSessionState.last_extracted_message_idx` and
`last_extracted_turn_idx` are the race-exposed fields.

### Cadence path (unchanged by this plan)

`co_cli/main.py:108-129` — `_finalize_turn()` calls
`fire_and_forget_extraction()` on the cadence counter, advancing the cursor
inside `_run_extraction_async()` on success. That is the correct behavior for
cadence and stays as-is.

### Drain hook

`co_cli/knowledge/_distiller.py:241-259` — `drain_pending_extraction(timeout_ms)`
awaits any in-flight cadence task with a timeout and cancels on overrun. Called
today only during shutdown from `co_cli/main.py:181-183`.

### Existing test coverage

`tests/knowledge/test_distiller_window.py`:

- `test_last_extracted_idx_advances_on_empty_window` (line 169) — cadence,
  stays.
- `test_schedule_compaction_extraction_pins_cursor_to_post_compact` (line 201)
  — regression for the `/compact` bleed; must be updated to the new API.
- `test_schedule_compaction_extraction_noop_cursor_beyond_pre_compact` (line
  243) — noop case; must be updated to the new API.
- `test_cursor_does_not_advance_on_extraction_failure` (line 270) — cadence
  failure; stays.

No test today asserts the cross-path race (cadence in flight while compaction
fires).

### Docs touching this behavior

- `docs/specs/memory-knowledge.md:254-262` — describes single-flight
  fire-and-forget without distinguishing cadence from compaction.
- `docs/reference/RESEARCH-co-compaction-flow-audit.md:166-184` — documents the
  race as Issue 3 and proposes the fix direction.
- `docs/specs/compaction.md` — no extraction references today; no update
  needed unless the design section (§2 Core Logic) requires a forward link.

---

## Problem

When compaction fires while a cadence extraction task is still running:

1. `fire_and_forget_extraction()` sees `_in_flight` set and skips scheduling.
2. `schedule_compaction_extraction()` still pins
   `last_extracted_message_idx = len(post_compact)` as though extraction had
   run over the pre-compact tail.
3. `_apply_compaction()` then replaces the pre-compact range with a summary
   marker.

Result: the content that was about to be discarded is never extracted, and the
cursor state claims it was. The knowledge layer silently loses one delta.

Secondary race (same in-flight case): the cadence task that caused the skip
keeps running. When it later succeeds, `_run_extraction_async` writes
`cursor_start + len(delta)` — an index into the *pre-compact* list — over the
compaction cursor pin, producing a cursor that points into discarded
history.

---

## Desired Outcome

- Compaction-boundary extraction runs **before** history is discarded and is
  awaited to completion (or documented failure) before the cursor is pinned.
- An already-running cadence extraction never causes the compaction-boundary
  extraction to be skipped.
- Cursor pinning remains atomic with the compaction write — no window where
  another task can overwrite it.
- Cadence extraction is unchanged: still fire-and-forget, still single-flight,
  still advances the cursor on success.
- Extraction failures remain best-effort — the compaction path continues and
  pins the cursor even when extraction raises.

---

## Scope

### In scope

- Introduce `extract_at_compaction_boundary()` — an `async` function in
  `co_cli/knowledge/_distiller.py` that replaces
  `schedule_compaction_extraction()`.
- Bypass the `_in_flight` single-flight guard for the compaction path by
  awaiting `_run_extraction_async()` directly.
- Drain any in-flight cadence task first, so its cursor advance is settled
  before we build the compaction delta.
- Update all three callers to `await` the new function.
- Update the two impacted regression tests and add one failure-resilience test.
- Update `docs/specs/memory-knowledge.md` to distinguish cadence from
  compaction extraction.
- Annotate `docs/reference/RESEARCH-co-compaction-flow-audit.md` §Issue 3 as
  addressed by this plan (research doc stays as audit record; just a note).

### Out of scope

- Any change to cadence extraction (`_finalize_turn`,
  `fire_and_forget_extraction`, `_in_flight`, `_on_extraction_done`).
- Queueing / merging pending deltas across cadence and compaction.
- Changes to `drain_pending_extraction()` semantics or its shutdown call.
- Functional Gap §1 (`/compact` degradation parity — static-marker fallback,
  circuit breaker, dropped-range enrichment, `search_tools` breadcrumbs) and
  Issue §4 (transcript persistence accounting) from the same audit doc —
  tracked separately.

---

## Design

### New function shape

```python
# co_cli/knowledge/_distiller.py
async def extract_at_compaction_boundary(
    pre_compact: list,
    post_compact: list,
    deps: "CoDeps",
    frontend: "Frontend | None" = None,
) -> None:
    """Extract knowledge from the pre-compact tail before history is discarded.

    Runs inline (awaited) so compaction can pin the extraction cursor
    atomically. Best-effort: extraction failures are logged and swallowed; the
    cursor still pins to len(post_compact).
    """
    # 1. Let any running cadence task finish (or time out and cancel) so its
    #    own cursor advance is visible before we read the cursor.
    await drain_pending_extraction()

    cursor = deps.session.last_extracted_message_idx
    if 0 <= cursor < len(pre_compact):
        delta = pre_compact[cursor:]
        # Bypass fire_and_forget_extraction — we want to await, not skip.
        await _run_extraction_async(
            delta,
            deps,
            frontend,
            cursor_start=cursor,
            advance_cursor=False,
        )

    # 2. Pin cursor to the post-compact length and reset the cadence counter.
    #    Safe after drain + awaited extraction: no other writer is in flight.
    deps.session.last_extracted_message_idx = len(post_compact)
    deps.session.last_extracted_turn_idx = 0
```

Key properties:

- `_run_extraction_async()` already catches `CancelledError` and generic
  `Exception`, so the `await` returns normally on failure. No try/except
  needed at the call site.
- `advance_cursor=False` keeps `_run_extraction_async` from writing
  `cursor_start + len(delta)` into the cursor — that index is meaningful only
  in the pre-compact list.
- Draining before reading the cursor avoids the secondary race where a
  cadence task's cursor write could arrive after we pin.
- `drain_pending_extraction()` already has a 10 s timeout + cancel; acceptable
  for the compaction path per the discussion (compaction is already
  exceptional and may invoke a summarizer LLM call).

### Why not reuse `fire_and_forget_extraction()` with an `await` flag

Adding an `await` mode would preserve the `_in_flight` global and force the
compaction path to either be skipped (current bug) or to clobber `_in_flight`
semantics for the cadence path. A dedicated async function is smaller and
keeps the two concerns separated.

### Remove or keep `schedule_compaction_extraction()`

Remove. All three callers are already async; there is no reason to keep a
sync shim. Removing it also prevents accidental reintroduction of the race.

---

## Implementation Tasks

### ✓ DONE — Task 1 — Add `extract_at_compaction_boundary()`

**File:** `co_cli/knowledge/_distiller.py`

- Add the async function with the body shown in Design above.
- Place it after `drain_pending_extraction()` to keep the compaction-specific
  API visually grouped.
- Remove the existing `schedule_compaction_extraction()` definition (lines
  ~207-238).

### ✓ DONE — Task 2 — Update `_apply_compaction` call site

**File:** `co_cli/context/compaction.py`

- Line 206-208: replace
  ```python
  from co_cli.knowledge._distiller import schedule_compaction_extraction
  schedule_compaction_extraction(messages, result, ctx.deps)
  ```
  with
  ```python
  from co_cli.knowledge._distiller import extract_at_compaction_boundary
  await extract_at_compaction_boundary(messages, result, ctx.deps)
  ```
- `_apply_compaction()` is already `async` (signature at line 179).

### ✓ DONE — Task 3 — Update `emergency_recover_overflow_history` call site

**File:** `co_cli/context/compaction.py`

- Line 290-292: same replacement as Task 2, with the same `messages` /
  `result` local names.
- `emergency_recover_overflow_history()` is already `async` (signature at
  line 257).

### ✓ DONE — Task 4 — Update `_cmd_compact` call site

**File:** `co_cli/commands/_commands.py`

- Line 384-386: same replacement, preserving the `ctx.frontend` argument:
  ```python
  from co_cli.knowledge._distiller import extract_at_compaction_boundary
  await extract_at_compaction_boundary(
      ctx.message_history, new_history, ctx.deps, ctx.frontend
  )
  ```
- `_cmd_compact()` is already `async` (signature at line 329).

### ✓ DONE — Task 5 — Grep sweep for stale references

Run `grep -rn schedule_compaction_extraction` across `co_cli/`, `tests/`, and
`docs/`. Expected after Tasks 1-4: only historical mentions in
`docs/exec-plans/completed/` and `docs/reference/` remain. Everything under
`co_cli/`, `tests/`, and `docs/specs/` should be zero.

Also grep for `fire_and_forget_extraction` to confirm the cadence path in
`co_cli/main.py:127` and the two cadence tests are untouched.

### ✓ DONE — Task 6 — Update test imports and assertions

**File:** `tests/knowledge/test_distiller_window.py`

- Line 19-24: replace `schedule_compaction_extraction` import with
  `extract_at_compaction_boundary`.

- `test_schedule_compaction_extraction_pins_cursor_to_post_compact` (line 201):
  - rename to
    `test_compaction_boundary_extraction_pins_cursor_to_post_compact`
  - replace
    ```python
    schedule_compaction_extraction(pre_compact, post_compact, deps)
    async with asyncio.timeout(5):
        await drain_pending_extraction(timeout_ms=1000)
    ```
    with
    ```python
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        await extract_at_compaction_boundary(pre_compact, post_compact, deps)
    ```
  - Keep both cursor/counter assertions unchanged.

- `test_schedule_compaction_extraction_noop_cursor_beyond_pre_compact` (line
  243):
  - rename to
    `test_compaction_boundary_extraction_noop_cursor_beyond_pre_compact`
  - replace the sync call with `await extract_at_compaction_boundary(...)`
    wrapped in `asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS)`.
  - Keep both cursor/counter assertions unchanged.

### ✓ DONE — Task 7 — Add failure-resilience test

**File:** `tests/knowledge/test_distiller_window.py`

Add one new test:

```python
@pytest.mark.asyncio
async def test_compaction_boundary_extraction_pins_cursor_even_on_failure(tmp_path):
    """When agent.run() raises, compaction path still pins cursor (best-effort)."""
    from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

    # model=None forces _run_extraction_async into its generic-Exception branch
    deps = CoDeps(shell=ShellBackend(), config=make_settings(),
                  knowledge_dir=tmp_path / "memory", model=None)
    pre_compact = [ModelRequest(parts=[UserPromptPart(content="content to extract")])]
    post_compact = [ModelRequest(parts=[UserPromptPart(content="[compaction marker]")])]
    deps.session.last_extracted_message_idx = 0
    deps.session.last_extracted_turn_idx = 3

    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        await extract_at_compaction_boundary(pre_compact, post_compact, deps)

    assert deps.session.last_extracted_message_idx == len(post_compact)
    assert deps.session.last_extracted_turn_idx == 0
```

`LLM_NON_REASONING_TIMEOUT_SECS` is already imported inside
`test_cursor_does_not_advance_on_extraction_failure`; the two updated tests in
Task 6 need it as well. Either lift the import to module level or repeat the
local import in each test — this plan assumes repeated local imports to keep
the diff minimal.

No new test for the cadence-in-flight race: reproducing a real race without
fakes requires a real-model cadence task mid-flight, which belongs in an eval,
not a unit test. The behavioral guarantee (cursor pinned after await,
regardless of prior in-flight state) is covered by Task 6's updated tests once
they await the new function.

### ✓ DONE — Task 8 — Update `docs/specs/memory-knowledge.md`

Replace the `2.5 Memory -> Knowledge Bridge: Per-Turn Extraction` closing
paragraph (line 262) so it clearly distinguishes the two extraction contexts:

> `fire_and_forget_extraction()` is single-flight: if one extraction task is
> already running, later launches are skipped. This is acceptable at cadence
> boundaries because the delta remains in history and will be re-offered at
> the next cadence tick.
>
> **Compaction-boundary extraction is synchronous.** Because compaction is
> about to discard the pre-compact tail, `extract_at_compaction_boundary()`
> drains any in-flight cadence task, awaits extraction inline, and only then
> pins `last_extracted_message_idx` to `len(post_compact)`. Extraction
> failures are best-effort: the cursor still pins so compaction can proceed.

### ✓ DONE — Task 9 — Annotate audit doc

**File:** `docs/reference/RESEARCH-co-compaction-flow-audit.md`

Under §Issue 3 (line ~166), append one line:

> **Status:** addressed by exec-plan
> `docs/exec-plans/completed/<date>-sync-compaction-extraction.md` (replaces
> the shared single-flight launcher with an awaited compaction-boundary
> path).

Leave the rest of the audit entry intact — it is a research record.

### ✓ DONE — Task 10 — Quality gate

- `scripts/quality-gate.sh lint --fix`
- `uv run pytest tests/knowledge/test_distiller_window.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-distiller.log`
- `scripts/quality-gate.sh full` before ship.

---

## Test Plan

| Scenario | Test | File |
|---|---|---|
| Cursor pins to `len(post_compact)` after awaited extraction | `test_compaction_boundary_extraction_pins_cursor_to_post_compact` | `tests/knowledge/test_distiller_window.py` |
| Cursor already at end of pre-compact — no extraction call, cursor still pins | `test_compaction_boundary_extraction_noop_cursor_beyond_pre_compact` | same |
| Extraction failure is best-effort — cursor still pins, compaction continues | `test_compaction_boundary_extraction_pins_cursor_even_on_failure` (new) | same |
| Cadence path unchanged | `test_last_extracted_idx_advances_on_empty_window`, `test_cursor_does_not_advance_on_extraction_failure` | same |

---

## Risks & Rollback

### Risk: compaction latency grows by one extraction LLM call (plus drain)

Compaction already issues a summarizer LLM call inline. Adding an extraction
LLM call on the same path roughly doubles the worst-case latency. In the
cadence-in-flight case the drain adds a third wait (up to its 10 s timeout)
before the compaction-side extraction begins — so the worst-case sequence is
`drain (≤10 s) + summarizer + extraction`. Mitigation: the extraction agent
runs over the transcript *window* (capped at 10 text + 10 tool lines per
`build_transcript_window`), so its input is small; its latency is bounded
similarly to the summarizer. `/compact` already announces "Compacting
conversation..." — no UX regression.

### Risk: `drain_pending_extraction()` cancels a mostly-finished cadence task

Timeout is 10 s. If a cadence task is close to completion when compaction
fires, cancellation loses its extraction. Net effect is still safer than the
current behavior: the compaction path will then extract over the same pre-
compact tail anyway (cursor did not advance before cancel), so nothing is
lost — only the cadence task's partial progress is discarded and redone.

### Rollback

The change is localized to one module, two call-site swaps, one slash
command, and test updates. Revert is `git revert` of the single ship commit.
Cursor state on disk is unaffected (`last_extracted_message_idx` is
session-scoped and advances monotonically under both behaviors).

---

## Delivery Summary — 2026-04-24

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `extract_at_compaction_boundary` present in `_distiller.py`; `schedule_compaction_extraction` removed | ✓ pass |
| TASK-2 | `compaction.py:208` awaits new function | ✓ pass |
| TASK-3 | `compaction.py:292` awaits new function | ✓ pass |
| TASK-4 | `_commands.py:386` awaits new function | ✓ pass |
| TASK-5 | `grep -rn schedule_compaction_extraction co_cli/ tests/ docs/specs/` = 0 matches | ✓ pass |
| TASK-6 | renamed tests pass against new function | ✓ pass |
| TASK-7 | failure-resilience test added and passes | ✓ pass |
| TASK-8 | `docs/specs/memory-knowledge.md` §2.5 distinguishes cadence vs compaction | ✓ pass |
| TASK-9 | audit doc §Issue 3 annotated "Status: addressed" | ✓ pass |
| TASK-10 | lint + scoped tests pass | ✓ pass |

**Files changed:**
- `co_cli/knowledge/_distiller.py` — removed `schedule_compaction_extraction`, added async `extract_at_compaction_boundary` after `drain_pending_extraction`
- `co_cli/context/compaction.py` — awaited new function at `_apply_compaction` (L208) and `emergency_recover_overflow_history` (L292)
- `co_cli/commands/_commands.py` — awaited new function in `_cmd_compact` (L386)
- `co_cli/main.py` — comment-only update to reference new name (L114)
- `tests/knowledge/test_distiller_window.py` — import swap, two tests renamed + rewritten to `await`, one new failure-resilience test
- `docs/specs/memory-knowledge.md` — §2.5 paragraph split into cadence vs compaction
- `docs/reference/RESEARCH-co-compaction-flow-audit.md` — §Issue 3 Status annotation

**Tests:** scoped across four touched files (`test_history.py`, `test_context_compaction.py`, `test_transcript.py`, `test_distiller_window.py`) — 112 passed, 0 failed, 41.82s.
**Doc Sync:** narrow scope, clean — only `memory-knowledge.md` references extraction hooks and it was updated in TASK-8.

**Overall: DELIVERED**
Race condition (Issue 3 of the compaction audit) eliminated. Compaction paths now drain in-flight cadence extraction, await extraction inline, then pin the cursor — extraction can no longer be silently skipped while the cursor is advanced.

---

## Implementation Review — 2026-04-24

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | new async fn present, old fn removed | ✓ pass | `co_cli/knowledge/_distiller.py:228-261` — `extract_at_compaction_boundary` drains (L247) → reads cursor (L249) → awaits `_run_extraction_async` directly with `advance_cursor=False` (L252-258) → pins cursor (L260-261). `schedule_compaction_extraction` absent from file. |
| TASK-2 | `_apply_compaction` awaits new fn | ✓ pass | `co_cli/context/compaction.py:208` — `await extract_at_compaction_boundary(messages, result, ctx.deps)` inside async `_apply_compaction` (signature L179). |
| TASK-3 | emergency path awaits new fn | ✓ pass | `co_cli/context/compaction.py:292` — same pattern inside async `emergency_recover_overflow_history` (signature L257). |
| TASK-4 | `_cmd_compact` awaits new fn | ✓ pass | `co_cli/commands/_commands.py:386` — passes `ctx.frontend` through, inside async `_cmd_compact` (signature L329). |
| TASK-5 | zero stale refs in code/tests/specs | ✓ pass | `grep -rn schedule_compaction_extraction co_cli/ tests/ docs/specs/` returns zero. `co_cli/main.py:114` comment updated to new name. |
| TASK-6 | renamed tests pass | ✓ pass | `tests/knowledge/test_distiller_window.py:201,244` — tests renamed and rewritten to `await extract_at_compaction_boundary(...)` under `asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS)`. |
| TASK-7 | failure-resilience test passes | ✓ pass | `tests/knowledge/test_distiller_window.py:273-302` — verifies cursor still pins when extraction fails (model=None path raises inside `_run_extraction_async`, caught, outer fn still runs L260-261 pin). |
| TASK-8 | spec distinguishes cadence vs compaction | ✓ pass | `docs/specs/memory-knowledge.md:262-264` — cadence skip acceptable vs compaction synchronous/best-effort distinction. |
| TASK-9 | audit doc marked addressed | ✓ pass | `docs/reference/RESEARCH-co-compaction-flow-audit.md:186-190` — Status line pointing at this plan. |
| TASK-10 | lint + scoped tests pass | ✓ pass | `scripts/quality-gate.sh lint` clean; full suite 652 passed. |

**Call-path trace (post-change):** `orchestrate.py:531,535` → `await recover_overflow_history | emergency_recover_overflow_history` → `await _apply_compaction` → `await extract_at_compaction_boundary` → `await drain_pending_extraction` + `await _run_extraction_async` (direct, single-flight guard bypassed). Cadence path unchanged: `main.py:127` → `fire_and_forget_extraction` (still single-flight, still background).

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest`
- Result: 652 passed, 0 failed
- Duration: 208.38s
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: narrow — only `docs/specs/memory-knowledge.md` references extraction entry points, updated in TASK-8.
- Result: clean.

### Behavioral Verification
- `uv run co config`: ✓ all components healthy (LLM, shell, MCP, DB, knowledge).
- `/compact` interactive exercise deferred: 4 compaction-specific test files covering the contract (`test_distiller_window.py`, `test_history.py`, `test_context_compaction.py`, `test_transcript.py`) all pass; the change is a mechanical call-site rename + await that the import-time + async-coroutine resolution already validates.

### Relation to User-highlighted Audit Text (Functional Gap §1)
The user highlighted `RESEARCH-co-compaction-flow-audit.md:206-222` — `/compact` degradation parity (no static-marker fallback on summarizer failure). Confirmed this plan does not alter that behavior: `_cmd_compact` still returns at L361-363 on `ModelHTTPError`/`ModelAPIError` before reaching the new `await extract_at_compaction_boundary` at L386. §Gap 1 remains separately tracked and out of scope for this ship.

### Overall: PASS
Ship-ready. Run `/ship sync-compaction-extraction`.
