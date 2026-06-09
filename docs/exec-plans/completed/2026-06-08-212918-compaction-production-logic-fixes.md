# Compaction production-logic fixes

## Context

Seven production-logic findings (ISSUE-1, ISSUE-2, ISSUE-4…ISSUE-8) in the context-compaction pipeline
(processor order `dedup → evict → spill → proactive`, `co_cli/agent/orchestrator.py:65-70`). The headline
is ISSUE-2: the proactive loop has no hard convergence guard. The rest are miscomputed or stale supporting
metrics. ISSUE-1 and ISSUE-8 share a floor-vs-window root.

**ISSUE-3 is owned by `prior-summary-dedicated-slot`, not this plan.** That plan fixes the same defect
(prior-summary carry-forward erosion) via a dedicated `PRIOR SUMMARY` slot + raw-marker exclusion,
peer-aligned with hermes/opencode/codex/openclaw. ISSUE-3's *preferred* option was a structural head-pin
(never re-summarize the marker — a hard survival guarantee); that is **rejected by design** (boundary
changes are out of scope there, a head-pin accumulates markers across passes, and no surveyed peer does it).
What ships is an LLM-mediated slot+exclusion carry-forward, not a structural guarantee. This plan does not
touch `compact_messages` or the summarizer feed.

**ISSUE-8 is a calibration question, not a clear bug.** `test_savings_uses_floor_inclusive_basis`
(`tests/test_flow_compaction_proactive.py:270`) asserts floor-inclusive savings is correct (a floor-excluded
basis would overstate yield and falsely reset the counter); ISSUE-8 argues the inverse (it understates yield
and falsely *trips* the gate). Same tradeoff from opposite ends — resolved by folding it into the eval
re-pin (TASK-7), not by flipping the tested basis.

## Problem & Outcome

The proactive compaction loop has no hard convergence guard; several supporting metrics (tail sizing, spill
accumulator, savings ratio, `spill_errors`, status estimate, focus selection) are miscomputed or stale.
Individually low-to-medium severity; together they let a pathological transcript thrash indefinitely.
(Cross-compaction memory erosion — ISSUE-3 — is fixed by the separate `prior-summary-dedicated-slot` plan.)

**Outcome:** compaction converges or escalates explicitly; the floor-vs-window levers (`tail_fraction`,
`min_proactive_savings`) measure what their docstrings claim and are re-pinned by the coherence eval.

**Failure cost:** without ISSUE-2 (convergence guard), a long session on a small-window model (qwen3 / 64k —
the documented regime) can enter a static-marker-per-request treadmill that never converges — every model
request pays for a no-op compaction pass with no escalation and no error surfaced. The companion erosion
risk (summary silently dropping early facts) is carried by `prior-summary-dedicated-slot`.

## Scope

**In:** `co_cli/context/compaction.py`, `_compaction_boundaries.py`, `history_processors.py`,
`co_cli/config/compaction.py` (Field docstrings are source, not specs), `evals/eval_context_stability.py`,
and the matching `tests/test_flow_compaction_*.py`.

**Out:**
- `docs/specs/compaction.md` — updated by `sync-doc` post-delivery, never in a task.
- **ISSUE-3 (prior-marker preservation)** — owned by `prior-summary-dedicated-slot`; this plan does not
  touch `compact_messages` or the summarizer feed.
- ISSUE-8 as a code change to the savings *basis* — see Open Questions; it is resolved as an
  eval-calibration step (TASK-7), not a basis flip, because the current basis is a tested deliberate choice.
- Any backward-compat shim or migration code (zero-backward-compat policy).

## Behavioral Constraints

- `proactive_window_processor` is **fail-open**: any new escalation path (TASK-5) must still return the
  original `messages` unchanged when its fallback returns `None`, and must let `asyncio.CancelledError`
  propagate.
- `commit_compaction` remains the single writer of `compaction_applied_this_turn`; runtime stays untouched
  until commit (Task-3 atomicity invariant in `compact_messages`).
- `_MIN_RETAINED_TURN_GROUPS=1` is a hardcoded correctness invariant — not touched.
- No task may regress the existing `eval_context_stability.py` coherence gate or its
  `_MAX_PROACTIVE_PASSES` bound.

## High-Level Design

Three bands, ordered low-risk → high-value → coordinated-eval:

1. **Independent low-risk fixes** (no cross-dependencies): ISSUE-4 (accumulator), ISSUE-5 (focus marker
   skip), ISSUE-6 (`spill_errors` split), ISSUE-7 (status write-back). Each is a localized, testable change.
