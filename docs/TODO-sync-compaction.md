# TODO: Synchronous Compaction — Replace Async Pre-computation with Inline Summarization

Task type: refactor

## Context

co-cli's compaction currently splits summarization and compaction across two turns:
- **Turn N idle time**: `HistoryCompactionState.on_turn_end()` spawns an async `precompute_compaction()` task that runs an LLM call to generate a summary
- **Turn N+1**: `truncate_history_window()` (synchronous history processor) checks if the pre-computed summary matches current boundaries; if stale or absent, falls back to a static marker `"[N messages trimmed]"` with no content

Both fork-cc and gemini-cli converge on the simpler pattern: **summarize inline when compaction triggers, synchronously, in one code path**. See `docs/reference/RESEARCH-peer-session-compaction.md` §5b for convergence analysis.

**Current-state validation (C1):**
- All referenced code confirmed present: `HistoryCompactionState` at `_history.py:531`, `precompute_compaction()` at `:449`, `Compaction` at `_types.py:29`, `precomputed_compaction` on `CoRuntimeState` at `deps.py:341`, threshold constants at `_history.py:445-446`, lifecycle wiring in `main.py:80,106,132,183,251-252`.
- No prior tasks shipped — fresh plan.
- Open question resolved by inspection: `console` is importable directly in `_history.py` (used throughout `co_cli/display/_core.py`); no frontend abstraction needed for a one-line status message.

## Problem

The async pre-computation adds complexity without proportional benefit:

1. **Staleness logic**: `Compaction` stores `(message_count, head_end, tail_start)` at computation time. Any change between turns discards the result — silent fallback to a useless static marker.
2. **Silent quality degradation**: when the pre-computed summary is stale (common when tools add messages between turns), the model sees `"[Earlier conversation trimmed — N messages removed]"` with zero context about what was trimmed. This is the worst compaction outcome — context loss with no signal.
3. **Lifecycle machinery**: `HistoryCompactionState` exists solely to manage this complexity — task spawning, harvesting, cancellation, staleness checks, shutdown cleanup. 60+ lines of orchestration code.
4. **Two threshold systems**: approaching thresholds (70% tokens / 80% messages) for pre-computation vs trigger thresholds (85% tokens / message count) for actual compaction. These interact in non-obvious ways.
5. **Hard to extend**: adding circuit breaker, enriched prompt, or structured summaries requires touching both the async pre-computation path AND the synchronous processor — two code paths that must stay in sync.
6. **Race conditions**: task cancellation, `CancelledError` handling, done-check-before-harvest — all async machinery that's hard to reason about and test.

Failure cost: When pre-computed summary is stale (the common case during tool-heavy turns), the model loses all context about trimmed messages. The agent silently degrades — it forgets prior decisions, repeats work, and loses track of multi-step plans.

## Outcome

Compaction becomes a single synchronous code path: when `truncate_history_window()` decides to compact, it generates the summary inline via `summarize_messages()` and injects it immediately. No pre-computation, no staleness, no static marker fallback, no task lifecycle.

The latency cost (one LLM call during the history processor) is acceptable:
- Compaction fires rarely (only when threshold is crossed)
- The summarization model uses `ROLE_SUMMARIZATION` with `reasoning_effort=none` — fast
- fork-cc and gemini-cli both accept this latency; gemini-cli blocks `executeTurn()` synchronously
- A "compacting..." indicator can be shown (like fork-cc does) to signal the delay

## Scope

**In scope:**
- Convert `truncate_history_window()` to call `summarize_messages()` inline when compaction triggers
- Remove `precompute_compaction()` function
- Remove `HistoryCompactionState` class
- Remove `Compaction` dataclass from `_types.py`
- Remove `precomputed_compaction` field from `CoRuntimeState` in `deps.py`
- Remove `compactor` lifecycle from `main.py` (`on_turn_start`, `on_turn_end`, `shutdown`)
- Remove approaching-threshold constants (`_PRECOMPACT_TOKEN_RATIO`, `_PRECOMPACT_MSG_RATIO`)
- Add "compacting..." indicator during inline summarization
- Add circuit breaker: failure counter on `CoRuntimeState`, stop LLM summarization after 3 consecutive failures (fall back to static marker only then)

