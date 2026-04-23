# Plan: Compaction Summary Schema — Pending/Resolved Split and Merge Contract

Task type: `code-feature`

---

## Context

Gap #3 and Gap #9 from `docs/reference/RESEARCH-compaction-co-vs-hermes-gaps.md`.

**Gap #3 (Risk: Medium):** co-cli's `_SUMMARIZE_PROMPT` has no `## Pending User Asks` or
`## Resolved Questions` sections. When a summary spans multiple open questions, the resuming
LLM cannot tell which were already answered. Hermes's explicit split is load-bearing for
multi-step interactive sessions.

**Gap #9 (corrected scope, Risk: Low):** co-cli detects prior summaries and passes them via
`## Additional Context` with the instruction *"integrate its content — do not discard it.
Update sections with new information."* The gap is that this instruction is unstructured —
no schema governs state transitions. Hermes's merge contract (`In Progress → Completed`,
`Pending User Asks → Resolved Questions`) makes resolved/pending tracking deterministic.
Gap #9 fix requires Gap #3's sections to exist first; with them in place, the merge
instruction becomes a one-liner rider.

**No other gaps are dependencies.** Gap #4 (defensive handoff framing) is independent and
not in scope. Gap #3 and Gap #9 both live in `_SUMMARIZE_PROMPT` in
`co_cli/context/summarization.py` — implementation collapses to a single file edit plus test
updates.

**Current-state validation:** Read `co_cli/context/summarization.py` — `_SUMMARIZE_PROMPT`
confirmed to have seven sections (Goal, Key Decisions, Errors & Fixes, Working Set,
Progress, Next Step, conditional User Corrections) with no Pending/Resolved split. The
`_build_summarizer_prompt` function assembles: base template → context addendum (if any) →
personality addendum (if active). The prior-summary merge instruction sits at the bottom of
`_SUMMARIZE_PROMPT` as a free-text paragraph. No phantom features or schema mismatches found.
No stale exec-plan found for this slug.

---

## Problem & Outcome

**Problem:** The summary template has no dedicated slots for open user questions or their
resolution status.

**Failure cost:** When compaction fires mid-session, unanswered questions can be carried
forward as implicitly resolved (the LLM has no slot to mark them pending), or answered
questions can be re-raised by the resuming model because the summary gives no signal that
they were closed. Both failure modes grow worse with each successive compaction cycle.

**Outcome:** After this change, every summary produced under pressure will have a
`## Pending User Asks` section for unanswered questions and a `## Resolved Questions`
section for answered ones. On recompression, an explicit transition instruction tells the
LLM to migrate items between slots — replacing the open-ended "integrate" guidance with a
deterministic merge contract.

---

## Scope