2. **High-value convergence fix:** ISSUE-2 (no-progress guard + escalation to `recover_overflow_history`).
   This is the headline of the plan — without it a pathological transcript thrashes forever.
3. **Floor-vs-window + eval re-pin:** ISSUE-1 (floor-aware tail, approach B) then TASK-7 re-runs
   `eval_context_stability.py` to re-pin `tail_fraction` and decide the ISSUE-8 calibration.

**Intentional basis asymmetry (do not "fix" as drift).** TASK-6 makes the *tail* floor-aware while TASK-7
deliberately leaves the *savings ratio* floor-inclusive. These are different levers on different bases by
design: the tail is a budget that must match the trigger's floor-inclusive frame so re-trigger headroom is
honest, whereas savings is a *relative-yield* gate where floor-inclusion is a calibration knob (and is
test-defended at `test_flow_compaction_proactive.py:270`). A future reader must not collapse them to one
basis.

## Tasks

### ✓ DONE TASK-1 — Fix spill accumulator drift (ISSUE-4)
- **files:** `co_cli/context/history_processors.py`, `tests/test_flow_compaction_spill_largest_tool_results.py`
- Accumulate freed **chars** as an int across the loop and floor-divide once (`aggregate = starting_tokens
  - chars_freed // CHARS_PER_TOKEN`), so both the in-loop `aggregate <= threshold` break and the terminal
  `effective_after` / `fallback_to_summarize` classification use a non-drifting value.
- Keep the existing `_spill_largest_first` 4-tuple return `(spilled_by_id, aggregate, spilled_count,
  spill_errors)` unchanged — the caller unpack at `history_processors.py:474` must not need edits.
- **shares `history_processors.py` + the spill test file with TASK-3** — sequence them (no parallel edit to
  `_spill_largest_first` / `_collect_tool_return_candidates`).
- **done_when:** a test transcript with ≥3 spilled parts that truly falls under threshold is classified
  `below_threshold` (empty skip_reason), where the per-item floor-division would have misclassified it.
- **success_signal:** the spill terminal decision matches a fresh full recount within 0 tokens.
- **prerequisites:** none

### ✓ DONE TASK-2 — Skip markers in proactive focus resolution (ISSUE-5)
- **files:** `co_cli/context/compaction.py`, `tests/test_flow_compaction_proactive.py`
- In `_resolve_proactive_focus`, skip `UserPromptPart`s that are compaction markers. Test **both**
  `is_compaction_marker` (summary/static) **and** `TODO_SNAPSHOT_PREFIX` — `is_compaction_marker` does not
  match the todo snapshot (`_compaction_markers.py:117`), so the marker-skip is incomplete without it.
- Guard the accepted branch with `isinstance(content, str)` before slicing `content[-200:]` —
  `UserPromptPart.content` may be a non-str sequence; the current unguarded `return part.content[-200:]` is
  a pre-existing latent bug fixed here since this loop is being rewritten (review discipline: fix
  pre-existing issues in touched code).
- **done_when:** with the most-recent `UserPromptPart` being an inserted marker/todo-snapshot, focus falls
  through to the latest real user message.
- **success_signal:** focus is never marker boilerplate during a pre-input thrash sequence.
- **prerequisites:** none

### ✓ DONE TASK-3 — Split `spill_errors` from "too small to spill" (ISSUE-6)
- **files:** `co_cli/context/history_processors.py`, `tests/test_flow_compaction_spill_largest_tool_results.py`
- Pre-filter spillable candidates to `len(content) > TOOL_RESULT_PREVIEW_CHARS`, or count too-small
  candidates under a separate span attribute. `request.spill_errors` must reflect only real I/O failures.
- **done_when:** a transcript of many ≤1500-char tool returns emits `request.spill_errors == 0` (not one
  per candidate).
- **success_signal:** a real spill I/O failure is the only thing that increments `request.spill_errors`.
- **prerequisites:** none

### ✓ DONE TASK-4 — Write back status estimate after L3 (ISSUE-7)
- **files:** `co_cli/context/compaction.py`, `tests/test_flow_compaction_proactive.py`
- In `_record_proactive_outcome`, set `ctx.deps.runtime.current_request_tokens_estimate = tokens_after`
  before `commit_compaction` (it already computes `tokens_after` at `compaction.py:384`).
- **done_when:** after a proactive pass applies, `deps.runtime.current_request_tokens_estimate` equals the
  post-compaction `effective_request_tokens(result)`, not the pre-compaction spill estimate.