**Out of scope:**
- Enriching the compact prompt with plan/tool context (separate TODO, builds on this)
- Structured summary format (separate TODO, builds on this)
- `/compact` command changes (already synchronous, unaffected)

## Behavioral Constraints

- `truncate_history_window()` remains a pydantic-ai history processor — it just becomes blocking when compaction fires
- The static marker `"[N messages trimmed]"` becomes the circuit-breaker fallback only (after 3 consecutive summarization failures), not the default path
- On summarization failure (single attempt): log warning, use static marker for this turn, increment `compaction_failure_count`
- On summarization success: reset `compaction_failure_count` to 0
- When `compaction_failure_count >= 3`: skip LLM call entirely, use static marker directly, log at warning level that circuit breaker is active
- The "compacting..." indicator must appear before the LLM call and clear after (use `console.print` directly — resolved by inspection, no frontend abstraction needed)
- Model resolution: `truncate_history_window` resolves the summarization model via `ctx.deps.model_registry.get(ROLE_SUMMARIZATION)` — `model_registry` is already available on `CoDeps`
- Personality awareness: pass `personality_active=bool(ctx.deps.config.personality)` to `summarize_messages()` to preserve personality context in summaries
- When `model_registry` is `None` (sub-agents, tests, minimal bootstrap): skip LLM summarization and use static marker directly. Do not increment `compaction_failure_count` — configuration absence is not a transient failure

## Implementation Plan

### ✓ DONE — TASK-0: Make `truncate_history_window` summarize inline

Convert the processor to call `summarize_messages()` directly when compaction triggers. Remove the pre-computed summary consumption logic. Resolve the summarization model via `ctx.deps.model_registry.get(ROLE_SUMMARIZATION, _none_resolved)`. Guard: when `model_registry is None`, skip LLM and use static marker (do not increment failure counter). Add try/except for `ModelHTTPError | ModelAPIError` with static marker fallback. Pass `personality_active=bool(ctx.deps.config.personality)`. Update `_make_processor_ctx` in `tests/test_history.py` to pass a real `ModelRegistry` so the inline summarization path is reachable in tests.

files: `co_cli/context/_history.py`, `tests/test_history.py`
done_when: `uv run pytest tests/test_history.py tests/test_context_compaction.py -x` passes; `truncate_history_window` calls `summarize_messages()` inline; grep confirms no reference to `precomputed_compaction` in `truncate_history_window` function body
success_signal: N/A (refactor — no user-visible behavior change; summary quality improves silently)

### ✓ DONE — TASK-1: Add circuit breaker to `CoRuntimeState`

Add `compaction_failure_count: int = 0` to `CoRuntimeState`. In the inline summarization path: increment on failure, reset on success, skip LLM call when count >= 3 (use static marker directly, log warning). Do NOT reset in `reset_for_turn()` — the counter is cross-turn state (like `precomputed_compaction` was).

files: `co_cli/deps.py`, `co_cli/context/_history.py`, `tests/test_history.py`
prerequisites: [TASK-0]
done_when: `uv run pytest tests/test_history.py -x` passes; new test `test_circuit_breaker_skips_llm_after_three_failures` in `tests/test_history.py` verifies: after setting `compaction_failure_count=3` on deps, `truncate_history_window` uses static marker without calling `summarize_messages()`
success_signal: N/A (safety mechanism — only observable after 3 consecutive LLM failures)

### ✓ DONE — TASK-2: Remove async pre-computation machinery

Delete `precompute_compaction()`, `HistoryCompactionState`, `Compaction` dataclass, `precomputed_compaction` field on `CoRuntimeState`, `Compaction` import in `deps.py`, approaching-threshold constants, the `asyncio` import (dead after class removal), and all lifecycle wiring in `main.py` (`compactor` variable, `on_turn_start`/`on_turn_end`/`shutdown` calls, `compactor` parameter on `_finalize_turn` and `_run_foreground_turn`).

