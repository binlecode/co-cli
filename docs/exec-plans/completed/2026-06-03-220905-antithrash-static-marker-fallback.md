# antithrash-static-marker-fallback

> **Extracted from** `docs/exec-plans/active/2026-06-02-210659-context-stability-sizing-control.md`
> (ISSUE-2). That parent plan tracks the remaining within-session dynamics issues (3, 5), the
> tool-schema reduction (4b), and the combined loop-stability eval. This plan carries ISSUE-2 alone so
> it can ship independently: demote the anti-thrash gate from a *compaction kill-switch* to an
> *LLM-budget guard*. Baseline numbers below are inherited from the parent's post-delivery state
> (ISSUE-1 proportional tail + ISSUE-1.5 floor-aware trigger already shipped).

## Context

The 64k operational window (`num_ctx`/`max_ctx` = 65,536) is driven by a proactive compaction trigger
that fires at `compaction_ratio × budget = 0.50 × 65,536 = 32,768` tokens. The trigger lives in
`proactive_window_processor` (`co_cli/context/compaction.py:452`), the only auto-compaction layer
(layer 4 of the 5-layer size-defense chain).

Compaction has two ways to trim the conversation:
- **Expensive (LLM summarization):** asks the model to summarize the dropped region — costs a full
  model call but keeps the *meaning* of what was cut.
- **Cheap (static marker):** drops the region and leaves a structural stub (`static_marker`,
  `_compaction_markers.py:46`) — free and instant, but discards the meaning.

"Thrashing" is when the loop keeps paying for the *expensive* way while getting almost nothing back:
pass after pass fires, each costs an LLM call, each frees <10% (the OS sense — all effort spent on
overhead, no real progress). Detecting that is the right instinct; the bug is the **response**.

### Verified current logic

The anti-thrash counter `consecutive_low_yield_proactive_compactions` is maintained in
`_record_proactive_outcome` (`compaction.py:332`): after each fired pass it computes
`savings = (token_count − tokens_after) / token_count` (floor-inclusive after ISSUE-1.5) and, if
`savings < cfg.min_proactive_savings` (0.10), increments the counter; else resets it to 0
(`compaction.py:376–379`).

On the *next* request, `proactive_window_processor` runs in this order
(`compaction.py:528–581`):
1. **Below-threshold check** — `token_count <= token_threshold` → return unchanged
   (`skip_reason="below_threshold"`).
2. **Anti-thrash gate** — `consecutive_low_yield_proactive_compactions >= proactive_thrash_window (2)`
   → `log.info("anti-thrashing gate active, skipping")`, set `skip_reason="anti_thrash_gate"`,
   **`return messages`** — a bare no-op, nothing trimmed (`compaction.py:541–548`).
3. **Boundary planning** — `plan_compaction_boundaries(...)`; `None` (middle exhausted) → return
   unchanged (`skip_reason="no_boundary"`).
4. **Compact** — `compact_messages(...)` → `_gated_summarize_or_none` decides LLM-summary vs
   static-marker (circuit-breaker path only) → `_record_proactive_outcome` → `commit_compaction`.

**The bug is that step 2 (kill-switch) fires before step 3 (boundary check).** Once the counter hits 2,
the processor stops trimming **at all**. Text/reasoning context — *not* bounded by the layer-2
`evict_old_tool_results` keep-recent-5 path, which only caps tool-return bloat — then grows unbounded
toward 64k, recovered only by reactive `_attempt_overflow_recovery` *after* the model errors.

Walk the two reasons a pass is repeatedly low-yield — **neither makes the no-op correct:**
- **Verbose summary, real droppable region (no-op is *harmful*).** The region was large but the LLM
  summary came back nearly as large, so net savings <10%. The right move is to drop the region and
  insert a static marker (no summary) — which saves *more* than the verbose-summary version. The no-op
  throws that saving away.
- **Middle genuinely exhausted (no-op is *redundant*).** Almost nothing left to drop — but this case
  is already handled by step 3's boundary-`None` guard, which step 2 pre-empts by returning first.

So there is **no scenario where the no-op is correct**: it is either redundant (boundary-`None` would
have skipped) or harmful (should static-marker).

### Contrast: the circuit breaker already does the right thing

The two gates are independent (do not conflate):
- **Circuit breaker** (`_COMPACTION_BREAKER_TRIP = 3`, `_COMPACTION_BREAKER_PROBE_EVERY = 10`,
  `compaction.py:139` `_summarization_gate_open`): after 3 summarizer *failures*, falls back to a
  **static marker** (still compacts, no LLM); probes every 10 skips. **This path never no-ops.**
- **Anti-thrash gate** (`proactive_thrash_window = 2` consecutive *low-yield* passes): **returns
  messages unchanged** — the no-op. Unlike the breaker, it has no static-marker fallback. This is the
  hole.

