# Drop `reported` from the compaction trigger — realtime-local only (peer-aligned)

## Context

Split out of `docs/exec-plans/active/2026-06-02-210659-context-stability-sizing-control.md` (ISSUE-3).
The old ISSUE-3 framing — *"lower `spill_ratio` below `compaction_ratio` to create a band"* — is
**superseded** by this plan. The band was a band-aid over the real defect; this plan removes the defect.

**The defect (verified at source).** Both compaction triggers compare against
`max(local_estimate, reported)`, where `reported = deps.runtime.last_reported_input_tokens` is a
**provider-reported usage state var** carried from the *previous* model response:

- L2 spill — `enforce_request_size` (`co_cli/context/history_processors.py:399-400`):
  `trigger = max(static_floor_tokens + estimate_message_tokens(messages), reported)`.
- L3 summarize — `proactive_window_processor` (`co_cli/context/compaction.py:497-505`):
  `token_count = max(effective_request_tokens(messages), reported)` (with `reported` zeroed only when
  `compaction_applied_this_turn`).

Spill (L2) lowers the **realtime** payload by moving tool returns to disk, but it **cannot lower
`reported`** — only a fresh provider round-trip can. So after a successful spill, L3 re-reads
`max(realtime_after_spill, reported)`; if `reported` is still above the summarize threshold, L3 fires an
**LLM summarization that the spilled payload did not require**. `commit_compaction`
(`compaction.py:332-334`) papers over this by overwriting `reported` with a post-compaction local
estimate — a second band-aid that only exists to keep the stale trigger value fresh.

**Why `reported` was added.** As a floor against the `chars/4` estimator
(`estimate_message_tokens`, `summarization.py:40-65`) undercounting and suppressing the trigger
(*"bias toward earlier compaction"*, docstrings). The intent was overflow safety.

**Peer evidence (verified at source, HEADs 2026-06-03/04).** The two peers that run **the same
spill→summarize two-tier chain co has** both drive their trigger off a **single realtime-local count
with no provider-reported floor** — and both use the **identical `chars/4` estimator** co uses:

| Peer | Same chain shape? | Trigger signal | Estimator |
|---|---|---|---|
| **hermes-agent** | yes (tool-prune → LLM summary) | realtime recount, **no `reported` floor** | `chars // 4` (`chat_completion_helpers.py:83-103`; `_CHARS_PER_TOKEN`, `context_compressor.py:813`); trigger `should_compress(prompt_tokens)` fed by `estimate_request_tokens_rough()` (`conversation_loop.py:642`) |
| **openclaw** | yes (truncate / compact / both routes) | realtime recount, **no `reported` floor** | `chars / CHARS_PER_TOKEN_ESTIMATE` (=`chars/4`) + CJK code-point adjustment (`utils/cjk-chars.ts:6-40`); `estimatePrePromptTokens(messages)` (`preemptive-compaction.ts:19-119`) |
| opencode | no (single overflow check) | provider-reported usage only | `tokens.total` (`session/overflow.ts:22`) |
| codex | no | anchored hybrid: `reported_baseline + estimate(items since last response)` (`history.rs:304-321`) | — |

co is the **lone outlier** bolting `max(local, reported)` onto an estimator its chain-peers trust bare.
The estimator-quality objection ("co's `chars/4` is too crude to drop the floor") is **refuted** — hermes
and openclaw drop the floor on the *identical* `chars/4` heuristic. They tolerate estimator error because
the **overflow backstop** is the real safety net (openclaw: runner catches PI overflow; co:
`_attempt_overflow_recovery` on HTTP 400/413 → `strip_all_tool_returns` → retry). The realtime trigger is
a proactive optimization on top of that backstop, not the sole guard.

**Conscious non-divergence.** codex's anchored hybrid is a coherent alternative (keeps a provider anchor,
fixes staleness via a delta). We are **not** adopting it — it keeps a provider-usage dependency in the
trigger, which is exactly what we want gone. We align with the chain-shaped majority (hermes/openclaw).

## Problem & Outcome

**Problem.** `reported` in the compaction **trigger** makes L3 summarization fire when the post-spill
realtime payload did not need it (the stale-`reported`-dominates-the-`max()` case), and forces a
post-compaction overwrite band-aid to keep the value fresh. co is the only chain-shaped peer carrying a
provider-usage floor in its trigger.

