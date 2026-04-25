# Plan: Compaction Hardening — Follow-up

Task type: code-refactor + small features

## Context

Carry-over from `2026-04-21-115119-compaction-hardening.md` (now archived). Three of the
original six gaps remain open in the current code, plus one design issue surfaced during the
close-out review:

1. `CompactionSettings` accepts `proactive_ratio >= hygiene_ratio` without complaint, which
   inverts the intended ordering of the two trigger layers. `maybe_run_pre_turn_hygiene`
   (`co_cli/context/compaction.py:386-415`) and `summarize_history_window`
   (`co_cli/context/compaction.py:331-383`) are siblings — both call `_run_window_compaction`
   directly, with their own threshold check (`hygiene_ratio` and `proactive_ratio`
   respectively). The higher hygiene threshold is intended as a safety net at run_turn entry
   for sessions where the in-loop M3 trigger was suppressed by the anti-thrash gate. If
   `proactive_ratio >= hygiene_ratio`, M3 would fire mid-turn at the lower threshold while
   M0 wouldn't fire at run_turn entry until the higher one — ordering inverted, safety-net
   semantics broken (`co_cli/config/_compaction.py:15-22`).
2. `gather_compaction_context` joins file paths, session todos, and prior summaries, then
   truncates the combined result at `_CONTEXT_MAX_CHARS = 4_000`. A 3 KB prior summary leaves
   working-set anchors (file paths, todos) starved precisely when the summarizer needs them most
   (`co_cli/context/_compaction_markers.py:154–179`).
3. The anti-thrashing gate in `summarize_history_window` (`co_cli/context/compaction.py:369-370`)
   emits a `log.info` when active and offers no user-visible escape — invisible in normal
   CLI use.
4. `summarize_dropped_messages` (`co_cli/context/compaction.py:109–150`) mixes three concerns:
   the *gate* (model presence + circuit breaker + skip-count bookkeeping), the *side
   effect* (`announce` console print), and the *LLM call*. It returns `None` from four
   different branches. The function reads as "summarize" but is in practice "maybe-summarize-or-skip-or-fail".

## Problem & Outcome

**Problem 1 (T1, ratio validation):** Footgun. A misconfigured `~/.co-cli/settings.json` can
silently disable hygiene compaction without any error or log warning.

**Problem 2 (T2, per-source caps):** The 4 KB shared budget is non-deterministic across
sessions — the more compactions accumulate, the more the prior-summary slice eats budget that
should belong to working-set anchors. Quality of the next summary depends on session age.

**Problem 3 (T3, gate visibility):** When the anti-thrashing gate trips and context stabilizes
in the 75–88% dead zone, proactive compaction stays suppressed for the rest of the session
with no user-visible signal. The user has no way to know `/compact` would help.

**Problem 4 (T4, gate/summarizer separation):** Code-reading cost and test surface. The
function name promises "summarize"; the body does not. Tests have to construct the full
`CoDeps` runtime to exercise the LLM call because the gate is fused in. Future callers that
want different gating (e.g. forced manual `/compact`) cannot reuse the summarizer cleanly.

**Outcome:**

- Misconfigured ratios fail loudly at config-load time with a `ValueError`.
- Each compaction-context source has its own cap; the joined result still respects a total cap.
- When the anti-thrashing gate trips, the user sees a one-line hint suggesting `/compact`.
- `summarize_dropped_messages` is a pure LLM call. Gate logic lives in
  `apply_compaction` (the only caller) or a clearly named helper. Skip-count bookkeeping
  stays adjacent to the gate decision.

## Scope

**In:**

- `co_cli/config/_compaction.py` — add `model_validator(mode="after")` enforcing
  `proactive_ratio < hygiene_ratio`.
- `co_cli/context/_compaction_markers.py` — replace the single `_CONTEXT_MAX_CHARS` cap with
  per-source caps in `gather_compaction_context`. Total joined cap remains for safety.
- `co_cli/context/compaction.py` — anti-thrashing gate in `summarize_history_window` emits
  a one-line console hint to the user the first time it trips per session, suggesting
  `/compact`. Subsequent trips stay log-only to avoid spam. (The companion plan
  `2026-04-25-120059-compaction-trigger-unify.md` will later relocate this gate into a
  shared private helper; coupling the hint tightly to the gate site keeps the relocation
  mechanical.)