- **success_signal:** the `main.py:467` status line reflects compacted size after an L3 pass.
- **prerequisites:** none

### ✓ DONE TASK-5 — No-progress guard + escalation (ISSUE-2)
- **files:** `co_cli/context/compaction.py`, `tests/test_flow_compaction_proactive.py`, `tests/test_flow_compaction_recovery.py`
- After an applied proactive pass, if `tokens_after >= token_count` (the pass grew or held tokens), log an
  error and escalate to **`recover_overflow_history(ctx, messages)`** — it takes a `RunContext`, not deps
  (signature `compaction.py:409`; sole existing caller `orchestrate.py:721`).
- **Surface `tokens_after`:** it is currently computed and discarded inside `_record_proactive_outcome`
  (`compaction.py:384`). Make `_record_proactive_outcome` **return** `tokens_after` so the processor reads
  it without recomputing.
- **The anti-thrash static path escalates too (intended).** The `summarize=False` static-marker pass flows
  through the same `_record_proactive_outcome` tail, so a static marker that fails to shrink tokens also
  trips the guard and escalates — desirable, not a special case to suppress.
- **Commit ordering is intentional (commit-then-escalate):** `_record_proactive_outcome` already calls
  `commit_compaction` + bumps the thrash counter before the guard runs; `recover_overflow_history` then
  calls `commit_compaction` + `_reset_thrash_state` again. The bool re-write is idempotent and the counter
  reset is the desired post-recovery state — do **not** add a second guard path that double-counts.
- Honor fail-open: if recovery returns `None`, return the original `messages`.
- **done_when:** with a spy/mock on `recover_overflow_history`, a pathological transcript whose
  unconditionally-retained last turn group alone exceeds the tail budget calls it **exactly once** (not a
  re-fired identical no-op pass); a `None` return leaves `messages` unchanged.
- **success_signal:** `co trace` shows escalation, not a repeating zero-savings proactive span, on a
  non-converging transcript.
- **prerequisites:** none

### ✓ DONE TASK-6 — Floor-aware tail sizing + docstring correction (ISSUE-1)
- **files:** `co_cli/context/_compaction_boundaries.py`, `co_cli/context/compaction.py`, `co_cli/context/history_processors.py`, `co_cli/config/compaction.py`, `tests/test_flow_compaction_boundaries.py`
- `plan_compaction_boundaries` is a **pure** function and cannot read `cfg`. Add **two** keyword params with
  defaults: `static_floor_tokens: int = 0` **and** `compaction_ratio: float = 1.0` (the planner has no
  module-level `COMPACTION_RATIO` — that constant does not exist). Compute `usable_trigger = max(0,
  int(budget * compaction_ratio) - static_floor_tokens)` and `tail_budget = tail_fraction /
  compaction_ratio * usable_trigger`. All three callers (`proactive_window_processor`,
  `spill_largest_tool_results`, `recover_overflow_history`) pass `cfg.compaction_ratio` and
  `deps.static_floor_tokens`.
- **Default-reduction invariant:** the current planner computes `tail_budget = tail_fraction * budget` as a
  **float** (`_compaction_boundaries.py:170`), compared `>` at line 182. Keep `tail_budget` a **float** (no
  outer `int()`) — wrapping it in `int()` floors the comparison and can shift a boundary by one token. With
  `compaction_ratio=1.0` default and `static_floor_tokens=0`, `usable_trigger = int(budget * 1.0) = budget`
  and `tail_budget = tail_fraction / 1.0 * budget = tail_fraction * budget` — algebraically identical to
  today, so the three existing calls in `test_flow_compaction_boundaries.py` (lines 44, 63, 84) stay green
  without edits. (Re-confirm those three lines after the change; they call the planner positionally with no
  new kwargs.)
- Correct **both** Field docstrings in `config/compaction.py`: the `tail_fraction` doc (`:44`, drop the "20%
  of the trigger point" claim) **and** the parallel stale example in the `compaction_ratio` doc (`:34`,
  `tail budget = tail_fraction × 32k ≈ 3.2k … ≈20% of the 16k trigger`). Mirror the floor caveat across both.
- **done_when:** with a large `static_floor_tokens`, the planned tail is strictly smaller than the
  floor-blind result, and post-compaction headroom below the next trigger no longer undershoots
  `tail_fraction < compaction_ratio`.
- **success_signal:** on the 64k/large-floor regime the next request does not immediately re-trigger
  compaction.
