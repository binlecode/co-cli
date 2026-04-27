# Plan: Iterative Summary Compaction (Cross-Compaction Memory Compensator)

Task type: feature addition — closes the one remaining content-routing gap (cross-compaction working memory) in the compaction subsystem so that a smaller `tail_fraction` becomes viable.

## Context

co-cli's compaction subsystem currently runs `compaction_ratio = 0.80` and `tail_fraction = 0.40`. The 0.40 tail size is necessary for the four functions (original task framing, cross-compaction working memory, action trace, latest reasoning) to fit. Reviewing what already exists vs. what hermes (`hermes-agent/agent/context_compressor.py`) ships:

| Function | Co-cli today | Hermes equivalent | Status |
|---|---|---|---|
| (1) Original task framing | First turn group as head (`_compaction_boundaries.find_first_run_end`); `_anchor_tail_to_last_user` re-anchors latest user prompt | `protect_first_n = 3` messages + compression-note injected into system prompt on first compaction (`compress :1080-1084`) | Functional but narrower; not a blocker |
| (2) Cross-compaction working memory | LLM summary marker regenerated from scratch each pass; no `_previous_summary` carried across compactions | Iterative summary updates (`__init__ :299` init, `_generate_summary :646-660` branches on `_previous_summary`, `:705` updates it on success, `on_session_reset :208` clears) | **MISSING — load-bearing for aggressive shape** |
| (3) Action trace | Per-tool semantic markers via `_tool_result_markers.semantic_marker`, applied by `truncate_tool_results` to older-than-5 calls per tool. Handlers cover every member of `COMPACTABLE_TOOLS`; generic fallback for unknowns. | `_summarize_tool_result :66-185` per-tool one-liners; same pattern, applied by `_prune_old_tool_results :336-468` Pass 2 | **Already closed** ✓ |
| (4) Latest reasoning | Tail reverse walk + tool-call/result group integrity in `plan_compaction_boundaries` | Same pattern | Already closed ✓ |

Function 3 was the gap I flagged as "Gap B" in earlier discussion; that analysis was wrong. `co_cli/context/_tool_result_markers.py` already mirrors hermes's per-tool one-liner pattern (e.g. `[shell] ran \`cmd\` → exit 0, N lines` matches hermes's `[terminal] ran \`cmd\` -> exit 0, N lines output`). The placeholder string `[tool result cleared — older than 5 most recent calls]` is a documented last-resort fallback for non-string ToolReturnPart content (multimodal), not the normal path.

The genuinely missing compensator is **iterative summary updates**. This plan adds it. Closing this single gap is what makes the follow-up drop-M0 + aggressive-shape plan (`2026-04-26-211800-drop-m0-go-hermes.md`) safe at lower `compaction_ratio` and `tail_fraction`.

## Problem & Outcome

**Problem.** Today co-cli has only *partial* prior-summary integration:

- `_gather_prior_summaries` (`co_cli/context/_compaction_markers.py:142-154`) scrapes prior summary text from the **dropped range** and feeds it as enrichment context alongside file paths and todos.
- `_SUMMARIZE_PROMPT` (`co_cli/context/summarization.py:157-162`) instructs *"If a prior summary exists in the conversation, integrate its content — do not discard it"* with explicit section transitions.

Both paths are conditional and passive. Failure modes:

1. **Dropped-range dependency.** When the prior summary lands in the head (re-anchored first turn) or the tail (preserved suffix), `_gather_prior_summaries` finds nothing in `dropped` and the next pass starts from scratch.
2. **Passive enrichment, not structured update.** The prior summary arrives as one of three "## Additional Context" entries, not as the explicit `PREVIOUS SUMMARY: {prior}\nNEW TURNS TO INCORPORATE: {new}` template hermes uses. The prompt's "integrate" instruction is advice, not a template the model must complete.
3. **No state ownership.** The prior summary lives only in message content. Once it leaves the message stream (head/tail eviction by a later pass, or session save/load), there is no field carrying it forward.

By compaction-4 the original framing has degraded — project-level decisions ("we use JWT not sessions"), task continuity, and blocker history dilute across passes. A larger tail does not save you because the information was never reliably *placed* in the tail — it was placed in the marker, which is ephemeral.