**Outcome.** Both triggers (L2 spill, L3 summarize) key off a **single realtime-local count**
(`effective_request_tokens`) with no `reported` floor — matching hermes/openclaw. A successful spill
deterministically suppresses an unnecessary summarize because L3 re-reads the *same* realtime value the
spill just lowered. The post-compaction `reported` overwrite is deleted (its only purpose is gone). The
overflow **backstop** is unchanged and remains the safety net.

**Failure cost:** unchanged from today if not done — L3 fires avoidable LLM summarization passes after a
spill already fit the payload, adding a full summarizer call to a turn's wall-clock (the most expensive
single operation in the loop) and compacting history that did not need compacting, diluting the middle.

## Scope

**In scope — remove `reported` from the TRIGGER path:**
- `enforce_request_size` (L2): drop the `max(.., reported)`; trigger on `effective_request_tokens`.
- `proactive_window_processor` (L3): drop the `max(.., reported)` and the
  `compaction_applied_this_turn → reported = 0` branch; trigger on `effective_request_tokens`.
- `commit_compaction`: delete the post-compaction overwrite of `last_reported_input_tokens` (it existed
  solely to keep the trigger value fresh — dead once the triggers stop reading it).
- Replace the ISSUE-3 block in the parent plan with a one-line pointer to this plan.

