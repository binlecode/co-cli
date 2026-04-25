# Plan: Compaction Trigger Layer — Unify M0 and M3 Naming

Task type: code-refactor (rename + thin adapters; no behavior change)

## Context

`co_cli/context/compaction.py` exposes two near-identical trigger functions sitting on top of
the shared `_run_window_compaction` core:

- `summarize_history_window` (`compaction.py:364-424`) — registered as the last
  `history_processor`. Computes budget + threshold, gates on `proactive_ratio` (0.75) and the
  anti-thrash counter (with a one-shot console hint when the gate first trips per session),
  delegates to `_run_window_compaction`, then updates the thrash counter based on yield.
  Despite the name, it never calls a summarizer — the summary is four layers down
  (`_run_window_compaction → apply_compaction → _gated_summarize_or_none → summarize_dropped_messages → summarize_messages`).
- `maybe_run_pre_turn_hygiene` (`compaction.py:427-456`) — called once from
  `co_cli/context/orchestrate.py:574` at `run_turn()` entry. Same body as
  `summarize_history_window` minus the thrash gate, with a different ratio (`hygiene_ratio`
  0.88), wrapped in `try/except` for fail-open, and constructs its own `RunContext` because
  the caller has `CoDeps`, not `RunContext`.

Spec `docs/specs/compaction.md:46` labels the pre-turn path "M0" and admits it "uses same M3
planner + summarizer". Spec line 18 simultaneously lists "Two-layer hygiene with separate
thresholds (hermes pattern rejected)" as a non-goal — the implementation contradicts the
non-goal because M0 is, materially, a second hygiene layer.

Anti-patterns visible in the current shape:

1. **Misleading name on the M3 trigger.** `summarize_history_window` is a gate-and-dispatch,
   not a summarizer. Readers must trace four layers down to discover the actual summary call.
2. **Asymmetric `maybe_` prefix.** Both functions decide internally whether to act. Only one
   advertises that in its name.
3. **Asymmetric error handling.** Hygiene swallows all exceptions (fail-open); the M3
   processor lets them propagate. Same underlying call, two different contracts depending on
   caller.
4. **Spec/code drift on M0/M3 framing.** Spec presents M0 as a distinct mechanism; code
   shows M3 fired at a second lifecycle point with a different gate.

Cleanly separable from the now-shipped
`2026-04-25-115715-compaction-hardening-followup.md` (commit `2536cd9`) — that delivery
reshaped `summarize_dropped_messages` into a pure LLM call and introduced the private
`_summarization_gate_open` predicate plus the `_gated_summarize_or_none` orchestrator (the
gate-vs-summarizer split *inside* the LLM call). It also added the one-shot anti-thrash UI
hint and the `proactive_ratio < hygiene_ratio` validator. This plan reshapes the trigger
layer *above* `_run_window_compaction`. The hardening-followup is in `main`; merge churn risk
is now zero, only call-chain references in this plan needed to be refreshed against shipped
code.

**Sibling plan ordering.** `2026-04-25-120808-compaction-runtime-flag-cleanup.md` is a
single-task `/deliver` plan that collapses `compacted_in_current_turn` and
`history_compaction_applied` into one field. Both plans edit `co_cli/context/compaction.py`.
Ship this trigger-unify plan first — it is the larger refactor with more surface area, and
the flag-cleanup plan absorbs the rename onto the new shape (its rename picks up the
reference inside `_compact_window_if_pressured` as one extra line). After this plan ships,
the flag-cleanup plan's task list extends from one rename site to two; no other rework.

## Problem & Outcome

**Problem 1 (naming).** `summarize_history_window` does not summarize. The name forces every
new reader of `co_cli/agent/_core.py:142-147` (the `history_processors` list) to chase the
call chain to discover what it actually does.

**Problem 2 (duplication).** Two functions with ~80% identical bodies, differing only in:
threshold ratio, anti-thrash gate presence, post-run thrash bookkeeping, fail-open wrapping,
and how `RunContext` is obtained. Each future change to the trigger layer (logging, metrics,
new gate, modified threshold logic) must be applied to both.

**Problem 3 (asymmetric contract).** Fail-open in one path and propagate-failure in the
other is not principled — it reflects "wrap whatever the caller couldn't handle". The
`history_processor` slot has no exception handler around it either; if `summarize_history_window`
raised, the turn would fail. The asymmetry is incidental, not designed.

