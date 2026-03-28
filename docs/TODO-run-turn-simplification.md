# TODO — `run_turn()` Simplification

This delivery keeps `run_turn()` as the turn-level orchestrator and simplifies the implementation shape where it is heavier than necessary today.

The goal is not to split orchestration across more layers or to remove real responsibilities. The goal is to make the existing responsibilities easier to audit, test, and modify without changing runtime behavior.

## Position

`run_turn()` should remain the single owner of one full user turn.

That is the correct boundary because one turn genuinely includes:

- one or more streamed segments
- deferred approval resume
- provider retry handling
- usage-limit grace summary
- interrupt recovery
- final `TurnResult` construction

So the problem is not that `run_turn()` is central. The problem is that the current implementation carries some avoidable coordination complexity inside that central function.

## Problem Summary

The latest code shows that `run_turn()` is conceptually correct but implementation-heavy in a few specific ways:

### 1. Parallel mutable locals carry the turn state

`run_turn()` currently coordinates several interdependent mutable locals:

- `result`
- `streamed_text`
- `http_retries_left`
- `current_input`
- `current_history`
- `current_deferred_results`
- `turn_limits`
- `turn_usage`
- `backoff_base`

This works, but it makes correctness depend on remembering which paths must update which subset of state.

The reflected-HTTP-400 path already depends on one subtle invariant:

- retry must use `current_history`, not the stale `message_history`

That kind of invariant is exactly what becomes fragile when state is spread across independent locals.

### 2. Segment execution pattern is repeated

The function executes a streamed segment in multiple places:

1. initial segment
2. approval-resume segment(s)
3. grace-summary segment after `UsageLimitExceeded`

The behavior differs slightly per case, but the structure is similar enough that some of the update logic is duplicated:

- call the segment runner
- capture `result`
- capture `streamed_text`
- update usage
- clear or preserve approval-decision payload

This is not catastrophic duplication, but it is enough to obscure the main state-machine shape.

### 3. Exception paths are mixed into one long loop body

The current implementation interleaves:

- normal segment execution
- approval interception
- final-output handling
- usage-limit fallback
- HTTP error classification
- model/network retry handling
- interrupt recovery

All of those belong to `run_turn()`, but today they sit close enough together that the function reads more densely than the underlying state model requires.

### 4. Approval-decision terminology is still weak in the turn state

The code still uses SDK-derived language like `current_deferred_results`, even though semantically this state is:

- approval decisions for pending deferred tool calls

That naming makes the orchestration state harder to read than necessary.

### 5. `TurnOutcome` still carries dead states

`TurnOutcome` includes values not emitted by current orchestration. That is not an implementation bug, but it is extra conceptual surface around a function that is already dense.

## Non-Goals

- no redesign of approval flow
- no removal of provider retry handling
- no change to interrupt recovery behavior
- no move of turn orchestration out of `run_turn()`
- no new framework layer over pydantic-ai
- no behavioral changes to streaming, MCP approvals, or context warnings

## Simplification Strategy

The simplification should follow one rule:

Keep the number of real responsibilities the same, but reduce the number of places a reader must mentally reconstruct turn state.

That implies:

- one explicit private state carrier
- smaller private transition helpers where they reduce duplication
- cleaner terminology
- no public API expansion

## ✓ DONE — Task 1 — Introduce A Private `_TurnState` Dataclass

### Why

The function already has a coherent turn-state model; it is just encoded as separate locals instead of one explicit object.

### Files

- `co_cli/context/_orchestrate.py`
- `docs/DESIGN-core-loop.md`
- tests covering retries, approvals, interrupts

### Changes

1. add a private `_TurnState` dataclass in `_orchestrate.py`
2. move mutable turn-scoped fields into `_TurnState`, including:
   - current input
   - current history
   - current tool approval decisions
   - latest `AgentRunResult`
   - latest `streamed_text`
   - latest usage
   - retry budget
   - backoff state
3. keep `turn_limits` either:
   - on `_TurnState`, or
   - as one explicit immutable local if that reads better
4. use field names that describe semantics, not SDK leakage

### Recommendation

Prefer names like:

- `tool_approval_decisions`
- `latest_result`
- `latest_usage`
- `retry_budget_remaining`

over names like:

- `current_deferred_results`
- `result`
- `turn_usage`
- `http_retries_left`

### Done When

- the main control flow no longer relies on many parallel mutable locals
- important invariants are carried in one named object

## ✓ DONE — Task 2 — Rename Approval-Decision State Inside `run_turn()`

