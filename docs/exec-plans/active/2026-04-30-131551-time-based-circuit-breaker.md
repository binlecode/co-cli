# Plan: Time-based Circuit-Breaker Cooldown for Context Compaction

## Context
co-cli currently uses a turn-based counter (`compaction_skip_count`) to implement a circuit breaker for summarization failures. When the summarizer fails, it trips after 3 failures and then probes every 10 skips. However, if provider issues are intermittent, a string of failures could make probes sparse (`count % 10 == 0`), leading to prolonged periods without compaction even if the provider has recovered. Hermes-agent uses a time-based deterministic cooldown (`_summary_failure_cooldown_until`).

## Problem & Outcome
**Problem:** The turn-based probe interval makes recovery from intermittent provider failures unpredictable and potentially slow, as it depends on the user continuing to interact with the system (turns) rather than elapsed time.
**Failure cost:** Context compaction may remain disabled for an unnecessarily long time after the provider recovers, leading to bloated context and potential overflow errors, degrading the user experience or causing session loss.

## Scope
- Introduce a time-based cooldown mechanism for the compaction circuit breaker.
- Replace or augment the turn-based probe mechanism with a deterministic time-bound check.
- Retain the static marker fallback during the cooldown period.

## Behavioral Constraints
- Cooldown should trigger after a predefined number of consecutive failures (e.g., 3).
- The cooldown period should be configurable (e.g., 60 seconds).
- After the cooldown expires, the next compaction attempt should probe the LLM.
- If the probe succeeds, the circuit breaker resets.
- If the probe fails, the cooldown starts again (potentially with backoff, but fixed is acceptable for V1 parity).

## High-Level Design
1. Update `RuntimeState` (in `co_cli/deps.py`) to replace or augment `compaction_skip_count` with:
   - `compaction_failure_count: int` (consecutive failures)
   - `compaction_cooldown_until: float | None` (timestamp when cooldown expires)
2. Update `_summarization_gate_open` and related logic in `co_cli/context/compaction.py`:
   - Check if `time.time() < compaction_cooldown_until`. If so, skip LLM and return static marker.
   - If cooldown expired or not set, allow LLM call.
3. Update `apply_compaction` / `_run_window_compaction` error handling:
   - On success: reset `compaction_failure_count` and `compaction_cooldown_until`.
   - On failure: increment `compaction_failure_count`. If `compaction_failure_count >= 3`, set `compaction_cooldown_until = time.time() + COOLDOWN_SECONDS`.
4. Update configuration `CompactionConfig` in `co_cli/config/_compaction.py` to add `circuit_breaker_cooldown_secs: int = 60`.

## Implementation Plan

### TASK-1: Configuration Update
**files:** 
- `co_cli/config/_compaction.py`
**done_when:** `CompactionConfig` includes `circuit_breaker_cooldown_secs: int = 60`.

### TASK-2: Runtime State Update
**files:** 
- `co_cli/deps.py`
- `co_cli/commands/core.py`
**done_when:** `RuntimeState` includes `compaction_cooldown_until: float | None = None` and `compaction_failure_count: int = 0`. Command handlers `/new` and `/clear` correctly reset both fields.

### TASK-3: Circuit Breaker Logic
**files:** 
- `co_cli/context/compaction.py`
**done_when:** `_summarization_gate_open` uses `time.time()` against `compaction_cooldown_until` to determine if the gate is open. Tested via TASK-5.

### TASK-4: Error Handling and State Management
**files:** 
- `co_cli/context/compaction.py`
**done_when:** Exception handling in `_run_window_compaction` sets `compaction_cooldown_until` when `compaction_failure_count` reaches the threshold (3), and successful summarization resets both.

### TASK-5: Tests
**files:** 
- `tests/context/test_context_compaction.py`
**done_when:** `pytest tests/context/test_context_compaction.py` passes, proving the time-based cooldown blocks calls during the window and allows a probe after it expires.

## Testing
- Unit tests mocking `time.time()` to verify state transitions (trip -> cooldown -> probe -> reset/re-trip).
- Verify static marker is still generated during cooldown.

## Open Questions
- Should we implement exponential backoff for the cooldown? (Decision: No, stick to fixed 60s for simplicity and parity with hermes-agent for now).

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev time-based-circuit-breaker`