**Outcome:**

- Trigger layer reads honestly: each function name describes the lifecycle point and the
  gate, not the leaf.
- One shared core function captures "compact when over threshold"; two thin adapters express
  per-lifecycle policy (ratio, thrash gate, error contract) as parameters.
- Spec wording aligns with code: M0 and M3 are documented as the same mechanism fired at two
  lifecycle points, with the higher hygiene ratio explained as a safety net for sessions
  where M3's anti-thrash gate suppressed a needed compaction.
- Zero observable behavior change. Same thresholds, same gates, same fallbacks, same logs.

## Scope

**In:**

- `co_cli/context/compaction.py` — rename `summarize_history_window` → `proactive_window_processor`,
  rename `maybe_run_pre_turn_hygiene` → `pre_turn_window_compaction`. Both become thin
  wrappers over a private `_compact_window_if_pressured(ctx, messages, *, ratio,
  apply_thrash_gate) -> list[ModelMessage]` that owns the shared gate-and-dispatch body.
  Pre-turn wrapper continues to build `RunContext` from `CoDeps` and to wrap the call in
  `try/except` for fail-open.
- `co_cli/context/compaction.py` — update the `__all__` list (`compaction.py:58-82`).
- `co_cli/agent/_core.py` — update the import and `history_processors=[…]` registration
  (`_core.py:18, 146`).
- `co_cli/context/orchestrate.py` — update the import and call site (`orchestrate.py:66, 574`).
- `co_cli/context/_compaction_boundaries.py` — update the docstring reference at
  `_compaction_boundaries.py:176`.
- `evals/eval_compaction_quality.py`, `tests/context/test_context_compaction.py`,
  `tests/context/test_history.py` — update imports and any references to the renamed
  functions. Tests that assert on the function name in a string (if any) update; tests that
  exercise behavior should pass unchanged.
- `docs/specs/compaction.md` and `docs/specs/core-loop.md` — `/sync-doc` after delivery
  handles the M0/M3 framing rewrite, the `__all__` listing at `compaction.md:665`, the
  diagrams at `compaction.md:74` and `compaction.md:257`, the prose mentions at
  `compaction.md:457, 610`, and the M0 hygiene flow references at `core-loop.md:81, 271`.
  **Spec edits are not tasks in this plan** — they are an output of delivery via the
  auto-invoked `/sync-doc` step. `docs/REPORT-*.md` files reference the old names but are
  intentionally frozen-in-time per repo policy and must not be edited.

**Out:**

- No threshold tuning. `proactive_ratio=0.75` and `hygiene_ratio=0.88` stay as-is. The
  shipped hardening-followup added the `proactive_ratio < hygiene_ratio` validator already.
- No changes to `_run_window_compaction`, `apply_compaction`, `_gated_summarize_or_none`,
  `_summarization_gate_open`, `summarize_dropped_messages`, or any layer below the trigger
  functions — the hardening-followup already shipped those surfaces.
- No changes to the anti-thrash counter, circuit breaker, or skip-count logic. Cadence
  preserved exactly.
- No changes to the anti-thrash UI hint emission text or the `compaction_thrash_hint_emitted`
  one-shot flag on `CoRuntimeState`. Move the call site, do not edit the behavior.
- No new config knobs. The shared core takes parameters; settings remain unchanged.
- No version bump for spec-only edits performed by `/sync-doc`. This plan's `pyproject.toml`
  bump covers the code rename.

## Behavioral Constraints

1. **Zero behavior change.** Same thresholds, same gates, same logs, same fallbacks, same
   side effects on `deps.runtime`. A test running before and after the refactor must produce
   identical observable results — token counts, message lists, log lines, runtime flags.
2. **`history_processor` signature preserved.** `proactive_window_processor` remains
   `(ctx, messages) -> messages` — pydantic-ai requires this exact shape for processors
   registered on `Agent(history_processors=[...])`.
3. **Pre-turn fail-open preserved.** `pre_turn_window_compaction` still wraps the call in a
   broad `try/except` and returns the original `message_history` on any exception. The
   M3 processor still propagates exceptions — the asymmetry is documented in the wrapper
   docstrings as intentional (different lifecycle, different blast radius).
4. **`asyncio.CancelledError` propagates.** The pre-turn wrapper's `except Exception`
   already excludes `BaseException`; preserve this property.
