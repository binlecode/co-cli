# Plan: Simplify Compaction Backoff Logic

## Context
After 3 consecutive summarization failures, co-cli blocks LLM calls and falls back to static marker. Every 10th blocked call, it lets one LLM call through to test if the provider is back. This is implemented via a separate `_circuit_breaker_should_skip` function with hardcoded magic numbers 3 and 10.

## Problem & Outcome
**Problem:** The logic is over-engineered — a named helper function wrapping two lines of arithmetic, with two magic numbers buried in the body.
**Fix:** Inline the check, name the constants.

## Scope
- Remove `_circuit_breaker_should_skip`.
- Inline the backoff check in `_summarization_gate_open`.
- Name the two magic numbers as module-level constants.
- No behavior change.

## Implementation Plan

### ✓ DONE — TASK-1: Simplify
**files:**
- `co_cli/context/compaction.py`

Replace:
```python
_CIRCUIT_BREAKER_PROBE_EVERY: int = 10

def _circuit_breaker_should_skip(skip_count: int) -> bool:
    if skip_count < 3:
        return False
    skips_since_trip = skip_count - 3
    return skips_since_trip == 0 or skips_since_trip % _CIRCUIT_BREAKER_PROBE_EVERY != 0
```

With:
```python
_COMPACTION_BREAKER_TRIP: int = 3
_COMPACTION_BREAKER_PROBE_EVERY: int = 10
```

And in `_summarization_gate_open`, replace the `_circuit_breaker_should_skip(count)` call with:
```python
# skips_since_trip == 0 blocks the first trip; subsequent probes fire every N skips
skips_since_trip = count - _COMPACTION_BREAKER_TRIP
if count >= _COMPACTION_BREAKER_TRIP and (skips_since_trip == 0 or skips_since_trip % _COMPACTION_BREAKER_PROBE_EVERY != 0):
```

**done_when:** `_circuit_breaker_should_skip` is gone, logic is inlined, both constants are named.

### ✓ DONE — TASK-2: Tests
**files:**
- `tests/test_flow_compaction_proactive.py`

Add tests covering:
- `skip_count` 0–2: gate open
- `skip_count` 3–12: gate closed
- `skip_count` 13: gate open (first probe)
- `skip_count` 14–22: gate closed
- `skip_count` 23: gate open (second probe)

**done_when:** `pytest tests/test_flow_compaction_proactive.py -x` passes.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev time-based-circuit-breaker`

## Delivery Summary — 2026-04-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_circuit_breaker_should_skip` is gone, logic is inlined, both constants are named | ✓ pass |
| TASK-2 | `pytest tests/test_flow_compaction_proactive.py -x` passes | ✓ pass |

**Tests:** scoped (touched files) — 27 passed, 0 failed
**Doc Sync:** narrow scope — no public API changes; no spec updates needed

**Overall: DELIVERED**
Removed `_circuit_breaker_should_skip`, named both magic numbers as `_COMPACTION_BREAKER_TRIP` and `_COMPACTION_BREAKER_PROBE_EVERY`, inlined the gate logic with a clarifying comment, and added 24 parametrized circuit breaker tests covering all probe cadence boundaries.

## Implementation Review — 2026-04-30

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `_circuit_breaker_should_skip` is gone, logic is inlined, both constants are named | ✓ pass | compaction.py:91-98 — two named constants; compaction.py:115-118 — inlined gate logic; no remaining references to old name |
| TASK-2 | `pytest tests/test_flow_compaction_proactive.py -x` passes | ✓ pass | test_flow_compaction_proactive.py:171-234 — 5 test functions covering all 7 probe cadence boundaries (counts 0-2, 3-12, 13, 14-22, 23) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Module docstring listed only 3 tests; 5 new test functions uncatalogued | test_flow_compaction_proactive.py:1-9 | minor | Updated docstring to include Tests 4-8 entry |
| Spec table used old constant name `_CIRCUIT_BREAKER_PROBE_EVERY`; trip threshold `3` unnamed | docs/specs/compaction.md:386,496 | minor | Updated to `_COMPACTION_BREAKER_PROBE_EVERY`; added `_COMPACTION_BREAKER_TRIP` row |
| core-loop.md referenced `_CIRCUIT_BREAKER_PROBE_EVERY` | docs/specs/core-loop.md:282 | minor | Updated to `_COMPACTION_BREAKER_TRIP` and `_COMPACTION_BREAKER_PROBE_EVERY` |

### Tests
- Command: `uv run pytest -v`
- Result: 110 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: narrow — all code changes confined to `co_cli/context/compaction.py`; spec updates were constant renames only
- Result: fixed — updated `_CIRCUIT_BREAKER_PROBE_EVERY` → `_COMPACTION_BREAKER_PROBE_EVERY` in compaction.md (×2) and core-loop.md (×1); added missing `_COMPACTION_BREAKER_TRIP` row to constants table

### Behavioral Verification
No user-facing changes — circuit breaker is a purely internal mechanism with no visible output change. CLI starts clean (`uv run co --help` confirms startup). Skipped chat/trace verification.

### Overall: PASS
Logic equivalence confirmed at compaction.py:115-118; all 110 tests green; stale constant names in two specs fixed.