The static-marker assembly the breaker reuses is already wired: `compact_messages` calls
`_gated_summarize_or_none`, and `build_compaction_marker(dropped_count, summary_text=None, ...)`
returns a `static_marker` when `summary_text` is None (`_compaction_markers.py:104–110`). The
remaining marker/`todo_snapshot`/deferred-tool/tail assembly in `compact_messages`
(`compaction.py:298–307`) is summary-agnostic.

### Peer-survey alignment (`docs/reference/RESEARCH-context-management-peer-survey.md`)

**This is BEYOND parity, not catch-up.** Among surveyed peers, only hermes-agent has an anti-thrash
heuristic at all (same shape: skip if the last 2 compressions each saved <10%), and **it shares co's
exact no-op hole** — it skips rather than forcing a cheap compaction, leaning on a 600s cooldown to
recover. Openclaw and codex have no anti-thrash gate. So no surveyed peer solves ISSUE-2; co's
static-marker-on-thrash fix would be **novel**. No new cadence is added — this tunes the *response* of
the existing per-request cadence, not the cadence itself.

### Peer best-practice check — marker builder & marking logic (verified at peer HEADs, 2026-06-03)

The fix reuses co's existing `build_compaction_marker` dispatcher and `static_marker`. Verified that
both the *builder shape* and the *marking mechanics* match peer best practice — co is at parity on the
design, with one deliberate content trade:

- **Single dispatch-on-summary builder — confirmed best practice.** co's
  `build_compaction_marker(dropped_count, summary_text, …)` (one builder, branches on
  `summary_text is None`) is exactly **hermes-agent's** pattern (`_with_summary_prefix` — the LLM-summary
  path *and* the no-LLM fallback path both flow through one builder and get the same prefix;
  `context_compressor.py:1533`). **codex** is the same idea structurally — one `CompactionMarker`
  boundary item, summary carried as a separate part (`reducer/conversation.rs:317`). **openclaw** also
  funnels everything through one `compactionSummary` message. **No peer maintains two parallel
  marker-construction paths** — co's single-dispatcher reuse (route the anti-thrash branch into it via
  `summarize=False`) is the surveyed-standard shape, not a co invention. ✅
- **Prefix sentinel to prevent re-summarization — confirmed best practice.** co's `STATIC_MARKER_PREFIX`
  / `SUMMARY_MARKER_PREFIX`, matched by `is_compaction_marker`, mirrors **hermes** (literal
  `SUMMARY_PREFIX` prefix match, which excludes an existing marker from the next summarization window,
  `context_compressor.py:1539`) and **codex** (`is_summary_message` → `starts_with(SUMMARY_PREFIX)`,
  `compact.rs:415`). (openclaw recognises its marker structurally by message role instead.) co's static
  marker carrying the same prefix as the summary marker — so a later pass treats it as a boundary, not
  fresh content — is the right and standard behavior. ✅