- **prerequisites:** none (independent code change; calibration validated in TASK-7)
- **note:** `fork_deps` (`deps.py:409-436`) does not copy `static_floor_tokens` (defaults to 0), so the
  floor-aware tail is floor-blind in forked paths. The proactive processor runs on the main agent where the
  floor is set, so this is not a live bug — but interpret TASK-7's re-pin on the main-agent floor only.

### ✓ DONE TASK-7 — Eval re-pin + ISSUE-8 calibration decision
- **files:** `evals/eval_context_stability.py`, `co_cli/config/compaction.py`
- Re-run `eval_context_stability.py` after TASK-6. Re-pin `tail_fraction` from the coherence result. Use the
  same run to decide ISSUE-8: keep the floor-inclusive savings basis (current tested decision) and, only if
  the eval shows premature anti-thrash degrade, re-pin `min_proactive_savings` — do **not** flip the basis
  unless the eval proves the false-trip failure mode dominates the false-reset one.
- **Reuse `prior-summary-dedicated-slot`'s eval extension:** it authors the multi-pass carry-forward probe
  in `eval_context_stability.py` (run logged, not gated). This re-pin runs on top of its carry-forward
  changes (marker preservation affects what the probe sees) and **promotes** that logged probe to a gating
  signal for the `tail_fraction` re-pin. A red gate here is a calibration result for this plan's lever, not
  a regression in the other plan's observational metric.
- **done_when:** `eval_context_stability.py` passes (coherence probe + `≤ _MAX_PROACTIVE_PASSES` bound) with
  the re-pinned levers committed.
- **success_signal:** the coherence gate passes at the re-pinned `tail_fraction`.
- **prerequisites:** TASK-6

## Testing

- Per-task functional tests assert observable behavior only (classification outcome, focus value, span
  attribute, escalation call) — never structural field presence (per testing policy).
- Reuse the existing `tests/test_flow_compaction_*.py` fixtures and `_tight_settings()` / floor harness
  already present in `test_flow_compaction_proactive.py`.
- TASK-7 is the integration gate: `eval_context_stability.py` is a real-data UAT run (no test stores, no
  caps). Tail the spans log during the run to watch LLM call timing.
- Run scoped pytest with `-x`; pipe to a timestamped `.pytest-logs/` file.

## Spec Sync (post-impl — `docs/specs/compaction.md`, via `sync-doc`)

`docs/specs/compaction.md` documents the current formulas and flow exactly, so these changes invalidate
specific passages. `sync-doc` (auto-invoked by `/orchestrate-dev` after delivery) must propagate them — the
spec is **never** edited inside a task. Ordered critical → minor:

- **★ CRITICAL — Convergence guard + escalation (TASK-5 / ISSUE-2).** The spec's §1.1 end-to-end trace and
  §2.5 `proactive_window_processor` flow show no no-progress path. Add: after an applied pass, if
  `tokens_after >= token_count` the processor escalates to `recover_overflow_history` (which re-commits and
  resets thrash state) rather than returning a no-op. Update §1.5 runtime-flag map (escalation is a new
  caller of `commit_compaction` / `_reset_thrash_state` on the proactive path).
- **★ CRITICAL — Floor-aware tail sizing (TASK-6 / ISSUE-1).** §2.5 line `tail_budget = tail_fraction ×
  budget` (and the `0.10 × 32K ≈ 3.2K` example) and the §2.5 boundary-planner invariant become stale.
  Replace with the floor-aware form `tail_fraction / compaction_ratio × usable_trigger`, document the new
  `plan_compaction_boundaries(static_floor_tokens, compaction_ratio)` signature, and correct the
  `tail_fraction` semantics (a fraction of *usable* trigger headroom, not raw window).
- **★ CRITICAL — Intentional basis asymmetry.** §2.5 currently documents savings as "both bases
  floor-inclusive." After TASK-6 the **tail is floor-aware while savings stays floor-inclusive** — the spec
  must state this asymmetry explicitly (different levers, different bases, by design) so a future editor
  does not collapse them. Mirror the plan's "Intentional basis asymmetry" note.
- **△ CONDITIONAL — Re-pinned lever values (TASK-7 / ISSUE-8).** If the eval re-pins `tail_fraction` and/or
  `min_proactive_savings`, update every documented default/example in §1.2 and §2.5 to the new values.
  Only if the values actually change.