### Why

Even after the broader terminology cleanup, `run_turn()` should not keep SDK jargon in its main state machine when a clearer semantic name is available.

### Files

- `co_cli/context/_orchestrate.py`
- tests around approval resume

### Changes

1. rename local turn-state fields and variables from `deferred_*` wording to `tool_approval_decisions`
2. keep literal SDK names only at the API boundary:
   - `DeferredToolResults`
   - `deferred_tool_results=...`
3. update inline comments so they describe:
   - "approval decisions for pending deferred tool calls"
   rather than:
   - "deferred tool results"

### Done When

- `run_turn()` reads in Co's own semantics rather than raw SDK terminology

## ✓ DONE — Task 3 — Extract One Private "Run One Segment" Transition Helper

### Why

The repeated segment-execution pattern can be reduced without obscuring control flow.

### Files

- `co_cli/context/_orchestrate.py`
- `tests/test_orchestrate.py`

### Changes

Add one small helper, for example `_execute_stream_segment(state, ...)`, that:

1. calls `_run_stream_segment(...)`
2. stores the returned `AgentRunResult`
3. stores `streamed_text`
4. updates usage
5. clears consumed approval decisions where appropriate

### Guidance

- do not build a generic orchestration framework
- do not hide the turn state machine behind many helper layers
- one helper is enough if it makes the repeated pattern obvious

### Done When

- initial and resumed segment execution share one clear update path
- the main loop reads more like a turn controller than a bag of assignments

## ✓ DONE — Task 4 — Extract Focused Exception/Transition Helpers Where They Reduce Density

### Why

The exception handling belongs in `run_turn()`, but the current body is dense enough that some branches should have one named owner.

### Files

- `co_cli/context/_orchestrate.py`
- tests covering each branch

### Changes

Extract a small number of private helpers, only where they improve clarity materially:

1. `_handle_usage_limit_exceeded(...)`
   - owns grace-summary behavior
2. `_handle_http_model_error(...)`
   - owns HTTP 400 reflection vs 429/5xx retry vs terminal error classification
3. `_handle_model_api_error(...)`
   - owns network/timeout retry behavior
4. `_build_interrupted_turn_result(...)`
   - owns dangling-call patching, abort-marker append, and interrupted `TurnResult`

### Guidance

- helpers should operate on `_TurnState`
- do not move normal-case control flow out of `run_turn()`
- stop extracting helpers once the main function becomes easy to read linearly

### Done When

- each major exceptional path has one obvious owner
- `run_turn()` remains the orchestration entrypoint but is less visually dense

## ✓ DONE — Task 5 — Collapse Dead `TurnOutcome` Variants

### Why

A heavy function should not also carry dead public-state surface.

### Files

- `co_cli/context/_orchestrate.py`
- `co_cli/main.py`
- docs and tests mentioning `TurnOutcome`

### Changes

1. reduce `TurnOutcome` to only variants emitted by current code
2. remove dead branches in callers where appropriate
3. update docs/tests accordingly

### Done When

- `TurnOutcome` exactly matches live behavior
- no dead orchestration branches remain

## ✓ DONE — Task 6 — Tighten The Happy-Path Contract Of `_run_stream_segment()`

### Why

`run_turn()` is heavy partly because the lower-level helper's return contract is implicit. If `_run_stream_segment()` guarantees a final `AgentRunResult`, the turn-level code becomes easier to trust.

### Files

- `co_cli/context/_orchestrate.py`
- `tests/test_orchestrate.py`
- `docs/DESIGN-core-loop.md`

### Changes

1. add an explicit guard that a final `AgentRunResultEvent` was seen
2. make the helper's docstring state this clearly
3. update docs so the happy path is explicit:
   - turn
   - segment
   - event
   - final result handoff

### Done When

- `_run_stream_segment()` has a crisp, enforced contract
- `run_turn()` can treat segment execution as a trustworthy primitive

## ✓ DONE — Task 7 — Clarify Control-Flow Vocabulary In Docs

### Why

The code becomes easier to maintain when docs use the same mental model:

- turn
- segment
- event
- tool approval decisions

### Files

- `docs/DESIGN-core-loop.md`
- `docs/DESIGN-tools.md`
- `docs/DESIGN-index.md`

### Changes

1. document `run_turn()` as the outer turn loop
2. document `_run_stream_segment()` as the inner segment loop
3. document `agent.run_stream_events(...)` as the event loop
4. keep approval-decision semantics explicit in the approval section

### Done When