**In scope — full removal of `last_reported_input_tokens` (decided; supersedes the earlier "keep for
telemetry" scoping).** A dependency audit (2026-06-04) found that after the two triggers stop reading it,
the **only** other consumer is the overflow telemetry at `orchestrate.py:567-582` — and that consumer
does **not** need the state var. The principle is that `reported` only ever reflected *the size at the
last LLM response time* — a backward snapshot, not current state — so it should not be carried as a
runtime status var at all. The telemetry already holds `turn_state.latest_result` (the `AgentRunResult`);
the provider's real input count for the final request lives in that result's **last `ModelResponse`**
(`.usage.input_tokens`). Re-sourcing from the result keeps provider ground-truth (no `chars/4`
degradation) and lets the field, its writer, and its resets all go. (Earlier OQ-1 wrongly claimed full
removal forces a `chars/4` estimate — it conflated removing the trigger floor with losing the signal; the
signal is recoverable from the in-hand result.)
- **Re-source semantics (load-bearing):** use the **last `ModelResponse`'s** `usage.input_tokens`, NOT
  `latest_result.usage()` — the latter is accumulated RunUsage (summed across the segment's requests) and
  would over-count the single-prompt overflow check.
- Delete: `deps.py` field `last_reported_input_tokens`, the `TokenTrackingCapability`
  (`token_tracking.py`, whose sole job is writing it) + its registration, and the `clear.py`/`new.py`
  resets.

**In scope — `turn_usage` / status-line context-% (TASK-5, pulled in per user direction).** A *separate*
provider-usage field, `turn_usage.input_tokens` (`_merge_segment_usage`, `orchestrate.py:188`), feeds the
status-line "context used %" at `main.py:448-449` — the same response-time-snapshot-as-status-var
anti-pattern (and, as an accumulator, it over-counts on multi-request turns). Re-based on the realtime
`current_request_tokens_estimate`; `turn_usage` + `_merge_segment_usage` then have no readers and are
removed. `latest_usage` (separate, feeds spans/`TurnResult`) is untouched.

**Untouched — direct usage observability.** `llm/call.py:71-76`, `observability/capability.py:199-204`,
and `orchestrate.py:841-846` read usage straight off the pydantic-ai response/result; they depend on
neither field and need no change.

**Out of scope — the `spill_ratio`/`compaction_ratio` band.** With `reported` gone from the trigger,
equal ratios (both 0.50) already yield clean spill-first / summarize-fallback: L2 spills the realtime
payload under 0.50, L3 re-reads the same lowered realtime value and fast-paths. The band is no longer a
fix — at most an optional oscillation-damping knob. No ratio change in this plan.

## Behavioral Constraints

- The overflow backstop (`_attempt_overflow_recovery`, HTTP 400/413 path) is unchanged and remains the
  last-resort safety net. This plan must not weaken it.
- `compaction_applied_this_turn` retains its within-turn re-trigger short-circuit role
  (`deps.py:212`); only the `reported = 0` branch keyed on it is removed.
- No new provider-usage dependency in either trigger. The sanctioned remedy for any future `chars/4`
  undercount is improving the estimator (openclaw-style CJK/JSON adjustment in
  `estimate_message_tokens`), **never** reintroducing a `reported` floor.

## High-Level Design

Both triggers already compute a floor-inclusive realtime local (`effective_request_tokens` =
`static_floor_tokens + estimate_message_tokens`, `summarization.py:124-133`). The change is purely
**deletion of the `max(.., reported)` wrapper** so that realtime local *is* the trigger:

- L2: `trigger = effective_request_tokens(deps, messages)` (was `max(floor + local, reported)`).
- L3: `token_count = effective_request_tokens(deps, messages)` (was `max(local, reported)`); delete the
  `reported` fetch and the `compaction_applied_this_turn → reported = 0` branch.
- `commit_compaction`: delete line 334 (the overwrite) and trim the docstring; keep the
  `compaction_applied_this_turn = True` write.

`reported` then has exactly one reader (the overflow telemetry at `orchestrate.py:567`) and two writers
(`token_tracking.py`, and the `clear`/`new` resets) — a clean telemetry-only lifecycle, fully decoupled
from compaction triggering.

## Tasks

### ✓ DONE TASK-1 — Remove `reported` from L2 spill trigger (BOTH read sites)
- **files:** `co_cli/context/history_processors.py`,
  `tests/test_flow_compaction_enforce_request_size.py`
- **action:** In `enforce_request_size`, drop `reported_total` from **both** `max(..)` sites:
  - line ~400 `trigger = max(static_floor_tokens + local_total, reported_total)` →
    `trigger = effective_request_tokens(ctx.deps, messages)` (or `static_floor_tokens + local_total`).
  - **line ~451** `effective_after = max(static_floor_tokens + local_after, reported_total)` →
    `effective_after = static_floor_tokens + local_after`. This is the post-spill site the Outcome
    targets: if left in, a stale-high `reported` still forces `fallback_to_summarize` after a spill that
    fit the payload, defeating the whole change.
  - Update the algorithm docstring (step 1) and **drop** the `request.reported_tokens` event attr
    (it exists to debug the `max()`; see OQ-2 — resolved to drop).
  - **Rename/repurpose** the two existing tests whose names assert the removed mechanic —
    `test_high_reported_local_small_nothing_spilled` (~line 240) and
    `test_high_reported_large_local_spills` (~line 260) — into realtime-driven cases (they still pass on
    content size, but their "reported dominates" framing now lies).
- **done_when:** a chain test builds spillable string-content `ToolReturnPart` pressure with a
  **stale-high `reported`** set on runtime, and asserts L2's spill decision is driven solely by the
  realtime payload — spill fires when realtime > spill threshold regardless of `reported`, and **no-ops
  (no spill, total unchanged) when realtime ≤ threshold even if `reported` is high** (this exercises the
  line-451 path too). `uv run pytest tests/test_flow_compaction_enforce_request_size.py -x` passes.
- **success_signal:** with a high stale `reported`, L2 no longer treats the request as over-threshold once
  the realtime payload is under it.
- **prerequisites:** none.

### ✓ DONE TASK-2 — Remove `reported` from L3 summarize trigger + delete post-compaction overwrite
- **files:** `co_cli/context/compaction.py`, `tests/test_flow_compaction_proactive.py`
- **action:**
  - In `proactive_window_processor`, delete the `reported` fetch (`:497`) and the
    `compaction_applied_this_turn → reported = 0` branch; set
    `token_count = effective_request_tokens(ctx.deps, messages)`. **Drop** the
    `compaction.reported_tokens` span attr (`:529`) and the `reported` log fields (`:517`/`:520`) —
    they become constant-0 once the fetch is gone (OQ-2, resolved to drop).
  - In `commit_compaction`, delete the `last_reported_input_tokens = post_token_estimate` overwrite
    (`:334`). `post_token_estimate` (`:332`) exists **solely** to feed that overwrite — nothing else
    reads it, so **remove line 332 too**; the function reduces to
    `ctx.deps.runtime.compaction_applied_this_turn = True`. Trim the now-moot partial-commit-atomicity
    rationale from the docstring (`:320-330`), not just edit it. Keep the
    `compaction_applied_this_turn = True` write (retains its within-turn re-trigger role, `deps.py:212`).
  - **Delete or rewrite** `test_thrash_counter_not_incremented_for_reported_driven_compaction`
    (`tests/test_flow_compaction_proactive.py:~400-467`): its entire premise is that `token_count =
    max(local, reported)` keeps savings positive under a reported-driven trigger — the mechanic this plan
    removes. Once gone, `token_count = effective_request_tokens` and the test's assertion (counter == 0)
    is obsolete by design. Update the `_record_proactive_outcome` docstring (`:348-351`) that calls
    `token_count` "the trigger's `max(local, reported)`".
  - **Rename/rewrite** `test_fresh_reported_unchanged_by_floor`
    (`tests/test_flow_compaction_proactive.py:~230-253`) — sets `reported=300` with a "max() picks the
    report" premise; the assertion likely still passes (local stays under threshold) but the framing now
    lies, same category as the TASK-1 enforce-test renames.
- **done_when:** a test reproduces the wasted-summary case — set `reported` above the summarize threshold,
  run L2 spill so the realtime payload drops below it, then run L3 and assert L3 **fast-paths**
  (`below_threshold` skip reason, **zero summarizer LLM calls**) instead of summarizing. `uv run pytest
  tests/test_flow_compaction_proactive.py tests/test_flow_compaction_enforce_request_size.py -x` passes.
- **success_signal:** after a spill that fits the payload, L3 does not fire an LLM summarization driven by
  a stale `reported`.
- **prerequisites:** TASK-1 (shared trigger-signal change; land together for a coherent chain test).

### ✓ DONE TASK-3 — Re-source overflow telemetry from the result, then delete the field + capability
- **files:** `co_cli/context/orchestrate.py`, `co_cli/context/token_tracking.py` (delete),
  `co_cli/agent/build.py` (TWO capability-registration sites), `co_cli/deps.py`,
  `co_cli/commands/clear.py`, `co_cli/commands/new.py`,
  `tests/test_flow_compaction_slash_commands.py`, the nearest existing orchestrate/telemetry test.
- **action:**
  - Rewrite `orchestrate.py:567-582` to read the final request's input directly from the result the
    consumer already holds: `latest_input = latest_result.response.usage.input_tokens`. Use
    `.response` (the idiomatic "last `ModelResponse` in history" accessor, already used one line up at
    `:563` for `finish_reason`) — **not** a hand-rolled `new_messages()` walk and **not**
    `latest_result.usage()` (accumulated RunUsage, would over-count). Keep the `ctx_overflow_check` event
    and the "Context limit reached (N / M tokens)" status behavior identical. The existing `latest_input >
    0` guard (`:568`) makes a zero-token final response benign (today's `input_tokens > 0` write-guard
    skipped those too) — preserve the guard.
  - Delete `TokenTrackingCapability` (`token_tracking.py`) and **both** registration entries in
    `co_cli/agent/build.py` (`:55` chat agent, `:112` task agent — task agents have no reader of the
    field; the only readers are the two triggers + overflow telemetry, all chat-path).
  - Delete the `last_reported_input_tokens` field on `deps.py` (and its docstrings `:180`/`:221-222`);
    delete the `clear.py:13` / `new.py:20` resets.
  - In `tests/test_flow_compaction_slash_commands.py`, rewrite
    `test_cmd_clear_wipes_history_and_resets_compaction_state` (`:26`/`:38` set
    `last_reported_input_tokens = 42_000` and assert `/clear` resets it to `None`) — drop the obsolete
    field-reset assertion, keep the `result == []` history-wipe assertion.
- **done_when:** a test drives a turn whose final request input ≥ `model_max_ctx` and asserts the
  "Context limit reached" status still fires off the **provider** count (not `chars/4`); `grep -rn
  "last_reported_input_tokens" co_cli/` returns nothing; `uv run pytest
  tests/test_flow_compaction_enforce_request_size.py tests/test_flow_compaction_proactive.py
  tests/test_flow_compaction_summarization.py tests/test_flow_compaction_slash_commands.py -x` is green.
- **success_signal:** the overflow warning still uses the provider's real input count after the field is gone.
- **prerequisites:** TASK-1, TASK-2 (the field's trigger readers must be gone before deleting it).

### ✓ DONE TASK-4 — Replace ISSUE-3 in the parent plan with a one-line pointer
- **files:** `docs/exec-plans/active/2026-06-02-210659-context-stability-sizing-control.md`
- **action:** Replace the entire `### ISSUE-3 — Zero band …` block (~line 132-166) with a one-line
  pointer: *"ISSUE-3 — split out and superseded; see
  `docs/exec-plans/active/2026-06-04-130800-drop-reported-realtime-trigger.md` (root cause was
  `reported` in the trigger `max()`, not the spill/summarize band)."* Then **reconcile every other band
  reference** so nothing dangles:
  - **Load-bearing:** the loop-stability eval's `prerequisites: ISSUE-3, ISSUE-5 fixes required`
    (~line 243) and its `done_when` (~line 239, "spill-to-disk passes precede LLM-summarization passes") —
    re-point the ISSUE-3 prerequisite to **this** plan. The assertion stays valid (drop-reported satisfies
    "spill precedes summarize" *better* than the band did).
  - Band-fix prose at ~lines 78, 107, 113/116, 287, and the config table (~line 313) — update to reflect
    that ISSUE-3 is now "drop `reported`," not "lower `spill_ratio`."
  - Do not touch other ISSUEs.
  - Stale (non-breaking) eval docstring: `evals/eval_session_continuity.py:467` references the
    `max(.., reported)` mechanic — clean it.
- **done_when:** the parent plan's ISSUE-3 section is a single pointer line, the eval prerequisite points
  to this plan, and `grep -n "ISSUE-3\|band" <parent plan>` shows no orphaned band-fix prose.
- **success_signal:** N/A (doc edit).
- **prerequisites:** none.

### ✓ DONE TASK-5 — Re-base the status-line context-% on the realtime estimate; remove `turn_usage`
- **files:** `co_cli/main.py`, `co_cli/deps.py`, `co_cli/context/orchestrate.py`,
  `co_cli/commands/clear.py`, `co_cli/commands/new.py`, `tests/test_display.py`.
- **action:**
  - In `_build_status_snapshot` (`main.py:448-449`), compute `context_pct` from
    `deps.runtime.current_request_tokens_estimate` (the realtime-local estimate already written by
    `enforce_request_size` each request — pure realtime once TASK-1 de-`reported`s it) instead of
    `turn_usage.input_tokens`. Guard for `None`/`0` and `model_max_ctx > 0` as today.
  - `turn_usage`'s only reader was `main.py:449`; once re-based it is dead. Delete the `turn_usage` field
    (`deps.py:195`), its reset (`deps.py:235`), its docstring (`deps.py:175`), and `_merge_segment_usage`
    (`orchestrate.py:172-190`) + its call site (`orchestrate.py:429`). Leave `latest_usage` untouched —
    it is a *separate* object feeding spans/`TurnResult`, independent of `turn_usage`.
  - Reset `current_request_tokens_estimate` on `/clear` and `/new` (replacing the TASK-3 reset deletions
    in `clear.py:13`/`new.py:20`; those handlers don't call `reset_for_turn()`, so without this the
    indicator would show the pre-wipe estimate until the next turn runs `enforce_request_size`).
  - Rewrite the **three** affected tests in `tests/test_display.py` to set/assert via
    `current_request_tokens_estimate` instead of `turn_usage`:
    `test_build_status_snapshot_no_turn_usage_produces_none_context_pct` (~`:351`, asserts
    `turn_usage is None` — would `AttributeError` after field deletion),
    `test_build_status_snapshot_zero_max_ctx_produces_none_context_pct` (~`:358`), and
    `test_build_status_snapshot_context_pct_from_usage` (~`:372`).
- **done_when:** the rewritten `tests/test_display.py` snapshot tests assert `context_pct` tracks the
  **realtime current-payload** estimate (`current_request_tokens_estimate / model_max_ctx`), not the
  accumulated provider usage; `grep -rn "turn_usage" co_cli/` returns nothing; `uv run pytest
  tests/test_display.py -x` passes.
- **success_signal:** the status-line "context %" reflects current context fill (and no longer creeps
  past the true fill on multi-request turns).
- **prerequisites:** TASK-1 (so `current_request_tokens_estimate` is the pure realtime estimate, not
  `max(local, reported)`).

### Spec sync (post-delivery — NOT a task; reconciled by the auto `/sync-doc` after `/orchestrate-dev`)
Per project rule, `docs/specs/` is never listed in a task's `files:` — specs are reconciled by sync-doc
after delivery. These runtime specs document the deleted field/capability and **must** be reconciled then:
- `docs/specs/compaction.md` — L2 trigger table (~`:67`), the dedicated `last_reported_input_tokens`
  lifecycle subsection (~`:210-216`), the `commit_compaction` writer rows (~`:479`/`:490`/`:539`/`:715`),
  and the `/clear` test mapping (~`:824`).
- `docs/specs/core-loop.md` — the overflow-check description that reads the field (~`:322`).

## Testing

- Trigger tests must set a **stale-high `reported`** on runtime and prove it no longer influences either
  trigger — this is the behavioral heart of the change (mirror `done_when`, assert observable trigger
  decisions, not field presence).
- Reuse existing fixtures in `tests/test_flow_compaction_enforce_request_size.py` and
  `tests/test_flow_compaction_proactive.py`; spillable pressure must be string-content `ToolReturnPart`s
  (L2 only spills those).
- Per project policy: run with `-x`, pipe to a timestamped `.pytest-logs/` file, tail the log for LLM-call
  timing (the L3 fast-path assertion should show **zero** summarizer LLM calls).

## Open Questions

1. **Full removal vs telemetry-only — RESOLVED: full removal.** The dependency audit showed the lone
   non-trigger consumer (overflow telemetry, `orchestrate.py:567`) can read the provider's real input
   count from the `AgentRunResult` it already holds (the last `ModelResponse`'s `usage.input_tokens`), so
   the field is pure redundancy. The earlier "full removal degrades the warning to `chars/4`" claim was
   wrong — it conflated removing the trigger floor with losing the signal. Full removal keeps provider
   ground-truth in the warning **and** eliminates the status var. Folded into TASK-3.
2. **`compaction.reported_tokens` / `request.reported_tokens` span attrs.** **RESOLVED — drop** (folded
   into TASK-1/TASK-2 actions). They exist to debug the `max()`; emitting a constant-0 attr post-change is
   worse than removing them. No known dashboard dependency.
3. **The band knob — RESOLVED: keep both 0.50.** With `reported` gone, equal vs band-below is irrelevant
   to ladder correctness (spill runs first and lowers the realtime value C re-reads regardless). The one
   relationship that still matters is the **`spill_ratio <= compaction_ratio` validator invariant**
   (`compaction.py _validate_shape`) — if violated, summarize fires in the gap before spill runs. That
   validator **stays**; this plan changes no ratios and removes no invariant.
4. **Status-line `turn_usage` — RESOLVED: pulled into this plan (TASK-5).** `main.py:448-449` computes the
   "context used %" from `turn_usage.input_tokens` — the same provider-reported, response-time snapshot
   anti-pattern (and, being an accumulator, it over-counts across multi-request turns). Re-based on the
   realtime-local `current_request_tokens_estimate`. (PO's C3 note preferred deferring this for coherence;
   user directed it in-scope so the "don't use a response snapshot as a live status var" principle lands
   consistently in one change.)

## Final — Team Lead

Plan approved. **Converged over two review rounds.** Round 1 (scoped-removal version): Core Dev's two C1
blockers (the second `reported` read at `history_processors.py:451`, and the reported-driven
thrash-counter test) + minors adopted; C2 confirmed. Round 2 (full-removal version, after the dependency
audit): C3 re-review — PO approved with no blockers; Core Dev raised four completeness gaps (both
`build.py` registration sites `:55`/`:112`, the `test_flow_compaction_slash_commands.py` `/clear` breaker,
the idiomatic `.response.usage.input_tokens` accessor, and the `compaction.md`/`core-loop.md` spec-sync via
post-delivery sync-doc) — all adopted; C4 confirmed resolution. Both reviewers at `Blocking: none`.

**Scope settled to FULL removal** (matches your "remove `reported` completely"). The dependency audit
confirmed the only non-trigger consumer — the overflow telemetry at `orchestrate.py:567` — can read the
provider's real input count from the `AgentRunResult` it already holds (`latest_result.response.usage.
input_tokens`), so the field, its `TokenTrackingCapability` writer (both registration sites), and the
`clear`/`new` resets all go (TASK-3) with no `chars/4` degradation. The earlier "keep for telemetry"
scoping is withdrawn.

**OQ-3 / OQ-4 settled (user, Gate 1):** ratios stay 0.50/0.50 — equal-vs-band is irrelevant to the ladder
once `reported` is gone, but the `spill_ratio <= compaction_ratio` validator invariant is kept (OQ-3). The
status-line context-% is **pulled into this plan** as **TASK-5** — re-based on the realtime
`current_request_tokens_estimate`, with the now-readerless `turn_usage` + `_merge_segment_usage` removed
(OQ-4). C5 reviewed TASK-5: claims 1–4/6 clean at source; one completeness gap (three `tests/test_display.py`
snapshot tests) adopted.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev drop-reported-realtime-trigger`

## Delivery Summary — 2026-06-04

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | L2 spill decision driven solely by realtime payload; no-op under threshold despite high provider usage; `pytest test_flow_compaction_enforce_request_size.py` green | ✓ pass |
| TASK-2 | chain test: after L2 spill fits payload, L3 fast-paths (below_threshold, zero summarizer calls); `pytest proactive + enforce` green | ✓ pass |
| TASK-3 | overflow warning fires off provider count from result; `grep last_reported_input_tokens co_cli/` empty; 4-file compaction suite green | ✓ pass |
| TASK-4 | parent-plan ISSUE-3 → single pointer line; eval prereq re-pointed; no orphaned band prose | ✓ pass |
| TASK-5 | status-line context-% tracks `current_request_tokens_estimate`; `grep turn_usage co_cli/` empty; `pytest test_display.py` green | ✓ pass |

**Tests:** scoped — 67 passed, 0 failed (enforce_request_size, proactive, summarization, slash_commands, orchestrate_length_retry, display). All LLM-call durations healthy (warm: 1.9–25s; length-retry is ≥2 sequential calls by design). The L3 fast-path chain test ran with **zero summarizer LLM calls** as required.

**Doc Sync:** fixed (full scope on the two affected runtime specs).
- `compaction.md` — L2 trigger table, §1.5 trigger-basis subsection rewritten (realtime-local, no provider floor), `last_reported_input_tokens` runtime-field row deleted, `current_request_tokens_estimate` row extended (status-line reader + `/clear`/`/new` reset), L2 algorithm step 1 + span attrs, L3 STEP-1/5/6 pseudocode, §2.5 helper list, `commit_compaction` writer rows, test-mapping rows (`/clear`, trigger, new chain row).
- `core-loop.md` — `turn_usage` runtime-field row → `current_request_tokens_estimate`, removed the `_merge_turn_usage` finalize step, L3 token-count basis, overflow-check now reads `latest_result.response.usage.input_tokens`.
- Source-docstring fixes (sync-doc step 2b2): `deps.py` `compaction_applied_this_turn` comment (dropped stale re-trigger-short-circuit claim — now session-branching + OTEL only) and the matching `commit_compaction` docstring.

**Extra files (beyond plan `files:`):**
- ⚠ `evals/eval_trust_visibility.py` — collateral: W6.B read the deleted `deps.runtime.turn_usage`; re-based on `current_request_tokens_estimate` (unchanged on a local short-circuit, faithful equivalent).
- Orphan-import cleanup created by these changes: `deepcopy` (orchestrate.py), `RunUsage` (deps.py, test_display.py).

**Scope deviation (noted for review):** the plan's TASK-1/TASK-2 done_when specified setting a *stale-high `reported`* on runtime to prove it's ignored. Because TASK-3 fully deletes that field (and a plain dataclass would silently accept a dead attribute), the trigger tests instead represent the stale-high provider signal **authentically** as a prior `ModelResponse` carrying high `usage.input_tokens` (the exact source the deleted `TokenTrackingCapability` read) — proving the trigger ignores provider-reported size with no reference to the deleted field. End-state-coherent and survives TASK-3.

**Overall: DELIVERED**
All five tasks pass `done_when`; lint clean; 67 scoped tests green; specs reconciled. One collateral eval fix and orphan-import cleanups folded in.

**Next step:** `/review-impl drop-reported-realtime-trigger` — full suite + evidence scan + auto-fix → verdict appended to plan.

## Implementation Review — 2026-06-04

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | L2 spill driven solely by realtime payload; no-op under threshold despite high provider count; enforce suite green | ✓ pass | `history_processors.py:399` trigger = `static_floor + local_total` (no `max(.., reported)`); `:449` `effective_after` de-floored; `request.reported_tokens` attr dropped. `test_small_realtime_no_spill_despite_high_provider_usage` / `test_large_realtime_spills_and_post_spill_estimate_is_realtime` set a prior `ModelResponse(usage.input_tokens=20_000)` and assert spill/no-spill is content-driven |
| TASK-2 | chain: after L2 spill fits payload, L3 fast-paths (zero summarizer calls); proactive+enforce green | ✓ pass | `compaction.py:484-507` `token_count = effective_request_tokens(...)`, `reported` fetch + `applied→0` branch + span/log fields deleted; `commit_compaction:320-327` reduced to single `compaction_applied_this_turn = True` (post-overwrite + `post_token_estimate` removed). `test_l3_fastpaths_after_l2_spill_fits_payload` ran **0.00s, 0 model calls** |
| TASK-3 | overflow warning fires off provider count from result; `grep last_reported_input_tokens co_cli/` empty; 4-file suite green | ✓ pass | `orchestrate.py:539` `latest_input = latest_result.response.usage.input_tokens` (idiomatic `.response`, **not** accumulated `.usage()`), guarded by `assert latest_result is not None`; `TokenTrackingCapability` + both `build.py:54/110` registrations + `token_tracking.py` + `deps.py` field + `clear`/`new` resets all deleted; grep clean. `test_overflow_warning_uses_provider_input_count` asserts provider-count status |
| TASK-4 | parent-plan ISSUE-3 → pointer; eval prereq re-pointed; no orphaned band prose | ✓ pass | parent plan `:135` single pointer line; `:213` eval prereq → this plan; every `band` ref reframed as "no band needed"; `eval_session_continuity.py:463` docstring de-`reported`ed |
| TASK-5 | status-line context-% tracks `current_request_tokens_estimate`; `grep turn_usage co_cli/` empty; display green | ✓ pass | `main.py:448-450` context_pct from `current_request_tokens_estimate`; `turn_usage` field/reset + `_merge_segment_usage` + call site deleted; `/clear`/`/new` reset the estimate; `test_build_status_snapshot_context_pct_from_realtime_estimate` asserts 0.47 = 47k/100k |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Extra file not in any task `files:` — deletes unrelated `test_manifest_includes_user_installed_skills` | tests/test_flow_skill_manifest.py | scope (not blocking) | Pre-existing in-flight change (already modified at session start); unrelated to this plan, not reverted. **Flag for staged-file hygiene at `/ship` — do not stage unless intended.** |
| `uv.lock` co-cli version line 0.8.282→0.8.300 | uv.lock | minor | Benign `uv sync` drift; `/ship` owns the version bump |
| Pre-existing in-flight docs in diff (REPORT-eval-*, RESEARCH-*, other active/completed exec-plans, vision-input/uat plans) | docs/* | scope (not blocking) | Unrelated to this plan; leave to their own work. Verify staging at ship |

_All test assertions reviewed per the in-flight request "ensure all are functional, behavioral validations": every rewritten/new test asserts observable behavior (spill/no-spill, `result is` identity for fast-path, status text, context_pct value) — no structural field-presence checks. The `/clear` test correctly dropped its old structural field-reset assertion, keeping the behavioral `result == []`._

### Tests
- Command: `uv run pytest -x -q`
- Result: **619 passed, 0 failed** (1 warning)
- Log: `.pytest-logs/20260604-163433-review-impl.log`
- LLM timing healthy: slowest are daemon-infra (26.5s) and by-design multi-call length-retry (21s); chain fast-path test `test_l3_fastpaths_after_l2_spill_fits_payload` = **0 model calls** (done_when's "zero summarizer calls"); overflow test = 1.8s / 1 call.

### Behavioral Verification
- `uv run co chat` (EOF smoke): ✓ orchestrator builds with the de-`TokenTrackingCapability` capability list, banner "✓ Ready", status-line renders — the `build.py` capability removal does not break agent construction.
- `success_signal`s verified via functional tests (status-line %, overflow provider-count warning, spill→no-summarize chain) — no `co status` command exists; live status-line/overflow surface is exercised by the rewritten `test_display.py` and `test_overflow_warning_uses_provider_input_count` rather than a manual REPL turn.

### Overall: PASS
All five tasks meet `done_when` with file:line evidence; full suite green; lint clean; chat boots. The only non-blocking notes are staged-file hygiene items (unrelated in-flight changes in the working tree) to confirm before `/ship`.