5. **Anti-thrash counter cadence unchanged.** `consecutive_low_yield_proactive_compactions`
   still increments after an M3 run with `< cfg.min_proactive_savings` yield, and resets
   otherwise. Pre-turn does not touch the counter (matches today).
6. **Public surface stays in `__all__`.** Renamed names appear in `compaction.py`'s
   `__all__`. Old names are removed — this is a breaking rename, but the only callers are
   in-tree (verified via grep against the seven files listed in the Files Affected table).
7. **Preserve the anti-thrash UI hint shipped by the hardening followup.** The
   `2026-04-25-115715-compaction-hardening-followup.md` delivery added a one-shot
   user-visible hint at the anti-thrash gate trip site in `summarize_history_window`
   (`co_cli/context/compaction.py:402-412`), gated by
   `ctx.deps.runtime.compaction_thrash_hint_emitted: bool` on `CoRuntimeState`. This plan
   moves that gate into the new private `_compact_window_if_pressured` core. The hint must
   move with the gate verbatim — same trip condition (`apply_thrash_gate=True` branch only),
   same once-per-session semantics (read + set the bool flag), same text mentioning
   `/compact`. Re-run `tests/context/test_history.py::test_thrash_gate_emits_user_hint_once_per_session`
   after TASK-1 to verify behavior preservation.
8. **Preserve the `budget <= 0` defensive guard.** Today's `maybe_run_pre_turn_hygiene`
   has `if budget <= 0: return message_history` (`compaction.py:441-442`). In practice
   `resolve_compaction_budget` cannot return ≤0 (falls through to `config.llm.ctx_token_budget`,
   always positive), so the check is dead code today — but it is the only thing preventing
   `_run_window_compaction(ctx, messages, budget=0)` if a future config path ever resolves
   to zero. Keep the guard in the unified core (one line after `budget = …`), so the
   refactor preserves rather than regresses the defensive surface.

## Task Breakdown

| # | Task | Effort | Risk | Files |
|---|------|--------|------|-------|
| ✓ DONE — TASK-1 | Extract `_compact_window_if_pressured(ctx, messages, *, ratio, apply_thrash_gate)` private core in `compaction.py`. Body is the shared gate-and-dispatch from today's `summarize_history_window`, parameterized on the thrash-gate flag. | S | Low | `co_cli/context/compaction.py` |
| ✓ DONE — TASK-2 | Rewrite `summarize_history_window` → `proactive_window_processor` as a thin wrapper over the new core. Update `__all__`. | XS | Low | `co_cli/context/compaction.py` |
| ✓ DONE — TASK-3 | Rewrite `maybe_run_pre_turn_hygiene` → `pre_turn_window_compaction` as a thin wrapper: build `RunContext`, fail-open `try/except`, call core with `apply_thrash_gate=False`. Update `__all__`. | XS | Low | `co_cli/context/compaction.py` |
| ✓ DONE — TASK-4 | Update imports + registration: `co_cli/agent/_core.py:18,146`, `co_cli/context/orchestrate.py:66,574`, `co_cli/context/_compaction_boundaries.py:176`, evals, tests. | S | Low | `co_cli/agent/_core.py`, `co_cli/context/orchestrate.py`, `co_cli/context/_compaction_boundaries.py`, `evals/eval_compaction_quality.py`, `tests/context/test_context_compaction.py`, `tests/context/test_history.py` |
| ✓ DONE — TASK-5 | Patch-version bump in `pyproject.toml` (odd = bugfix, even = feature; this is a refactor with no behavior change → patch only, even step). | XS | Low | `pyproject.toml` |

All tasks confined to the seven files listed. No new modules, no new config groups.

## Files Affected

| File | Tasks |
|------|-------|
| `co_cli/context/compaction.py` | TASK-1, TASK-2, TASK-3 |
| `co_cli/agent/_core.py` | TASK-4 |
| `co_cli/context/orchestrate.py` | TASK-4 |
| `co_cli/context/_compaction_boundaries.py` | TASK-4 (docstring only) |
| `evals/eval_compaction_quality.py` | TASK-4 |
| `tests/context/test_context_compaction.py` | TASK-4 |
| `tests/context/test_history.py` | TASK-4 |
| `pyproject.toml` | TASK-5 |

## Ordering Constraint

