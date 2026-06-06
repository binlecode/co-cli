# l2-spill-tail-protection

> **Sibling of `2026-06-04-201014-read-view-emission-spill-cap.md`; both extracted from the parent
> `2026-06-02-210659-context-stability-sizing-control.md` (within-session sizing dynamics).**
> This plan carries the **primary** fix for "the model must see what it just read": the L2 force-spill
> (`spill_largest_tool_results`) currently spills the freshest tool return before the model ever processes it.
> The read-view plan (pagination + limit-constant dedup) depends on this for its visibility guarantee.

## Context

When the model calls a read tool, the lifecycle is:

1. Model emits the tool call (request R1 response).
2. The framework runs the tool, appends a `ToolReturnPart`, and assembles request **R2** — the *first*
   request that carries the result back to the model.
3. History processors run on R2 **before sending**. `spill_largest_tool_results` (L2,
   `co_cli/context/history_processors.py:364`) runs here, ahead of `proactive_window_processor` (L3,
   `compaction.py:460`).
4. R2 is sent to the model.

L2 collects **every** string `ToolReturnPart` across the whole message list with **no tail/recency
protection** (`_collect_tool_return_candidates`, `history_processors.py:312-321`) and force-spills them
**largest-first** (`_spill_largest_first`, `:341`) until the request fits. So the just-produced read — the
largest, freshest return, the one the model is about to consume — is the **prime spill target**. By the
time R2 reaches the model, the content is already a `<persisted-output>` placeholder.

**The defect:** the model never sees the content it just asked for. And this bites *exactly* when it
matters — loading a large doc is what pushes the request over `deps.spill_threshold_tokens`, triggering L2.
The "recoverable — page the sidecar" story is hollow under pressure: re-reading adds content L2 spills
again. The emission-spill bypass that read tools carry (so content lands inline) is silently undone by L2
one request later.

**The asymmetry that reveals the fix:** L3 *does* preserve a recent tail (`head | marker | … | tail`), via
the shared planner `plan_compaction_boundaries(messages, budget, tail_fraction)`
(`_compaction_boundaries.py:130`). But **L2 runs first and has no such protection**, so L2 pre-empts the
tail L3 is careful to keep. L2 should respect the **same** tail boundary L3 already uses.

## Problem & Outcome

**Problem.** L2 force-spill has no tail protection and runs before L3 (which does). It spills the freshest
tool return — the content the model is about to read — before the model sees it once.

**Outcome.** `spill_largest_tool_results` excludes the protected tail from its spillable set, using the **same**
`plan_compaction_boundaries` boundary L3 and overflow-recovery already share. Tool returns at message index
`>= tail_start` are preserved; L2 spills only head/middle returns (`< tail_start`), largest-first, as today.

**Invariant established:** *a tool result is visible to the model at least once — on the request
immediately following its production — before it becomes spill-eligible.* The freshest read lives in the
last turn-group, which `plan_compaction_boundaries` always retains (`_MIN_RETAINED_TURN_GROUPS=1`), so it
survives R2 and the model sees it. As it ages past the tail on later rounds, it becomes spillable (L2) or
summarizable (L3) normally — but the model has already seen it.

## Scope

**In scope:**
- `spill_largest_tool_results`: compute `tail_start` via `plan_compaction_boundaries(messages, budget,
  cfg.tail_fraction)` and exclude `ToolReturnPart`s at index `>= tail_start` from the spillable candidates.
  This requires tracking each candidate's message index (today `_collect_tool_return_candidates` discards
  it).
- Scoped behavioral tests proving a fresh large tool return survives L2 under aggregate pressure while an
  aged one still spills.

**Out of scope:**
- L1 emission spill and the read/view pagination + limit-constant dedup — owned by
  `2026-06-04-201014-read-view-emission-spill-cap.md`.
- L3 summarization tail logic — unchanged; this plan only makes L2 *match* it.
- The genuine-overflow path (tail alone exceeds budget) — already handled by `recover_overflow_history`
  (`strip_all_tool_returns` → compact) on HTTP 400; this plan does not change it.
- `docs/specs/` updates (handled by `sync-doc` post-delivery).

## Behavioral Constraints