Hermes's `_previous_summary: Optional[str]` field (init at `:299`) carries the prior summary across compactions. On the next compaction, `_generate_summary :646-660` branches:

```python
if self._previous_summary:
    # iterative update path: PREVIOUS SUMMARY + NEW TURNS, with preserve/add/move/remove discipline
    prompt = f"""You are updating a context compaction summary...
    PREVIOUS SUMMARY: {self._previous_summary}
    NEW TURNS TO INCORPORATE: {content_to_summarize}
    Update the summary using this exact structure. PRESERVE all existing
    information that is still relevant. ADD new completed actions...
    Move items from "In Progress" to "Completed Actions" when done...
    CRITICAL: Update "Active Task" to reflect the user's most recent
    unfulfilled request..."""
else:
    # first-compaction path: TURNS TO SUMMARIZE, structured handoff
    prompt = ...
```

After successful generation (`:705`): `self._previous_summary = summary` (raw template content, pre-prefix-strip per `:703`). On failure paths (`:711` no provider, `:748` transient error), `_previous_summary` is **untouched** — failure isolation by silence, not by explicit reset. Reset happens only at session boundaries (`on_session_reset :208`).

**Outcome:**

- `apply_compaction` carries `deps.runtime.previous_compaction_summary` forward across compactions.
- The summarizer prompt branches: with a previous summary, build an *update* prompt; without one, build the existing from-scratch prompt. State accumulates instead of dilutes.
- Effective tail content increases at the same `tail_fraction = 0.40` size; no threshold change in this plan.
- The follow-up drop-M0 + aggressive-shape plan becomes safe to ship at lower `compaction_ratio` and `tail_fraction` because cross-compaction memory now lives in the marker (where hermes puts it), not borrowed from tail tokens.

## Scope

**In:**

- `co_cli/deps.py` — add `previous_compaction_summary: str | None = None` to `CoRuntimeState` (`deps.py:117`) in the cross-turn bucket alongside `consecutive_low_yield_proactive_compactions` and `compaction_thrash_hint_emitted`.
- `co_cli/commands/session.py` — `/new` and `/clear` handlers currently reset *no* runtime state (only `/new` rotates `session.session_path`). This plan introduces the first session-boundary runtime reset hook, scoped narrowly: reset `previous_compaction_summary = None` only. `/compact` and `/resume` do NOT reset. The other session-bounded cross-turn fields (`consecutive_low_yield_proactive_compactions`, `compaction_thrash_hint_emitted`) are out of scope here — the follow-up `drop-m0-go-hermes` plan redesigns their lifecycle (adds resets at OR-1/OR-2/M3-success/`_cmd_compact`), and bundling session-boundary resets for them now would intrude on the context system's self-managing observation logic before that redesign lands. Mirrors hermes's `on_session_reset` semantics (`context_compressor.py:203-210`) for the new field only.
- `co_cli/context/summarization.py` — add a `previous_summary: str | None = None` parameter to `summarize_messages`. When provided, build the iterative-update prompt branch; otherwise build the existing from-scratch branch. Both share the structured-template body the existing prompt produces. The iterative branch's distinguishing instructions: PRESERVE existing relevant info, ADD new completed actions (continue numbering), MOVE items from "In Progress" to "Completed Actions" when done, MOVE answered questions to "Resolved Questions", REMOVE only clearly obsolete state, CRITICAL update Active Task to most recent unfulfilled user request. Emit a single `log.info("compaction_summarize_branch=%s", "iterative" if previous_summary else "from_scratch")` at the branch point — observability minimum so ops can grep for whether the iterative path is firing in production without paying for full telemetry.
- `co_cli/context/_compaction_markers.py` — `_gather_prior_summaries` becomes a fallback path: when `previous_compaction_summary` is non-None, the iterative-update prompt branch owns prior-summary integration and `_gather_prior_summaries` should not also feed the same content as enrichment (would duplicate inside the prompt). The simplest discipline: `gather_compaction_context` accepts the runtime state it already has access to via `ctx`, and skips the prior-summaries gather when `ctx.deps.runtime.previous_compaction_summary is not None`. When the field is None (first compaction in a session, or post-reset), `_gather_prior_summaries` retains its current passive-enrichment role for prior summaries embedded in the dropped range — this preserves the existing fallback for transcripts loaded with summary content but no runtime state.
- `co_cli/context/compaction.py` — `apply_compaction` reads `deps.runtime.previous_compaction_summary` on entry, threads it to `summarize_dropped_messages` → `summarize_messages`. On successful summary generation, write the new summary text back to `deps.runtime.previous_compaction_summary`. On failure (summarizer raise, circuit breaker, no model), leave the prior value untouched.
- Tests for the iterative path and the from-scratch regression.
- `pyproject.toml` patch-version bump (even = feature).

