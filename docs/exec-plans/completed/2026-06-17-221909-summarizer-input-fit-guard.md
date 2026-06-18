# Summarizer Input Fit Guard (degrade-safe when the region won't summarize)

## Context

co compacts by summarizing the dropped middle in a **single** LLM call.
`summarize_messages` (`co_cli/context/summarization.py:351-420`) assembles
`task_prompt` + `user_message` (`"TURNS TO SUMMARIZE:\n{serialized}"`, plus an
optional `PRIOR SUMMARY` slot) and calls `llm_call` directly — **with no check that
the assembled prompt fits the model context window.** There is no chunking and no
codex/opencode-v2-style "if the summary input won't fit, degrade" pre-flight guard.

Today's behavior when the dropped region is too large to summarize in one call
(traced through the source):
1. `summarize_messages` calls `llm_call`, which makes a real provider request that
   returns a context-length error (HTTP 400/413).
2. `_gated_summarize_or_none` (`compaction.py:240-287`) catches it via the generic
   `except Exception` → labels it `CompactionFallbackReason.SUMMARIZER_ERROR`,
   **increments `compaction_skip_count`** (toward the circuit-breaker trip,
   `:271-277`), and returns `None`.
3. `compact_messages` builds a **static marker** (drops the whole middle) — the pass
   still shrinks, so the no-progress guard is satisfied.
4. The overflow path (`orchestrate.py:_attempt_overflow_recovery:697-725`) is a
   one-shot (`overflow_recovery_attempted`), so a still-overflowing retry terminates
   cleanly as "unrecoverable" — **no infinite loop.**

So co already *degrades* — but at three real costs:
- a **wasted provider round-trip** (latency; the "71.95s-scare" slow-failure class the
  `CompactionFallbackReason` docstring already calls out, `:124`);
- the failure is **mislabeled `SUMMARIZER_ERROR` and poisons the circuit breaker** —
  a legitimately-oversized region counts the same as a flaky model, so a single huge
  paste can push `compaction_skip_count` toward tripping the breaker for *subsequent
  normal* compactions;
- the static marker **discards the whole middle** with no recap, when a smaller
  summarization boundary might have preserved one (pre-drop content is still captured
  for the dream daemon via `_snapshot_and_kick_review`, so it is not lost from the
  system, only from the live recap).

Current state is consistent and plannable. No pre-flight size check exists anywhere
on the summarizer path; `deps.model_max_context_tokens` is the authoritative window
(`summarization.py:129-137`); `estimate_message_tokens` / `CHARS_PER_TOKEN` already
provide the local estimator.

## Problem & Outcome

**Problem.** A pathological dropped region whose **non-tool** text survives
tool-return stripping (one enormous user paste, a giant pasted log, a huge assistant
block) makes the summarizer prompt exceed the model context. The current path only
discovers this *after* a wasted provider round-trip, then miscounts it as a model
failure that erodes the circuit-breaker budget for healthy compactions.

**Failure cost.** Not a crash (the one-shot recovery + generic catch already prevent
that) — the cost is silent and cumulative: wasted slow round-trips on oversized
regions, and circuit-breaker poisoning that can demote *later, healthy* compactions
to static markers because an unrelated big-paste turn ran the skip count up.

**Outcome.** Before the summarizer `llm_call`, estimate whether the assembled prompt
(task prompt + serialized turns + prior-summary slot + reserved output) fits
`deps.model_max_context_tokens`. If it does not, **skip the call** and degrade to the
existing static-marker path via a *distinct* fallback reason that does **not** count
against the circuit breaker. Net: no wasted round-trip, correct breaker accounting,
identical end-state (static marker) for the genuinely-unsummarizable case.

## Scope

**In:**
- A pre-flight fit estimate in the summarizer path and a typed
  `SummarizerInputTooLarge` raised when the assembled prompt won't fit.
