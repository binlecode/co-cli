# TODO: Simplify Compaction Trigger — Remove Message Count

**Task type:** refactor
**Origin:** §4a-assessment in `docs/reference/RESEARCH-peer-session-compaction.md`

## Context

The sliding-window history processor (`truncate_history_window` in `co_cli/context/_history.py`) uses a dual trigger: compaction fires when EITHER message count exceeds `max_history_messages` (default 40) OR token count exceeds 85% of budget. Peer analysis (fork-cc, gemini-cli) shows 0/2 convergence on message-count triggering — both rely purely on token thresholds. The message-count path is over-design that adds a second code path, a config surface (`CO_CLI_MAX_HISTORY_MESSAGES` env var + `max_history_messages` field in Settings/CoConfig), and couples tail-size computation to `max_history_messages // 2`.

No stale TODO files found. No doc/source inaccuracies beyond the scope of this change.

## Problem & Outcome

**Problem:** The dual trigger adds unnecessary complexity — two independent trigger conditions, a config field that no peer system uses, and a tail-count formula coupled to message count rather than context budget.
**Failure cost:** Internal complexity that makes compaction harder to reason about and test. Additionally, the tail-count formula directly affects how much recent context survives compaction — getting this wrong degrades conversation quality in long sessions.

**Outcome:** Token-only trigger. `max_history_messages` removed from config chain. Tail-count derived from message array length (no config dependency). Fewer lines, fewer fields, simpler tests.

## Scope

**In scope:**
- Remove message-count branch from `truncate_history_window` trigger condition
- Remove `max_history_messages` field from `Settings`, `CoConfig`, env var mapping, eval deps
- Remove `DEFAULT_MAX_HISTORY_MESSAGES` constant
- Replace `tail_count = max(4, max_msgs // 2)` with `tail_count = max(4, len(messages) // 2)`
- Update `_compute_compaction_boundaries` signature (drop `max_msgs` parameter)
- Update tests to trigger via token threshold instead of message count
- Clean up DESIGN-context.md and RESEARCH doc strikethrough annotations

**Out of scope:**
- Changing the 85% token threshold
- Changing the compaction summarization logic
- Adding new trigger mechanisms (warning tiers, freed-token subtraction)

## Behavioral Constraints

1. **Token trigger unchanged:** `should_compact = token_count > int(budget * 0.85)` — exact same formula, no threshold change.
2. **Head-pinning preserved:** `head_end = find_first_run_end(messages) + 1` — unchanged.
3. **Tail alignment preserved:** `_align_tail_start` logic unchanged — still walks forward past orphaned `ToolReturnPart`.
4. **Circuit breaker unchanged:** 3-failure threshold, `model_registry is None` guard — no change.
5. **Tail size parity:** New `tail_count = max(4, len(messages) // 2)` maintains the old behavior's ratio (half of messages as tail). Examples: 30 messages → 15 tail, 60 messages → 30 tail, 6 messages → 4 tail (floor). Note: conversations with fewer than ~8 messages will always produce an invalid boundary (`tail_start <= head_end`) regardless of divisor — token-threshold triggers on very short conversations are silently no-ops. This is existing behavior, not a regression.

## High-Level Design

Single-pass removal. The `max_history_messages` field threads through: `config.py` (Settings field + env mapping + constant) → `deps.py` (CoConfig field + `create_config` wiring) → `_history.py` (trigger condition + boundary computation parameter) → `evals/_deps.py` (eval deps builder). Remove from all layers. Replace tail-count formula. Update tests to use token-triggered compaction.

**Tail-count decision:** `max(4, len(messages) // 2)` — keeps half of messages as tail, maintaining parity with the old `max(4, max_history_messages // 2)` default. Scales with conversation length. Minimum 4 ensures short conversations always have a viable tail. No config dependency.

## Implementation Plan

### TASK-1: Remove message-count trigger and decouple tail-count

files:
- `co_cli/context/_history.py`

Changes:
1. In `_compute_compaction_boundaries`: change signature from `(messages, max_msgs)` to `(messages)`. Replace `tail_count = max(4, max_msgs // 2)` with `tail_count = max(4, len(messages) // 2)`.
2. In `truncate_history_window`: remove `max_msgs = ctx.deps.config.max_history_messages`. Simplify trigger to `should_compact = token_count > token_threshold`. Update call site `_compute_compaction_boundaries(messages, max_msgs)` → `_compute_compaction_boundaries(messages)`. Update docstring to remove message-count reference.

done_when: `grep -n "max_msgs\|max_history_messages" co_cli/context/_history.py` returns zero matches AND `uv run pytest tests/test_context_compaction.py tests/test_history.py` passes (non-regression check — tests still reference the old field until TASK-2/3).
success_signal: N/A (refactor — no user-visible behavior change)

### TASK-2: Remove `max_history_messages` from config chain

files:
- `co_cli/config.py`
- `co_cli/deps.py`
- `evals/_deps.py`

