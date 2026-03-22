# TODO — Core Loop Idiomatic Alignment

This delivery targets concrete anti-pattern drift and unnecessary complexity in the core loop. It does not redesign co-cli around a new runtime model. The goal is to keep the current product behavior while tightening ownership boundaries, reducing state-machine fragility, and staying closer to pydantic-ai idioms where that improves correctness and maintainability.

## Problem Summary

The deep code scan found that the system is still broadly pydantic-ai idiomatic, but the orchestration layer has accumulated avoidable complexity:

- `run_turn()` in `co_cli/context/_orchestrate.py` owns too many state transitions at once: stream driving, deferred approval resume, provider retry policy, grace-turn fallback, interrupt repair, and user-facing output coordination.
- the turn state is split across parallel locals (`message_history` vs `current_history`, `current_input` vs `current_deferred_results`, local `turn_usage` vs `deps.runtime.turn_usage` elsewhere), which is correct today but easy to break
- `TurnOutcome` contains reserved variants (`"stop"`, `"compact"`) that are not emitted by the current implementation
- post-turn lifecycle remains split between `main.py`, `_orchestrate.py`, and `_history.py`, which is acceptable for a CLI but should be made more explicit and less ad hoc
- the old DESIGN doc drifted from code, which is a symptom that the flow is harder to reason about than it should be

The right response is not “replace pydantic-ai abstractions with custom ones.” The right response is to simplify local orchestration code until the remaining custom behavior is clearly justified.

## Non-Goals

- no rewrite of the REPL
- no redesign of shell approval from command-scoped classification to blanket tool-level approval
- no new framework layer over pydantic-ai
- no migration of slash commands into agent tools
- no speculative workflow engine or generalized turn graph

## Task 1 — Collapse Dead `TurnOutcome` States

### Why

`TurnOutcome = Literal["continue", "stop", "error", "compact"]` in `co_cli/context/_orchestrate.py`, but the current implementation only returns `"continue"` and `"error"`. `main.py` still branches on `"stop"`, which creates false API surface area and invites future confusion.

### Files

- `co_cli/context/_orchestrate.py`
- `co_cli/main.py`
- `docs/DESIGN-core-loop.md`
- tests that assert or mention `TurnOutcome`

### Changes

1. reduce `TurnOutcome` to the variants actually emitted today
2. update `TurnResult` docs and comments to match
3. remove dead `"stop"` handling from `main.py` unless a real stop-producing path is introduced in the same change
4. remove any stale test expectations or docs that describe reserved turn outcomes as active behavior

### Done When

- `TurnOutcome` exactly matches emitted states
- no dead branches remain in `main.py`
- docs describe only live behavior

## Task 2 — Introduce An Explicit `TurnState` Dataclass Inside `_orchestrate.py`

### Why

`run_turn()` currently coordinates multiple locals whose relationships matter:

- `current_input`
- `current_history`
- `current_deferred_results`
- `turn_usage`
- `http_retries_left`
- `backoff_base`
- `result`
- `streamed_text`

This is not broken, but it is state-machine code written as loosely related locals. The reflected-400 path already depends on mutating `current_history` rather than `message_history`, which is exactly the kind of detail that gets regressed later.

### Files

- `co_cli/context/_orchestrate.py`
- tests covering retries, approvals, interrupts
- `docs/DESIGN-core-loop.md`

### Changes

1. add a private `@dataclass` such as `_TurnState` in `_orchestrate.py`
2. move retry-loop mutable state into that dataclass
3. make approval resume and HTTP-400 reflection update named fields on `_TurnState` rather than free locals
4. keep the structure private to `_orchestrate.py`; do not export a new public abstraction
5. preserve current behavior exactly while making transitions explicit

### Guidance

- do not invent a generic orchestration framework
- do not split logic across many helper objects
- prefer one private state carrier plus a few small helper functions

### Done When

- `run_turn()` no longer coordinates parallel mutable locals for core state
- approval resume, retry reflection, and interrupt recovery operate on one explicit state object
- control flow is easier to audit linearly

## Task 3 — Unify Turn Usage Ownership

### Why

The main turn loop tracks usage in a local `turn_usage` variable, while delegation tools also write to `deps.runtime.turn_usage`. That creates two partial usage models with no explicit contract for which one is authoritative.

### Files

- `co_cli/context/_orchestrate.py`
- `co_cli/tools/delegation.py`
- any consumers of `deps.runtime.turn_usage`
- `docs/DESIGN-core-loop.md`

### Changes

1. decide whether turn usage is:
   - local to `run_turn()`, or
   - a runtime-owned accumulator on `deps.runtime`
2. remove the duplicate path
3. if `deps.runtime.turn_usage` remains, initialize and reset it explicitly at turn boundaries in `_orchestrate.py`
4. if local ownership remains, delegation tools should return usage through explicit call contracts rather than mutating runtime state

### Recommendation

Prefer runtime-owned usage if there is real cross-tool accumulation that must survive nested tool execution. Otherwise keep it local and remove runtime mutation. Do not keep both.

### Done When

- exactly one source of truth exists for per-turn usage accounting
- delegation tools and `run_turn()` follow the same contract
- context-ratio warnings read from the chosen source consistently

## Task 4 — Extract Small Transition Helpers From `run_turn()`

### Why

The current `run_turn()` function is doing several distinct jobs in one block:

- initial stream segment execution
- deferred approval collection and resume
- grace-turn fallback
- provider error classification and retry policy
- interrupt recovery
- final output / status handling

This is acceptable up to a point, but it is now dense enough that correctness depends on careful rereading of a long function.

