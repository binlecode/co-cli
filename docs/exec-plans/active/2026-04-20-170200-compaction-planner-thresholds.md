# TODO: Compaction Planner + Threshold Hardening

**Slug:** `compaction-planner-thresholds`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc` — update `docs/specs/compaction.md` with the explicit protected-tail policy, active-user anchoring invariant, threshold floor, and anti-thrashing controls.

---

## Context

Research:
- [RESEARCH-peer-compaction-survey.md](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-peer-compaction-survey.md)

Prerequisite: `compaction-foundation` plan must ship first. This plan depends on:
- `resolve_compaction_budget()` returning raw `context_window`
- Ratios applying directly to raw context
- `PROACTIVE_COMPACTION_RATIO = 0.75` as the single knob for proactive firing

Current-state validation, grounded in source inspection:
- `co-cli` has a token-budget boundary planner in [plan_compaction_boundaries() in co_cli/context/_history.py](/Users/binle/workspace_genai/co-cli/co_cli/context/_history.py:216). The old message-count tail bug is gone. The planner already accepts `min_groups_tail: int = 1` (shipped in `compaction-foundation`) and enforces it in the walk loop — but this is a bare default arg, not a config-sourced value. There is no active-user anchoring and no bounded overrun policy.
- `co-cli` does not guarantee the latest `UserPromptPart` stays in the retained tail. Planner alignment or oversized recent turns could, in principle, push the latest user turn into the dropped middle. Hermes enforces retention explicitly in [_ensure_last_user_message_in_tail() in hermes-agent/agent/context_compressor.py](/Users/binle/workspace_genai/hermes-agent/agent/context_compressor.py:885).
- `co-cli` threshold structure is shallow beyond the single proactive ratio. No threshold floor for small-context models, no anti-thrashing backoff when consecutive compactions yield low savings. Hermes has both:
  - Threshold floor via `MINIMUM_CONTEXT_LENGTH = 64_000` — `threshold_tokens = max(int(context_length * threshold_percent), MINIMUM_CONTEXT_LENGTH)` in [hermes-agent/agent/context_compressor.py](/Users/binle/workspace_genai/hermes-agent/agent/context_compressor.py:264)
  - Anti-thrashing via `_ineffective_compression_count` tracking runs with <10% savings in [should_compress() in hermes-agent/agent/context_compressor.py](/Users/binle/workspace_genai/hermes-agent/agent/context_compressor.py:310)
- `co-cli` currently uses module-level ALLCAPS constants (`PROACTIVE_COMPACTION_RATIO`, `HYGIENE_COMPACTION_RATIO`, `TAIL_FRACTION`) for all tuning knobs. Hermes pattern: user-tunable ratios live in a config namespace (`compression.threshold`, `compression.target_ratio`) passed as constructor args; internal-only mechanics are `_underscore_prefixed` module constants never aliased back from config. Co-cli should follow suit.

## Problem & Outcome

Problem:
- The planner's preserved live window is implicit in `TAIL_FRACTION * budget` rather than an explicit policy. No invariants defend the latest user turn against aggressive compaction.
- A single large recent turn can push the planner's backward walk past the last user message, dropping the active request into the summarized middle.
- The single proactive ratio is fragile on small-context models where `0.75 * context_window` may be too low to allow meaningful accumulation before each compaction cycle.
- Repeated low-yield compactions (each saving <10%) consume model-call slots without meaningful benefit, eventually stalling progress.

Failure cost:
- Active user request could silently disappear into a summary, confusing downstream behavior
- Compaction on small-context models fires too early and too often
- Compaction can loop indefinitely on low-yield histories with no structural backoff

Outcome:
- Make the protected live-window policy explicit: named constants for tail ratio, minimum retained turn groups, bounded soft-overrun multiplier
- Add active-user anchoring — the latest `UserPromptPart` must be in the retained tail
- Add a threshold floor (`MIN_COMPACTION_THRESHOLD_TOKENS`) so small-context models don't compact at unreasonably small counts
- Add anti-thrashing runtime state that skips proactive compaction (but NOT overflow recovery, NOT hygiene) when consecutive compactions yield below a named minimum savings

Intended result: compaction produces a coherent live context under pressure, small-context models compact at sensible floors, and low-yield compaction loops back off gracefully.

---

## Scope

In scope:
- Explicit protected-tail policy constants in `co_cli/context/_history.py`
- Active-user anchoring step inside `plan_compaction_boundaries()` or its callers
- Bounded soft-overrun multiplier so one large recent turn doesn't collapse the tail
- `MIN_COMPACTION_THRESHOLD_TOKENS` floor applied to proactive trigger
- Anti-thrashing runtime state + check for proactive path only
- Regression tests for each invariant and the anti-thrashing backoff behavior

Out of scope (covered by other plans):
- Budget simplification — see `compaction-foundation` (prerequisite)
- Per-batch tool defense — see `compaction-foundation`
- Real-token compaction input — see `compaction-foundation`
- Pre-turn hygiene — see `compaction-hygiene-pass`

Out of scope (not planned):
- Replacing `plan_compaction_boundaries()` with a different algorithm. Extend, don't replace.
- Summarizer prompt changes.
- User-facing configuration for any of the new constants.

---

## Behavioral Constraints

- Extend the current planner — `plan_compaction_boundaries()` remains the single source of truth for proactive, hygiene, and overflow paths.
- Anti-thrashing must degrade to "skip proactive compaction" only. It must not disable overflow recovery in [co_cli/context/orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py:658) or pre-turn hygiene (from `compaction-hygiene-pass`).
- Keep all new threshold knobs as named module constants, not user settings.
- Active-user anchoring is an invariant, not a heuristic — a test must demonstrably fail when it breaks.
- Specs are post-ship sync output, not plan tasks.

---

## High-Level Design

### 0. CompactionSettings config submodel

Add `co_cli/config/_compaction.py` with a new `CompactionSettings(BaseModel)` submodel, and wire it into `Settings` as `compaction: CompactionSettings`. All tuning knobs move here — no module-level ALLCAPS constants for these values. Access at call sites is `ctx.deps.config.compaction.<field>`.

Fields:
```python
class CompactionSettings(BaseModel):
    proactive_ratio: float = 0.75        # CO_COMPACTION_PROACTIVE_RATIO
    hygiene_ratio: float = 0.88          # CO_COMPACTION_HYGIENE_RATIO
    tail_fraction: float = 0.40          # CO_COMPACTION_TAIL_FRACTION
    min_threshold_tokens: int = 32_000   # CO_COMPACTION_MIN_THRESHOLD_TOKENS
    tail_soft_overrun_multiplier: float = 1.25  # CO_COMPACTION_TAIL_SOFT_OVERRUN_MULTIPLIER
    min_proactive_savings: float = 0.10  # CO_COMPACTION_MIN_PROACTIVE_SAVINGS
    proactive_thrash_window: int = 2     # CO_COMPACTION_PROACTIVE_THRASH_WINDOW