- **· MINOR — Spill accumulator mechanics (TASK-1 / ISSUE-4).** §2.4 describes the loop accumulator; reword
  to "chars summed across the loop, floor-divided once" (no per-item floor-division drift).
- **· MINOR — `spill_errors` semantics (TASK-3 / ISSUE-6).** §2.4 span-attribute list: `request.spill_errors`
  now counts only real I/O failures; note the separate too-small handling.
- **· MINOR — L3 status write-back (TASK-4 / ISSUE-7).** §1.5 map: `current_request_tokens_estimate` now
  also written by the proactive (L3) path, not only L2 spill.
- **· MINOR — Focus marker-skip (TASK-2 / ISSUE-5).** Where focus resolution is described, note that
  compaction and todo-snapshot markers are skipped.

## Open Questions

- **ISSUE-6 / ISSUE-7 eval coverage.** Their `success_signal`s (status line, span attribute) are **not**
  exercised by `eval_context_stability.py` — they rest on per-task unit assertions alone. TASK-7's eval
  gate is not a substitute for those unit tests.

## Final — Team Lead

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> **Sequencing:** ship **after** `prior-summary-dedicated-slot` (it owns the `compact_messages` /
> summarizer-feed changes and authors the shared `eval_context_stability.py` extension TASK-7 reuses).
> Disjoint functions — no code conflict.
> Once approved (and after `prior-summary-dedicated-slot` lands), run: `/orchestrate-dev compaction-production-logic-fixes`

## Delivery Summary — 2026-06-09

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | ≥3 spilled parts truly under threshold classified `below_threshold` (single floor-division, no drift) | ✓ pass |
| TASK-2 | most-recent marker/todo-snapshot → focus falls through to latest real user message | ✓ pass |
| TASK-3 | many ≤1500-char returns emit `request.spill_errors == 0` | ✓ pass |
| TASK-4 | `current_request_tokens_estimate` == post-compaction `effective_request_tokens(result)` after L3 | ✓ pass |
| TASK-5 | no-progress pass escalates to `recover_overflow_history` exactly once; `None` leaves `messages` unchanged | ✓ pass |
| TASK-6 | large `static_floor_tokens` → planned tail strictly smaller than floor-blind (`compaction_ratio=1.0` default reduces to legacy) | ✓ pass |
| TASK-7 | `eval_context_stability.py` passes (coherence + ≤ `_MAX_PROACTIVE_PASSES`); levers re-pinned | ✓ pass (no value change) |

**Team:** TL (TASK-2/4/5 on `compaction.py`, then cross-cutting TASK-6, then TASK-7) ∥ Dev-1 (TASK-1/3 on `history_processors.py`). Groups A/B ran in parallel on disjoint files; TASK-6 integrated both.

**Tests:** scoped — 53 passed, 0 failed (boundaries 5, spill 13, recovery 6, proactive 29). LLM summarizer calls healthy (1.8–3.6s). `eval_context_stability.py`: CS.A PASS (10 turns, 4 fired passes ≤ cap 30, coherence OK — recalled `SILVER-FALCON-2029`, carry-forward preserved), CS.B PASS (5 summarizer/focus passes, savings 16–25%), CS.C skipped (eval-scaffold limit, unit-guarded). Exit 0.

**Runtime check (tracing/LLM/eval):** clean. No errors/retries/timeouts/overflow/surrogate across 1409 log lines or the spans. Compaction bounded sawtooth (`tokens_after` ~12.5–14k below the 16384 trigger every pass), `no_progress_escalation` never fired (every pass made real progress). One slow tail call (61.9s) RCA'd as decode-bound (3057 output tokens — model echoing the batch into `memory_create`), 34% of the 180s per-turn budget; not a defect.

**TASK-7 / ISSUE-8 decision:** `tail_fraction` stays 0.10 (coherence gate passes with the floor-aware tail in place); `min_proactive_savings` stays 0.10 and the floor-inclusive savings basis is kept (eval showed no premature anti-thrash degrade — `anti_thrash=0`, savings well above 10%). No `config/compaction.py` value edits.

**Doc Sync:** fixed — `docs/specs/compaction.md` (floor-aware tail formula, no-progress escalation flow, basis asymmetry, L3 status write-back, `spill_errors` semantics, single floor-division, focus marker-skip, +6 test gates). Cross-doc index clean.

**Overall: DELIVERED**
All 7 tasks pass `done_when`, lint clean, scoped tests green, eval gate green, doc sync fixed.

## Implementation Review — 2026-06-09