- A new `CompactionFallbackReason.INPUT_TOO_LARGE`; `_gated_summarize_or_none`
  catches the typed exception **distinctly** — emits the new reason, returns `None`,
  and does **not** increment `compaction_skip_count` (not a model failure; retrying
  the same region won't help; must not trip the breaker).
- OTEL `compaction_fallback` event carrying the new reason (the existing mechanism).
- Functional tests: oversized prompt → no `llm_call` made → static marker → skip
  count unchanged; in-budget prompt → call proceeds as today.

**Out:**
- **Chunking / chunk→summarize→merge (openclaw).** Rejected by design: a multi-stage
  merge adds an extra LLM stage, fights small-model plan coherence, and co is the
  deliberate lone non-chunker among peers. Not in scope, not a follow-up.
- **Boundary-shrinking to preserve a recap** (re-plan a smaller dropped region that
  *does* fit, instead of a static marker). A plausible richer degrade, but a larger
  change to `plan_compaction_boundaries`; deferred to Open Questions, not built here.
- Touching the no-progress / anti-thrash logic, or the one-shot overflow-recovery
  guard in `orchestrate.py`.
- Specs (`docs/specs/compaction.md`) — updated by `sync-doc` post-delivery.

## Behavioral Constraints

- **Realtime-local estimate, no provider round-trip to detect overflow.** The fit
  check is a local char/token estimate against `deps.model_max_context_tokens` minus
  the reserved output cap — never a trial request, never provider-reported usage.
- **Breaker integrity.** `INPUT_TOO_LARGE` must not increment `compaction_skip_count`
  and must not count as a probe — it is orthogonal to the flaky-model breaker. Only
  genuine `SUMMARIZER_ERROR` / `EMPTY_SUMMARY` move the counter (unchanged).
- **Identical degrade end-state.** When the guard fires, the result is the same static
  marker the generic catch produces today — same `build_compaction_marker` static
  branch, same shrink, same dream-daemon snapshot. The change is *how we get there*
  (pre-flight, correctly accounted), not the end-state.
- **Conservative threshold.** The estimate must err toward *attempting* the summary
  (false "too large" wastes the recap), so include a small safety margin only against
  the hard window, matching the existing `chars/4` estimator's bias.
- **Both paths covered.** The guard lives at the summarizer chokepoint, so it protects
  both proactive compaction and `recover_overflow_history`'s PATH 2 (which calls
  `compact_messages` → the same summarizer).

## High-Level Design

```
# summarization.py — in summarize_messages, BEFORE entering the asyncio.timeout ctx
instruction_str = f"{_SUMMARIZER_SYSTEM_PROMPT}\n\n{task_prompt}"   # bind the exact sent string
assembled_tokens = (len(instruction_str) + len(user_message)) // CHARS_PER_TOKEN
if assembled_tokens + cap > deps.model_max_context_tokens - _FIT_SAFETY_MARGIN:
    raise SummarizerInputTooLarge(assembled_tokens, deps.model_max_context_tokens)
async with asyncio.timeout(LLM_RUN_TIMEOUT_SECS):
    return await llm_call(..., instructions=instruction_str, ...)   # unchanged otherwise
```

The guard is raised **before** the `asyncio.timeout` context (`:414`) — a local size
check must not be wrapped in the LLM timeout (CD-m-2). It estimates against the exact
assembled instruction string + `user_message` (not a phantom `instructions` var);
`serialized` dominates, so a coarse `len()//CHARS_PER_TOKEN` estimate is fine (CD-m-1).

```
# compaction.py — _gated_summarize_or_none
try:
    summary_text = await summarize_dropped_messages(...)
except SummarizerInputTooLarge:
    _emit_compaction_fallback(CompactionFallbackReason.INPUT_TOO_LARGE)
    return None                                  # NOTE: no skip_count increment
except Exception:
    ... existing SUMMARIZER_ERROR branch (unchanged) ...
```

- `SummarizerInputTooLarge` is a small typed exception in `summarization.py`
  (the module that raises it); `compaction.py` imports it. It carries the estimated
  and budget token counts for the span/log.
- `_FIT_SAFETY_MARGIN` is a commented constant — a **fixed token headroom against the
  hard window**, deliberately not a ratio (its comment must say so, since its neighbors
  `SUMMARY_*_RATIO` are fractional — CD-m-3); small, biased toward attempting; margin
  sizing is trace-tunable (Open Questions).
