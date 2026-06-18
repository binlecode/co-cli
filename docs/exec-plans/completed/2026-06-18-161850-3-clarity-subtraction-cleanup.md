# Clarity-by-Subtraction Cleanup Batch (R1/R3, R11, R9)

**Slug:** `clarity-subtraction-cleanup` · **Created:** 2026-06-18 16:18:50

## Context

Promoted from the `rules-conformance-cleanup` deferred backlog. Three independent,
behavior-preserving cleanups that share one review posture (no behavior change +
green suite), drawn from `.agent_docs/review.md` "Clarity by Subtraction" and
`code-conventions.md`. Each was read-confirmed by the audit (the DaemonState
removals additionally survived a blind cold-read refutation pass).

## Tasks

### ✓ DONE TASK-1 — Strip write-only `DaemonState` fields (R1)
`DaemonState.start_time` (`state.py:23`), `spawn_origin` (`:24`),
`spawn_session_id` (`:25`) are write-only — set at construction
(`daemons/dream/process.py:209-211`), never read; the status JSON sources these
from the PID file (`pid_data`), not the instance. Remove the three fields and their
constructor assignments; update the 3 test `_make_state()` helpers.
Keep `current_item` (the live in-flight carrier). Whole-class removal is a
behavior change, NOT in scope (R3 collapse deferred — needs a design call on
whether `current_item` should be surfaced in status).
- **files:** `co_cli/daemons/dream/state.py`, `co_cli/daemons/dream/process.py`, the 3 test files constructing `DaemonState` (grep `_make_state`/`DaemonState(`)
- **done_when:** the three fields are gone; full suite green; no behavior change

### ✓ DONE TASK-2 — Collapse `_split_frontmatter_raw` duplication (R11)
`daemons/dream/_housekeeping.py:317` reimplements frontmatter-delimiter splitting
that `memory/frontmatter.py` already owns (same file already imports
`parse_frontmatter`). Add a `split_frontmatter_raw(text) -> (raw, body)` helper to
the shared `memory/frontmatter.py` primitive; delete the private dream copy and
import the shared one.
- **files:** `co_cli/memory/frontmatter.py`, `co_cli/daemons/dream/_housekeeping.py`
- **done_when:** `_split_frontmatter_raw` no longer defined in `_housekeeping.py`; the shared helper is used; full suite green; no behavior change

### ✓ DONE TASK-3 — Rename `src_` abbreviation to `source_` (R9)
`index/_retrieval.py:142,322,398` use locals `src_sql`/`src_params`; `code-conventions.md:26`
forbids the domain abbreviation (`source_` not `src_`). Rename at all three sites.
- **files:** `co_cli/index/_retrieval.py`
- **done_when:** no `src_sql`/`src_params` identifier remains in the file; full suite green; no behavior change

## Verification
`scripts/quality-gate.sh full`. Each task is independent — a block on one does not
gate the others.

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | three `DaemonState` fields gone; no behavior change | ✓ pass |
| TASK-2 | `_split_frontmatter_raw` no longer in `_housekeeping.py`; shared helper used | ✓ pass |
| TASK-3 | no `src_sql`/`src_params` identifier remains in `_retrieval.py` | ✓ pass |

**Tests:** scoped — 53 passed, 0 failed (dream daemon, housekeeping, retrieval, index)
**Doc Sync:** clean — no spec references the removed internals; `dream.md:496` documents `co dream status` JSON sourced from the PID file (`pid_data`), which is unchanged.

**Notes:**
- TASK-1: removing `start_time=time.time()` orphaned `import time` in `test_override_snapshot.py` and `test_loop.py` — both removed (dead code created by the change). `process.py` and `test_timeout_retry.py` retain `time` (still used).
- TASK-2: shared `split_frontmatter_raw` carries a docstring noting its strict `---\n` delimiter contract must not be unified with `parse_frontmatter`'s regex (per Gate 1 review).

**Overall: DELIVERED**
All three behavior-preserving cleanups landed; lint clean, scoped tests green. Ready to bundle-ship with plans #2/#4.

## Implementation Review — 2026-06-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | three `DaemonState` fields gone; no behavior change | ✓ pass | `state.py:23` — only `current_item` remains; `process.py:208` constructs bare `DaemonState()`. Grep confirms `start_time`/`spawn_origin`/`spawn_session_id` no longer reference the instance. `process.py:165-166` status keys source from `pid_data` (PID file), unchanged — exactly as scoped. |
| TASK-2 | `_split_frontmatter_raw` no longer in `_housekeeping.py`; shared helper used | ✓ pass | `frontmatter.py:49` defines `split_frontmatter_raw`; `_housekeeping.py:28` imports it, `:311` calls it. Deleted dream copy is byte-identical to the moved body — behavior preserved. Underscore correctly dropped (cross-package import). |
| TASK-3 | no `src_sql`/`src_params` identifier remains in `_retrieval.py` | ✓ pass | Grep for `src_sql`/`src_params`/`src_` in `_retrieval.py` returns nothing; all 3 sites renamed to `source_sql`/`source_params`. |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest tests/daemons/dream/ tests/index/ tests/test_retrieval_degradation.py`
- Result: 72 passed, 0 failed (full plan surface — dream daemon incl. `test_process.py` status keys, index, retrieval)
- Log: `.pytest-logs/*-review-impl.log`
- Note: working tree also carries sibling plans #2/#4; their files are out of this plan's scope. `scripts/quality-gate.sh full` is the bundle-ship safety net.

### Behavioral Verification
No user-facing changes — pure internal refactor (write-only field removal, shared-helper move, local-variable rename). `uv run co --help`: ✓ boots (import + bootstrap graph loads). No `success_signal` declared.

### Overall: PASS
Three behavior-preserving cleanups verified at source with file:line evidence; deleted helper byte-identical to its shared replacement; lint clean, plan surface green. Ship.