**Out:**

- No threshold tuning. `compaction_ratio = 0.80`, `tail_fraction = 0.40`, `min_proactive_savings = 0.10`, `proactive_thrash_window = 2` all stay. Tuning lands in the follow-up drop-M0 + aggressive-shape plan.
- No M0 layer changes. Removal lives in the follow-up plan.
- No tool-result one-liner work. `_tool_result_markers.semantic_marker` already covers this gap; the existing implementation matches hermes's `_summarize_tool_result` pattern across the per-tool surface.
- No head expansion (`protect_first_n = 3` analogue + system-prompt compaction note). Optional future work; co-cli's "first turn group" head is functional today and head expansion is not load-bearing for the aggressive-shape goal.
- No assistant-side `tool_call` argument truncation (Pass 3 of hermes's `_prune_old_tool_results :449-466`). Co-cli's `enforce_batch_budget` covers oversized current-batch tool returns; pre-existing assistant args truncation is a separate optimization.
- No cross-session persistence of `previous_compaction_summary`. The summary is per-session memory; `/resume` re-derives via the first compaction in the resumed session. Matches hermes's `on_session_reset :203-210` clearing behavior — they don't persist across sessions either.
- No changes to `apply_compaction`'s public signature (`bounds`, `announce`, `focus`); only the internal summarizer call changes.
- Spec edits in `docs/specs/` are not tasks. `/sync-doc` after delivery picks up the iterative-summary narrative in `compaction.md` and the runtime-state field map in `system.md`.

## Behavioral Constraints

1. **State location.** `previous_compaction_summary` lives on `CoDeps.runtime` (`CoRuntimeState`, `deps.py:117`), defaults to `None`, and resets on session-boundary events (`/new`, `/clear`). Manual `/compact` and `/resume` do NOT reset. This mirrors hermes's `on_session_reset :203-210` (clears `_previous_summary`) — note hermes does not have a `/resume` analogue, so the `/resume` behavior here is co-cli-specific: keep the field at None on resume since the runtime state was discarded with the prior session, and the next compaction in the resumed session generates a fresh first-pass summary. Other session-bounded cross-turn fields are not reset here (see Scope).
2. **Iterative path branches on state.** When `previous_compaction_summary is None`, the summarizer takes the existing from-scratch path with no behavioral change vs. today. When it is non-None, the summarizer takes the iterative-update path. First-compaction behavior in any session is identical to today (regression-tested). Manual `/compact` participates in iterative semantics whenever `previous_compaction_summary` is non-None — consistent with hermes's manual `/compress` running through `_generate_summary` with the same branch. The user-facing implication: a manual `/compact` mid-session updates the running summary rather than starting from scratch, and `/compact` immediately after a `/new` (state reset) starts from scratch.
3. **Failure isolation via silence.** Iterative-summary path failure (summarizer raises, circuit breaker tripped, model absent) falls through to the existing static-marker fallback. The prior `previous_compaction_summary` value is left untouched so the next compaction can retry. This matches hermes's failure paths at `:711` (RuntimeError → cooldown set, summary returned None, `_previous_summary` untouched) and `:748` (transient error → cooldown, returned None, `_previous_summary` untouched). The shared invariant: only successful generation overwrites the field.
4. **Backward compatibility.** Sessions resumed via `/resume` resume with `previous_compaction_summary = None` — the next compaction is a from-scratch run, then iterative thereafter.
5. **Stored summary is template content, not prefixed marker.** Hermes stores the raw structured template content (`:703-705`: `summary = content.strip(); self._previous_summary = summary`) before applying `_with_summary_prefix`. The iterative path then feeds this raw content as PREVIOUS SUMMARY in the next prompt. Co-cli should follow the same pattern: store the LLM's raw output, not the prefixed marker assembled by `build_compaction_marker`.
6. **No new public surface.** `co_cli/context/compaction.py` `__all__` is unchanged. The `previous_summary` parameter on `summarize_messages` is keyword-only with default None.
7. **Single source of prior-summary truth per pass.** When `previous_compaction_summary` is non-None, the iterative-update prompt branch owns prior-summary integration and `_gather_prior_summaries` is suppressed in `gather_compaction_context` to avoid feeding the same content twice. When the field is None, `_gather_prior_summaries` retains its current passive-enrichment role for prior summaries embedded in the dropped range — preserving the fallback for transcripts loaded with summary content but no runtime state (e.g. `/resume` first-compaction, where the dropped range may include a prior session's marker).

## Task Breakdown

Ordered by call-flow (state → summarizer → apply_compaction → tests → version).

| # | Task | done_when | Files |
|---|------|-----------|-------|
| ✓ DONE — TASK-1 | Add `previous_compaction_summary: str \| None = None` to `CoRuntimeState`. Update the cross-turn-bucket field-map docstring (`deps.py:127-130`) to list the new field. In `/new` and `/clear` handlers, reset `deps.runtime.previous_compaction_summary = None` only (other session-bounded cross-turn fields are out of scope per Scope). `/compact` and `/resume` do NOT reset. | Field exists with default `None`; reset is observable after `/new` and `/clear`; `/compact` and `/resume` do not touch it. (Reset behavior verified by TASK-4 test e.) | `co_cli/deps.py`, `co_cli/commands/session.py` |
| ✓ DONE — TASK-2 | Add `previous_summary: str \| None = None` keyword-only parameter to `summarize_messages`. When provided, build the iterative-update prompt branch (preserve existing relevant info, add new completed actions, move resolved items, remove obsolete state, update Active Task to most recent unfulfilled user request); otherwise build the existing from-scratch prompt. Both share the structured-template body. Emit `log.info("compaction_summarize_branch=%s", "iterative" if previous_summary else "from_scratch")` at the branch point. Update `gather_compaction_context` so that when `ctx.deps.runtime.previous_compaction_summary` is non-None, `_gather_prior_summaries` is skipped (avoid feeding the same content twice — once as `PREVIOUS SUMMARY:` and once as enrichment). | Iterative-update prompt is structurally distinguishable from from-scratch (asserts the `PREVIOUS SUMMARY:` and `NEW TURNS TO INCORPORATE:` template strings are present on iterative path, absent on from-scratch); enrichment context omits prior summaries when runtime field is set; from-scratch path output structurally equivalent to today (regression test); branch log line emits the expected value. | `co_cli/context/summarization.py`, `co_cli/context/_compaction_markers.py` |
| ✓ DONE — TASK-3 | Thread `previous_compaction_summary` through `apply_compaction` → `summarize_dropped_messages` → `summarize_messages`. Add `previous_summary: str \| None = None` keyword-only parameter to `summarize_dropped_messages` (`compaction.py:131-150`) — the intermediate function that currently calls `summarize_messages` — and forward it. In `apply_compaction` (`compaction.py:202-238`), read `deps.runtime.previous_compaction_summary` on entry, pass to `_gated_summarize_or_none` → `summarize_dropped_messages` → `summarize_messages`. On successful generation (summary text returned non-None), write back to `deps.runtime.previous_compaction_summary` BEFORE applying `build_compaction_marker` at `:224` (store raw template content, not the prefixed marker). On failure (summary None: model absent, circuit breaker, summarizer raise), leave the prior value untouched. | After a successful compaction, `deps.runtime.previous_compaction_summary` holds the raw summary text (no `[CONTEXT COMPACTION — REFERENCE ONLY]` prefix); after a failed compaction the prior value is intact. | `co_cli/context/compaction.py` |
| ✓ DONE — TASK-4 | Tests in `tests/context/test_context_compaction.py`: (a) **unit-style iterative-branch test** — call `summarize_messages` directly with a known `previous_summary` containing a distinctive marker string, assert the returned summary preserves that marker (one real LLM call); (b) **threading test** — pre-seed `deps.runtime.previous_compaction_summary`, drive one `apply_compaction`, assert the field was read (iterative prompt path taken) and written back with raw template content (no marker prefix); (c) **failure-isolation test** — pre-seed the field, force a summarizer failure (no model configured), assert the prior value is untouched; (d) **enrichment-suppression test** — pre-seed `previous_compaction_summary`, place a `SUMMARY_MARKER_PREFIX` `ModelRequest` in the dropped range, call `gather_compaction_context`, assert the result does not contain "Prior summary:" (no LLM call); (e) **reset-behavior test** — pre-seed `previous_compaction_summary` non-None, dispatch `/new` and `/clear` separately, assert each resets the field; dispatch `/compact` and `/resume` separately, assert neither resets the field. All real-deps, no mocks. Plus: extend `evals/eval_compaction_flow_quality.py` with a multi-pass scenario asserting a distinctive token from compaction-1's summary survives into compaction-3's marker — the value-claim evidence at ship time. The expensive 3-pass behavioral test is intentionally not a unit test; eval cost for multi-pass is acceptable. | Five new tests pass; eval scenario passes; full `tests/context/` suite passes. Iterative-branch test fails if TASK-2 forgot the new prompt template; threading test fails if TASK-3 stored the prefixed marker; enrichment-suppression test fails if TASK-2 missed the `_gather_prior_summaries` skip; reset-behavior test fails if TASK-1 reset is missed or wrongly bundled with other fields. | `tests/context/test_context_compaction.py`, `evals/eval_compaction_flow_quality.py` |
| ✓ DONE — TASK-5 | Patch-version bump in `pyproject.toml` (even = feature). | Version bumped by +1; new version is even-numbered. | `pyproject.toml` |

## Files Affected

| File | Tasks |
|------|-------|
| `co_cli/deps.py` | TASK-1 |
| `co_cli/commands/session.py` | TASK-1 |
| `co_cli/context/summarization.py` | TASK-2 |
| `co_cli/context/_compaction_markers.py` | TASK-2 |
| `co_cli/context/compaction.py` | TASK-3 |
| `tests/context/test_context_compaction.py` | TASK-4 |
| `evals/eval_compaction_flow_quality.py` | TASK-4 |
| `pyproject.toml` | TASK-5 |

## Ordering Constraint

**This plan is a hard prerequisite for `2026-04-26-211800-drop-m0-go-hermes.md`** (drop-M0 + aggressive shape). The drop-M0 plan lowers `compaction_ratio` to ~0.65 and `tail_fraction` to ~0.20; those values are only safe after this plan closes the iterative-summary gap. Shipping drop-M0 with aggressive defaults before this plan would expose the cross-compaction-memory failure mode (decisions diluted across passes, model losing project-level continuity).

This plan can also ship independently. Landing iterative summary at the current `0.80 / 0.40` shape is strictly an improvement: the same trigger and tail size, with cross-compaction memory now reliably preserved in the marker.

## Out of Scope (deferred)

- Threshold tuning (lives in drop-M0 + aggressive follow-up).
- Head expansion (`protect_first_n` analogue + system-prompt compaction note). Defensible after this plan ships and aggressive-shape data is available; not load-bearing for the gap-closure goal.
- Cross-session persistence of `previous_compaction_summary`. Adds complexity (which file? overwritten by what process? lifecycle vs. transcript file?) for marginal gain — `/resume` re-derives via the first compaction in the resumed session. Hermes does not persist either.
- Iterative-summary token budget tuning. Hermes uses `summary_target_ratio` (`:239`) to scale; co-cli uses `summarize_messages`'s existing budget. Revisit after iterative path stabilizes.
- Telemetry on iterative-summary effectiveness (how often does the marker preserve cross-compaction decisions). Useful but not in this plan's scope.
- Hermes's summary-failure cooldown mechanism (`_summary_failure_cooldown_until` :303, 711, 748). Co-cli has its own `compaction_skip_count` circuit breaker (`compaction.py:87-128`); the two mechanisms are functionally similar (back off after failures) but structurally different. Bridging them is orthogonal scope.

## Design Note — Iterative Summary Prompt Structure

Mirrors hermes's structure (`context_compressor.py:646-672`). Sketch only — exact prompt text owned by TASK-2:

```
You are updating a context compaction summary. A previous compaction
produced the summary below. New conversation turns have occurred since
then and need to be incorporated.

PREVIOUS SUMMARY:
{previous_summary}

NEW TURNS TO INCORPORATE:
{dropped_content}

Update the summary using this exact structure. PRESERVE all existing
information that is still relevant. ADD new completed actions to the
numbered list (continue numbering). Move items from "In Progress" to
"Completed Actions" when done. Move answered questions to "Resolved
Questions". Update "Active State" to reflect current state. Remove
information only if it is clearly obsolete. CRITICAL: Update "Active
Task" to reflect the user's most recent unfulfilled request — this is
the most important field for task continuity.

{structured_template_sections_already_used_by_first_pass}
```

The structured-template sections (Active Task, Goal, Constraints, Completed Actions, Active State, ...) are shared with the from-scratch path. The iterative path's distinguishing instruction is the preserve/add/move/remove discipline.

## Design Note — Why Store Raw Template, Not Prefixed Marker

Hermes (`:703-705`) stores `summary = content.strip()` as `_previous_summary` BEFORE applying `_with_summary_prefix(summary)` for the in-context marker (`:708`). The stored value is the LLM's raw structured-template output — Active Task, Goal, Completed Actions, etc. — without the `[CONTEXT COMPACTION — REFERENCE ONLY]` prefix.

When the next compaction's iterative prompt feeds this back as `PREVIOUS SUMMARY:`, the model sees clean structured content to update, not a prefixed handoff marker that includes "treat as background reference, do not act on" framing. Feeding back the prefixed version would confuse the update path's PRESERVE/ADD/MOVE discipline.

Co-cli's equivalent split: `apply_compaction` calls `summarize_dropped_messages` → `summarize_messages` to get the raw summary text, then `build_compaction_marker(dropped_count, summary_text)` to assemble the in-context marker. The raw `summary_text` is what should be stored in `previous_compaction_summary`. The marker assembly happens after, downstream of the storage write.

## Design Note — Why Not Persist Across Sessions

Persistence is tempting but adds disproportionate complexity for marginal gain:

- Where does it live? `~/.co-cli/sessions/{slug}.summary.md` paired with the transcript? In a SQLite column?
- When is it loaded? On `/resume`, presumably — but the transcript reload is already the primary state restore; the summary is at most an optimization.
- What happens on transcript-only resume (without summary file)? The next compaction starts fresh — same as today.

The marginal gain is one fewer "from-scratch" compaction per resumed session. The marginal cost is a new persistence path with its own corruption modes. Defer until telemetry shows resumed sessions disproportionately suffer from cold-start compactions. Hermes does not persist either; their `on_session_reset :203-210` clears the field unconditionally.

## Delivery Summary — 2026-04-27

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | Field exists with default `None`; reset observable after `/new` and `/clear` | ✓ pass |
| TASK-2 | Iterative prompt structurally distinguishable; enrichment suppression when field set; from-scratch regression | ✓ pass |
| TASK-3 | `previous_compaction_summary` holds raw text after success; prior value intact after failure | ✓ pass |
| TASK-4 | 5 new tests pass (3 non-local, 2 `@pytest.mark.local`); eval step `step_16_iterative_summary_3_pass` added | ✓ pass |
| TASK-5 | `pyproject.toml` version bumped to `0.8.26` (even = feature) | ✓ pass |

**Tests:** scoped (`tests/context/`) — 137 passed, 5 deselected (`@pytest.mark.local`), 0 failed; lint PASS
**Doc Sync:** fixed — `compaction.md` sections 2.3, 2.4, 2.6, 4 updated for iterative-summary additions; `system.md` `CoRuntimeState` field map updated

**Overall: DELIVERED**
All 5 tasks shipped. `previous_compaction_summary` field wired from `CoRuntimeState` through `apply_compaction` → `summarize_messages`; iterative-update prompt branch active on second and later compactions per session; `/new` and `/clear` reset the field; failure isolation by silence preserved. Version bumped to 0.8.26.

## Implementation Review — 2026-04-27

### Evidence

**TASK-1: `CoRuntimeState.previous_compaction_summary`**
- Field declared: `co_cli/deps.py:160` — `previous_compaction_summary: str | None = None`
- Listed as cross-turn in docstring: `co_cli/deps.py:130`
- `/clear` resets to `None`: `co_cli/commands/session.py:16`
- `/new` resets to `None`: `co_cli/commands/session.py:32`
- `/compact` does NOT reset: `co_cli/commands/session.py:37-87` — no reset in handler
- `/resume` does NOT reset: `co_cli/commands/session.py:90-118` — no reset in handler

**TASK-2: `summarize_messages` iterative branch**
- `previous_summary: str | None = None` keyword-only param: `co_cli/context/summarization.py:252`
- `log.info("compaction_summarize_branch=%s", ...)`: `co_cli/context/summarization.py:269`
- Iterative branch when provided: `co_cli/context/summarization.py:272-273` — calls `_build_iterative_template`
- `_build_iterative_template` builds PREVIOUS SUMMARY + NEW TURNS TO INCORPORATE: `co_cli/context/summarization.py:169-195`
- `gather_compaction_context` skips `_gather_prior_summaries` when field non-None: `co_cli/context/_compaction_markers.py:184-188`

**TASK-3: Threading through `apply_compaction`**
- Reads `ctx.deps.runtime.previous_compaction_summary`: `co_cli/context/compaction.py:228`
- Passes to `_gated_summarize_or_none`: `co_cli/context/compaction.py:229-231`
- Passes through to `summarize_dropped_messages`: `co_cli/context/compaction.py:179`
- Passes through to `summarize_messages`: `co_cli/context/compaction.py:151`
- Writes back raw text on success: `co_cli/context/compaction.py:232-233` — guarded by `if summary_text is not None`
- Prior value untouched on failure: gate-closed path returns `None`, guard prevents write

**TASK-4: Five new tests + eval step**
- (a) Iterative-branch unit test: `tests/context/test_context_compaction.py:588` — `@pytest.mark.local`, PASSED (4.4s, real Ollama call)
- (b) Threading test: `tests/context/test_context_compaction.py:621` — `@pytest.mark.local`, PASSED (12s, real Ollama call)
- (c) Failure-isolation test: `tests/context/test_context_compaction.py:647` — no LLM, PASSED
- (d) Enrichment-suppression test: `tests/context/test_context_compaction.py:661` — no LLM, PASSED
- (e) Reset-behavior test: `tests/context/test_context_compaction.py:685` — covers /clear, /new, /compact (empty), /resume (no sessions), PASSED
- Eval step 16: `evals/eval_compaction_flow_quality.py:2213` — 3-pass cross-compaction memory preservation with `JWT_ROTATION_7779` distinctive token

**TASK-5: Version bump**
- `pyproject.toml:6` — `version = "0.8.26"` (even = feature) ✓

### Issues Found & Fixed

None found. All requirements verified by direct code read. No stale imports, no dead code, no logic gaps.

### Tests

Full pytest suite: **523 passed, 1 failed** in 168s.

Failure: `tests/llm/test_llm_gemini.py::test_gemini_noreason_faster_than_reasoning` — RCA: Gemini API transient latency (87s for a one-word reply, exceeding 30s `LLM_GEMINI_NOREASON_TIMEOUT_SECS`). This test is pre-existing, unmodified in this branch (last touched in `9675ecd`), and makes no call to code changed by this PR. The other two Gemini tests in the same run passed in 2.4s each. The failure is cloud API degradation, not a regression. Pre-existing test not related to compaction tail compensators.

All 5 TASK-4 tests passed. Both `@pytest.mark.local` tests ran against live Ollama.

### Doc Sync

`docs/specs/compaction.md` and `docs/specs/system.md` already reflect the new feature:
- `compaction.md` documents `previous_compaction_summary` at lines 431-451, iterative/from-scratch branches at lines 565-566, enrichment-suppression rule at line 524, `summarize_messages` signature at line 682, test coverage at line 694
- `system.md` lists `previous_compaction_summary` in `CoRuntimeState` field map at line 117

No sync needed.

### Behavioral Verification

`uv run co config` — all components healthy: LLM Online (Ollama qwen3.5:35b-a3b-think), Shell Active, Google Configured, Web Search Configured, MCP 1 ready, Database Active.

### Overall: PASS

All 5 tasks implemented correctly, verified against source, 523/524 tests passing. The single failure is a pre-existing Gemini API latency test unrelated to this feature. Lint clean. Docs up to date. Ship-ready.