- docs and code use the same control-flow vocabulary
- no important orchestration concept depends on reader inference

## ✓ DONE — Task 8 — Keep `run_turn()` As The Single Turn Aggregation Point

### Why

Simplification should not accidentally hollow out the right abstraction boundary.

### Files

- `co_cli/context/_orchestrate.py`
- related docs

### Changes

As part of implementation review, reject refactors that would:

- move retry policy into `_run_stream_segment()`
- move approval policy into `_run_stream_segment()`
- split turn ownership across multiple public entrypoints
- create a generic state-machine framework

### Done When

- `run_turn()` remains the only public owner of a full user turn
- simplification reduces accidental complexity without changing ownership boundaries

## Recommended Implementation Sequence

1. introduce `_TurnState`
2. rename approval-decision state fields
3. rename `_run_stream_turn()` to `_run_stream_segment()` if not already done in the same delivery
4. add the explicit final-result contract to `_run_stream_segment()`
5. extract one segment-execution helper
6. extract only the exception helpers that materially reduce density
7. collapse dead `TurnOutcome` states
8. update docs
9. run full pytest

## Test Plan

Mandatory regression run after code changes:

```bash
mkdir -p .pytest-logs
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-run_turn_simplification.log
```

Targeted areas to watch during implementation:

- approval resume behavior
- reflected HTTP 400 retry behavior
- 429/5xx/network retry behavior
- usage-limit grace summary behavior
- interrupt recovery and dangling tool-call patching
- `co.turn` span attributes

Relevant tests:

- `tests/test_orchestrate.py`
- `tests/test_commands.py`
- `tests/test_tool_approvals.py`
- `tests/test_context_overflow.py`

## Acceptance Criteria

- `run_turn()` remains the single turn-level orchestration entrypoint
- mutable turn state is represented explicitly rather than as many parallel locals
- approval-decision terminology is clear inside the turn state machine
- segment execution duplication is reduced
- major exceptional paths have clearer owners
- dead `TurnOutcome` states are removed
- no behavioral regressions are introduced
- full pytest suite passes and is logged under `.pytest-logs/`