The prerequisite hardening-followup plan has shipped (commit `2536cd9`, version 0.8.8) —
ordering is satisfied. Both plans edit `co_cli/context/compaction.py`; the hardening-followup
delivery reshaped `summarize_dropped_messages` and `apply_compaction` (the layers below) and
this plan now reshapes the trigger layer above. The split kept each diff focused on one
mental model.

## Out of Scope (deferred)

- Collapsing the two ratios into one. Two thresholds at two lifecycle points is intentional
  per the now-shipped `proactive_ratio < hygiene_ratio` validator (hardening-followup TASK-1,
  in `co_cli/config/_compaction.py:40-49`).
- Re-evaluating the M0 layer's existence. Spec line 18 lists "Two-layer hygiene with
  separate thresholds (hermes pattern rejected)" as a non-goal; reconciling that wording
  with the M0 implementation belongs in a spec-driven design plan, not a rename refactor.
- Removing the pre-turn fail-open wrapper. Whether `run_turn` should propagate compaction
  failures is a contract question, separate from naming.
- Merging `_run_window_compaction` + `apply_compaction` layers. Their separation is load-
  bearing for the overflow recovery and `/compact` paths.

## Design Note — Unified Shape (TASK-1)

```python
async def _compact_window_if_pressured(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    *,
    ratio: float,
    apply_thrash_gate: bool,
) -> list[ModelMessage]:
    """Compact the history window when token pressure exceeds ``ratio * budget``.

    Shared core for the M3 history-processor trigger and the M0 pre-turn trigger. The
    caller supplies the threshold ratio and decides whether to consult the
    anti-thrash gate; everything else (token counting, planner, summarizer, thrash
    counter updates) lives here.
    """
    ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    budget = resolve_compaction_budget(ctx.deps.config, ctx_window)
    if budget <= 0:
        return messages
    cfg = ctx.deps.config.compaction

    reported = (
        0 if ctx.deps.runtime.compacted_in_current_turn
        else latest_response_input_tokens(messages)
    )
    token_count = max(estimate_message_tokens(messages), reported)
    token_threshold = max(int(budget * ratio), cfg.min_context_length_tokens)

    if token_count <= token_threshold:
        return messages

    if apply_thrash_gate and (
        ctx.deps.runtime.consecutive_low_yield_proactive_compactions
        >= cfg.proactive_thrash_window
    ):
        log.info("Compaction: proactive anti-thrashing gate active, skipping")
        if not ctx.deps.runtime.compaction_thrash_hint_emitted:
            from co_cli.display._core import console
            console.print(
                "[dim]Compaction paused: recent passes freed too little context. "
                "Run /compact to force a manual pass.[/dim]"
            )
            ctx.deps.runtime.compaction_thrash_hint_emitted = True
        return messages

    result = await _run_window_compaction(ctx, messages, budget)
    if result is None:
        return messages

    if apply_thrash_gate:
        tokens_after = estimate_message_tokens(result)
        savings = (token_count - tokens_after) / token_count if token_count > 0 else 0.0
        if savings < cfg.min_proactive_savings:
            ctx.deps.runtime.consecutive_low_yield_proactive_compactions += 1
        else:
            ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0
    return result


async def proactive_window_processor(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """M3 history-processor: compact mid-turn when proactive_ratio is exceeded."""
    return await _compact_window_if_pressured(
        ctx, messages,
        ratio=ctx.deps.config.compaction.proactive_ratio,
        apply_thrash_gate=True,
    )


async def pre_turn_window_compaction(
    deps: CoDeps,
    message_history: list[ModelMessage],
) -> list[ModelMessage]:
    """M0 pre-turn hygiene: compact at run_turn() entry when hygiene_ratio is exceeded.

    Fail-open: any exception returns ``message_history`` unchanged so the turn proceeds.
    The thrash gate is intentionally bypassed — pre-turn is the safety net for sessions
    where the in-loop M3 trigger was suppressed.
    """
    try:
        raw_model = deps.model.model if deps.model else None
        ctx = RunContext(deps=deps, model=raw_model, usage=RunUsage())
        return await _compact_window_if_pressured(
            ctx, message_history,
            ratio=deps.config.compaction.hygiene_ratio,
            apply_thrash_gate=False,
        )
    except Exception:
        log.warning("Pre-turn hygiene compaction failed — skipping", exc_info=True)
        return message_history
```