In scope:
- Extend `_SUMMARIZE_PROMPT` with `## Pending User Asks` and `## Resolved Questions`
  sections (Gap #3).
- Extend the prior-summary integration instruction in `_SUMMARIZE_PROMPT` with an explicit
  state-transition contract: pending → resolved when answered; unanswered stays pending
  (Gap #9).
- Update `test_context_compaction.py` to cover the new sections and the merge instruction.

Out of scope:
- Gap #4 (defensive handoff framing / `_summary_marker` preamble) — independent, not needed
  for Gap #3/#9 to function.
- Any changes to `_SUMMARIZER_SYSTEM_PROMPT`, `_PERSONALITY_COMPACTION_ADDENDUM`,
  `_build_summarizer_prompt` signature, or `_gather_prior_summaries` detection logic.
- Eval runs or behavioral benchmarks (prompt-only change; no new code paths, no new config).

---

## Behavioral Constraints

- `_SUMMARIZE_PROMPT` must still produce a valid handoff summary when no prior summary exists
  — both new sections are conditional (skip if no content; do not generate filler).
- The merge instruction must fire only when a prior summary is present in context. The
  instruction lives in the base prompt — it is a no-op when no prior summary appears (the
  LLM has nothing to migrate).
- Section ordering must be preserved: new sections insert after `## Progress` and before
  `## Next Step`, keeping `## Next Step` last (it is the continuation anchor).
- `_build_summarizer_prompt` signature and assembly order must not change — existing callers
  (`/compact` slash command and `summarize_history_window`) must be unaffected.
- `test_build_summarizer_prompt_variants` must continue to pass — the parametrized cases
  remain valid; new tests are additive.

---

## High-Level Design

Both gaps collapse to a single prompt edit in `co_cli/context/summarization.py`:

**New sections (Gap #3)** inserted after `## Progress`:

```
## Pending User Asks
Questions the user asked that are unanswered at the point of compaction. List each
verbatim or near-verbatim. Skip this section if there are none — do not write "None".

## Resolved Questions
Questions that were asked and answered within the compacted range. One line per question:
"Q: <question> → A: <one-sentence answer>". Skip if none.
```

**Extended merge contract (Gap #9)** replacing the current open-ended integration paragraph:

```
If a prior summary exists in the conversation, integrate its content — do not discard it.
Apply these transitions:
- Items in a prior '## Pending User Asks' that are now answered → move to '## Resolved Questions'.
- Items that remain unanswered → keep in '## Pending User Asks'.
- Items in a prior '## Resolved Questions' → carry forward as-is.
Do not re-raise resolved questions as pending. Update all other sections with new information.
```

No new imports, no new functions, no config changes, no API surface changes.

---

## Implementation Plan

### ✓ DONE — TASK-1 — Extend `_SUMMARIZE_PROMPT` (Gap #3 + Gap #9)

**What:** Add `## Pending User Asks` and `## Resolved Questions` sections and replace the
open-ended prior-summary integration paragraph with an explicit state-transition contract.

```
files:
  - co_cli/context/summarization.py

done_when: |
  uv run python -c "
  from co_cli.context.summarization import _SUMMARIZE_PROMPT
  assert '## Pending User Asks' in _SUMMARIZE_PROMPT
  assert '## Resolved Questions' in _SUMMARIZE_PROMPT
  assert \"move to '## Resolved Questions'\" in _SUMMARIZE_PROMPT
  assert 'Skip this section if there are none' in _SUMMARIZE_PROMPT
  assert _SUMMARIZE_PROMPT.index('## Pending User Asks') < _SUMMARIZE_PROMPT.index('## Next Step')
  print('PASS')
  "

success_signal: Compaction summaries include Pending/Resolved slots; recompression
  summaries carry forward only still-open questions as pending.
```

### ✓ DONE — TASK-2 — Update tests (Gap #3 + Gap #9 coverage)

**What:** Add behavioral tests that call `_build_summarizer_prompt()` and assert on the
assembled prompt string — exercising the real assembly path, not the raw constant.

```
files:
  - tests/test_context_compaction.py

prerequisites: [TASK-1]

done_when: |
  uv run pytest tests/test_context_compaction.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-compaction-schema.log

success_signal: N/A — test-only change; no user-visible behavior.
```

---

## Testing

- `test_build_summarizer_prompt_variants` — parametrized cases remain valid; confirm no
  regression (the baseline template is `_SUMMARIZE_PROMPT`, which now includes the new
  sections — all existing assertions still hold).
- New test `test_summarize_prompt_pending_resolved_sections` — calls
  `_build_summarizer_prompt(_SUMMARIZE_PROMPT, context=None, personality_active=False)` and
  asserts the assembled string contains `## Pending User Asks` and `## Resolved Questions`.
- New test `test_summarize_prompt_merge_contract` — calls `_build_summarizer_prompt` and
  asserts the assembled string contains `"move to '## Resolved Questions'"`.
- New test `test_summarize_prompt_skip_guard` — calls `_build_summarizer_prompt` and asserts
  the assembled string contains `"Skip this section if there are none"`.
- Full suite gate: `uv run pytest tests/test_context_compaction.py -x` with tee log.

---

## Open Questions

None — all answerable by inspection. Both gaps are prompt-text-only changes in a
single function. No architectural decisions required.

---

## Final — Team Lead

Plan approved. C1 fast-path: PO `Blocking: none`, Core Dev blocking issues CD-M-1 and
CD-M-2 resolved by updating task `done_when` fields and rewriting proposed tests to
exercise `_build_summarizer_prompt()` at the assembly boundary. All five minor issues
adopted. No open questions remain.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev compaction-summary-schema`

---

## Delivery Summary — 2026-04-21

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | Python assertions: `## Pending User Asks` and `## Resolved Questions` in `_SUMMARIZE_PROMPT`, merge contract present, ordering correct | ✓ pass |
| TASK-2 | `uv run pytest tests/test_context_compaction.py -x` | ✓ pass |

**Tests:** full suite — 596 passed, 0 failed
**Doc Sync:** fixed — `docs/specs/compaction.md` section 2.6 updated with new prompt sections and merge contract description; section 4 test file entry updated.

**Overall: DELIVERED**
Both prompt-text gaps landed in `co_cli/context/summarization.py`; three new tests cover section presence, merge contract, and skip guard; full suite green.

---

## Implementation Review — 2026-04-21

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | Python assertions (5 checks) | ✓ pass | `summarization.py:131` — `## Pending User Asks`; `:134` — `## Resolved Questions`; `:137` — `## Next Step` (ordering preserved); `:152-154` — merge contract with conditional gate; `:133` — skip guard |
| TASK-2 | `uv run pytest tests/test_context_compaction.py -x` | ✓ pass | `test_context_compaction.py:312` — calls `_build_summarizer_prompt()` assembly boundary; `:319` — merge contract assertion; `:325` — skip guard assertion; no mocks, no fixtures, no duplicate coverage |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -v`
- Result: 596 passed, 0 failed
- Log: `.pytest-logs/20260421-233518-review-impl.log` (nearest timestamp)

### Doc Sync
- Scope: narrow — all changes confined to `co_cli/context/summarization.py` (prompt text, no API) and test file; no cross-cutting changes
- Result: clean — `docs/specs/compaction.md` was already updated during delivery (section 2.6 section list + merge contract description; section 4 test coverage description)

### Behavioral Verification
- `uv run co config`: ✓ healthy (all components nominal)
- Prompt-only change — no user-facing CLI surface modified. `success_signal` is a prompt-contract guarantee: `_SUMMARIZE_PROMPT` at lines 131–157 contains both slots and the merge contract, confirmed by re-running TASK-1 `done_when`. No regression path exists.

### Overall: PASS
Both gaps closed with minimal surgical edits to `_SUMMARIZE_PROMPT`; three load-bearing tests added; full suite green; spec accurate. Ship with `/ship compaction-summary-schema`.

