# TODO: Compaction Hygiene Pass — Pre-Turn Maintenance Event

**Slug:** `compaction-hygiene-pass`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc` — update `docs/specs/compaction.md` and `docs/specs/core-loop.md` with the new pre-turn hygiene event, its trigger ratio, and where it fits in the foreground turn flow.

---

## Context

Research:
- [RESEARCH-peer-compaction-survey.md](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-peer-compaction-survey.md)

Prerequisite: `compaction-foundation` plan must ship first. This plan depends on:
- `resolve_compaction_budget()` returning raw `context_window`
- `PROACTIVE_COMPACTION_RATIO = 0.75` applying to raw context
- Ratios being directly interpretable as percentages of the context window

Current-state validation, grounded in source inspection:
- `co-cli` has two compaction event families today: per-request history processing (history processors fire before each model call) and one-shot overflow recovery inside [run_turn() in co_cli/context/orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py:654). There is no maintenance-style compaction event before the turn enters the agent loop.
- The history processors fire inside `run_turn()` at model-call time, not at turn entry. A resumed session whose loaded history is already over threshold has its first compaction opportunity only when the first model API call is about to fire — after the user's new message has been appended and preflight injection is done.
- `_finalize_turn()` in [co_cli/main.py](/Users/binle/workspace_genai/co-cli/co_cli/main.py:95) calls [persist_session_history() in co_cli/context/transcript.py](/Users/binle/workspace_genai/co-cli/co_cli/context/transcript.py:87) which already branches to a child transcript when compaction is applied — pre-turn hygiene can reuse this machinery directly.
- Hermes has two distinct pre-request mechanisms: in-agent preflight compression inside the agent loop, and gateway transcript hygiene before the agent starts (see [gateway/run.py](/Users/binle/workspace_genai/hermes-agent/run_agent.py:3668)). Co does not need both — one maintenance pass before `run_turn()` streaming begins suffices.

## Problem & Outcome

Problem:
- A resumed session whose loaded history is already above the proactive threshold has no chance to compact between "session loaded" and "first model call." The history processor waits until model-call time.
- Between turn start and first model call, co runs preflight injection (safety, recall) on the full resumed history, wasting work on content that is about to be compacted away.
- If the user's new message plus the already-oversized history crosses the overflow boundary, the history processor can't catch it — it falls into `recover_overflow_history` on the first failed model call.

Failure cost:
- Resumed oversized sessions waste a model-call slot on maintenance before the user's real turn can be answered
- Overflow-recovery path is heavier and less graceful than proactive compaction; users see "Context overflow — compacting and retrying…" instead of seamless maintenance
- Preflight injection builds against a stale oversized history, adding cost for no benefit

Outcome:
- Add a pre-turn compaction helper that evaluates `message_history` before [run_turn() in co_cli/main.py](/Users/binle/workspace_genai/co-cli/co_cli/main.py:168).
- Trigger at a higher ratio than proactive (`HYGIENE_COMPACTION_RATIO = 0.88` of context window) to leave room for rough-estimate imprecision without firing on healthy sessions.
- Persist compacted histories through the existing child-transcript branch — no new transcript format.

Intended result: resumed oversized sessions compact before the turn starts, never waiting for a model-bound segment to trigger maintenance.

---

## Scope

In scope:
- Pre-turn hygiene helper invoked from `run_turn()` after `reset_for_turn()` and before `_run_model_preflight`
- `HYGIENE_COMPACTION_RATIO = 0.88` as a named module constant
- Hygiene helper reuses existing child-transcript branching via `history_compaction_applied`
- Regression tests for the trigger path and transcript branching

Out of scope (covered by other plans):
- Budget resolution simplification — see `compaction-foundation` (prerequisite)
- Per-batch aggregate tool defense — see `compaction-foundation`
- Real-token compaction input — see `compaction-foundation`
- Protected live-window and active-user anchoring — see `compaction-planner-thresholds`
- Threshold floor and anti-thrashing — see `compaction-planner-thresholds`

Out of scope (not planned):
- Hard message-count cap (`_HARD_MSG_LIMIT = 400`). Hermes has this as a death-spiral breaker for API-disconnect scenarios that lose `last_prompt_tokens`. Co's token estimator does not depend on provider usage reporting, so the death spiral does not apply. Adopting `400` without the underlying failure mode would be cargo-culting a magic number. If co later observes hygiene-bypass scenarios in its own operational data, reintroduce with data-backed calibration.
- Hygiene in slash-command paths that replace transcripts before re-entering the turn loop. First cut prefers the foreground chat path only.
- New persisted settings or `*Config` models.

---

## Behavioral Constraints

- Reuse the existing transcript-branching model in [co_cli/context/transcript.py](/Users/binle/workspace_genai/co-cli/co_cli/context/transcript.py:87); do not rewrite or truncate existing transcripts.
- `HYGIENE_COMPACTION_RATIO` is a named module constant in `co_cli/context/_history.py`, not a user setting.
- Hygiene is additive, not a replacement for overflow recovery or proactive compaction.
- The hygiene pass must run OUTSIDE `Agent(history_processors=...)` — it is a pre-turn event, not a per-request transformer.
- Fail open: if budget resolution returns an unusable value (no context window known), skip the hygiene pass rather than blocking the turn.
- Specs are post-ship sync output, not plan tasks.

---

## High-Level Design

Hermes gateway hygiene sits higher than its agent compressor in the threshold structure: `_hyg_threshold_pct = 0.85` of raw context, intentionally higher than the agent's `0.50` threshold. The gap exists because hygiene uses rough estimates (no real prompt_tokens yet for this turn) while the agent compressor uses API-reported tokens. A lower hygiene threshold would cause premature firing on every turn due to estimator overestimation.

The same reasoning applies to co. After `compaction-foundation` ships:
- Proactive trigger is at `0.75 * context_window`, using real API tokens when available (enhanced `summarize_history_window`)
- Hygiene trigger operates pre-turn, before any model call has given us real tokens for this state — it must use rough estimates only

Hygiene must sit meaningfully above `0.75` to avoid false positives from estimator imprecision (30–50% overestimate on code/JSON-heavy content). `0.88` gives a 13-percentage-point margin:
- Real tokens at 75% of context → rough estimate ~97% → would NOT fire hygiene (correct; proactive will handle during turn)
- Real tokens at 85% of context → rough estimate ~110% → fires hygiene (correct; needs maintenance before turn)

Insertion point is `run_turn()` in [co_cli/context/orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py:590), immediately after `deps.runtime.reset_for_turn()` and before `_run_model_preflight`. This placement ensures `history_compaction_applied` is set after the per-turn reset and remains readable by `_finalize_turn`. The helper:
1. Reads the current `message_history`
2. Computes a rough-estimate token count and compares against `HYGIENE_COMPACTION_RATIO * resolve_compaction_budget(...)`
3. If over, constructs a `RunContext` directly (same pattern as `_run_model_preflight` in `co_cli/context/orchestrate.py:566`) and calls the existing planner + summarization path
4. Replaces `message_history` in-place with the compacted result and sets `deps.runtime.history_compaction_applied = True` so `_finalize_turn` → `persist_session_history` branches to a child transcript

No new persistence logic, no new transcript format. Existing machinery.

---

## Implementation Plan

## ✓ DONE — TASK-1: Add pre-turn hygiene compaction helper

files: `co_cli/context/_history.py`, `co_cli/main.py`, `tests/test_context_compaction.py`, `tests/test_transcript.py`

Implementation:
- Add `HYGIENE_COMPACTION_RATIO: float = 0.88` as a named module constant in `co_cli/context/_history.py` with a docstring explaining the relationship to `PROACTIVE_COMPACTION_RATIO` and the rough-estimate imprecision rationale.
- Add a helper function to `co_cli/context/_history.py`:
  ```python
  async def maybe_run_pre_turn_hygiene(
      deps: CoDeps,
      message_history: list[ModelMessage],
      model,
  ) -> list[ModelMessage]:
      """Pre-turn hygiene compaction: compact if history is above HYGIENE_COMPACTION_RATIO."""
  ```
  Responsibilities:
  - Resolve budget via `resolve_compaction_budget(deps.config, context_window)` where `context_window` comes from the model's context spec (same resolution path used by the history processor).
  - If budget resolution yields an unusable value (≤ 0 or None), return `message_history` unchanged.
  - Compute rough-estimate token count via `estimate_message_tokens(message_history)` — the function takes a full list, not a single message.
  - If `token_count <= int(budget * HYGIENE_COMPACTION_RATIO)`, return unchanged.
  - Otherwise, construct a `RunContext(deps=deps, model=model, usage=RunUsage())` and call `summarize_history_window(ctx, message_history)`.
  - Set `deps.runtime.history_compaction_applied = True` when compaction actually runs (so `_finalize_turn` branches to child transcript).
  - Return the compacted result.
- Fail open: any exception in the estimation or compaction path logs at warning and returns the original `message_history` unchanged. The turn proceeds.
- Wire into `run_turn()` in [co_cli/context/orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py:590). Call `message_history = await maybe_run_pre_turn_hygiene(deps, message_history, agent.model)` immediately after `deps.runtime.reset_for_turn()` and before `_run_model_preflight`. The `history_compaction_applied` flag set by the helper survives to `_finalize_turn` because it is set after the per-turn reset.

done_when: |
  `maybe_run_pre_turn_hygiene` exists and is called from `run_turn()` after `reset_for_turn()` and before `_run_model_preflight`;
  a resumed session whose loaded history exceeds `HYGIENE_COMPACTION_RATIO * context_window` (rough estimate) is compacted before `run_turn` starts;
  `history_compaction_applied` is set so the post-turn transcript write branches to a child;
  a healthy resumed session whose history is below the hygiene threshold is not modified;
  helper fails open — an exception in estimation or compaction does not block the turn
success_signal: long-lived resumed sessions no longer need to wait for the first model-bound segment of a turn before maintenance compaction can run
prerequisites: []

## ✓ DONE — TASK-2: Regression tests and transcript branching coverage

files: `tests/test_context_compaction.py`, `tests/test_transcript.py`

Coverage must include:
- Pre-turn hygiene compacts a resumed session whose rough-estimate tokens exceed `HYGIENE_COMPACTION_RATIO * context_window`
- Pre-turn hygiene does NOT modify a session whose tokens are below the hygiene threshold (including sessions above proactive but below hygiene — those are the proactive layer's job)
- Pre-turn hygiene sets `history_compaction_applied = True` when it compacts, and the subsequent `persist_session_history` call branches to a child transcript
- Pre-turn hygiene leaves `history_compaction_applied` unchanged when it doesn't compact
- Pre-turn hygiene fails open when budget resolution yields an unusable value
- Pre-turn hygiene fails open when the compaction path raises
- The latest user turn survives pre-turn hygiene (this also validates downstream planner invariants; `compaction-planner-thresholds` will add the explicit invariant — this test asserts current behavior)
- Hygiene threshold of `0.88` is not triggered by a fresh session with rough-estimate tokens below 75% of context

done_when: |
  all hygiene paths (trigger, no-op, fail-open, transcript branching) are covered by direct pytest assertions
success_signal: regressions in hygiene firing, silent-pass, or transcript branching are caught by the test suite
prerequisites: [TASK-1]

---

## Testing

During implementation, scope to affected tests first:

- `mkdir -p .pytest-logs && uv run pytest tests/test_context_compaction.py tests/test_transcript.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-compaction-hygiene-pass.log`

Before shipping:

- `mkdir -p .pytest-logs && uv run pytest tests/test_context_compaction.py tests/test_transcript.py tests/test_history.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-compaction-hygiene-pass-full.log`
- `scripts/quality-gate.sh full`

---

## Open Questions

- Whether hygiene should also run in slash-command paths that replace transcripts (e.g. `/resume`, `/compact`) before re-entering the turn loop. First cut: foreground chat path only. Revisit if operational cases appear where slash-command paths leave oversized histories.
- Whether the hygiene helper's rough estimator should walk the full history or use a cached count. First cut: full walk (runs once per turn entry, cost is small). Optimize later if profiling shows the walk dominates turn-entry time.

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/context/_history.py` | `maybe_run_pre_turn_hygiene` correctly fails open, uses `RunContext` with `RunUsage()`, and delegates to existing `summarize_history_window` — no new paths. | clean | TASK-1 |
| `co_cli/context/_history.py` | `HYGIENE_COMPACTION_RATIO = 0.88` correctly documented with the 13-point gap rationale; one place. | clean | TASK-1 |
| `co_cli/context/orchestrate.py` | `reset_for_turn()` → `maybe_run_pre_turn_hygiene()` → `frontend.on_status` ordering verified at line 612–615; `history_compaction_applied` is set after the per-turn reset (correct). | clean | TASK-1 |
| `tests/test_context_compaction.py` | 7 hygiene tests: trigger, no-op (below threshold), no-op (proactive zone), flag set, flag unset, fail-open unusable budget, latest user turn survives. All assert observable behavior. | clean | TASK-2 |
| `tests/test_transcript.py` | `test_finalize_turn_branches_child_transcript_when_history_compacted` uses `FILE_DB_TIMEOUT_SECS` from `tests._timeouts` (repo policy). Fixture wired to production path. | clean | TASK-2 |
| `docs/specs/compaction.md` | M0 row added to mechanism table; `HYGIENE_COMPACTION_RATIO` added to constants table; `_history.py` Files row updated. | clean | sync-doc |
| `docs/specs/core-loop.md` | Diagram node I updated; M0 paragraph added to §2.4; `_history.py` Files row updated. | clean | sync-doc |

**Overall: clean**

---

## Delivery Summary — 2026-04-20

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `maybe_run_pre_turn_hygiene` wired in `run_turn()` after `reset_for_turn()`, before `_run_model_preflight`; oversized resumed sessions compact before agent loop; `history_compaction_applied` set for child transcript branching; healthy sessions untouched; fails open | ✓ pass |
| TASK-2 | All hygiene paths (trigger, no-op, fail-open, transcript branching, user-turn survival) covered by direct pytest assertions | ✓ pass |

**Tests:** full suite — 583 passed, 0 failed
**Independent Review:** clean
**Doc Sync:** fixed (compaction.md: M0 row + HYGIENE_COMPACTION_RATIO constant + _history.py description; core-loop.md: diagram + §2.4 M0 paragraph + _history.py description)

**Overall: DELIVERED**
Pre-turn hygiene compaction (M0) is live; resumed oversized sessions compact before the agent loop starts, not at the first model-call boundary.