**Note on the dropped `reported_input_tokens` parameter.** Today's `maybe_run_pre_turn_hygiene`
accepts `reported_input_tokens` as an explicit kwarg, and the caller in `orchestrate.py:577`
reads it via `latest_response_input_tokens(message_history)` before passing it in. That
read targets *message metadata*, not `deps.runtime.turn_usage` — `reset_for_turn()` does
not affect it. The shared core's re-read via `latest_response_input_tokens(messages)`
against the same `message_history` produces an identical value, so the parameter is
redundant and is dropped. Update `orchestrate.py:574-578` to call
`pre_turn_window_compaction(deps, message_history)` with no third argument, and remove
the now-unused `latest_response_input_tokens` import there if no other caller in the file
needs it.

## Delivery Summary — 2026-04-25

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_compact_window_if_pressured(ctx, messages, *, ratio, apply_thrash_gate)` private core present in `compaction.py`, with the `budget <= 0` defensive guard preserved per Constraint #8 | ✓ pass |
| TASK-2 | `proactive_window_processor` thin wrapper present; `summarize_history_window` removed; `__all__` updated | ✓ pass |
| TASK-3 | `pre_turn_window_compaction(deps, message_history)` thin wrapper present (no `reported_input_tokens` param); `maybe_run_pre_turn_hygiene` removed; `__all__` updated | ✓ pass |
| TASK-4 | All seven listed files renamed; `grep -rE "summarize_history_window|maybe_run_pre_turn_hygiene" co_cli tests evals` returns zero in-tree matches (only `evals/eval_compaction_quality-result.md` retains old names — frozen eval output snapshot, intentionally untouched per the plan's spec-coverage note); scoped tests pass | ✓ pass |
| TASK-5 | `pyproject.toml` bumped 0.8.8 → 0.8.10 (+2 even step for clean refactor) | ✓ pass |

**Tests:** scoped `tests/context/test_context_compaction.py` + `tests/context/test_history.py` — 91 passed, 0 failed (63.88s). Suite includes `test_thrash_gate_emits_user_hint_once_per_session` (Constraint #7 verification — UI hint preserved verbatim through the move into `_compact_window_if_pressured`).
**Lint:** clean (one auto-fix: ruff-organized import block in `tests/context/test_history.py` after the rename).
**Doc Sync:** fixed — `compaction.md` (5 sites: 2 mermaid nodes, proactive-wrapper prose, proactive→overflow handoff prose, Files-section entry now describes `_compact_window_if_pressured` core), `core-loop.md` (6 sites including a corrected wrong claim that no provider-reported count is available pre-turn — `max(estimate, reported)` is the actual signal), `prompt-assembly.md` (1 site).

**Implementation deltas vs plan:**
- Removed `tests/context/test_pre_turn_hygiene_no_op_first_turn_zero_reported` (line 495 pre-rename). After the `reported_input_tokens` parameter was dropped, the test's setup (`_make_messages(4)` + `reported_input_tokens=0`) became a structural duplicate of `test_pre_turn_hygiene_no_op_below_threshold` (same fixture, same `result is msgs` assertion). Per the no-duplicate-tests policy in CLAUDE.md.
- Migrated `test_pre_turn_hygiene_fires_when_reported_tokens_exceed_threshold` to inject the over-threshold token count via message metadata (`_make_messages(..., last_input_tokens=...)`) instead of via the dropped kwarg. The migrated test drives the same production path the orchestrator uses (`latest_response_input_tokens(message_history)`) and verifies the identical max-of-two property — a test-quality improvement that follows from the parameter drop.
- Renamed test function `test_summarize_history_window_static_marker_when_no_model` → `test_proactive_window_processor_static_marker_when_no_model` to match the new function name.
- Spec wording fix in `core-loop.md:271` — pre-existing inaccuracy ("no provider-reported count is available pre-turn") corrected as part of the same rename pass; both old and new code paths read provider-reported tokens at pre-turn via `latest_response_input_tokens`.

**Overall: DELIVERED**

Refactor preserves observable behavior while collapsing two near-identical trigger functions into a single shared core with thin per-lifecycle wrappers. Names now describe the lifecycle point and the gate, not the leaf summarizer four layers down. The asymmetric error contract (M3 propagates, M0 fails open) is now documented as an intentional lifecycle difference in `pre_turn_window_compaction`'s docstring.

## Implementation Review — 2026-04-25

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `_compact_window_if_pressured(ctx, messages, *, ratio, apply_thrash_gate)` private core in `compaction.py` with `budget <= 0` guard preserved | ✓ pass | `compaction.py:364-369` (signature), `:392-393` (guard), `:408-416` (anti-thrash UI hint moved verbatim — Constraint #7), `:423-429` (thrash counter update gated on `apply_thrash_gate` — Constraint #5) |
| TASK-2 | `proactive_window_processor` thin wrapper, `summarize_history_window` removed, `__all__` updated | ✓ pass | `compaction.py:433-448` (wrapper, signature `(ctx, messages) -> list[ModelMessage]` — Constraint #2), `:76` (in `__all__`), grep clean across `co_cli/`, `tests/`, `evals/`, `docs/specs/` |
| TASK-3 | `pre_turn_window_compaction(deps, message_history)` thin wrapper (no `reported_input_tokens` kwarg), `maybe_run_pre_turn_hygiene` removed, `__all__` updated | ✓ pass | `compaction.py:451-475` (wrapper, `try/except Exception` excludes `BaseException`/`CancelledError` — Constraints #3, #4), `:75` (in `__all__`); intentional fail-open vs propagate asymmetry documented in docstring |
| TASK-4 | All renames propagated; `grep -rE` returns zero stale references in active code; scoped tests pass | ✓ pass | `agent/_core.py:18, 146` (import + processor registration), `orchestrate.py:66, 573` (import + call site), `_compaction_boundaries.py:176` (docstring), eval + 2 test files renamed; only `evals/eval_compaction_quality-result.md` retains old names — frozen eval output snapshot, intentionally untouched |
| TASK-5 | Version bump 0.8.8 → 0.8.10 (+2 even step for refactor) | ✓ pass | `pyproject.toml:7` |

**Behavioral preservation trace (Constraint #1):**
- M3 path: line-for-line equivalent to `summarize_history_window` body, modulo the new `budget <= 0` early return at line 392 (unreachable in production via `resolve_compaction_budget`).
- M0 path: caller's pre-read of `latest_response_input_tokens(message_history)` at the old `orchestrate.py:577` is now performed inside `_compact_window_if_pressured` against the same `message_history`. `reset_for_turn()` runs before the call, leaving `compacted_in_current_turn=False`, so the read returns the identical value the old caller computed.

### Issues Found & Fixed

No issues found. Adversarial self-review challenged each pass (M3 line-by-line equivalence, migrated test driving production path, duplicate-test removal scope, asymmetric-contract documentation, missed-callers sweep, `compacted_in_current_turn` semantics for M0) — none escalated to blocking severity. Phase 4 auto-fix loop was a no-op.

### Tests

- **Command:** `uv run pytest`
- **Result:** 640 passed, 0 failed
- **Duration:** 220.51s
- **Log:** `.pytest-logs/<timestamp>-review-impl.log`

Suite includes `tests/context/test_history.py::test_thrash_gate_emits_user_hint_once_per_session` (Constraint #7 verification — UI hint preserved verbatim through the move into `_compact_window_if_pressured`) and the migrated `test_pre_turn_hygiene_fires_when_reported_tokens_exceed_threshold` (now drives the production `latest_response_input_tokens` lookup via real `RequestUsage` metadata on a real `ModelResponse`, no mocks).

### Doc Sync

- **Scope:** full — public API rename touches the registered history processor and the `run_turn()` call site
- **Result:** clean (executed in Phase 6 of orchestrate-dev; final re-verify via `grep -rE` over `docs/specs/` confirms zero stale references)

### Behavioral Verification

- `uv run co config`: ✓ system healthy — agent constructs cleanly, all integrations resolve (LLM Online, Shell Active, MCP context7 ready, Database 586 MB)
- Full pytest suite (640 tests) exercises `build_agent()` and `pre_turn_window_compaction()` end-to-end — confirms the renamed processor registration and `run_turn` callsite work in the real agent build path

### Lint

- `scripts/quality-gate.sh lint` — clean (199 files, no findings)

### Overall: PASS

Refactor delivers the planned trigger-layer rename and core extraction with zero observable behavior change. All eight Behavioral Constraints verified against source. Adversarial review surfaced no blocking findings. Ship-ready.
