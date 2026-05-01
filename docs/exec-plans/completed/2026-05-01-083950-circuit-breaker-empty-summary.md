# Plan: Fix Circuit Breaker Silent Failure on Empty LLM Summary

## Context

The compaction circuit breaker trips after `_COMPACTION_BREAKER_TRIP` (3) consecutive
summarization failures and blocks further LLM calls until a probe succeeds.
`compaction_skip_count` is the counter driving this: it increments on failure and resets
to 0 on success.

"Success" is currently defined as: `summarize_dropped_messages` returned without raising.
But an LLM can return an empty string without raising — a silent failure. In that case:

1. `_gated_summarize_or_none` resets `compaction_skip_count = 0` (line 188) — treating it as success.
2. Returns the empty `summary_text` to `apply_compaction`.
3. `apply_compaction` detects `not _is_valid_summary(summary_text)` and downgrades to static marker (lines 244-246).
4. The counter stays at 0 — the failure was never counted.

A model that consistently returns empty summaries will never trip the breaker.
The circuit breaker only engages for exception-raising failures.

## Problem & Outcome

**Problem:** `_gated_summarize_or_none` defines "success" as no exception, rather than a
valid (non-empty) summary. Empty-string returns silently reset the counter.

**Fix:** Move the validity check into `_gated_summarize_or_none`, before the counter reset.
If the LLM returns an empty/whitespace string, treat it as a failure: increment the counter,
return None. Remove the now-dead validity check from `apply_compaction`.

## Scope

- Fix `_gated_summarize_or_none` in `co_cli/context/compaction.py`.
- Remove dead code (empty-summary check) from `apply_compaction`.
- Update the function docstring to reflect the new contract.
- Add a test asserting `compaction_skip_count` resets to 0 after a successful (non-empty) real LLM compaction.
- No behavior change on the happy path; no behavior change when LLM raises.

## Implementation Plan

### ✓ DONE — TASK-1: Fix `_gated_summarize_or_none`

**files:**
- `co_cli/context/compaction.py`

Replace the success path in `_gated_summarize_or_none` (after the try/except, currently
lines 188-189):

```python
    # current (wrong):
    ctx.deps.runtime.compaction_skip_count = 0
    return summary_text
```

With:

```python
    if not _is_valid_summary(summary_text):
        log.warning(
            "Compaction summarizer returned empty output — counting as failure (count=%d)",
            ctx.deps.runtime.compaction_skip_count + 1,
        )
        ctx.deps.runtime.compaction_skip_count += 1
        return None
    ctx.deps.runtime.compaction_skip_count = 0
    return summary_text
```

Update the docstring to: "... the success reset of `compaction_skip_count` on a valid
(non-empty) summary, and the fall-through-to-static-marker path when the summarizer raises
or returns empty."

Then in `apply_compaction`, remove the now-dead block (currently lines 244-246):

```python
    # remove this — unreachable: _gated_summarize_or_none now only returns valid strings or None
    if summary_text is not None and not _is_valid_summary(summary_text):
        log.warning("Compaction summarizer returned empty output; downgrading to static marker.")
        summary_text = None
```

**done_when:** `grep -n "_is_valid_summary" co_cli/context/compaction.py` shows the check
inside `_gated_summarize_or_none` (line ~188) and NOT inside `apply_compaction`.

### ✓ DONE — TASK-2: Regression test — counter resets to 0 after valid compaction

**files:**
- `tests/test_flow_compaction_proactive.py`

Add one test to `test_flow_compaction_proactive.py` that runs a full above-threshold
compaction with a real LLM and asserts `compaction_skip_count == 0` after success.
This pins the happy-path counter reset so a future fix cannot accidentally leave
the counter non-zero after a valid compaction.

```python
@pytest.mark.asyncio
async def test_successful_compaction_resets_skip_count() -> None:
    """compaction_skip_count resets to 0 after a successful (non-empty) LLM summary.

    Deletion regression: would not detect a future change that leaves the counter
    non-zero after a successful compaction, silently degrading circuit breaker
    accuracy.
    """
    settings = _tight_settings()
    model = build_model(settings.llm)
    deps = CoDeps(
        shell=ShellBackend(),
        model=model,
        config=settings,
        session=CoSessionState(),
    )
    deps.runtime.compaction_skip_count = 2  # pre-trip warm count
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    messages = _above_threshold_messages()

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await proactive_window_processor(ctx, messages)

    assert result is not messages
    assert deps.runtime.compaction_skip_count == 0, (
        "skip_count must reset to 0 after a successful summarization"
    )
```

**Note on test coverage gap:** The empty-string failure path in `_gated_summarize_or_none`
(LLM returns `""` without raising) cannot be reliably exercised with a real Ollama model.
Testing it requires either a fake model (violates no-mocks policy) or a real LLM known to
return empty (non-deterministic). The fix is verified structurally (TASK-1 grep) and
behaviorally on the happy path (TASK-2 test). The failure path is covered by code inspection.

**done_when:** `pytest tests/test_flow_compaction_proactive.py -x` passes including the
new `test_successful_compaction_resets_skip_count` test.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev circuit-breaker-empty-summary`

## Delivery Summary — 2026-05-01

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep -n "_is_valid_summary" co_cli/context/compaction.py` shows check inside `_gated_summarize_or_none` and NOT inside `apply_compaction` | ✓ pass |
| TASK-2 | `pytest tests/test_flow_compaction_proactive.py -x` passes including `test_successful_compaction_resets_skip_count` | ✓ pass |

**Tests:** scoped (`tests/test_flow_compaction_proactive.py`) — 28 passed, 0 failed
**Doc Sync:** fixed (`docs/specs/compaction.md` — `_gated_summarize_or_none` description, circuit breaker table success definition, error table empty-string row, test gate coverage row)

**Overall: DELIVERED**
Moved `_is_valid_summary` check into `_gated_summarize_or_none` so empty LLM returns increment the circuit breaker counter; removed the now-dead check from `apply_compaction`; added regression test pinning the counter-reset-on-success path.

## Implementation Review — 2026-05-01

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `grep -n "_is_valid_summary"` inside `_gated_summarize_or_none`, NOT in `apply_compaction` | ✓ pass | `compaction.py:189` — `if not _is_valid_summary(summary_text):` inside `_gated_summarize_or_none`; no reference in `apply_compaction` (lines 227–266) |
| TASK-2 | `pytest tests/test_flow_compaction_proactive.py -x` passes including `test_successful_compaction_resets_skip_count` | ✓ pass | `test_flow_compaction_proactive.py:239` — sets `skip_count=2`, calls `proactive_window_processor`, asserts `skip_count==0` post-success |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -x`
- Result: 111 passed, 0 failed (run twice — review-impl and ship safety net)
- Log: `.pytest-logs/20260501-084751-review-impl.log`

### Doc Sync
- Scope: narrow — self-contained change to `_gated_summarize_or_none`, no public API rename
- Result: clean (already fixed by orchestrate-dev: `compaction.md` lines 372, 384–387, 460, 554 accurate)

### Behavioral Verification
- No user-facing surface changed (no CLI commands, no chat-visible tools, no output formatting). Circuit breaker counter logic is internal. Full test suite covers the observable path.

### Overall: PASS
Circuit breaker empty-summary fix is correctly implemented, tested, and documented.
