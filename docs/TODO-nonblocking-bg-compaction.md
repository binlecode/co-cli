# TODO: Non-Blocking Background Compaction Join

## Principle

Background maintenance is best-effort optimization, not a foreground turn
dependency.

If a speculative compaction precompute has already finished, the next turn
should consume it. If it is still running, the next turn should proceed
without waiting and fall back to normal inline history compaction only if
history policy actually requires it.

Foreground waits are appropriate for user-visible active work. They are not
appropriate for speculative latency optimization.

---

## Current State (validated 2026-03-21)

`chat_loop()` always awaits `bg_compaction_task` before starting the next LLM
turn:

- `co_cli/main.py:159-166` unconditionally checks `if bg_compaction_task is not None`
  and then `await`s it
- if the task succeeds, `deps.runtime.precomputed_compaction` is populated
- if it fails, `deps.runtime.precomputed_compaction = None`
- only after that join completes does `chat_loop()` call
  `run_turn_with_fallback(...)`

This means the next turn can block on an LLM-backed summarization task created
for speculative compaction precompute:

- `co_cli/context/_history.py:457-532` computes background compaction
- that path can invoke `_run_summarization_with_policy(...)`
- so the "background" task can still be expensive and slow

The comment in `main.py` says:

- "Join background compaction if it completed while user was typing"

But the actual code does not check whether the task already completed. It
joins whenever a task exists.

So the real behavior today is:

1. turn N finishes
2. background compaction task is spawned
3. user types turn N+1
4. `chat_loop()` blocks on the prior background task if it is still running
5. only then does turn N+1 start

This is a responsiveness regression disguised as an optimization.

---

## Why This Is A Problem

### 1. It violates the intended background-work contract

`precompute_compaction()` is speculative maintenance. Its purpose is to reduce
latency on a future turn if compaction is needed. It is not required to answer
the user's next message.

Blocking the foreground turn on that task inverts the contract:

- the optimization becomes a prerequisite
- the user waits for maintenance work they did not ask for

### 2. It weakens the REPL responsiveness model

The current main-loop design otherwise preserves a clean contract:

- user submits input
- CLI does minimal pre-turn bookkeeping
- `run_turn()` starts promptly

The unconditional join introduces a hidden synchronous barrier before every
turn that follows a compaction-precompute spawn.

### 3. It is out of step with converged peer practice

The repository's own research notes point toward async memory upkeep and
background execution as decoupled flows:

- `docs/reference/RESEARCH-peer-systems.md:63` — async memory upkeep is
  becoming standard
- `docs/reference/RESEARCH-peer-systems.md:233` — background task models are
  explicit, transparent, and not coupled to immediate foreground interaction
- `docs/reference/RESEARCH-peer-systems.md:293` — move more
  extraction/consolidation into background flows

Local reference repos align with that direction:

- `nanobot` heartbeat tasks run on a periodic wake-up loop rather than
  blocking the next interactive message
- `Letta` explicitly treats background work as work whose status can be
  checked later, and persists some message-related maintenance after returning
  a response
- `Codex` does wait on background terminals, but those are active
  user-visible execution surfaces, not speculative maintenance jobs

The best-practice distinction is:

- user-visible active background execution may justify waiting or explicit
  "waiting" UX
- speculative background maintenance should be opportunistic and skippable

`co`'s current compaction precompute is in the second category, but is
implemented with the blocking behavior of the first.

### 4. The current design doc language is inaccurate or underspecified

The updated `DESIGN-core-loop.md` correctly shows a pre-turn await of the
background task, but the design still needs to state the exact semantics:

- current implementation waits even when the task is not finished
- this is a design tradeoff, not just a join detail
- if refactored, the doc must explicitly say the precomputed result is used
  opportunistically rather than synchronously awaited

---

## Target Design

Use opportunistic consumption semantics:

1. Spawn `bg_compaction_task` after a turn, as today.
2. On the next user turn, inspect the task without blocking:
   - if it is done and succeeded, move the result into
     `deps.runtime.precomputed_compaction`
   - if it is done and failed, clear the cached result and discard the task
   - if it is still running, do not await it; leave the task alone and start
     the turn immediately
3. Let normal history policy decide whether inline compaction is needed.
4. If inline compaction happens while the older background task is still
   running, ensure stale background output cannot be consumed later for the
   wrong history snapshot.

The key behavior change is:

- background compaction is best-effort
- foreground turn start is not blocked by it

### Non-Goals

- no redesign of the compaction algorithm itself
- no change to compaction thresholds
- no change to the summary format
- no change to approval behavior
- no new scheduling system or recurring task engine

---

## Required Refactor Decisions

Before implementation, the code needs an explicit policy for stale background
results.

Today `CompactionResult` includes `message_count`, and the history processor
already validates that the precomputed result still matches the current
history shape before using it. That is necessary but not sufficient once the
task is allowed to outlive the start of the next turn.

The implementation must make a deliberate choice:

### Option A — Drop any still-running task at next-turn start

At the beginning of the next turn:

- if `bg_compaction_task.done()`: harvest result
- else: ignore it for this turn and cancel it, then clear the handle

Pros:

- simplest lifecycle
- avoids stale completion racing with later turns
- easier mental model and docs

Cons:

- wastes some background work if the task was close to completion

### Option B — Allow task to continue, but only harvest completed results at safe checkpoints