- The `cap` (reserved output) is already computed in `summarize_messages`
  (`:378-379`); the guard reuses it so the reservation is consistent with the call.

## Tasks

### ✓ DONE TASK-1 — Pre-flight fit guard + typed exception in `summarize_messages`
- **files:** `co_cli/context/summarization.py` (new `SummarizerInputTooLarge`,
  `_FIT_SAFETY_MARGIN`, the pre-`llm_call` estimate)
- **done_when:** `uv run pytest tests/context/test_summarizer_fit_guard.py` passes —
  with `co_cli.context.summarization.llm_call` monkeypatched to record calls, a `deps`
  whose `model_max_context_tokens` is smaller than the assembled prompt makes
  `summarize_messages` raise `SummarizerInputTooLarge` **without** calling `llm_call`;
  a `deps` with an ample window calls `llm_call` exactly once and returns its output.
- **success_signal:** An oversized summarizer prompt is detected locally and never
  reaches the provider.
- **prerequisites:** none

### ✓ DONE TASK-2 — Distinct `INPUT_TOO_LARGE` fallback that spares the circuit breaker
- **files:** `co_cli/context/compaction.py` (new `CompactionFallbackReason.INPUT_TOO_LARGE`;
  the distinct `except` branch in `_gated_summarize_or_none`)
- **done_when:** `uv run pytest tests/context/test_input_too_large_fallback.py` passes —
  when the summarizer raises `SummarizerInputTooLarge`, `compact_messages` returns a
  result whose dropped region is a **static marker** (no LLM summary), and
  `deps.runtime.compaction_skip_count` is **unchanged** across the pass; and — through
  the same `compact_messages` boundary, with the monkeypatched summarizer raising a
  plain `Exception` — the skip count still **increments** (the existing
  `SUMMARIZER_ERROR` behavior is untouched). Both halves assert through
  `compact_messages`, not by calling the private `_gated_summarize_or_none` directly
  (CD-m-4).
- **success_signal:** An oversized region degrades to a static marker without eroding
  the circuit-breaker budget that protects healthy compactions.
- **prerequisites:** TASK-1

## Testing

- Functional, no real LLM: both tasks use a monkeypatched `llm_call` seam (the real
  module name, no signature change) and crafted `deps` (small vs ample
  `model_max_context_tokens`). Assertions are observable outcomes — whether the
  provider call was made, the marker is static, the skip count moved — mirroring
  `done_when`. No structural assertions.
- No new eval: this is a degrade-path correctness/efficiency fix with no model-quality
  dimension to measure on real data. The existing `eval_summarizer_fidelity.py` and
  `eval_context_stability.py` continue to exercise the in-budget happy path unchanged.

## Open Questions

- **Static marker vs. boundary-shrink.** When the region won't fit, this plan degrades
  to a static marker (drop the middle). A richer alternative is to re-plan a *smaller*
  dropped region that does fit and still produce a recap. That requires feeding the fit
  budget back into `plan_compaction_boundaries` and is a materially larger change —
  deferred. Decide at Gate 1 whether the static-marker degrade is sufficient (the
  pre-drop snapshot already preserves full content for the dream daemon, which argues
  it is).
- **Margin sizing.** `_FIT_SAFETY_MARGIN` starts conservative (bias toward attempting
  the summary). Whether real oversized regions cluster just over the window — making a
  larger margin worthwhile to avoid a 400 that the estimate narrowly missed — is a
  tuning question answerable only from production `INPUT_TOO_LARGE` vs late-400 trace
  rates, not guessable up front.

## Final — Team Lead