files: `co_cli/context/_history.py`, `co_cli/context/_types.py`, `co_cli/deps.py`, `co_cli/main.py`
prerequisites: [TASK-0, TASK-1]
done_when: `grep -r "precompute_compaction\|HistoryCompactionState\|precomputed_compaction\|_PRECOMPACT_TOKEN_RATIO\|_PRECOMPACT_MSG_RATIO\|class Compaction" co_cli/` returns 0 matches; `uv run pytest tests/test_history.py tests/test_context_compaction.py -x` passes
success_signal: N/A (removal — no user-visible change)

### ✓ DONE — TASK-3: Add compacting indicator

Show `"[dim]Compacting conversation...[/dim]"` via `console.print()` before the inline `summarize_messages()` call in `truncate_history_window`. Import `console` from `co_cli.display._core`. No clear-after needed — the next model response output naturally follows.

files: `co_cli/context/_history.py`
prerequisites: [TASK-0]
done_when: grep confirms `console.print` call with "Compacting" in `truncate_history_window` function body; `uv run pytest tests/test_history.py -x` passes (indicator is a print side-effect, not testable without mocks — manual verification)
success_signal: N/A (cosmetic enhancement — no behavioral contract; verified manually)

### ✓ DONE — TASK-4: Update tests

Update `tests/test_history.py`: remove `test_truncate_history_window_uses_precomputed_result` and `test_truncate_history_window_stale_precomputed_uses_static_marker` (both test removed pre-computation paths). Remove `Compaction` import. Update `tests/test_context_compaction.py` if any references to removed machinery exist. Clean up `tests/test_subagent_tools.py`: remove `precomputed_compaction=None` explicit kwarg (line 62) and `assert isolated.runtime.precomputed_compaction is None` (line 88) — field no longer exists.

files: `tests/test_history.py`, `tests/test_context_compaction.py`, `tests/test_subagent_tools.py`
prerequisites: [TASK-0, TASK-1, TASK-2, TASK-3]
done_when: `uv run pytest -x` passes (full suite); `grep -r "precomputed_compaction\|class Compaction\|from.*_types import.*Compaction" tests/` returns 0 matches
success_signal: N/A (test maintenance)

## Testing

- TASK-0: Existing `test_truncate_history_window_static_marker_when_no_precomputed` still passes (now the only non-circuit-breaker path is inline summarization with LLM fallback to static marker); compaction trigger tests in `test_context_compaction.py` unaffected (they test boundary logic, not summary source)
- TASK-1: New test sets `compaction_failure_count=3` on deps and verifies static marker is used without LLM call
- TASK-2: Grep confirms zero references to removed machinery in `co_cli/`
- TASK-3: Manual verification of compacting indicator during a real session
- TASK-4: Full test suite green; no stale imports or references to removed types

## Final — Team Lead