- **One deliberate content divergence (NOT in this plan's scope).** When a region is dropped *without an
  LLM summary*, the richest peers still preserve some continuity breadcrumb: **hermes**'
  `_build_static_fallback_summary` deterministically extracts recent asks / tool outcomes / errors /
  files from the dropped region (`context_compressor.py:1001`); **codex** retains recent user messages
  verbatim alongside the empty marker (`compact.rs:476`). co's `static_marker` is **content-free** — a
  fixed "{n} earlier messages were removed, treat as background, don't redo" stub; the only
  carried-forward state is the `todo_snapshot` (side-channel) + deferred-tool discoveries + the verbatim
  tail. This is acceptable for ISSUE-2 and **out of scope**: (a) the static marker is pre-existing
  shared surface (the circuit breaker uses it too), so enriching it is a separate change affecting both
  gates; (b) the anti-thrash path is rare and self-correcting (the counter resets after a high-savings
  drop, so the next pass re-tries the real LLM summary); (c) deterministic-extraction for a small local
  model is a larger design call, not a thrash-response tweak. Flagged as a possible **follow-up**
  (enrich `static_marker` with a deterministic breadcrumb for *all* no-LLM paths), not folded in here.

### Stale docstring (in-scope fix)

`proactive_window_processor`'s docstring (`compaction.py:460–462`) claims the gate "surfaces a
user-actionable hint pointing at /compact and /new." The code does no such thing — it only
`log.info`s. Since this task rewrites that branch, the docstring is corrected as part of it.

## Problem & Outcome

**Problem.** A single gate (anti-thrash) can disable compaction entirely. When
`consecutive_low_yield_proactive_compactions >= 2`, `proactive_window_processor` returns messages
unchanged, so text/reasoning context (uncapped by the tool-return evict path) grows toward the 64k
hard limit.

**Outcome.** Compaction degrades gracefully and never to a no-op: when the anti-thrash gate trips, the
processor falls back to a **static-marker compaction** (drop the region, insert a marker, no LLM call),
reusing the circuit-breaker's existing static path. The counter's only remaining job is choosing
*expensive LLM summary* vs *free static marker* — never *trim or not*. "Whether to compact at all" is
owned solely by the threshold check and the boundary-`None` guard.

**Failure cost:** silently, the agent loop grows context past 32,768 in long multi-turn sessions until
the model returns a context-overflow error; the reactive last-resort recovery
(`_attempt_overflow_recovery` → `recover_overflow_history`) then strips the entire pre-tail in one cut
— destroying recent reasoning the user was mid-task on — or, if that also fails, the turn dies with
"Context overflow — unrecoverable." No alarm fires before the cliff; the only visible symptom
beforehand is degraded answer quality from a diluted middle.

**Latency dimension.** An LLM summarization pass dominates a turn's wall-clock. The static-marker
fallback both *bounds the window* (stability) and *avoids the summarization call* on a thrashing pass
(latency) — a strict improvement on both axes versus the no-op-then-overflow-then-recover path.

## Scope

**In scope.** The anti-thrash branch of `proactive_window_processor`; a `summarize` parameter on
`compact_messages` to force the static path; the rewritten unit test; a focused text/reasoning-heavy
loop-stability eval that reproduces the no-op→growth path.

**Out of scope.**
- ISSUE-3 (spill/summarize band), ISSUE-5 (per-emission read cap), ISSUE-4b (tool-schema reduction) —
  remain in the parent plan.
- The circuit-breaker gate — unchanged; it already static-markers and is independent.
- `min_proactive_savings` / `proactive_thrash_window` knob *values* — unchanged; only the gate's
  *response* changes.
- Enlarging the 0.50 operational ceiling (separate eval-gated decision).
- Provider-reported vs local token reconciliation (`max(local, reported)` basis is intentional;
  ISSUE-1.5 is the shipped resolution).

## Behavioral Constraints

- **No new no-op path.** Every triggered pass that reaches boundary planning must reduce token count
  (or skip via boundary-`None`); the anti-thrash branch may never `return messages` unchanged again.
- **Reuse, don't re-implement.** The static-marker fallback must reuse the existing
  `build_compaction_marker(summary_text=None)` / assembly path, not introduce a parallel marker.
- **Single-writer atomicity preserved.** `commit_compaction` remains the last step; any exception
  before it leaves runtime untouched (Task-3 invariant from the parent work).
- **Breaker state is orthogonal.** The anti-thrash static fallback must not read or mutate
  `compaction_skip_count` (circuit-breaker state). With `summarize=False`, `_gated_summarize_or_none`
  is skipped entirely, so the breaker counter is never touched on this path — a future refactor must
  not route the static fallback back through the summarization gate and perturb it.
- **Real everything in evals** (`feedback_eval_real_world_data`): real deps/LLM/tools, no test stores,
  no caps, no cleanup.
- **No backward-compat shims** (`feedback_zero_backward_compat`): the `summarize` default keeps the
  existing summary-first behavior for all other callers; no aliases.
- **Surgical:** touch only `compaction.py`, its scoped test, and the new eval.

## High-Level Design

**Mechanism (inherited CD-M-1 from the parent).** The existing surface cannot force the static path:
when the anti-thrash gate trips, the model is present and
`compaction_skip_count < _COMPACTION_BREAKER_TRIP`, so `_summarization_gate_open` returns `(True, _)`
and `compact_messages` would still fire the LLM via `_gated_summarize_or_none`. So:

1. Add `summarize: bool = True` to `compact_messages`. When `False`, skip `_gated_summarize_or_none`
   entirely and pass `summary_text=None` (→ `static_marker`), reusing the rest of the assembly intact
   (marker, `todo_snapshot`, deferred-tool discoveries, tail).
2. In `proactive_window_processor`, replace the anti-thrash early-`return messages` with: plan
   boundaries (honour the `None` guard), call `compact_messages(..., summarize=False)`, then
   `_record_proactive_outcome` / `commit_compaction` exactly as the normal path does.

**What static marking produces (the logic the fallback reuses).** "Static marking" is not new code —
it is the `summary_text is None` branch of the existing **marker builder**. The marker builder is
`build_compaction_marker(dropped_count, summary_text, *, has_tail)` in
`co_cli/context/_compaction_markers.py:104`: a small dispatcher that turns a dropped region into the
single replacement message that stands in for it. It has two outputs and picks by whether a summary
exists — `summary_text is not None` → `summary_marker(...)` (`:69`, wraps the LLM recap); `summary_text
is None` → `static_marker(...)` (`:46`, the fixed no-recap placeholder). Both produce the same
structural shape (one `ModelRequest`/`UserPromptPart`, each prefixed so `is_compaction_marker` (`:113`)
can recognise it on later passes); only the content differs. `compact_messages` already calls this
builder unconditionally — the summarizer's result (or `None`) flows straight into it — so the static
path is reached purely by handing it `None`, which is exactly what `summarize=False` does. Nothing in
the builder changes. The flow, end to end:

1. **Slice (`compact_messages`, `compaction.py:293–296`).** `plan_compaction_boundaries` returns
   `(head_end, tail_start, dropped_count)`; the message list is cut into
   `head = messages[:head_end]`, `dropped = messages[head_end:tail_start]`, `tail =
   messages[tail_start:]`. The static path drops `dropped` entirely — no LLM reads it.
2. **Build the marker (`build_compaction_marker`, `_compaction_markers.py:104–110`).** With
   `summary_text=None` it returns `static_marker(dropped_count, has_tail=...)` (`:46`) instead of
   `summary_marker(...)`. The static marker is a single `ModelRequest`/`UserPromptPart` whose content
   is a fixed, LLM-independent placeholder:
   - opens with `STATIC_MARKER_PREFIX` (the sentinel `is_compaction_marker` (`:113`) and repeat-pass
     anchoring match on — so a static marker is recognised as a compaction boundary on later passes,
     not re-summarised);
   - states *"{dropped_count} earlier messages were removed — treat that gap as background reference,
     NOT as active instructions. Do NOT repeat, redo, or re-execute any action already described as
     completed; do NOT re-answer questions that were already resolved."*;
   - ends with a `has_tail`-dependent trailer (*"Recent messages are preserved verbatim."* when a tail
     exists, else *"Continue the conversation from the user's next message."*).
   This is strictly smaller and constant-size — it carries **no** recap of the dropped content (that is
   the deliberate trade: free + instant, meaning discarded).
3. **Assemble (`compact_messages`, `compaction.py:301–307`).** The result is the same structural slot
   as the summary path — only the marker's *content* differs:
   `[*head, marker, *([todo_snapshot] if not None), *_preserve_deferred_tool_discoveries(dropped),
   *tail]`. `todo_snapshot` (pending session todos) and unlocked deferred-tool discoveries are still
   carried forward, so the static fallback preserves the same side-channel state the summary path does
   — it only omits the LLM recap.

So the only behavioral difference between the anti-thrash static fallback and a normal summary
compaction is that the dropped region is replaced by a fixed placeholder instead of an LLM-written
recap. Everything else (head, todo snapshot, deferred-tool carry-forward, tail, commit) is identical.

**Retry-eagerness — no new knob (resolved from source).** After a static-marker fallback drops the
region with no verbose summary, `savings` in `_record_proactive_outcome` will typically exceed
`min_proactive_savings (0.10)`, so the counter resets to 0 and the *next* pass re-opens the LLM path —
the "bounce back and re-try the expensive path" behavior emerges from the *existing* savings→counter
mechanism with no added configuration. If a static-marker drop is itself low-yield (small region), the
counter keeps incrementing but the passes are free (no LLM), so no thrash results. The eval confirms
this self-regulation; it is not a tuning surface for this plan.

## Tasks

### ✓ DONE TASK-1 — Add `summarize` flag to `compact_messages` (the static-marking path)
- **files:** `co_cli/context/compaction.py`
- **action:** Add `summarize: bool = True` keyword-only parameter to `compact_messages`
  (`compaction.py:275`). When `False`, skip the `_gated_summarize_or_none(ctx, dropped, focus=focus)`
  call (`compaction.py:297`) and set `summary_text = None` directly — this drives
  `build_compaction_marker(dropped_count, None, has_tail=...)` down its `static_marker` branch
  (`_compaction_markers.py:108–110`), emitting the fixed placeholder (no LLM recap). The rest of the
  assembly is untouched and runs identically for both branches: `marker`, the optional `todo_snapshot`,
  `_preserve_deferred_tool_discoveries(dropped)`, and `tail` (`compaction.py:298–307`). The function
  still returns `(result, summary_text)`, so `summary_text` is `None` on the static path (the caller's
  signal that no recap was produced). All other callers keep the default `summarize=True` and behave
  exactly as before — all three callers (`recover_overflow_history` `compaction.py:430`, the `/compact`
  command `commands/compact.py:43`, and `proactive_window_processor`) omit the new kwarg, so the default
  preserves their behavior with no edits. Also correct the now-conditional `compact_messages` docstring
  (`compaction.py:282–291`): it currently states it "runs the gated summarizer over the dropped middle"
  unconditionally — reword to note the summarizer is skipped when `summarize=False`.
- **done_when:** `compact_messages(ctx, messages, bounds, summarize=False)` returns `(result, None)`
  where (a) `result` contains exactly one marker `ModelRequest` whose `UserPromptPart` content starts
  with `STATIC_MARKER_PREFIX` (a static, not summary, marker), (b) the `dropped` region is absent from
  `result` while `head` and `tail` are preserved verbatim, (c) any pending `todo_snapshot` and unlocked
  deferred-tool discoveries are still carried forward, and (d) no summarizer LLM call is made;
  `uv run pytest tests/test_flow_compaction_proactive.py -x` passes.
- **success_signal:** the forced-static path produces a structurally valid compacted history — fixed
  placeholder in the dropped region's slot, side-channel state preserved — without a model call.
- **prerequisites:** none.

### ✓ DONE TASK-2 — Anti-thrash branch falls back to static-marker compaction
- **files:** `co_cli/context/compaction.py`
- **action:** In `proactive_window_processor` (`compaction.py:541–548`), replace the
  early-`return messages` with the static-marker path. Keep the `log.info` /
  `span.set_attribute("compaction.skip_reason", "anti_thrash_gate")` for triage, but **drop the
  existing `span.set_attribute("compaction.fired", False)` write** — let `_record_proactive_outcome`
  own `compaction.fired = True` (`compaction.py:374`), so the new path produces the
  `skip_reason="anti_thrash_gate"` + `fired=True` combination (no other path produces it; intended for
  observability). Also correct the stale docstring (`compaction.py:460–462`) — remove the "surfaces a
  user-actionable hint pointing at /compact and /new" claim and describe the static-marker fallback.
- **structure — share one tail, do not duplicate (satisfies "Reuse, don't re-implement"):** rather than
  copy-pasting the boundary→compact→record block into the anti-thrash branch, set a local
  `summarize = False` when the gate trips and **fall through to the single existing**
  `plan_compaction_boundaries(...)` → `compact_messages(...)` → `_record_proactive_outcome(...)` tail
  (`compaction.py:550–580`). This keeps the `None`-boundary guard (`no_boundary` skip), the
  `token_count` argument, and the span writes uniform across both paths; only the `summarize` flag
  differs. Note `focus` is computed at `compaction.py:575` (`_resolve_proactive_focus`) and is **only
  consumed by the summarizer**, so it is irrelevant when `summarize=False` — either pass it through
  harmlessly (unused) or skip computing it on the static path; do **not** reference an undefined `focus`
  in a separate branch.
- **status callback on the static path:** the "Compacting conversation…" progress callback fires inside
  `_gated_summarize_or_none` (`compaction.py:215–216`), which is skipped when `summarize=False` — so the
  static path correctly shows no spinner, only the closing message from `_record_proactive_outcome`.
  Confirm this stays true (no separate "Compacting…" emit added to the static path).
- **status-callback truthfulness (CD-M-1):** the anti-thrash fallback flows through
  `_record_proactive_outcome`'s 3-way `status_callback` (`compaction.py:351–357`), whose `else` branch
  (model present + `summary_text is None`) currently emits **"Summarizer failed — used static
  marker."** — a lie for a *deliberate* skip where the summarizer never ran. Thread the skip cause into
  `_record_proactive_outcome` via a **plain `summary_skipped: bool = False`** argument set by the
  anti-thrash branch so it emits a truthful message such as **"Compacted (static marker)."** for the
  anti-thrash case, while the genuine-failure and model-absent branches keep their existing wording.
  **Do NOT** reuse or extend the existing `CompactionFallbackReason` enum (`compaction.py:115`) for this
  — that enum feeds the OTEL `_emit_compaction_fallback` event, a separate concern from the user-facing
  status string. A binary "was the summarizer deliberately skipped?" is all this branch distinguishes; a
  bool is the right shape, an enum is over-coupling.
- **done_when:** with the gate tripped and `token_count` above threshold,
  `proactive_window_processor` returns a *new* list whose token count is below the input's, containing
  a `static_marker` marker, with `compaction_applied_this_turn is True`, **no summarizer LLM call**, and
  the status-callback string is **not** the "Summarizer failed" wording (it reflects an intentional
  static-marker compaction); `uv run pytest tests/test_flow_compaction_proactive.py -x` passes.
- **success_signal:** the anti-thrash gate engaging trims context instead of leaving it unchanged, and
  the user-visible status message truthfully reports a static-marker compaction (not a failure).
- **prerequisites:** TASK-1.

### ✓ DONE TASK-3 — Rewrite the anti-thrash unit test for the new behavior
- **files:** `tests/test_flow_compaction_proactive.py`
- **action:** Rewrite `test_anti_thrash_gate_skips_compaction_after_consecutive_low_yield`
  (`:137`) — its name and body currently assert the no-op (`result is messages`,
  `compaction_applied_this_turn is False`). Rename to reflect the fallback (e.g.
  `test_anti_thrash_gate_falls_back_to_static_marker`) and assert observable behavior: the returned
  history is shorter / fewer message tokens than the input, a `static_marker`-prefixed marker is
  present, `compaction_applied_this_turn is True`, and no summarizer LLM call was made (the
  `_TIGHT_MODEL` summarizer is never invoked — deterministic, no LLM needed on this path). Because the
  fallback makes **no LLM call**, the rewritten test must NOT call `ensure_ollama_warm` or wrap in
  `asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS)` (unlike the sibling above-threshold/floor-aware
  tests that hit the real summarizer) — keep it a fast, no-warm, no-timeout, Ollama-free deterministic
  path. **Fixture sizing:** the fixture must be large enough that `plan_compaction_boundaries` returns
  non-`None` after head + 10%-tail are carved out (i.e. a real droppable middle exists); otherwise the
  branch takes the `no_boundary` skip and no static marker is produced, so the assertions can't hold.
  The current `_above_threshold_messages()` may be too thin once the tail is reserved — verify it yields
  valid bounds (enough turn groups that `tail_start > head_end`) or extend it.
- **done_when:** `uv run pytest tests/test_flow_compaction_proactive.py -x` passes with the rewritten
  test asserting the static-marker fallback (not the old no-op).
- **success_signal:** N/A (test change).
- **prerequisites:** TASK-2.

### ✓ DONE TASK-4 — Focused loop-stability eval (text/reasoning-heavy, exercises ISSUE-2)
- **files:** `evals/eval_context_stability.py` (NEW)
- **action:** Drive a long multi-turn conversation (real LLM, real tools, real deps per
  `feedback_eval_real_world_data`) through a **reasoning/text-heavy phase** that accumulates
  text/reasoning with no spillable `ToolReturnPart` candidates — the only phase that engages the
  anti-thrash gate's previously-unreproduced no-op→growth path (a text middle has nothing for layer-2
  spill to bite on).