Plan approved. Cycle C1: both Core Dev and PO approved with no blocking issues. Core
Dev confirmed the mechanism against source (typed exception propagates cleanly through
the single `compact_messages` → `_gated_summarize_or_none` chokepoint; both `/compact`
and overflow PATH 2 are covered; fit budget against `model_max_context_tokens - cap -
margin` is correct with no double-counting; `INPUT_TOO_LARGE` ordered before the
generic `except`). Four minor items adopted: bind the exact assembled instruction
string for the estimate; raise before the `asyncio.timeout` context; document the
margin as a fixed window-headroom (not a ratio); assert both skip-count halves through
the `compact_messages` boundary. PO confirmed value (breaker-accounting correctness is
load-bearing, latency is secondary), chunking-rejection, and that this is orthogonal to
plan #2 — proceed independently.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev summarizer-input-fit-guard`

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | oversized prompt raises `SummarizerInputTooLargeError` without calling `llm_call`; in-budget calls it once and returns output | ✓ pass |
| TASK-2 | `SummarizerInputTooLargeError` → static marker, skip count unchanged; plain `Exception` → static marker, skip count increments — both through `compact_messages` | ✓ pass |

**Tests:** scoped — 4 passed, 0 failed (`tests/context/test_summarizer_fit_guard.py`, `tests/context/test_input_too_large_fallback.py`)
**Doc Sync:** fixed — `docs/specs/compaction.md`: §2.9 degradation row + §2.6 breaker counter-neutrality note + `_FIT_SAFETY_MARGIN` constant row added.

**Naming note:** the exception was renamed `SummarizerInputTooLarge` → `SummarizerInputTooLargeError` to satisfy ruff N818 and match the codebase convention (`ResourceBusyError`, `SSRFRedirectError`); `CompactionFallbackReason.INPUT_TOO_LARGE` keeps the plan's enum name.

**Overall: DELIVERED**
Pre-flight fit guard skips the doomed provider round-trip and degrades to a static marker without poisoning the circuit breaker; both summarizer-path callers (proactive + overflow PATH 2 + `/compact`) covered via the shared chokepoint.

**Next step:** `/review-impl summarizer-input-fit-guard`

---

## Follow-up Correction — 2026-06-18

The original delivery was marked DONE on its two new test files alone and was **not** run against the broader compaction suite. A later pass (triggered while delivering the `centralize-compaction-tuning-constants` plan) found the gaps the narrow done_when missed:

1. **Status mislabel (real bug).** `_record_proactive_outcome` routed the `INPUT_TOO_LARGE` degrade into the `"Summarizer failed — used static marker."` status, because it only saw `summary_text is None` + `summary_skipped=False` (an anti-thrash-only flag) + a present model. The breaker accounting was correct, but the **user-facing label** contradicted the plan's whole premise (a deliberate, non-failure degrade). Fixed by threading an `input_too_large` bool out of `_gated_summarize_or_none` → `compact_messages` (now returns `(result, summary_text, input_too_large)`) → `_record_proactive_outcome`, which reports `"Compacted (static marker)."` for it (same as the anti-thrash deliberate skip). `test_input_too_large_fallback.py` now asserts the flag on both halves.

2. **Two sibling regressions** in `tests/test_flow_compaction_proactive.py` (`test_successful_compaction_resets_skip_count`, `test_closing_callback_fires_compacted_on_success`). Both drove a *successful* summary at `model_max_context_tokens=200` — impossible under the guard, whose 2,000-token safety margin alone exceeds a 200 window, so it always trips. Fixed with a `_summary_fit_settings()` helper: an 8,192 window (guard passes) + `compaction_ratio=0.03` (the small 320-token fixture still triggers), keeping the real summary call as cheap as before. Three other `window=200` tests were unaffected — they assert outcomes a static marker also satisfies.

3. Signature ripple: `compact_messages`' three callers updated (`proactive`, recovery PATH 2, `/compact`).

**Verification:** full compaction suite green — `test_flow_compaction_proactive.py` 27 passed (incl. both real-LLM success tests), `test_input_too_large_fallback.py` + `test_summarizer_fit_guard.py` + 5 other compaction files passed; lint clean. Spec §2.6 closing-status line updated to attribute `"Compacted (static marker)."` to both deliberate degrades.

**Lesson:** a done_when scoped to a feature's own new test files is blind to regressions it induces in sibling behavior sharing the same chokepoint — `/review-impl`'s full-suite pass is what catches this. Treat this plan as review-impl'd by the above.
