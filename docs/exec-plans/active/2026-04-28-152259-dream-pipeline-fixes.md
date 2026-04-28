# Plan: Dream Pipeline Fixes

**Task type:** refactor — dead code removal and comment corrections. No user-visible behavior change.

## Context

Code review of `co_cli/memory/dream.py` and `co_cli/main.py` surfaced three issues:

1. Dead outer timeout and unreachable exception handler in `_maybe_run_dream_cycle` (`main.py:176-186`).
2. Misleading inline comment on `saves_so_far = 0` initialization (`dream.py:201-202`).
3. Misleading docstring on `build_dream_miner_agent` (`dream.py:121`).

No existing exec-plan for this slug. No stale TODO hygiene issues.

## Problem & Outcome

**Problem:** `_maybe_run_dream_cycle` wraps `run_dream_cycle` in a redundant `async with asyncio.timeout(60)` block. `run_dream_cycle` already catches its own `TimeoutError` internally and always returns `DreamResult` — it never re-raises. As a result: (a) the outer `asyncio.timeout(60)` context manager never fires, (b) the `except TimeoutError` clause on line 185 is unreachable dead code. The function docstring also claims "Bounded by a 60-second timeout." which is additionally stale once the outer timeout is removed.

**Failure cost:** No runtime failure, but the dead `except TimeoutError` silently misleads readers into believing timeout escalation is handled there. Any future refactor of `run_dream_cycle`'s internal timeout handling (e.g. bubbling `TimeoutError`) would hit the wrong handler and produce misleading log messages.

**Outcome:** Dead code removed; stale docstring and comment inaccuracies corrected. `_maybe_run_dream_cycle` is simpler and correct; `dream.py` comments match the actual logic.

## Scope

In scope:
- Remove `async with asyncio.timeout(60)` and `except TimeoutError` from `_maybe_run_dream_cycle` in `co_cli/main.py`.
- Update the `_maybe_run_dream_cycle` docstring to drop the "Bounded by a 60-second timeout." sentence.
- Fix the `saves_so_far = 0` initialization comment in `co_cli/memory/dream.py`.
- Fix the `build_dream_miner_agent` docstring in `co_cli/memory/dream.py`.

Out of scope:
- Any changes to `run_dream_cycle` internal timeout logic.
- Any behavioral changes to the dream cycle.
- Any spec doc updates.

## Behavioral Constraints

- `_maybe_run_dream_cycle` must still catch all exceptions from `run_dream_cycle` via `except Exception` and log without propagating — session shutdown must never fail due to a dream cycle error.
- `run_dream_cycle` timeout semantics are unchanged: internal `asyncio.timeout(timeout_secs)` still bounds the cycle; `DreamResult.timed_out` is still set and returned on timeout.
- No change to any function signature, return type, or public interface.

## High-Level Design

**TASK-1** (`main.py`): Strip the outer `async with asyncio.timeout(60)` wrapper and the unreachable `except TimeoutError` clause from `_maybe_run_dream_cycle`. Update the docstring to drop the "Bounded by a 60-second timeout." sentence (second sentence about errors/propagation is accurate and stays). Keep the `except Exception` guard and all logging unchanged.

Before:
```python
async def _maybe_run_dream_cycle(deps: CoDeps) -> None:
    """Run the dream cycle on session end when enabled via knowledge config.

    Bounded by a 60-second timeout. Errors are logged and never propagated —
    session shutdown must not fail because consolidation hit a snag.
    """
    ...
    try:
        async with asyncio.timeout(60):
            result = await run_dream_cycle(deps)
        if result.any_changes:
            logger.info(...)
    except TimeoutError:
        logger.warning("Dream cycle timed out after 60s")
    except Exception:
        logger.warning("Dream cycle failed", exc_info=True)
```

After:
```python
async def _maybe_run_dream_cycle(deps: CoDeps) -> None:
    """Run the dream cycle on session end when enabled via knowledge config.

    Errors are logged and never propagated — session shutdown must not fail
    because consolidation hit a snag.
    """
    ...
    try:
        result = await run_dream_cycle(deps)
        if result.any_changes:
            logger.info(...)
    except Exception:
        logger.warning("Dream cycle failed", exc_info=True)
```

Timeout is fully handled inside `run_dream_cycle`; the wrapper only needs to catch panics.

**TASK-2** (`dream.py`): Two comment fixes, no logic change:

1. `saves_so_far = 0` comment (line 201): current comment claims this covers a "zero-chunk case" that is structurally impossible — `window` is verified non-empty by the `if not window.strip(): continue` guard above, and `_chunk_dream_window` always returns at least one element. Replace with accurate comment: defensive initialization so `saves_so_far` is bound even if the agent raises on the very first chunk iteration.

2. `build_dream_miner_agent` docstring (line 121): "Hoist outside the chunk loop; call .run() per chunk" is misleading — the caller instantiates the agent per session (inside the session loop), not per cycle. Update to reflect actual usage: one agent per session, `.run()` called per chunk.

## Implementation Plan

### ✓ DONE — TASK-1: Remove dead timeout wrapper and fix stale docstring in `_maybe_run_dream_cycle`

```
files: co_cli/main.py
done_when: |
  grep -n "asyncio.timeout" co_cli/main.py shows no match in _maybe_run_dream_cycle;
  grep -n "except TimeoutError" co_cli/main.py shows no match in _maybe_run_dream_cycle;
  grep -n "Bounded by a 60-second timeout" co_cli/main.py shows no match
success_signal: N/A
prerequisites: none
```

### ✓ DONE — TASK-2: Fix misleading comments in `dream.py`

```
files: co_cli/memory/dream.py
done_when: |
  grep -n "zero-chunk case" co_cli/memory/dream.py shows no match;
  grep -n "Hoist outside the chunk loop" co_cli/memory/dream.py shows no match;
  uv run pytest tests/memory/test_knowledge_dream.py tests/memory/test_knowledge_dream_cycle.py -x -q passes
success_signal: N/A
prerequisites: none
```

## Testing

- `tests/memory/test_knowledge_dream.py` — state load/save and corrupt-state recovery
- `tests/memory/test_knowledge_dream_cycle.py` — cycle orchestration, dry-run, timeout
- `scripts/quality-gate.sh lint` — ruff passes on both changed files

No new tests required: TASK-1 removes dead code with no new behavior; TASK-2 is comment-only.

## Open Questions

None.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev dream-pipeline-fixes`

## Delivery Summary — 2026-04-28

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | no `asyncio.timeout` / `except TimeoutError` / "Bounded by a 60-second timeout" in `_maybe_run_dream_cycle` | ✓ pass |
| TASK-2 | no "zero-chunk case" / "Hoist outside the chunk loop" in `dream.py`; dream tests pass | ✓ pass |

**Tests:** scoped (`tests/memory/test_knowledge_dream.py`, `tests/memory/test_knowledge_dream_cycle.py`) — 12 passed, 0 failed
**Doc Sync:** fixed — `dream.md`: 6 fixes (`knowledge_save` → `memory_create` ×4, outer timeout claim removed from §2.1 and §2.7); `memory.md`: 3 fixes (expanded scope — confirmed `knowledge_save` → `memory_create` API rename)

**Overall: DELIVERED**
Dead outer timeout wrapper and unreachable `except TimeoutError` removed from `_maybe_run_dream_cycle`; stale docstring and both `dream.py` comments corrected; spec updated to match.