At the beginning of the next turn:

- if `done()`: harvest result
- if not done: continue without waiting

At a later checkpoint:

- only harvest the task if its result still matches the current history
- otherwise discard it

Pros:

- preserves more background work

Cons:

- more stateful and race-prone
- harder to explain and verify

Recommendation:

- implement **Option A** first

Reason:

- this is a REPL responsiveness fix, not a throughput optimization project
- the simpler lifecycle is easier to trust
- the code already has a correct inline fallback when no precomputed result is
  available

---

## Implementation Tasks

## TASK-1 — Make next-turn precompute consumption non-blocking

**Files:** `co_cli/main.py`

Replace the unconditional await in `chat_loop()` with opportunistic task
inspection.

Implementation shape:

```python
if bg_compaction_task is not None:
    if bg_compaction_task.done():
        try:
            deps.runtime.precomputed_compaction = bg_compaction_task.result()
        except Exception:
            deps.runtime.precomputed_compaction = None
        bg_compaction_task = None
    else:
        # Do not block foreground turn start.
        # Preferred v1: cancel and drop the stale speculative task.
        bg_compaction_task.cancel()
        bg_compaction_task = None
        deps.runtime.precomputed_compaction = None
```

Notes:

- if cancellation is used, do not `await` the task here
- cancellation cleanup should not become a new foreground wait
- keep the no-result path cheap and deterministic

**done_when:**

- starting a new user turn does not `await` a still-running background
  compaction task
- only already-completed tasks are harvested synchronously
- a still-running speculative task cannot block `run_turn_with_fallback(...)`
- `deps.runtime.precomputed_compaction` is populated only from a completed task

---

## TASK-2 — Make task lifecycle and stale-result policy explicit

**Files:** `co_cli/main.py`, `co_cli/context/_history.py`

Codify the chosen stale-result policy, not just the non-blocking check.

If implementing Option A:

- cancel and drop any unfinished task at next-turn start
- clear any pending handle before starting the foreground turn
- preserve the existing `message_count` validation in history compaction as a
  second line of defense

If implementation reveals that cancellation is noisy:

- explicitly swallow `CancelledError` only in cleanup paths where that is the
  intended result
- do not hide real summarization failures

**done_when:**

- there is one explicit lifecycle policy for unfinished compaction tasks
- stale background results cannot be consumed on later unrelated histories
- compaction still works correctly when no precomputed result is available

---

## TASK-3 — Tighten comments and naming around pre-turn bookkeeping

**Files:** `co_cli/main.py`

Update comments so they match the actual behavior.

Required comment changes:

- remove or rewrite "Join background compaction if it completed while user was
  typing"
- replace with wording that distinguishes:
  - harvest completed result
  - do not block on unfinished speculative work

Also add one short comment near task spawning that clarifies:

- this task is best-effort latency optimization for the next turn
- correctness does not depend on it

**done_when:**

- comments in `main.py` accurately describe the non-blocking behavior
- no comment implies unconditional join semantics

---

## TASK-4 — Tests: enforce non-blocking next-turn startup

**Files:** `tests/test_main.py` or `tests/test_chat_loop.py` if present, otherwise
`tests/test_history.py` plus a new focused chat-loop functional test module

Required coverage with real code paths and no mocks:

- when a prior background compaction task is already complete, the next turn
  harvests its result
- when a prior background compaction task is still running, the next turn does
  not wait on it before proceeding
- if the unfinished task is cancelled/dropped by policy, later turns do not
  consume its result
- inline compaction still works when no precomputed result is available

Because the repo forbids mocks, the tests should use real asyncio tasks and
real temp-backed deps. If a full REPL test is too heavy, extract a small pure
helper for "harvest or drop background compaction task" and test that helper
through real task objects.

Constraint:

- do not introduce a fake shell, fake model, or monkeypatch-based timing test

**done_when:**

- a regression test fails if next-turn startup reintroduces a blocking await on
  unfinished background compaction
- a regression test verifies completed-task harvesting still works

---

## TASK-5 — Update DESIGN docs to reflect opportunistic precompute use

**Files:** `docs/DESIGN-core-loop.md`, `docs/DESIGN-knowledge.md` only if it
references this behavior, and any other DESIGN doc that mentions background
compaction handoff

Required doc changes:

- in `DESIGN-core-loop.md`, change pre-turn flow language from "await prior
  bg_compaction task" to an opportunistic check/harvest description
- explain that `precomputed_compaction` is consumed if available, but the next
  turn does not block on unfinished speculative compaction work
- keep the distinction clear between:
  - best-effort background maintenance
  - required foreground turn work

Diagram change:

- the pre-turn node should no longer imply a blocking await of an unfinished
  task
- it should instead say something like:
  - "harvest completed bg_compaction result if available"

**done_when:**

- canonical DESIGN docs no longer describe the pre-turn step as an
  unconditional wait
- the docs explicitly state the best-effort semantics of background compaction

---

## Acceptance Checks

- Fast consecutive turns are not delayed by a still-running compaction
  precompute task.
- Completed precompute results are still reused when available.
- Inline compaction remains the correctness fallback.
- No stale precomputed summary can be applied to the wrong history snapshot.
- Comments and DESIGN docs match the actual behavior.

## Final — Team Lead

Plan draft complete.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev nonblocking-bg-compaction`