- **Reuse the shared boundary, don't coin a new one** — call `plan_compaction_boundaries` so L2's protected
  tail is *identical* to L3's and to overflow-recovery's. Two slightly-different "recent" notions would be a
  drift bug.
- **Protection is best-effort within what fits** — if the protected tail alone still overflows the request,
  L2 cannot reduce it (it skips the tail); the request proceeds and the existing HTTP-400
  `recover_overflow_history` path catches it. This is the genuine "single read bigger than the window" case;
  pagination (sibling plan) keeps it rare.
- **Aging is unchanged** — once a tool return falls outside the tail, it spills/summarizes exactly as today.
- **Surgical** (`CLAUDE.md`) — touch only `spill_largest_tool_results` and its candidate collection; do not alter
  the spill primitive, L3, or the recovery path.

## High-Level Design

`spill_largest_tool_results` currently:
```
candidates = _collect_tool_return_candidates(messages)          # every ToolReturnPart, no index
spillable  = [p for p in candidates if not already-spilled]
_spill_largest_first(spillable, ...)                            # size-only, no tail awareness
```

Change: thread the message index through candidate collection, compute the protected boundary once, and
drop tail returns from the spillable set:
```
budget     = resolve_compaction_budget(deps)                    # same value L3 + overflow-recovery use
cfg        = deps.config.compaction                             # same compaction config L3 reads
tail_start = boundary[1] if (boundary := plan_compaction_boundaries(messages, budget, cfg.tail_fraction)) else len(messages)
candidates = _collect_tool_return_candidates(messages)          # now (index, part)
spillable  = [p for (i, p) in candidates if i < tail_start and not already-spilled]
```
`_spill_largest_first` is unchanged — it just receives a tail-free candidate set. The `tool_budget.spill_largest_tool_results`
event gains a `request.tail_start` / `request.tail_protected_count` attribute for observability. When
`plan_compaction_boundaries` returns `None` (too few turns to form a tail), fall back to today's behavior
(no exclusion) — there is no meaningful tail yet.

**`budget` must be `resolve_compaction_budget(deps)`** — the *same* value L3 and `recover_overflow_history`
pass to `plan_compaction_boundaries` (it resolves to `deps.model_max_ctx`). `spill_largest_tool_results`
does **not** hold a `budget` variable today; it triggers on `deps.spill_threshold_tokens` /
`deps.static_floor_tokens`, so TASK-1 must call `resolve_compaction_budget(deps)` and read
`deps.config.compaction` for `tail_fraction`. Do **not** reuse `spill_threshold_tokens` as the budget — that
produces a *different* tail boundary than L3 and reintroduces the exact drift this plan forbids.

## Tasks

### ✓ DONE TASK-1 — Exclude the protected tail from L2 force-spill
- **files:** `co_cli/context/history_processors.py`
- **action:** In `spill_largest_tool_results`, resolve `budget = resolve_compaction_budget(deps)` and
  `cfg = deps.config.compaction` (the same sources L3 uses — NOT `spill_threshold_tokens`), then compute
  `tail_start` from `plan_compaction_boundaries(messages, budget, cfg.tail_fraction)`; make
  `_collect_tool_return_candidates` return each part with its message index; build `spillable` from parts at
  index `< tail_start` (and not already spilled). Leave `_spill_largest_first` and the threshold/trigger math
  unchanged. Add `request.tail_start` to the emitted event.
- **done_when:** L2's spillable set excludes `ToolReturnPart`s at index `>= tail_start`; when
  `plan_compaction_boundaries` returns `None`, behavior is unchanged from today.
- **success_signal:** a fresh tool return in the last turn-group is not stubbed on the next request even
  when total tokens exceed the spill threshold.
- **prerequisites:** none.

### ✓ DONE TASK-2 — Behavioral test: fresh read survives L2, aged read spills
- **files:** `tests/test_flow_compaction_spill_largest_tool_results.py` (the existing
  spill_largest_tool_results test module; `test_flow_compaction_processor_chain.py` covers the L2→L3 chain)