### Files

- `co_cli/context/_orchestrate.py`
- tests around approval loops, retries, interrupts

### Changes

Refactor `run_turn()` into a few private helpers with narrow contracts. Example split:

1. `_run_stream_segment(...)`
   - wraps the `_run_stream_turn()` invocation and state updates for `result` / usage
2. `_resume_after_approvals(...)`
   - applies `_collect_deferred_tool_approvals()`, updates state, and runs one resume segment
3. `_handle_usage_limit_exceeded(...)`
   - owns grace-turn logic
4. `_handle_model_http_error(...)`
   - owns reflection/backoff/abort classification
5. `_handle_model_api_error(...)`
   - owns network retry/backoff classification
6. `_build_interrupted_turn_result(...)`
   - owns dangling-call patching and abort-marker append

### Guidance

- helpers should operate on the private `TurnState`, not create a second abstraction tree
- do not move user-facing REPL responsibilities out of `main.py`
- do not push retry logic into tools

### Done When

- `run_turn()` reads as a short state-machine driver
- each exceptional path has one clear owner
- behavior remains byte-for-byte equivalent for approvals, retries, and interrupts

## Task 5 — Make Post-Turn Ownership Explicit

### Why

The current split is valid but implicit:

- `_orchestrate.py` owns in-turn execution
- `main.py` owns post-turn env cleanup, signal detection, session save, and background compaction scheduling
- `_history.py` owns later compaction consumption

This should remain split, but the code should make the boundary more deliberate and less incidental.

### Files

- `co_cli/main.py`
- `co_cli/context/_orchestrate.py`
- `co_cli/context/_history.py`
- `docs/DESIGN-core-loop.md`

### Changes

1. add one private helper in `main.py` for post-turn finalization, for example `_finalize_turn(...)`
2. move this logic into that helper without changing behavior:
   - message history replacement
   - env restore and skill-state clearing
   - signal detection gate
   - `precomputed_compaction` clearing
   - session touch/save
   - background compaction spawn
   - outcome banner handling
3. document that `_orchestrate.py` returns a `TurnResult` and never performs post-turn persistence itself

### Done When

- post-turn work is visually grouped in one place in `main.py`
- the boundary between orchestration and REPL lifecycle is explicit
- docs and code use the same vocabulary for that split

## Task 6 — Tighten Approval-Subject Legibility

### Why

The approval system is conceptually sound and idiomatic for pydantic-ai, but the current clarity depends on readers inferring too much from `_tool_approvals.py`. Since approvals are a safety boundary, the code should make the scope model obvious.

### Files

- `co_cli/tools/_tool_approvals.py`
- `co_cli/commands/_commands.py`
- `docs/DESIGN-core-loop.md`

### Changes

1. keep the unified `ApprovalSubject` model
2. add short, precise docstrings/comments where helpful:
   - why generic `tool` subjects are not rememberable
   - why shell is utility-scoped rather than full-command-scoped
   - why file approvals are scoped by `tool_name + parent_dir`
3. ensure `/approvals list` language mirrors the actual scope model
4. keep session-only semantics explicit; do not reintroduce persistent approval storage

### Done When

- approval scope is obvious from code and `/approvals` UI
- there is no implication of blanket tool auto-approval where the code is subject-scoped

## Task 7 — Add Regression Tests For The Simplified Contracts

### Why

If the goal is to reduce anti-pattern drift, the refactor must lock in behavior. The main risks are approval resume, retry-history mutation, and interrupt recovery.

### Files

- relevant functional tests in `tests/`

### Coverage To Add Or Update

1. reflected HTTP 400 retries use the mutated `current_history` path, not stale original history
2. approval resume stays in the same turn and uses `user_input=None`
3. `"a"` stores a scoped session rule only when the subject is rememberable
4. shell `DENY` bypasses deferred approvals completely
5. interrupted turns patch dangling tool calls and append the abort marker
6. post-turn finalization still clears skill env and `precomputed_compaction`
7. no tests reference removed `TurnOutcome` variants after Task 1

### Done When

- the simplified architecture is protected by tests at the behavior boundary
- future doc drift is less likely because behavior is asserted concretely

## Task 8 — Keep The DESIGN Doc Minimal And Post-Implementation

### Why

The previous doc became noisy because it duplicated logic in multiple narrative forms. That is a documentation smell usually caused by code paths that are hard to explain simply.

### Files

- `docs/DESIGN-core-loop.md`

### Changes

1. keep the rewritten short design doc structure
2. after each code task above lands, update only:
   - the diagrams
   - the explicit state/ownership statements
   - the config or file tables if needed
3. do not re-expand the doc with repeated prose explanations of the same control flow

### Done When

- the doc stays concise
- every diagram node maps directly to a real code transition
- no duplicated sections describe the same approval or retry path twice

## Delivery Order

1. Task 1 — collapse dead `TurnOutcome` states
2. Task 2 — introduce `_TurnState`
3. Task 4 — extract small transition helpers from `run_turn()`
4. Task 3 — unify turn usage ownership
5. Task 5 — make post-turn ownership explicit
6. Task 6 — tighten approval-subject legibility
7. Task 7 — add/update regression tests
8. Task 8 — final doc sync

## Acceptance Criteria

- the core loop remains behaviorally identical from the user’s perspective
- `_orchestrate.py` is easier to audit as a state machine
- there is one clear owner for turn usage
- dead outcome states are removed
- post-turn lifecycle ownership is explicit
- approval scope remains subject-based and legible
- docs and tests match the live code without reserved or speculative behavior
