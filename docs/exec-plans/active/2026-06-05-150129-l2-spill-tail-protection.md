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
tail_start = boundary[1] if (boundary := plan_compaction_boundaries(messages, budget, cfg.tail_fraction)) else len(messages)
candidates = _collect_tool_return_candidates(messages)          # now (index, part)
spillable  = [p for (i, p) in candidates if i < tail_start and not already-spilled]
```
`_spill_largest_first` is unchanged — it just receives a tail-free candidate set. The `tool_budget.spill_largest_tool_results`
event gains a `request.tail_start` / `request.tail_protected_count` attribute for observability. When
`plan_compaction_boundaries` returns `None` (too few turns to form a tail), fall back to today's behavior
(no exclusion) — there is no meaningful tail yet.

`budget` is the same value L2 already uses for its token math; `cfg.tail_fraction` is the L3 config value,
read from the same compaction config L3 reads.

## Tasks

### TASK-1 — Exclude the protected tail from L2 force-spill
- **files:** `co_cli/context/history_processors.py`
- **action:** In `spill_largest_tool_results`, compute `tail_start` from `plan_compaction_boundaries(messages,
  budget, cfg.tail_fraction)` (reusing the L3 config); make `_collect_tool_return_candidates` return each
  part with its message index; build `spillable` from parts at index `< tail_start` (and not already
  spilled). Leave `_spill_largest_first` and the threshold/trigger math unchanged. Add `request.tail_start`
  to the emitted event.
- **done_when:** L2's spillable set excludes `ToolReturnPart`s at index `>= tail_start`; when
  `plan_compaction_boundaries` returns `None`, behavior is unchanged from today.
- **success_signal:** a fresh tool return in the last turn-group is not stubbed on the next request even
  when total tokens exceed the spill threshold.
- **prerequisites:** none.

### TASK-2 — Behavioral test: fresh read survives L2, aged read spills
- **files:** `tests/test_flow_context_*` (the existing spill_largest_tool_results / sizing test module)
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

### TASK-3 — Cross-reference the sibling and parent plans
- **files:** `docs/exec-plans/active/2026-06-04-201014-read-view-emission-spill-cap.md`,
  `docs/exec-plans/active/2026-06-02-210659-context-stability-sizing-control.md`
- **action:** Note in the read-view plan that the visibility guarantee is delegated to this plan; add this
  plan to the parent's issue/extraction list. Touch only the cross-reference lines.
- **done_when:** both plans reference this one; no unrelated sections change.
- **success_signal:** N/A (doc pointer).
- **prerequisites:** none.

## Testing

- Scoped: the `spill_largest_tool_results` / sizing test module (real `ModelMessage`s, no LLM).
- The cross-cutting loop-stability eval that proves a fresh read survives under sustained load lives in the
  **parent** plan; this plan guarantees the tail-exclusion exists and is exercised by a scoped test.
- `scripts/quality-gate.sh full` at ship.

## Open Questions

- **Tail size for L2 vs L3.** This plan reuses L3's `tail_fraction` so the boundaries match. If L2 should
  protect a *larger* recent window than L3 keeps (e.g., protect the last 2 turns even when L3 would compact
  them), that's a deliberate divergence to raise — but matching is the clean default and the safer start.
- **Interaction with `evict_old_tool_results`.** Eviction (keep-5-per-tool) runs before L2 and already
  spares recent returns from collapse; confirm it never collapses a tail return that this plan means to
  preserve (it shouldn't — both spare recency — but worth a check during impl).

---

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev l2-spill-tail-protection`