- **action:** Build a message list (real `ModelMessage`s, no LLM) where total tokens exceed
  `spill_threshold_tokens` and a large `ToolReturnPart` sits in the last turn-group. Assert:
  (a) after `spill_largest_tool_results`, that fresh return is **not** replaced by `PERSISTED_OUTPUT_TAG` (it's in
  the protected tail); (b) an equally large `ToolReturnPart` in an *older* turn (before `tail_start`) **is**
  spilled; (c) when the tail alone still exceeds the threshold, the processor returns without spilling the
  tail (defers to the overflow path) rather than stubbing it.
- **done_when:** `uv run pytest <module> -x` passes (piped to a timestamped `.pytest-logs/` file per
  `CLAUDE.md`), including the new tests.
- **success_signal:** the model-visibility invariant holds — the most-recent read survives one round of L2.
- **prerequisites:** TASK-1.

### ✓ DONE TASK-3 — Cross-reference the sibling and parent plans
- **files:** `docs/exec-plans/active/2026-06-04-201014-read-view-emission-spill-cap.md`,
  `docs/exec-plans/active/2026-06-02-210659-context-stability-sizing-control.md`
- **action:** Note in the read-view plan that the visibility guarantee is delegated to this plan; add this
  plan to the parent's issue/extraction list. Touch only the cross-reference lines.
- **done_when:** both plans reference this one; no unrelated sections change.
- **success_signal:** N/A (doc pointer).
- **prerequisites:** none.

## Testing

- Scoped: `tests/test_flow_compaction_spill_largest_tool_results.py` (real `ModelMessage`s, no LLM).
- The cross-cutting loop-stability eval that proves a fresh read survives under sustained load lives in the
  **parent** plan; this plan guarantees the tail-exclusion exists and is exercised by a scoped test.
- `scripts/quality-gate.sh full` at ship.

## Resolved Decisions (Gate 1)

- **Tail size for L2 vs L3 — MATCH L3 (resolved).** `spill_largest_tool_results` reuses the *same*
  `plan_compaction_boundaries(messages, budget, cfg.tail_fraction)` boundary L3 and `recover_overflow_history`
  use — no larger or separate window. Rationale verified against source: co already protects a recent window
  in four places — `dedup_tool_results` and `evict_old_tool_results` via `_find_last_turn_start`; L3 and
  `recover_overflow_history` via `plan_compaction_boundaries`. `spill_largest_tool_results` is the lone
  content-reducer with no recency protection. Matching L3 is the drift-free default, and since the last
  turn-group is always retained (`_MIN_RETAINED_TURN_GROUPS=1`) it protects at least as much as the last-turn
  boundary evict/dedup use.