- `co_cli/context/compaction.py` — split `summarize_dropped_messages` so the LLM call is pure
  (raises on failure, returns `str`). Move the gate (model check, circuit breaker, skip-count
  bookkeeping, announce print, exception-to-static-marker fallback) into `apply_compaction`
  or a `_gated_summarize_or_none` helper. The public surface from `apply_compaction`'s
  perspective is unchanged: it still gets `summary_text: str | None`.
- Tests under `tests/context/` — add coverage for: ratio validator rejects bad config;
  per-source caps are independently respected; anti-thrashing UI hint emits once; the pure
  summarizer raises on summarizer failure (instead of silently returning None).

**Out:**

- No changes to summarizer prompt engineering or section structure (Goal / Key Decisions /
  etc.). The pure-summarizer extraction does not touch `co_cli/context/summarization.py`
  beyond what the call signature requires.
- No new runtime state on `CoRuntimeState` for the UI hint. A simple `bool` `thrash_hint_emitted`
  is sufficient if needed; prefer reusing `consecutive_low_yield_proactive_compactions` boundary
  transitions.
- No spec updates inline — `/sync-doc` runs after delivery handles `docs/specs/compaction.md`.
- No archive cleanup or further changes to the (now archived) parent plan.
- No version bump for documentation-only edits to the parent plan; this plan owns the
  `pyproject.toml` bump for code changes.

## Behavioral Constraints

1. **Ratio validator must reject at load time, not at first hygiene check.** Catching the
   error in `Settings(...)` construction surfaces it during startup, not 10 minutes into a
   session.
2. **Per-source caps must be independent of source order.** If file paths exceed their cap,
   todos must still get their full budget. Tests must verify this with a synthetic dropped
   range that maxes out file paths.
3. **UI hint must not spam.** First-trip-per-session only; subsequent trips log-only.
   Hint text must mention `/compact` by name so the user can act.
4. **Pure summarizer must propagate `asyncio.CancelledError`** (it already does today via
   the broad-but-not-`BaseException` `except Exception`; preserve this property in the
   refactor).
5. **`apply_compaction` return shape unchanged** — `tuple[list[ModelMessage], str | None]`.
   The `None` path now flows through the explicit gate-or-fallback wrapper; callers see no
   change.
6. **Skip-count semantics preserved.** The circuit breaker still trips at `>= 3`, probes
   every `_CIRCUIT_BREAKER_PROBE_EVERY = 10` skips, and resets on success. The refactor
   relocates the bookkeeping but does not alter the cadence.

## Task Breakdown

| # | Task | Effort | Risk |
|---|------|--------|------|
| ✓ DONE — TASK-1 | `model_validator` for `proactive_ratio < hygiene_ratio` in `CompactionSettings` | XS | Low |
| ✓ DONE — TASK-2 | Per-source caps in `gather_compaction_context` (file paths, todos, prior summaries) + retained total cap | S | Low |
| ✓ DONE — TASK-3 | One-shot user-visible hint when anti-thrashing gate first trips per session | XS | Low |
| ✓ DONE — TASK-4 | Extract gate from `summarize_dropped_messages` so the summarizer is a pure LLM call | S | Low |

All four are confined to two source files (`co_cli/config/_compaction.py`,
`co_cli/context/compaction.py`) and `co_cli/context/_compaction_markers.py`. Test files
follow the source split.

## Files Affected

| File | Tasks |
|------|-------|
| `co_cli/config/_compaction.py` | TASK-1 |
| `co_cli/context/_compaction_markers.py` | TASK-2 |
| `co_cli/context/compaction.py` | TASK-3, TASK-4 |
| `tests/context/` (new or existing) | All four |

## Out of Scope (deferred to separate plans)

- Auxiliary summarization model (cheaper LLM for compaction) — architectural, separate plan.
- Session rollover / compaction audit trail — requires new persistence infrastructure.
- Iterative summary evolution (hermes-style "In Progress → Completed" state machine) —
  prompt engineering, separate plan.