Changes:
1. `co_cli/config.py`: remove `DEFAULT_MAX_HISTORY_MESSAGES = 40`, remove `max_history_messages` field from `Settings`, remove `"max_history_messages": "CO_CLI_MAX_HISTORY_MESSAGES"` from env var mapping.
2. `co_cli/deps.py`: remove `DEFAULT_MAX_HISTORY_MESSAGES` from imports, remove `max_history_messages` field from `CoConfig`, remove `max_history_messages=s.max_history_messages` from `create_config()`.
3. `evals/_deps.py`: remove `"max_history_messages": s.max_history_messages` from eval deps builder.

prerequisites: [TASK-1]
done_when: `grep -rn "max_history_messages\|DEFAULT_MAX_HISTORY_MESSAGES" co_cli/ evals/` returns zero matches.
success_signal: N/A (refactor)

### TASK-3: Update tests

files:
- `tests/test_context_compaction.py`
- `tests/test_history.py`

Changes:
1. `tests/test_context_compaction.py`:
   - `test_compaction_triggers_on_real_input_tokens` (line 77): remove `max_history_messages=0` from `CoConfig()` — field no longer exists. Test already triggers via token threshold (90K > 85K).
   - `test_compaction_fallback_when_no_usage_data` (line 97): remove `max_history_messages=4`. This test currently relies on message-count trigger (10 msgs > 4). Rewrite to trigger via char-estimate token path with a tiny Ollama budget: `CoConfig(llm_provider="ollama-openai", llm_num_ctx=30)`. The 10 test messages from `_make_messages(10)` produce ~150 chars total → ~37 estimated tokens via `chars // 4`. Threshold = `int(30 * 0.85) = 25`. Since 37 > 25, compaction triggers via the estimate fallback.
   - `test_compaction_triggers_on_ollama_budget` (line 117): remove `max_history_messages=0` — field no longer exists. Test already triggers via token threshold (7200 > 6963).
2. `tests/test_history.py`:
   - `_make_processor_ctx` (line 32): remove `max_history_messages` parameter. To trigger compaction, switch to a tiny Ollama budget: `CoConfig(llm_provider="ollama-openai", llm_num_ctx=30)`. The `_make_messages(10)` content (~150 chars → ~37 estimated tokens) exceeds `int(30 * 0.85) = 25`.
   - Update docstring in `_make_messages` (line 66) to remove `max_history_messages=6` example; update boundary calculation example to use new `tail_count = max(4, len(messages) // 2)`.
   - `test_truncate_history_window_static_marker_when_no_model_registry` (line 91): use updated `_make_processor_ctx()` (no args).
   - `test_circuit_breaker_skips_llm_after_three_failures` (line 116): remove `max_history_messages=6` from `CoConfig()`, use same small-budget approach.

prerequisites: [TASK-1, TASK-2]
done_when: `uv run pytest tests/test_context_compaction.py tests/test_history.py -v` passes with all tests green AND `grep -n "max_history_messages" tests/test_context_compaction.py tests/test_history.py` returns zero matches.
success_signal: N/A (refactor)

### TASK-4: Update docs and reference files

files:
- `docs/DESIGN-context.md`
- `docs/DESIGN-core-loop.md`
- `docs/reference/RESEARCH-peer-session-compaction.md`
- `settings.reference.json`

Changes:
1. `docs/DESIGN-context.md` line 343: remove `max_history_messages` row from config table. Update compaction description (line ~235) to remove message-count trigger reference.
2. `docs/DESIGN-core-loop.md` line 229: update compaction behavior description to remove message-count reference. Line 325: remove `max_history_messages` row from config table.
3. `settings.reference.json` line 69: remove `"max_history_messages": 40` entry.
4. `docs/reference/RESEARCH-peer-session-compaction.md`: remove all `~~strikethrough~~` annotations added during analysis — replace with clean text reflecting the implemented state (token-only trigger). Note: this is cosmetic cleanup, not a blocker — do it while already in the file.
5. `CHANGELOG.md` references are historical records — leave as-is.

prerequisites: [TASK-1, TASK-2, TASK-3]
done_when: `grep -rn "max_history_messages" docs/DESIGN-context.md docs/DESIGN-core-loop.md settings.reference.json` returns zero matches AND no `~~` strikethrough markers remain in the research doc.
success_signal: N/A (doc cleanup)

## Testing

- TASK-1 and TASK-3 are the critical testing surface. All existing compaction tests must pass after migration to token-only triggering.
- No new test files needed — existing tests are updated to use token-based triggering.
- Regression surface: `tests/test_context_compaction.py` (token trigger, budget resolution), `tests/test_history.py` (boundary computation, circuit breaker, `/compact` dispatch).

## Open Questions

None — all decisions resolved during analysis.

## Final — Team Lead

Plan approved. Both Core Dev and PO returned `Blocking: none` on C2.

Key C1 revisions: tail formula `// 3` → `// 2` (parity with old behavior), test math fixed (`llm_num_ctx=30`, threshold 25 < 37 estimated tokens), added `settings.reference.json` + `docs/DESIGN-core-loop.md` to TASK-4.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev compaction-trigger-simplify`