- **tripping the gate reliably (real-LLM hazard):** anti-thrash trips only after **≥2 consecutive
  passes each saving <`min_proactive_savings` (0.10)**. A competent real summarizer compresses a text
  middle *well* → high savings → the counter resets every pass → **the gate never trips** and the
  static fallback is never exercised. To force genuine low-yield, the text-heavy middle must be
  **near-incompressible** — e.g. seed the conversation with high-entropy / low-redundancy content (dense
  unique identifiers, varied factual statements, non-repetitive prose) so the summary cannot shrink it
  below 90% of the region. Keep the floor + 10% tail large relative to the droppable middle (a thin
  middle vs the uncompactable floor+tail is itself low-yield), which makes the trip more reliable. Do
  **not** pre-seed `consecutive_low_yield_proactive_compactions` — that is the unit test's (TASK-3) job;
  the eval must trip the gate through real accumulated pressure or it is not validating the runtime
  path.
- **Hard assertions (within this plan's authority):** no context-overflow error; a
  bounded number of compaction passes; every triggered pass reduces token count (the anti-thrash branch
  produces a static-marker pass that trims, never a no-op); post-pass total stays below the trigger.
  **No coherence probe here (deliberately out of scope).** Coherence-after-trim is the parent plan's
  explicit gate for the shipped `tail_fraction 0.10` change (parent holds the `tail_fraction` revert
  lever; tail is out of scope here), so a coherence check this plan cannot act on is non-actionable
  scope bleed — it belongs to the parent's combined loop-stability eval, which already owns it. This
  eval asserts only the bounded-window invariants above; it does not build any recall-probe machinery.
  **Keep the eval minimal:** the load-bearing assertions are bounded-pass-count + no-overflow +
  every-pass-reduces + post-pass-below-trigger. Do not over-invest in entropy-tuning the seeded middle
  to force a deterministic gate trip — TASK-3 deterministically owns the tripped-state guarantee; this
  eval validates the bounded loop and *conditionally* the runtime trip (see gate-conditional `done_when`).
  Document the result block in the Delivery Summary. **Sequencing / file ownership:** this plan **ships first and owns
  creation** of `evals/eval_context_stability.py` with the text-heavy phase only; the parent's combined
  loop-stability eval (which lists `prerequisites: ISSUE-2, ISSUE-3, ISSUE-5` and is structurally inert
  until ISSUE-2 lands) is a downstream extension that adds the tool-output-heavy phase. Note this in the
  eval header.
- **done_when:** `uv run python evals/eval_context_stability.py` runs to completion with the loop
  bounded (no overflow error, every triggered pass reduces tokens, post-pass total below trigger);
  result block recorded. **The anti-thrash assertion is gate-conditional:** if the run trips the anti-thrash gate (≥2 consecutive <10% passes),
  it must confirm that pass was a static-marker compaction that reduced tokens (never a no-op); if the
  incompressible-middle setup does not trip the gate in a given run, the eval must **log that the gate
  did not engage** (so the result is not silently mistaken for a pass) and the bounded-loop assertions
  still hold. A run where the gate trips and no-ops is a hard failure.
- **success_signal:** under sustained text/reasoning pressure the window stays bounded — the
  no-op→growth path can no longer reach overflow.
- **prerequisites:** TASK-2.

## Testing
- Scoped unit: `tests/test_flow_compaction_proactive.py` (TASK-3 rewrite; the existing floor-aware,
  circuit-breaker, and savings tests must continue to pass unchanged).
- Empirical gate: `evals/eval_context_stability.py` (TASK-4) — tail the spans log on the run
  (`feedback_tail_log_every_test_run`), watch LLM-call timing live.
- `scripts/quality-gate.sh full` at ship.

## Open Questions
- **Reliably tripping the anti-thrash gate in TASK-4 (real summarizer).** The gate trips only on ≥2
  consecutive <10%-savings passes, but a competent summarizer keeps savings high, so the gate may not
  engage from organic load. The plan's mitigation is a near-incompressible / high-entropy middle plus a
  thin-middle-vs-floor+tail ratio; whether that *reliably* trips the gate in a single real-LLM run is
  unproven until the eval is built. Resolution path if it proves flaky: (a) tune the entropy/size of the
  seeded middle, or (b) accept the gate-conditional `done_when` (log non-engagement) and lean on TASK-3
  (which deterministically forces the tripped state) as the hard guarantee, with the eval validating the
  bounded loop. Do not resolve by pre-seeding the counter in the eval (that defeats runtime validation).
  Lean toward (b): TASK-3 is the hard guarantee, so TASK-4 need not force a trip — prefer accepting
  gate-conditional non-engagement over sinking effort into entropy-tuning a non-deterministic real-LLM run.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev antithrash-static-marker-fallback`

## Delivery Summary — 2026-06-03

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `compact_messages(..., summarize=False)` returns `(result, None)` with a static marker, dropped region absent, head/tail + todo/deferred preserved, no summarizer call; scoped pytest passes | ✓ pass |
| TASK-2 | gate tripped + above threshold → new shorter list with a `static_marker`, `compaction_applied_this_turn` True, no summarizer call, status string not the "Summarizer failed" wording; scoped pytest passes | ✓ pass |
| TASK-3 | scoped pytest passes with the rewritten test asserting the static-marker fallback (not the old no-op) | ✓ pass |
| TASK-4 | `eval_context_stability.py` runs to completion, loop bounded (no overflow, every fired pass reduces tokens, post-pass below trigger); gate-conditional non-engagement logged | ✓ pass |

**Implementation notes:**
- TASK-1/2 (`co_cli/context/compaction.py`): added keyword-only `summarize: bool = True` to `compact_messages` (skips `_gated_summarize_or_none` → `summary_text=None` → `static_marker` when False). The anti-thrash branch no longer `return messages`; it sets `summarize=False` and falls through the shared `plan_compaction_boundaries → compact_messages → _record_proactive_outcome` tail (keeps the `no_boundary` guard, `token_count`, and span writes uniform; drops the branch's `fired=False` write so `_record_proactive_outcome` owns `fired=True`). Added a `summary_skipped: bool` arg to `_record_proactive_outcome` so the anti-thrash path emits the truthful **"Compacted (static marker)."** instead of "Summarizer failed…". Breaker state (`compaction_skip_count`) is untouched on this path (`_gated_summarize_or_none` is skipped entirely). Stale docstrings (`proactive_window_processor`, `compact_messages`) corrected.
- TASK-3 (`tests/test_flow_compaction_proactive.py`): renamed to `test_anti_thrash_gate_falls_back_to_static_marker`; functional-only assertions (per user directive — no structural/flag checks): history is trimmed (no-op-regression guard) + a static (not summary) marker is present (proof no LLM ran). Deterministic, no `ensure_ollama_warm`, no timeout.
- TASK-4 (`evals/eval_context_stability.py`): real-LLM UAT at the real **64k** window. Final run PASS — 10 turns, 2 fired passes (47%/47.6% savings, both below trigger), no overflow, per-turn 14–60s (bounded). Anti-thrash gate did not engage organically (competent summarizer kept savings high) — logged as the accepted gate-conditional outcome; TASK-3 owns the deterministic tripped-state guarantee.

**Tests:** scoped — 23 passed, 0 failed (`tests/test_flow_compaction_proactive.py`). Eval — CS.A PASS.
**Doc Sync:** fixed — `docs/specs/compaction.md` (L3 trigger row, STEP 2/4 pseudocode, runtime-field table, 4-case status-callback list, caller table, test-mapping row, diagram, API signature row) updated from the old banner/no-op model to static-marker demotion (`summarize=False`).

**Extra files beyond plan scope (user-directed mid-delivery):**
- ⚠ `evals/_settings.py` (NEW) — centralized eval settings module (`EVAL_MAX_CTX` sourced from `load_config().llm.max_ctx`, `eval_max_ctx(override)` lever). Created per user directive that all evals share centralized, system-sourced settings; the eval pulls its window from it. (An interim `model_max_ctx=32768` override was tried and reverted — RCA showed the static-prefill floor is absolute, so a smaller window is NOT ratio-equivalent to 64k.)
- `docs/REPORT-eval-context-stability.md` (NEW) — eval report output.

**Overall: DELIVERED**
All four tasks pass; lint clean; scoped suite green; eval PASS at real 64k; doc-sync complete.

## Implementation Review — 2026-06-04

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `compact_messages(..., summarize=False)` → `(result, None)`, static marker, head/tail + side-channel preserved, no LLM; scoped pytest passes | ✓ pass | `compaction.py:281` keyword-only `summarize: bool = True`; `:302` `await _gated_summarize_or_none(...) if summarize else None` (summarizer skipped when False); `:304` `build_compaction_marker(len(dropped), summary_text, ...)` → `_compaction_markers.py:104-110` static branch on `None`; assembly (head/marker/todo/deferred/tail) unchanged `:306-312`; docstring `:283-296` |
| TASK-2 | gate tripped + above threshold → new shorter list w/ static marker, fired=True, no LLM, status ≠ "Summarizer failed"; scoped pytest passes | ✓ pass | `compaction.py:566-573` gate sets `summarize=False` (no `return messages`); shared tail `:575` planner → `:601-603` `compact_messages(..., summarize=summarize)` → `:606` `_record_proactive_outcome(..., summary_skipped=not summarize)`; `:367-368` emits `"Compacted (static marker)."`; `fired=True` owned by `_record_proactive_outcome:389`; breaker untouched (gate-off short-circuits `_gated_summarize_or_none` at `:302`); `no_boundary` guard preserved `:576-585` |
| TASK-3 | scoped pytest passes asserting the static-marker fallback (not the old no-op) | ✓ pass | `test_flow_compaction_proactive.py:139` renamed `test_anti_thrash_gate_falls_back_to_static_marker`; functional asserts: trim `:175`, static marker present `:185`, no summary marker `:188`, truthful status `:194-195`; deterministic (no `ensure_ollama_warm`/timeout) |
| TASK-4 | eval runs to completion, loop bounded (no overflow, every pass reduces, post-pass below trigger); gate non-engagement logged | ✓ pass | `eval_context_stability.py` real 64k via `make_eval_deps` + `eval_max_ctx()`; final run CS.A PASS — 10 turns, 2 fired passes (47.0% / 47.6%, both below 32,768 trigger), no overflow, per-turn 14–60s bounded; gate non-engagement logged (accepted gate-conditional outcome) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| TASK-2's new `"Compacted (static marker)."` status branch (CD-M-1 truthfulness; done_when's "status ≠ Summarizer failed" clause) had no behavioral test | `tests/test_flow_compaction_proactive.py:139` | blocking | Added status-callback capture + `assert "Compacted (static marker)." in captured` / `assert "Summarizer failed…" not in captured` to the anti-thrash test (functional/behavioral; deterministic, no LLM) |
| Test audit (user directive): pre-existing circuit-breaker `gate_open is …` cadence tests (`:306-364`) are unit-level (internal boolean), not user-observable | `tests/test_flow_compaction_proactive.py:306-364` | minor | Left as-is — pre-existing, out of this plan's surgical scope; guard a subtle probe cadence with no cheaper observable surface. Flagged, not changed. |

### Tests
- Command: `uv run pytest -q -p no:randomly` (full suite); blast radius `pytest <4 compaction test files>`
- Result: full suite **451 passed, 0 failed, 1 deselected** (299s); blast radius **39 passed**; eval CS.A **PASS**
- Log: `.pytest-logs/20260516-180318-review-impl-full.log`
- Note: the working tree carries an unrelated coworker workstream (`session/*`, `bootstrap/core.py`, `main.py`, other plans); suite green with it present, nothing to triage. Those files are out of this plan's scope and untouched.

### Behavioral Verification
- No `co status` command exists in this project (CLI: `chat`/`tail`/`trace`/`dream`/`google`). The user-facing surface changed (proactive-compaction status callback) fires mid-turn at ~32k context.
- Verified via: (a) deterministic unit test asserting the truthful `"Compacted (static marker)."` status (TASK-2 success_signal — user sees a truthful static-marker message, not a failure); (b) TASK-4 eval end-to-end — real `run_turn`, compaction fired and reduced tokens, window stayed bounded, no overflow (TASK-4 success_signal). TASK-1 success_signal (valid compacted history, no model call) confirmed by the deterministic unit test.

### Overall: PASS
All four `done_when` re-executed and pass; one blocking coverage gap (untested new status branch) found and fixed with a functional validator; full suite green at real 64k; lint clean; behavioral verification satisfied via the deterministic status test + the end-to-end eval. Ready for `/ship`.