## Delivery Summary — 2026-03-27

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_TurnState` in `_orchestrate.py`, main control flow uses `turn_state.*` | ✓ pass |
| TASK-2 | `tool_approval_decisions` replaces `current_deferred_results` in turn state machine | ✓ pass |
| TASK-3 | `_execute_stream_segment()` extracted; initial and approval-loop calls share one update path | ✓ pass |
| TASK-4 | `_handle_usage_limit_exceeded`, `_handle_http_model_error`, `_handle_model_api_error`, `_build_interrupted_turn_result` extracted | ✓ pass |
| TASK-5 | `TurnOutcome = Literal["continue", "error"]`; dead `"stop"` branch removed from `main.py` | ✓ pass |
| TASK-6 | `_run_stream_segment` (renamed); explicit `RuntimeError` guard when no `AgentRunResultEvent`; type hints added to `usage` and `deferred_tool_results` params | ✓ pass |
| TASK-7 | 10 stale references updated in `DESIGN-core-loop.md`, `DESIGN-tools.md`, `DESIGN-index.md` | ✓ pass |
| TASK-8 | `run_turn()` remains single public owner; no retry/approval policy pushed into helpers; no new public entrypoints | ✓ pass |

**Tests:** full suite — 465 passed, 0 failed
**Independent Review:** 1 blocking (missing type hints on `_run_stream_segment` params) — fixed; 1 minor (pre-existing import path in tests, not introduced by this change)
**Doc Sync:** fixed (10 stale `_run_stream_turn` / `current_deferred_results` / vocab references across 3 DESIGN docs)

**Overall: DELIVERED**
All 8 tasks shipped. `run_turn()` reduced from 264 lines of 9 parallel mutable locals to a readable turn controller backed by `_TurnState`, 4 exception helpers, and `_execute_stream_segment`. Tests at 465/465, no behavioral regressions.

Notable: the TODO missed that `tests/test_capabilities.py` also imported `_run_stream_turn` — found in TL assessment and fixed in TASK-6 scope.

## Addendum — Open Issues

### ✓ RESOLVED — None-as-retry sentinel in exception helpers

`_handle_http_model_error` and `_handle_model_api_error` (two-job helpers returning `TurnResult | None`) were replaced with four single-job helpers:

- `_reflect_http_400` — mutates state for HTTP 400 reflection, `-> None`
- `_apply_http_backoff` — mutates state for 429/5xx backoff, `-> None`
- `_apply_api_backoff` — mutates state for network backoff, `-> None`
- `_build_error_turn_result` — pure terminal `TurnResult` builder, shared by both terminal paths

`run_turn()` except blocks now end with `continue` (retry) or `return` (terminate). No sentinel contract. The `retry_result` intermediate variable is gone; `_span_result` is assigned once, directly before return.

**Resolved 2026-03-28. 465/465 tests pass.**

## Risks

- over-extracting helpers could make the code more abstract instead of clearer
- moving state into a dataclass can accidentally obscure mutation order if helper boundaries are poorly chosen
- collapsing dead outcome states may touch more tests/docs than expected
- renaming orchestration terms and simplifying state in the same delivery can create noisy diffs unless done deliberately

---

## Implementation Review — 2026-03-28

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `_TurnState` in `_orchestrate.py`, main control flow uses `turn_state.*` | ✓ pass | `_orchestrate.py:64-78` — `_TurnState` dataclass; `run_turn():695-699` — initialized from `user_input`, `message_history`, `http_retries` |
| TASK-2 | `tool_approval_decisions` replaces `current_deferred_results` | ✓ pass | `_orchestrate.py:73` — field name `tool_approval_decisions`; SDK name `deferred_tool_results=` only at API boundary line 470 |
| TASK-3 | `_execute_stream_segment()` extracted; initial and approval-loop calls share one update path | ✓ pass | `_orchestrate.py:444-475` — single helper; called at lines 706, 718 (initial + approval resume) |
| TASK-4 | Four exception helpers extracted | ✓ pass | `_handle_usage_limit_exceeded` line 483; `_reflect_http_400`/`_apply_http_backoff`/`_apply_api_backoff`/`_build_error_turn_result` lines 542-617 (None-as-retry addendum resolved) |
| TASK-5 | `TurnOutcome = Literal["continue", "error"]`; dead `"stop"` removed | ✓ pass | `_orchestrate.py:27`; `main.py:245` — only `"error"` branch, no `"stop"` |
| TASK-6 | `_run_stream_segment` renamed; `RuntimeError` guard; type hints on params | ✓ pass | `_orchestrate.py:288-391`; guard at line 387-390; `usage: Any \| None`, `deferred_tool_results: DeferredToolResults \| None` at lines 295-297 |
| TASK-7 | Stale vocab references updated in 3 DESIGN docs | ✓ pass | Confirmed no `_run_stream_turn`/`current_deferred_results` in `DESIGN-core-loop.md`, `DESIGN-tools.md`, `DESIGN-index.md` |
| TASK-8 | `run_turn()` remains single public owner; no retry/approval policy in helpers | ✓ pass | `_run_stream_segment` has no retry/approval logic; `_execute_stream_segment` does not decide policy; `run_turn()` is sole export |
| Addendum | None-as-retry sentinel resolved: explicit `continue`/`return` in except blocks | ✓ pass | `_orchestrate.py:756-775` — `continue` on retry paths, `return _span_result` on terminal paths; `retry_result` variable eliminated |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale `_run_stream_turn()` in Files table | `DESIGN-system.md:455` | blocking | Updated to `_run_stream_segment()` |
| Stale `Q -->|stop| Z` flowchart branch | `DESIGN-core-loop.md:118` | blocking | Removed; `"stop"` is not in `TurnOutcome` |
| Stale note claiming `"stop"` checked by `main.py` | `DESIGN-core-loop.md:124` | blocking | Replaced with accurate note: `TurnOutcome = Literal["continue", "error"]`, both emitted |
| TODO addendum still read "Decision deferred" | `TODO-run-turn-simplification.md` addendum | minor | Updated to ✓ RESOLVED with summary |

### Tests
- Command: `uv run pytest -x`
- Result: 465 passed, 0 failed
- Log: `.pytest-logs/20260328-*-review-impl.log`

### Doc Sync
- Scope: narrow — `DESIGN-system.md` (stale helper name), `DESIGN-core-loop.md` (stale `TurnOutcome` branch)
- Result: fixed (2 files, 3 inaccuracies)

### Behavioral Verification
No user-facing changes — the addendum fix is internal control-flow refactor only. `run_turn()` public signature, `TurnResult` shape, and all retry/approval behaviors are unchanged. Skipped.

### Overall: PASS
All 8 tasks confirmed at file:line. Addendum issue resolved cleanly. Three stale doc inaccuracies auto-fixed. 465/465 tests green. Ship.