```

Existing module constants `PROACTIVE_COMPACTION_RATIO`, `HYGIENE_COMPACTION_RATIO`, and `TAIL_FRACTION` are removed from `_history.py`; all call sites updated to read from `ctx.deps.config.compaction.*`. Hermes precedent: config-sourced ratios are passed as constructor/call args from the config object — never aliased back into module-level names.

### 1. Explicit protected live-window policy

Today's planner walks backward from the end of history and stops when cumulative tokens would exceed `tail_fraction * budget`. That's a budget-aware tail — but it's not a _policy_ with named guarantees.

After this plan:
- `tail_fraction` comes from `ctx.deps.config.compaction.tail_fraction` (default 0.40)
- `min_retained_turn_groups = 1` — correctness invariant hardcoded in the planner, not a config field. Setting it to 0 breaks the planner. `min_groups_tail=1` already exists as a parameter default from `compaction-foundation`; this plan removes the bare default and wires it to the hardcoded invariant.
- `tail_soft_overrun_multiplier` comes from `ctx.deps.config.compaction.tail_soft_overrun_multiplier` (default 1.25) — allows the actual retained tail to exceed `tail_fraction * budget` by up to this multiplier when honoring `min_retained_turn_groups` requires it
- Active-user anchoring: after planner proposes head/tail boundaries, walk backward from end to find the latest `UserPromptPart`. If that message is inside the dropped middle (index between head_end and tail_start), extend tail_start backward to include it. Applied to both proactive and overflow paths via the shared planner.

### 2. Threshold floor

`proactive_ratio * context_window` can be too aggressive on small-context models (e.g. 16K context → 12K trigger). Hermes' floor pattern adapted:

```python
threshold = max(
    int(budget * ctx.deps.config.compaction.proactive_ratio),
    ctx.deps.config.compaction.min_threshold_tokens,
)
```

Never compact below this absolute floor, regardless of the ratio. Default `min_threshold_tokens = 32_000` is overridable in `settings.json`.

### 3. Proactive anti-thrashing

Runtime state tracks recent proactive compaction effectiveness:
- `recent_proactive_savings: list[float]` — ring buffer of the last few compaction savings percentages (`(before - after) / before`); lives in runtime state in `co_cli/context/types.py` or `co_cli/deps.py`
- `min_proactive_savings` and `proactive_thrash_window` come from `ctx.deps.config.compaction.*`
- If the last `proactive_thrash_window` compactions all yielded < `min_proactive_savings`, skip proactive until a new turn moves the needle

Anti-thrashing gate wraps only the proactive path in `summarize_history_window`. Overflow recovery and hygiene both bypass the check. Reset of `recent_proactive_savings` happens at the call sites in `co_cli/context/orchestrate.py` where overflow recovery and hygiene fire — not inside the planner.

---

## Implementation Plan

## ✓ DONE — TASK-1: Add CompactionSettings, harden live-window policy, add active-user anchoring

files: `co_cli/config/_compaction.py` (new), `co_cli/config/_core.py`, `co_cli/context/_history.py`, `tests/test_history.py`, `evals/eval_compaction_quality.py`

Implementation:
- Create `co_cli/config/_compaction.py` with `CompactionSettings(BaseModel)`. Fields: `proactive_ratio=0.75`, `hygiene_ratio=0.88`, `tail_fraction=0.40`, `min_threshold_tokens=32_000`, `tail_soft_overrun_multiplier=1.25`, `min_proactive_savings=0.10`, `proactive_thrash_window=2`. `model_config = ConfigDict(extra="ignore")`.
- Add `compaction: CompactionSettings = Field(default_factory=CompactionSettings)` to `Settings` in `co_cli/config/_core.py`.
- Remove module-level constants `PROACTIVE_COMPACTION_RATIO`, `HYGIENE_COMPACTION_RATIO`, `TAIL_FRACTION` from `co_cli/context/_history.py`. Update all call sites to read from `ctx.deps.config.compaction.*`.
- Extend `plan_compaction_boundaries()` signature: replace the `tail_fraction` default arg with a required positional (callers always pass from config). Add `tail_soft_overrun_multiplier: float` parameter. Harden the walk loop:
  - If honoring the tail budget would retain zero turn groups, relax up to `tail_fraction * budget * tail_soft_overrun_multiplier`. The minimum retained groups invariant is hardcoded `1` — not a parameter.
  - If even the soft overrun is insufficient, accept the overrun and log at info.
- Add active-user anchoring as the final step inside `plan_compaction_boundaries()` before returning: walk backward from `len(messages) - 1`, find the last index containing a `UserPromptPart`. If that index is inside the dropped middle, extend `tail_start` back to the start of the group containing it.
- Update tests:
  - A history where one recent turn alone exceeds `tail_fraction * budget` — soft overrun kicks in, that turn is retained
  - A history where the latest `UserPromptPart` would land in the middle — anchoring pulls `tail_start` back
  - A normal-sized tail — anchoring is a no-op

done_when: |
  planner behavior names and enforces a protected live window with MIN_RETAINED_TURN_GROUPS, TAIL_SOFT_OVERRUN_MULTIPLIER, and active-user anchoring;
  the latest UserPromptPart is provably retained across proactive and overflow compaction;
  bounded soft overrun preserves a coherent live suffix under large recent-turn pressure
success_signal: the retained suffix behaves like live context, not leftover budget
prerequisites: []

## ✓ DONE — TASK-2: Add threshold floor and proactive anti-thrashing

files: `co_cli/context/_history.py`, `co_cli/context/types.py`, `co_cli/deps.py`, `co_cli/context/orchestrate.py`, `tests/test_context_compaction.py`

Implementation:
- In `summarize_history_window`, read threshold values from config (no module constants):
  ```python
  cfg = ctx.deps.config.compaction
  threshold = max(int(budget * cfg.proactive_ratio), cfg.min_threshold_tokens)
  ```
- Add `recent_proactive_savings: list[float]` to the runtime state struct in `co_cli/context/types.py` (or `co_cli/deps.py` — match where other per-session runtime state lives). Not reset per turn; persists across turns within a session.
- Track savings after each proactive compaction run: `savings = (tokens_before - tokens_after) / tokens_before`. Append to `recent_proactive_savings`, truncate to last `cfg.proactive_thrash_window` entries.
- Before running proactive compaction, check thrashing gate using `cfg.min_proactive_savings` and `cfg.proactive_thrash_window` from config. Gate applies ONLY to the proactive path in `summarize_history_window`.
- Critical: do NOT gate `recover_overflow_history` or `maybe_run_pre_turn_hygiene`.
- Reset `recent_proactive_savings` at the call sites in `co_cli/context/orchestrate.py` where overflow recovery and hygiene fire — not inside `_history.py`. This keeps the reset co-located with the callers that bypass the gate.

done_when: |
  co-cli has a threshold floor so small-context models don't compact at unreasonably small counts;
  repeated low-yield proactive compactions back off without disabling overflow recovery or hygiene;
  `recent_proactive_savings` is cleared when overflow or hygiene fires
success_signal: compaction becomes less fragile on small-context models and less noisy under low-yield histories
prerequisites: [TASK-1]

## ✓ DONE — TASK-3: Regression tests and eval coverage

files: `tests/test_history.py`, `tests/test_context_compaction.py`, `evals/eval_compaction_quality.py`

Coverage must include:
- `plan_compaction_boundaries()` honors `MIN_RETAINED_TURN_GROUPS` even when the last turn alone exceeds `TAIL_FRACTION * budget`
- `plan_compaction_boundaries()` applies `TAIL_SOFT_OVERRUN_MULTIPLIER` to cap the overrun
- Active-user anchoring pulls `tail_start` back when the latest `UserPromptPart` would be in the dropped middle
- Active-user anchoring is a no-op when the latest user turn is already in the retained tail
- Both proactive and overflow-recovery paths retain the latest user turn (shared planner contract)
- `summarize_history_window` uses `max(ratio * budget, MIN_COMPACTION_THRESHOLD_TOKENS)` — small-context test demonstrates the floor taking effect
- Anti-thrashing: after N compactions with <10% savings, the next proactive pass is skipped
- Anti-thrashing: overflow recovery still fires when proactive is gated
- Anti-thrashing: hygiene still fires when proactive is gated (if hygiene plan is shipped, else this test is deferred)
- Anti-thrashing state resets when overflow or hygiene fires
- Compaction eval: `evals/eval_compaction_quality.py` still passes with the new policy constants

done_when: |
  all new planner invariants (protected tail, soft overrun, active-user anchoring) have direct tests;
  threshold floor and anti-thrashing behavior are covered;
  overflow/hygiene independence from anti-thrashing is asserted
success_signal: the hardened planner and threshold behavior is constrained by real tests instead of surveyed invariants
prerequisites: [TASK-1, TASK-2]

---

## Testing

During implementation, scope to affected tests first:

- `mkdir -p .pytest-logs && uv run pytest tests/test_history.py tests/test_context_compaction.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-compaction-planner-thresholds.log`

Before shipping:

- `mkdir -p .pytest-logs && uv run pytest tests/test_history.py tests/test_context_compaction.py tests/test_transcript.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-compaction-planner-thresholds-full.log`
- `uv run python evals/eval_compaction_quality.py`
- `scripts/quality-gate.sh full`

---

## Open Questions

- Whether the anti-thrashing savings metric should be estimated tokens, message count reduction, or char count reduction. Hermes uses savings percentage after compression. First cut: token estimate before vs. after, consistent with threshold units.
- Whether `min_threshold_tokens = 32_000` is the right default floor. Hermes uses 64K. Co's value should be calibrated against the smallest context window a user might configure — overridable via `settings.json` so this is a safe first cut.
- Whether `recent_proactive_savings` should persist across session resume. First cut: no — fresh session starts fresh; thrash state is an in-session observation.

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| tests/test_context_compaction.py | Missing test verifying that clearing recent_proactive_savings (as hygiene/overflow do) unblocks the gate — subsequently added as test_savings_clear_unblocks_gate | minor (fixed) | TASK-3 |

**Overall: clean (1 minor, fixed before ship)**

All other files read and verified:
- `co_cli/config/_compaction.py` — new file, complete and correct; all 7 fields with sensible defaults
- `co_cli/config/_core.py` — env mappings complete for all 7 new compaction fields; Settings integration correct
- `co_cli/context/_history.py` — planner, soft-overrun, active-user anchoring, threshold floor, anti-thrashing all correct; savings tracking co-located with proactive path; overflow and hygiene paths unaffected by gate
- `co_cli/deps.py` — CoRuntimeState.recent_proactive_savings properly added with list[float] and field default
- `co_cli/context/orchestrate.py` — savings reset correct on both hygiene (always) and overflow paths
- `tests/test_history.py` — planner regression tests correct; circuit breaker tests updated for new floor; soft-overrun and active-user anchoring tests valid
- `tests/test_context_compaction.py` — threshold floor, anti-thrashing gate, window-not-full, savings-clear tests all correct; no mocks
- `evals/eval_compaction_quality.py` — plan_compaction_boundaries calls updated; min_threshold_tokens=0 in _make_ctx

---

## Delivery Summary — 2026-04-20

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | planner behavior names and enforces a protected live window with MIN_RETAINED_TURN_GROUPS, TAIL_SOFT_OVERRUN_MULTIPLIER, and active-user anchoring | ✓ pass |
| TASK-2 | threshold floor + anti-thrashing gate; recent_proactive_savings cleared when overflow or hygiene fires | ✓ pass |
| TASK-3 | all new planner invariants have direct tests; threshold floor and anti-thrashing behavior covered; overflow/hygiene independence asserted | ✓ pass |

**Tests:** full suite — 587 passed, 0 failed
**Independent Review:** 1 minor (fixed — added test_savings_clear_unblocks_gate)
**Doc Sync:** fixed (CompactionSettings config table, boundary planner pseudocode, anti-thrashing gate, soft-overrun, active-user anchoring; stale TAIL_FRACTION/PROACTIVE_COMPACTION_RATIO/HYGIENE_COMPACTION_RATIO references removed; Files section updated)

**Overall: DELIVERED**
CompactionSettings replaces module-level constants with user-tunable config; boundary planner hardened with soft-overrun and active-user anchoring; threshold floor and anti-thrashing gate added; 587 tests pass.

---

## Implementation Review — 2026-04-20

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | planner enforces protected live window with MIN_RETAINED_TURN_GROUPS, soft-overrun, active-user anchoring | ✓ pass | `_history.py:93` — `_MIN_RETAINED_TURN_GROUPS=1`; `_history.py:203` — `_anchor_tail_to_last_user`; `_history.py:270-285` — soft-overrun walk loop; `_history.py:291` — anchoring applied; `recover_overflow_history` at `_history.py:748-753` passes same config |
| TASK-2 | threshold floor + anti-thrashing gate; savings cleared when overflow or hygiene fires | ✓ pass | `_history.py:809` — `max(int(budget * proactive_ratio), min_threshold_tokens)`; `_history.py:816-821` — gate check; `_history.py:849-854` — savings tracking; `orchestrate.py:618` — reset post-hygiene; `orchestrate.py:682` — reset post-overflow; `deps.py:144` — `recent_proactive_savings: list[float]` not in `reset_for_turn()` |
| TASK-3 | all invariants covered by direct tests | ✓ pass | `test_history.py` — soft-overrun, active-user anchoring; `test_context_compaction.py` — threshold floor, gate activation, window passthrough, savings-clear, hygiene bypass |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Hygiene blocked by anti-thrashing gate: `maybe_run_pre_turn_hygiene` calls `summarize_history_window` without clearing savings first; an active gate silently skips hygiene for one turn, violating the spec constraint | `_history.py:882-885` | blocking | Cleared `recent_proactive_savings` inside `maybe_run_pre_turn_hygiene` before calling `summarize_history_window`; added `test_hygiene_not_blocked_by_anti_thrashing_gate` regression test |

### Tests
- Command: `uv run pytest -x`
- Result: 589 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: narrow — all tasks confined to `co_cli/context/_history.py`, `co_cli/config/_compaction.py`, `co_cli/deps.py`, `co_cli/context/orchestrate.py`; no public API renames
- Result: fixed in prior `orchestrate-dev` pass; confirmed still accurate post-fix

### Behavioral Verification
- `uv run co config`: ✓ healthy — system starts, config loads with `CompactionSettings`, LLM online
- No user-facing CLI surface changed — compaction improvements are internal

### Overall: PASS
Blocking finding (hygiene gated by anti-thrashing) found and fixed; 589 tests green; all TASK-1/TASK-2/TASK-3 spec requirements confirmed at file:line; behavioral verification clean. Ready to ship.