**Stance: issues exist — PASS earned.** 3 parallel cold-read evidence subagents + adversarial re-read of the load-bearing claims + full suite + behavioral check.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | ≥3 spilled parts under threshold → spill-fired-and-fit terminal | ✓ pass | `history_processors.py:354,377,379` — `chars_freed` int accumulator, single floor-division `starting_tokens - chars_freed // CHARS_PER_TOKEN`; 4-tuple + caller unpack `:489` unchanged |
| TASK-2 | most-recent marker/todo-snapshot → focus falls through to real user msg | ✓ pass | `compaction.py:485-493` — `isinstance(content,str)` guard + `is_compaction_marker(content) or content.startswith(TODO_SNAPSHOT_PREFIX)` skip |
| TASK-3 | many ≤1500-char returns → `spill_errors == 0` | ✓ pass | `history_processors.py:444` — `len(part.content) > TOOL_RESULT_PREVIEW_CHARS` spillable pre-filter; `spill_errors` increments only on >1500-char no-op spill |
| TASK-4 | estimate == post-compaction `effective_request_tokens(result)` | ✓ pass | `compaction.py:409-410` — `current_request_tokens_estimate = tokens_after` before `commit_compaction` |
| TASK-5 | no-progress pass escalates once; None → messages unchanged | ✓ pass | `compaction.py:355,411` (`-> int`, `return tokens_after`) + `:648-656` guard inside `try` (except `:658`), fail-open `return recovered if recovered is not None else messages` |
| TASK-6 | large static_floor → tail strictly smaller than floor-blind | ✓ pass | `_compaction_boundaries.py:135-136,183-184` — kwargs `static_floor_tokens=0`/`compaction_ratio=1.0`, `tail_budget` is FLOAT (no `int()`), reduces to `tail_fraction*budget` at defaults; all 3 callers pass cfg (`compaction.py:448,596`, `history_processors.py:429`); both Field docstrings corrected |
| TASK-7 | eval passes (coherence + ≤ `_MAX_PROACTIVE_PASSES`); levers committed | ✓ pass | `REPORT-eval-context-stability.md:11-17` CS.A/CS.B PASS (4≤30 passes, coherence OK); `config/compaction.py:40,64` levers unchanged (0.10/0.10) — "keep basis" decision; HARD bounded/per-pass/anti-thrash gates real (`eval...py:491-542`), coherence SOFT by design (`:556-558`) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Scope: `docs/specs/compaction.md` modified | — | not an issue | Expected sync-doc output (plan "Out" scope) |
| Scope: `docs/REPORT-eval-context-stability.md` modified | — | not an issue | Eval-run auto-appended entry (sanctioned eval output) |
| done_when wording: TASK-1 says "below_threshold" but spill-fired-and-fit terminal emits `""` | plan TASK-1 | minor | No code impact — test correctly asserts `""`; two distinct correct terminal strings. Noted, not changed |
| Untyped test spies `spy`/`none_recovery` + `monkeypatch` param | test_flow_compaction_proactive.py | minor | Repo-wide convention; monkeypatch is the collaborator seam TASK-5 done_when authorizes (SUT runs real). No action |

_No blocking findings — Phase 4 auto-fix loop empty. Adversarial re-read confirmed all PASS claims (float `tail_budget`, fail-open escalation placement) with no downgrades._

### Tests
- Command: `uv run pytest -x -p no:cacheprovider` (full suite)
- Result: **636 passed, 0 failed**, 1 warning, in 148s. No LLM call >30s. (3 `status=ERROR` spans are intentional surrogate-recovery test fixtures, model=fake — not failures.)
- Log: `.pytest-logs/20260516-180318-review-impl-full.log`
- Scoped re-verify: 53 compaction tests green (boundaries 5, spill 13, recovery 6, proactive 29)

### Behavioral Verification
- CLI app boots cleanly with the changed compaction stack (`uv run co trace --help` loads the module). `co status`/`co logs` are not commands in this CLI.
- `success_signal`s verified via the real-agent eval drive (`context-stability-20260609T052643Z`): TASK-4 `tokens_after` recorded on every fired pass; TASK-6 5 bounded passes each followed by below-threshold fast-paths (no re-trigger storm). TASK-5 escalation is pathological-only — correctly dormant in the healthy run, firing proven by `test_no_progress_escalates_to_recovery_once`.

### Overall: PASS
All 7 tasks implement their spec with file:line evidence; full suite green (636 passed); no blocking findings; behavioral success-signals confirmed. Ready for Gate 2 → `/ship`.