- Tool result deduplication (hash-based) beyond what `_dedup_tool_results.py` already does.
- Resolved vs Pending Questions split in summary sections.
- Anti-thrashing config option A (turn-based reset) — deferred until the UI hint shows
  whether users actually need an automatic escape.

## Design Note — Gate / Pure Summarizer Split (TASK-4)

Two reasonable shapes; pick one during implementation:

**Option A — Gate as predicate, summarizer as pure call, orchestration in `apply_compaction`:**

```python
def _summarization_gate_open(ctx) -> bool:
    """Side effect: increments compaction_skip_count when the breaker blocks."""
    if not ctx.deps.model:
        log.info("Compaction: model absent, using static marker")
        return False
    count = ctx.deps.runtime.compaction_skip_count
    if _circuit_breaker_should_skip(count):
        log.warning("Compaction: circuit breaker active (count=%d), static marker", count)
        ctx.deps.runtime.compaction_skip_count += 1
        return False
    if count >= 3:
        log.info("Compaction: circuit breaker probe (count=%d)", count)
    return True


async def summarize_dropped_messages(ctx, dropped, *, focus=None) -> str:
    """Pure: caller must gate first; raises on failure."""
    enrichment = gather_compaction_context(ctx, dropped)
    return await summarize_messages(
        ctx.deps, dropped,
        personality_active=bool(ctx.deps.config.personality),
        context=enrichment, focus=focus,
    )


# In apply_compaction:
summary_text: str | None = None
if _summarization_gate_open(ctx):
    if announce:
        from co_cli.display._core import console
        console.print("[dim]Compacting conversation...[/dim]")
    try:
        summary_text = await summarize_dropped_messages(ctx, dropped, focus=focus)
        ctx.deps.runtime.compaction_skip_count = 0
    except Exception:
        log.warning(
            "Compaction summarization failed — falling back to static marker",
            exc_info=True,
        )
        ctx.deps.runtime.compaction_skip_count += 1
```

**Option B — Pure summarizer alongside a thin `try_summarize_dropped_messages` wrapper:**

Keep both functions. The `try_*` wrapper preserves the current `str | None` return for any
caller that wants gated semantics; the pure version is callable directly when bypass is
intended (e.g. tests exercising the LLM path).

Option A is the leaner refactor — `apply_compaction` is the only caller today, so a wrapper
adds a layer with no second consumer. Option B is forward-friendly if a future flow wants
ungated summarization (no current need). Default to A; revisit only if a second caller
emerges.

## Delivery Summary — 2026-04-25

| Task | done_when (interpreted from plan Outcome + Behavioral Constraints) | Status |
|------|--------------------------------------------------------------------|--------|
| TASK-1 | `Settings(...)` rejects `proactive_ratio >= hygiene_ratio` at load time; `tests/bootstrap/test_config.py` ratio tests pass | ✓ pass |
| TASK-2 | Each source in `gather_compaction_context` is capped independently before joining; the total joined cap is retained as a safety net; per-source-cap tests pass with file-paths overflow not starving todos / prior summaries | ✓ pass |
| TASK-3 | First anti-thrashing gate trip per session emits a console hint mentioning `/compact`; subsequent trips stay log-only via `compaction_thrash_hint_emitted`; once-per-session test passes | ✓ pass |
| TASK-4 | `summarize_dropped_messages` is now a pure LLM call (raises on failure); gate logic moved to `_summarization_gate_open` and orchestration to `_gated_summarize_or_none` (Option A); pure-summarizer-raises test passes; `apply_compaction` return shape unchanged; eval introspection updated | ✓ pass |