Plan approved. C1: PO approved with no blocking issues; Core Dev raised 2 blocking issues (missed test file reference, `model_registry=None` guard) — both adopted and applied to plan.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev sync-compaction`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/deps.py:50-52` | Blank line left inside import group after `DEFAULT_SESSION_TTL_MINUTES` removal (`DEFAULT_TOOL_RETRIES,\n\n    DEFAULT_REASONING_DISPLAY,`) — stray whitespace in import block | minor | TASK-2 |
| `co_cli/deps.py` | Removal of `DEFAULT_SESSION_TTL_MINUTES` import, `session_ttl_minutes` field, `session_path` field, and addition of `sessions_dir`, `load_session` import, transcript imports in `main.py` are all **out of scope** for this TODO. These are session-transcript changes (likely from `TODO-session-transcript.md`), not sync-compaction tasks. They are bundled in the same diff but do not belong to any of TASK-0 through TASK-4. | minor | N/A (scope) |
| `co_cli/main.py` | Lines adding `load_session` import, `append_transcript`, `write_compact_boundary`, transcript-path existence check, session-ID rotation logic, and `write_compact_boundary` call are all out-of-scope session-transcript work bundled into this diff. Not harmful, but makes the diff harder to review against task specs. | minor | N/A (scope) |
| `docs/DESIGN-core-loop.md` | Still references `precomputed_compaction` (line 104, 232), `HistoryCompactionState` (lines 237, 313, 344), and `precompute_compaction()` (lines 239, 240). Stale after TASK-2. | blocking | TASK-2 |
| `docs/DESIGN-context.md` | Still references `HistoryCompactionState.on_turn_start/end`, `precompute_compaction`, `precomputed_compaction`, and the two-turn split description (lines 35-36, 43-44, 108-109, 130-131, 247). Stale after TASK-2. | blocking | TASK-2 |
| `docs/DESIGN-system.md` | Still references `HistoryCompactionState.shutdown()` (line 120) and `precomputed_compaction` in the state table (line 234). Stale after TASK-2. | blocking | TASK-2 |
| `docs/DESIGN-llm-models.md` | Line 245 still mentions `precompute_compaction` as a user of the summarization model. Stale after TASK-2. | minor | TASK-2 |
| `CHANGELOG.md:63` | References `precompute_compaction()` in the release notes for the token-count feature. Historical changelog entries are typically left as-is, so flagging as informational only. | minor | TASK-2 |
| `co_cli/context/_history.py:411` | `_none_resolved = ResolvedModel(model=None, settings=None)` is constructed on every compaction trigger. Negligible cost, but could be a module-level constant. Not blocking. | minor | TASK-0 |
| `co_cli/context/_history.py:410-413` | The deferred `from co_cli.display._core import console` inside the function body is acceptable for avoiding circular imports, but the import executes on every compaction trigger (not cached by Python's import machinery in function scope in all cases). Functionally fine -- Python does cache modules after first import -- just noting the pattern. | minor | TASK-3 |
| `tests/test_history.py` | `test_circuit_breaker_skips_llm_after_three_failures` passes `model_registry=_REGISTRY` to CoDeps, meaning if the circuit breaker logic were buggy and fell through, it would actually attempt an LLM call. This is good -- it tests the real guard path with a real registry, not a None bypass. No issue. | N/A | TASK-1 |
| `tests/test_history.py:14` | Removed `from dataclasses import replace` -- confirmed no remaining usage in file. Clean. | N/A | TASK-4 |
| `tests/test_subagent_tools.py` | Removal of `precomputed_compaction=None` kwarg and the assertion is correct -- field no longer exists. Clean. | N/A | TASK-4 |

### Spec Fidelity Summary

- **TASK-0**: Implemented correctly. `truncate_history_window` now calls `summarize_messages()` inline, has `model_registry is None` guard, and `try/except` fallback for `ModelHTTPError|ModelAPIError`.
- **TASK-1**: Implemented correctly. `compaction_failure_count: int = 0` added to `CoRuntimeState`, not reset by `reset_for_turn()`, circuit breaker skips LLM at `>= 3`.
- **TASK-2**: Code removal from `co_cli/` is complete -- all specified machinery deleted. However, DESIGN docs still contain stale references (3 blocking findings above).
- **TASK-3**: Implemented correctly. `console.print("[dim]Compacting conversation...[/dim]")` appears before the LLM call.
- **TASK-4**: Test cleanup is complete. Stale tests removed, imports cleaned, subagent test updated.

### Security

No command injection, path traversal, or SQL injection concerns. The inline summarization passes message content to `summarize_messages()` which uses a pydantic-ai Agent -- no raw string interpolation into shell or SQL.

### Cross-Task Coherence

All five tasks are internally consistent. The circuit breaker (TASK-1) correctly interacts with the inline path (TASK-0) and the indicator (TASK-3). The removal (TASK-2) and test cleanup (TASK-4) are consistent with TASK-0/1.

**Overall: 3 blocking / 5 minor**

The 3 blocking findings are all stale DESIGN doc references to removed machinery (`DESIGN-core-loop.md`, `DESIGN-context.md`, `DESIGN-system.md`). These must be updated via `/sync-doc` before shipping -- DESIGN docs are post-implementation documentation and must stay in sync with code per CLAUDE.md conventions.

**Post-review update:** All 3 blocking findings resolved by `/sync-doc` — DESIGN docs updated in-place.

## Delivery Summary — 2026-04-03

| Task | done_when | Status |
|------|-----------|--------|
| TASK-0 | pytest passes; inline `summarize_messages()` call; no `precomputed_compaction` in function body | ✓ pass |
| TASK-1 | pytest passes; `test_circuit_breaker_skips_llm_after_three_failures` verifies circuit breaker | ✓ pass |
| TASK-2 | grep returns 0 matches for removed machinery in `co_cli/`; pytest passes | ✓ pass |
| TASK-3 | grep confirms `console.print` with "Compacting"; pytest passes | ✓ pass |
| TASK-4 | full suite 298 passed; grep returns 0 matches for removed types in `tests/` | ✓ pass |

**Tests:** full suite — 298 passed, 0 failed
**Independent Review:** 3 blocking (stale DESIGN doc refs) / 5 minor — all blocking resolved by `/sync-doc`
**Doc Sync:** fixed (DESIGN-context.md, DESIGN-core-loop.md, DESIGN-system.md, DESIGN-llm-models.md — removed all stale `HistoryCompactionState`/`precomputed_compaction`/`precompute_compaction` references)

**Overall: DELIVERED**
Replaced async pre-computation with inline synchronous compaction, added circuit breaker (3-failure threshold), removed ~160 lines of lifecycle machinery, added "Compacting..." indicator, and cleaned all stale references across code and docs.

## Implementation Review — 2026-04-03

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-0 | pytest passes; inline `summarize_messages()` call; no `precomputed_compaction` in function body | ✓ pass | `_history.py:416` — `summarize_messages()` inline call; `:404-407` — `model_registry is None` guard; `:422-424` — `ModelHTTPError/ModelAPIError` catch; `:419` — `personality_active`; `:414` — `registry.get(ROLE_SUMMARIZATION, _none_resolved)` |
| TASK-1 | pytest passes; `test_circuit_breaker_skips_llm_after_three_failures` verifies circuit breaker | ✓ pass | `deps.py:345` — `compaction_failure_count: int = 0`; `_history.py:408` — `>= 3` check; `:421` — reset on success; `:424` — increment on failure; `deps.py:362-372` — `reset_for_turn()` does NOT touch counter; `test_history.py:111-133` — circuit breaker test with real `_REGISTRY` |
| TASK-2 | grep returns 0 matches in `co_cli/`; pytest passes | ✓ pass | `grep -r --include='*.py' ... co_cli/ tests/` returns 0 matches; `_history.py` — no `asyncio` import, no `precompute_compaction`, no `HistoryCompactionState`; `_types.py` — no `Compaction` class; `deps.py` — no `precomputed_compaction` field; `main.py` — no `compactor` variable or lifecycle calls |
| TASK-3 | grep confirms `console.print` with "Compacting" | ✓ pass | `_history.py:411-412` — lazy `console` import + `console.print("[dim]Compacting conversation...[/dim]")` before LLM call |
| TASK-4 | full suite passes; grep returns 0 matches in `tests/` | ✓ pass | `test_history.py` — `Compaction` import removed, precomputed tests removed, test renamed to `test_truncate_history_window_static_marker_when_no_model_registry`; `test_subagent_tools.py:61` — `CoRuntimeState()` without `precomputed_compaction`, line 86 assertion removed |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale `import asyncio` — unused after all async test functions removed from module-level imports | `test_context_compaction.py:3` | blocking (stale import) | Removed import; also cleaned stale `(TASK-1)` docstring reference |

### Tests
- Command: `uv run pytest -x -v`
- Result: 298 passed, 0 failed
- Log: `.pytest-logs/20260403-125712-review-impl.log`

### Doc Sync
- Scope: verified post-delivery sync-doc results
- Result: clean — all DESIGN docs updated by delivery's `/sync-doc`; one remaining `SKIP_PRECOMPACT_THRESHOLD` hit in `DESIGN-session-port-drift-analysis.md` is a fork-cc comparison (accurate, not stale)

### Behavioral Verification
- `uv run co config`: ✓ healthy — all components online, system starts cleanly
- Compacting indicator: user-visible only during long sessions (40+ messages); all `success_signal` values are N/A. Manual verification deferred to production use.

### Overall: PASS
All 5 tasks verified with file:line evidence. One blocking finding (stale `asyncio` import in `test_context_compaction.py`) found and fixed. Full test suite green (298/298). DESIGN docs clean. System starts healthy. Ship-ready.