- **Not an over-design; `evict_old_tool_results` interaction confirmed safe (resolved).** evict (keep-5
  per-tool) and dedup (identical-collapse) protect recency on the *count/duplication* axis;
  `spill_largest_tool_results` force-spills **largest-first** with *no* recency awareness — a different axis.
  They are orthogonal, not redundant: a fresh large read survives dedup and evict, then gets force-spilled by
  L2 today. No surveyed peer even has an L2 force-spill tier, but tail protection during compaction is
  universal across peers and pervasive within co — so this fix closes a real consistency gap; it does not add
  a new concept. (Impl still confirms evict never collapses a tail return this plan preserves — both spare
  recency, so it shouldn't.)

---

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev l2-spill-tail-protection`

---

## Delivery Summary — 2026-06-05

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | L2 spillable set excludes `ToolReturnPart`s at index `>= tail_start`; `None` boundary → unchanged | ✓ pass |
| TASK-2 | `uv run pytest tests/test_flow_compaction_spill_largest_tool_results.py -x` passes incl. new tests | ✓ pass |
| TASK-3 | both sibling and parent plans reference this one; no unrelated sections changed | ✓ pass |

**What shipped.** `spill_largest_tool_results` (`co_cli/context/history_processors.py`) now threads each candidate's
message index through `_collect_tool_return_candidates` and excludes the protected recent tail from the spillable
set. `tail_start` is computed from the **same** `plan_compaction_boundaries(messages, resolve_compaction_budget(deps),
cfg.tail_fraction)` boundary L3 and overflow recovery use — no new "recent" notion. When the planner returns `None`
(fewer than 2 turn groups) every candidate stays spillable (pre-tail-protection behavior). Two observability attrs
added: `request.tail_start`, `request.tail_protected_count`. The spill trigger (`spill_threshold_tokens`) and
`_spill_largest_first` are unchanged.

**Invariant established.** A tool result is visible to the model at least once — on the request immediately following
its production — before it becomes spill-eligible. The freshest read lives in the last turn group, which
`plan_compaction_boundaries` always retains (`_MIN_RETAINED_TURN_GROUPS=1`).

**Tests:** scoped — 13 passed, 0 failed (`test_flow_compaction_spill_largest_tool_results.py` 11 + `test_flow_compaction_processor_chain.py` 2). All new tests assert observable spill/visibility behavior only — no structural assertions.

**Doc Sync:** fixed — `compaction.md` §2.4 Scope rewritten (removed the now-false "no protected tail at this stage"
claim), §1.2 L2 row + algorithm step 3 add the tail-start filter, span attribute list adds the two new attrs, and the
test-coverage map adds the tail-protection cases. `observability.md` / `core-loop.md` / `prompt-assembly.md` clean.

**Overall: DELIVERED**
All three tasks pass `done_when`; lint clean; scoped + chain tests green; specs reconciled.

**Next step:** `/review-impl l2-spill-tail-protection` — full suite + evidence scan + behavioral verification → verdict appended.

---

## Implementation Review — 2026-06-05

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | L2 spillable excludes index `>= tail_start`; `None` boundary → unchanged | ✓ pass | `history_processors.py:420` `budget = resolve_compaction_budget(deps)` (same source as L3 `compaction.py:418`, NOT `spill_threshold_tokens`); `:421-422` `cfg.tail_fraction` from `deps.config.compaction`; `:327` `enumerate(messages)` yields true messages-list index (no desync from the `ModelRequest` skip); `:423` `tail_start = boundary[1] if boundary else len(messages)` — `boundary[1]` is `tail_start` per `_compaction_boundaries.py:36,199`; `:429` `index < tail_start and not …PERSISTED_OUTPUT_TAG`; `_spill_largest_first` (`:336-373`) and trigger math (`:415-418`) unchanged |
| TASK-2 | scoped module passes incl. new tests | ✓ pass | `test_flow_compaction_spill_largest_tool_results.py` — 11 passed. Tests assert only observable content (present vs `PERSISTED_OUTPUT_TAG`), no structural/event-attr assertions; real `ModelMessage`s, no mocks. Non-vacuity proven by simulating revert (planner→`None`): fresh return spills → assert at `:281` would fail |
| TASK-3 | both plans reference this one; surgical | ✓ pass | Parent `2026-06-02-210659` ISSUE-5 block (`:175-180`) — genuine new cross-reference, single clean hunk. Sibling `2026-06-04-201014` references at `:4,10-11,122,218` |

### Issues Found & Fixed
No blocking issues found. Evidence subagents and an adversarial cold re-read (which independently confirmed index/tuple-order/budget correctness from source and simulated the revert to prove test non-vacuity) produced zero confirmed-blocking or confirmed-minor findings requiring a fix.

**Observation (non-blocking, staging note for ship):** the sibling plan's working-tree diff is dominated by pre-existing Gate-1 *scope-narrowing prose* (the read/view plan's own refinement), not TASK-3 cross-reference edits — the sibling's references to this plan were already committed in HEAD. The only new cross-reference TASK-3 produced is the parent's ISSUE-5 block. The sibling edits belong to that sibling's own lifecycle; verify staging at ship so they are bundled deliberately, not as accidental scope.

### Tests
- Command: `uv run pytest -q`
- Result: 645 passed, 0 failed (136s; no stalled/slow calls)
- Log: `.pytest-logs/<ts>-review-impl.log`

### Behavioral Verification
- No user-facing surface changed (internal L2 history-processor logic — no CLI/tool/output/config/bootstrap change). `success_signal`s are model-visibility behaviors, verified directly by the Phase 5 behavioral tests (fresh read survives L2; aged read spills; protected tail alone defers without stubbing).
- `uv run co --help`: ✓ CLI entrypoint loads the changed module chain cleanly — no import/bootstrap regression. (`co status` is not a command in this CLI.)

### Overall: PASS
All three tasks satisfy `done_when` with file:line evidence confirmed under adversarial re-read; full suite green; lint clean; specs reconciled. Ready for Gate 2 → `/ship`.