**Files changed:**
- `co_cli/config/_compaction.py` — `model_validator(mode="after")` enforces `proactive_ratio < hygiene_ratio` (TASK-1).
- `co_cli/context/_compaction_markers.py` — added `_FILE_PATHS_MAX_CHARS`, `_TODOS_MAX_CHARS`, `_PRIOR_SUMMARIES_MAX_CHARS`; new `_cap` helper; `gather_compaction_context` caps each source before joining (TASK-2).
- `co_cli/context/compaction.py` — `_summarization_gate_open` predicate, `_gated_summarize_or_none` orchestrator, pure `summarize_dropped_messages` (no announce parameter, raises on failure); first-trip-per-session console hint at the anti-thrashing gate site (TASK-3, TASK-4).
- `co_cli/deps.py` — added `compaction_thrash_hint_emitted: bool = False` on `CoRuntimeState` for the once-per-session UI hint guard (TASK-3, declared as ⚠ Extra file: behavioral constraint #2 explicitly permits this bool).
- `evals/eval_compaction_quality.py` — Step 4h structural check updated to introspect `_summarization_gate_open` + `_gated_summarize_or_none` instead of the now-pure `summarize_dropped_messages` (declared as ⚠ Extra file: eval introspects internals that moved during the refactor).
- `tests/bootstrap/test_config.py` — three new tests for the ratio validator (rejects >=, rejects equal, accepts default ordering).
- `tests/context/test_history.py` — four new tests: per-source-cap independence with file-paths overflow, per-source file-paths cap respected solo, anti-thrashing gate one-shot hint, pure-summarizer raises on failure.
- `docs/specs/compaction.md` — sync-doc narrow scope: refreshed `apply_compaction` pseudocode and surrounding prose to describe the gate / pure-summarizer split, the per-source enrichment caps, and the updated file map.

**Tests:** scoped (`tests/bootstrap/test_config.py` + `tests/context/test_history.py` + `tests/context/test_context_compaction.py`) — 90 + 36 = 126 passed, 0 failed.
**Doc Sync:** narrow — fixed `compaction.md` Section 2 (apply_compaction pseudocode, gate-vs-pure-summarizer description, enrichment cap description, files table) for `co_cli/context/compaction.py` and `co_cli/context/_compaction_markers.py`.

**Overall: DELIVERED**

All four tasks shipped; lint clean; scoped tests green; spec updated. The (now archived) parent plan, the two new sibling plans (`2026-04-25-120059-compaction-trigger-unify.md`, `2026-04-25-120808-compaction-runtime-flag-cleanup.md`), and the parent-plan close-out edit remained untouched and unstaged — they belong to other workstreams.

## Review Verdict — 2026-04-25

Evidence-first re-read of every modified file plus a broader test pass.

**Files re-read in full:**
- `co_cli/config/_compaction.py` (50 lines)
- `co_cli/context/_compaction_markers.py` (194 lines)
- `co_cli/context/compaction.py` (456 lines)
- `co_cli/deps.py:118-164` (CoRuntimeState dataclass)
- `evals/eval_compaction_quality.py:725-770` (Step 4h structural check)
- `tests/context/test_history.py` (impacted regions)
- `tests/bootstrap/test_config.py` (impacted regions)
- `docs/specs/compaction.md:50-55, 425-470, 510-525, 660-672` (touched sections)

**Spec/code coherence checks:**
- `compaction.md:51` — references "pure `summarize_dropped_messages`" and per-source caps; matches `_compaction_markers.py:157-193` and `compaction.py:131-150`.
- `compaction.md:430-465` — pseudocode now calls `_gated_summarize_or_none`; matches `compaction.py:223`.
- `compaction.md:450-456` — three-function description matches the actual signatures and docstrings at `compaction.py:109-150, 153-183`.
- `compaction.md:516-519` — per-source caps narrative matches the constants + `_cap` helper.
- `compaction.md:665, 667` — files table matches the new public/private surface.
- `evals/eval_compaction_quality.py:725-768` — introspection targets exist verbatim: `not ctx.deps.model` at `compaction.py:117`, `compaction_skip_count` at `compaction.py:121`, `gather_compaction_context` at `compaction.py:143`, `_summarization_gate_open(ctx)` at `compaction.py:166`, `summarize_dropped_messages` at `compaction.py:175`. Source-line ordering check (`gate_call_line < summarize_call_line`) holds.

**Behavioral-constraint trace (plan §"Behavioral Constraints"):**
- BC1 ratio validator rejects at load time: `_compaction.py:40-49` runs in `model_validator(mode="after")`; `tests/bootstrap/test_config.py` confirms via `load_config()`.
- BC2 per-source caps independent of source order: `_compaction_markers.py:181-189` caps each source before the join; the file-paths-overflow test at `test_history.py:902-947` proves todos and prior summaries survive an oversize file-paths source.
- BC3 UI hint not spammed: `compaction.py:404-411` gates console output on `compaction_thrash_hint_emitted`; the once-per-session test at `test_history.py:172-194` exercises both first-trip and second-trip paths.
- BC4 `asyncio.CancelledError` propagates: pure summarizer at `compaction.py:131-150` has no `except`; orchestrator at `compaction.py:174-181` uses `except Exception`, which (Python 3.8+) does not catch `BaseException` subclasses including `CancelledError`. Verified by reading the body, not run-tested (would require cancellation harness).
- BC5 `apply_compaction` return shape unchanged: signature at `compaction.py:202-209` and return at `compaction.py:239` still `tuple[list[ModelMessage], str | None]`.
- BC6 skip-count cadence preserved: `_summarization_gate_open` increments on breaker-blocked skip (`compaction.py:124`); `_gated_summarize_or_none` increments on summarizer failure (`compaction.py:180`) and resets on success (`compaction.py:182`); `_circuit_breaker_should_skip` and `_CIRCUIT_BREAKER_PROBE_EVERY` unchanged. The pre-existing `test_circuit_breaker_skips_llm_after_three_failures`, `test_circuit_breaker_first_trip_is_skip`, and `test_circuit_breaker_probes_at_cadence` continue to pass.

**Adversarial probes:**
- "Could the hint emit from a sub-agent and be lost?" Sub-agent deps get a fresh `CoRuntimeState` via `fork_deps`; the parent agent's `compaction_thrash_hint_emitted` is independent. In production the user only interacts with the parent, so the parent's flag governs the visible hint — correct semantics.
- "Could the hint emit from the hygiene path?" `maybe_run_pre_turn_hygiene` does not touch the proactive thrash counter or the hint flag; it calls `_run_window_compaction` directly. The hint is M3-only by construction, matching the plan's `summarize_history_window` scoping.
- "Does `extra='ignore'` on `CompactionSettings` mask validator-relevant fields?" The validator reads only `proactive_ratio` and `hygiene_ratio`, both declared `Field`s. `extra='ignore'` only drops unknown keys; it cannot suppress validation of known fields.
- "Does the joined cap overshoot when all three sources are full?" 1500 + 2 (`\n\n`) + 1500 + 2 + 2000 = 5004 chars max before the final `[:_CONTEXT_MAX_CHARS]` slice cuts to 4000. Total cap still binds — safety net intact.
- "Does file-paths truncation produce malformed output?" `_cap` slices Python strings by codepoint, not bytes — no UTF-8 fragmentation. The cut may land mid-path, but that is acceptable as a budget signal to the summarizer (and it already lived inside a comma-joined list with no structural fragility).

**Sibling-plan reference drift:** the active sibling plan `docs/exec-plans/active/2026-04-25-120059-compaction-trigger-unify.md` describes the call chain as `_run_window_compaction → apply_compaction → summarize_dropped_messages → summarize_messages`. After this delivery the chain is `_run_window_compaction → apply_compaction → _gated_summarize_or_none → summarize_dropped_messages → summarize_messages`. Not updating: it is a peer plan owned by another workstream and its author will read live source/spec at delivery time.

**Test runs:**
- Scoped (Phase 3): `tests/bootstrap/test_config.py` + `tests/context/test_history.py` + `tests/context/test_context_compaction.py` — 126 passed.
- Wider (review): `tests/context/ tests/bootstrap/ tests/commands/ -x` — 243 passed in 90s, no failures, no skips.
- Lint: ruff check + format clean across 199 files.

**Findings:** none blocking. No stale imports, no orphan helpers, no dead code introduced, no test asserts truthy-only, no spec text contradicts the source. The two declared `⚠ Extra file:` deviations (`co_cli/deps.py`, `evals/eval_compaction_quality.py`) are necessary and explicitly contemplated by the plan (BC2 for the bool field; the eval introspects the function whose body moved).

**Verdict: PASS** — ready for `/ship` from the user.
