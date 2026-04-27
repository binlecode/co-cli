# Plan: Compaction Prompt Parity (hermes-agent best practices)

**Task type: code-feature**

---

## Context

Cross-repo audit compared co-cli's compaction prompts against hermes-agent
(`hermes-agent/agent/context_compressor.py`). Two tasks remain after re-review
(2026-04-27): TASK-1 (preamble suppression) and TASK-2 (session-state directive)
were superseded/subsumed by independent prompt evolution already in the codebase.

**Current-state validation:**

- `co_cli/context/summarization.py` — source is authoritative; `_SUMMARIZE_PROMPT`,
  `_SUMMARIZER_SYSTEM_PROMPT`, `_build_summarizer_prompt`, `summarize_messages`,
  `_build_iterative_template` all exist and match the code reviewed.
- `co_cli/context/_compaction_markers.py` — `summary_marker()` already contains the
  re-execute guard and post-summary scoping clause.
- `docs/specs/compaction.md` — references `_SUMMARIZE_PROMPT` and the marker
  prefix; spot-checked against source; accurate.
- Tests in `tests/context/test_context_compaction.py` cover `_build_summarizer_prompt`,
  section ordering assertions, iterative branch, and marker content — TASK-3 will
  add new tests; TASK-4 will add a single iterative-template test.

---

## Problem & Outcome

**Problem:** Two issues remain:

1. `## Progress` conflates completed actions, in-flight work, and remaining work in one
   unstructured blob; no tool attribution or numbering — the continuation model cannot
   distinguish what is done from what is pending.
2. `_build_iterative_template` references sections (`Completed Actions`, `In Progress`,
   `Active State`) that do not exist in the from-scratch `_SUMMARIZE_PROMPT`. The
   iterative path issues "MOVE items from 'In Progress' to 'Completed Actions'" and
   "UPDATE 'Active State'" instructions against sections the LLM has never written.

**Failure cost:** Compaction continuations cannot cleanly distinguish done/doing/todo
work, and the iterative-update path silently asks the LLM to operate on phantom
sections — degrading summary quality precisely when context pressure is highest.

**Outcome:** After this delivery:

- Completed actions are a numbered list with tool attribution; in-flight and remaining
  work are separate named sections, each explicitly framed.
- `_build_iterative_template` references only sections that exist in
  `_SUMMARIZE_PROMPT` (no orphaned `Active State` reference).

---

## Scope

**In scope:**

- `co_cli/context/summarization.py` — `_SUMMARIZE_PROMPT` Progress restructure
  (TASK-3) and `_build_iterative_template` orphan-reference cleanup (TASK-4)
- `tests/context/test_context_compaction.py` — add tests for both changes

**Out of scope:**

- `_SUMMARIZER_SYSTEM_PROMPT` (superseded by existing voice constraint).
- `_compaction_markers.py` / `summary_marker()` (subsumed by existing re-execute guard).
- Changing trigger thresholds, token accounting, or boundary planner.
- Changing hermes-agent's own prompts.
- Any `docs/specs/` edits (auto-handled by sync-doc post-delivery).
- `## Active State` and `## Blocked` sections (lower priority; hermes overlap with
  `## Working Set` and `## Errors & Fixes` is sufficient for this cycle). Note:
  TASK-4 *removes* the orphaned `Active State` reference from the iterative
  template — it does not introduce the section.
- Dynamic token budget injection (`_build_summarizer_prompt()` param) — deferred; see
  Open Questions.

---

## Behavioral Constraints

1. `_SUMMARIZE_PROMPT` must still contain `## Active Task` before `## Goal`
   (existing test `test_summarize_prompt_active_task_section` must pass unchanged).
2. `_SUMMARIZE_PROMPT` must still contain `## Next Step` before `## Critical Context`
   (existing test `test_summarize_prompt_critical_context_section` must pass unchanged).
3. `_SUMMARIZE_PROMPT` must still contain `## Pending User Asks`, `## Resolved Questions`,
   and the `"move to '## Resolved Questions'"` state-transition contract
   (existing tests `test_summarize_prompt_pending_resolved_sections` and
   `test_summarize_prompt_merge_contract` must pass unchanged).
4. `_SUMMARIZE_PROMPT` must still contain a `"Skip if none"` guard
   (`test_summarize_prompt_skip_guard` must pass).
5. The `"Your first sentence MUST start with 'I asked you...'"` voice constraint
   (currently at `_SUMMARIZE_PROMPT` line 123) must remain — this is the active
   anti-preamble mechanism.
6. `summary_marker()` must continue to contain both `"Do NOT repeat, redo, or
   re-execute any action already described as completed"` and `"respond only to
   user messages that appear AFTER this summary"`.
7. `SUMMARY_MARKER_PREFIX` and `STATIC_MARKER_PREFIX` string values must not change
   (sentinels used in `_gather_prior_summaries`, `is_compaction_marker`, and prior
   tests).
8. `_build_summarizer_prompt()` public signature must not change.
9. When `context=None` and `personality_active=False`, the assembled prompt must equal
   `_SUMMARIZE_PROMPT` (the constant itself — `test_build_summarizer_prompt_variants`
   is self-referential, so it remains valid after TASK-3 rewrites the constant).
10. Personality addendum must remain after context addendum in the assembled prompt
    (`test_build_summarizer_prompt_keeps_personality_after_context` must pass).
11. `## Completed Actions` must appear before `## In Progress`, which must appear
    before `## Remaining Work`, which must appear before `## Working Set` in
    `_SUMMARIZE_PROMPT`.
12. Only `## Progress` and `## Working Set` are replaced/restructured by TASK-3.
    `## Active Task`, `## Goal`, `## Key Decisions`, `## Errors & Fixes`, `## Pending
    User Asks`, `## Resolved Questions`, `## Next Step`, and `## Critical Context` are
    not moved, removed, or reordered.
13. After TASK-4, every section name referenced inside `_build_iterative_template`
    must exist as a `## ` heading in `_SUMMARIZE_PROMPT`. The `Active State`
    reference in the iterative template is the one violating this today and must be
    removed (not added to the from-scratch prompt).

---

## High-Level Design

### TASK-3: Restructure `_SUMMARIZE_PROMPT` Progress sections

Replace the current two flat sections (lines 135–138 in `summarization.py`):

```
## Working Set
[Files read/edited/created. URLs fetched. Active tools.]

## Progress
[Accomplished. In progress. Remaining.]
```

With four structured sections in the same location:

```
## Completed Actions
[Numbered list. Format each as: N. ACTION target — outcome [tool: name]
Example: 1. EDIT co_cli/auth.py:42 — changed `==` to `!=` [tool: file_edit]
Use the actual tool name from the invocation. Be specific: file paths,
line numbers, commands, exact outcomes. One entry per action.]

## In Progress
[Work actively under way at compaction time — what was being done.]

## Remaining Work
[Work not yet started — framed as context, not as instructions to execute.]

## Working Set
[Files read/edited/created. URLs fetched. Active tools.]
```

Ordering rationale: `Completed Actions → In Progress → Remaining Work → Working Set`
preserves temporal narrative (done → doing → todo) followed by the reference index.
Three distinct `##` sections are used rather than one section with subsections because
they allow the continuation model to locate and parse done/pending boundaries without
navigating nested structure. `## Working Set` is kept last (and its name unchanged)
because the iterative-update instructions in `_build_iterative_template` already
reference `'Completed Actions'` and `'In Progress'` by name — TASK-3 retires the
iterative template's two phantom-section references for those names. (The third
phantom reference, `'Active State'`, is retired by TASK-4.)

### TASK-4: Remove orphaned `Active State` reference from `_build_iterative_template`

`_build_iterative_template` at `summarization.py:187-190` issues four discipline
verbs to the LLM. Three target sections that exist (or will exist after TASK-3):

```
ADD new completed actions ...                        → ## Completed Actions  (TASK-3)
MOVE items from 'In Progress' to 'Completed Actions' → ## In Progress        (TASK-3)
MOVE answered questions to 'Resolved Questions'      → ## Resolved Questions (exists)
UPDATE 'Active State' to reflect current state.      → ## Active State       (DOES NOT EXIST)
```

The `UPDATE 'Active State'` instruction is orphaned — the LLM is being told to
update a section the schema never defined. `## Active State` is intentionally not
introduced. Therefore the orphaned line must be deleted from `_build_iterative_template`.

The change is a single-line string deletion. No structural impact on the iterative
branch; the remaining three discipline verbs continue to map to real sections.

---

## Implementation Plan

### ✓ DONE TASK-3 — Restructure Progress sections in `_SUMMARIZE_PROMPT`
- **files:** `co_cli/context/summarization.py`, `tests/context/test_context_compaction.py`
- **done_when:**
  ```
  uv run pytest tests/context/test_context_compaction.py -x
  ```
  All pre-existing section-ordering tests pass (Behavioral Constraints 1–4, 9–10).
  New test `test_summarize_prompt_progress_structure` asserts:
  - `"## Completed Actions"` in prompt
  - `"## In Progress"` in prompt
  - `"## Remaining Work"` in prompt
  - `"## Working Set"` in prompt
  - ordering: index(`Completed Actions`) < index(`In Progress`) < index(`Remaining Work`)
    < index(`Working Set`)
  - `"## Progress\n"` NOT in prompt (old section removed)
  - `"[tool: name]"` hint text in prompt (tool attribution instruction present)
  - `"## Active Task"` and `"## Goal"` positions unchanged from baseline (Constraint 12)
  Note: `test_build_summarizer_prompt_variants` no-extras baseline (`result ==
  _SUMMARIZE_PROMPT`) remains valid — the test compares against the constant itself,
  not a hardcoded string copy, so it auto-tracks the updated value.
- **success_signal:** Compaction summaries list actions as a numbered sequence with
  tool names; continuation model can unambiguously distinguish done/doing/todo.
- **prerequisites:** none

### ✓ DONE TASK-4 — Remove orphaned `Active State` reference from `_build_iterative_template`
- **files:** `co_cli/context/summarization.py`, `tests/context/test_context_compaction.py`
- **done_when:**
  ```
  uv run pytest tests/context/test_context_compaction.py -x -k "iterative"
  ```
  New test `test_iterative_template_references_only_existing_sections` asserts:
  - `"Active State"` NOT in `_build_iterative_template("prev")` output
  - `"Completed Actions"` IS in the output (still referenced; satisfied by TASK-3)
  - `"In Progress"` IS in the output
  - `"Resolved Questions"` IS in the output
  - Every `'<section>'` substring referenced inside the iterative template
    corresponds to a `## <section>` heading in `_SUMMARIZE_PROMPT`
    (constraint 13 — programmatic check via regex over the template body).
- **success_signal:** Iterative-update path no longer instructs the LLM to operate on
  a section the schema never defined.
- **prerequisites:** TASK-3 (the iterative template still references `Completed Actions`
  and `In Progress` after the cleanup; both must exist in the from-scratch prompt).

---

## Testing

Red-Green-Refactor pattern:

1. Write the new / updated test first (it fails on current code).
2. Apply the code change.
3. Confirm the file suite passes: `uv run pytest tests/context/test_context_compaction.py -x`.
4. Full suite gate before ship: `scripts/quality-gate.sh full`.

No evals are updated in this cycle — the changes are prompt-text only.
`evals/eval_compaction_quality.py` can be run manually post-ship to validate no
regression in summary quality.

---

## Open Questions

**Deferred: Dynamic token budget injection** — `_build_summarizer_prompt()` could accept
`target_tokens: int | None` and prepend `"Target ~{N} tokens for the summary."` (computed
as `estimate_message_tokens(dropped) // 5` by the caller `_summarize_dropped_messages()`
in `compaction.py`). Deferred because: (a) the formula is ported from hermes without
validation against co-cli usage patterns; (b) there is no mechanism to confirm the hint
actually improves summary density; (c) the signature change adds complexity. Revisit
after running `evals/eval_compaction_quality.py` on post-ship data.

---

## Final — Team Lead

Plan re-reviewed 2026-04-27 against current source. TASK-1 superseded, TASK-2
subsumed; remaining work is TASK-3 + TASK-4. Plan re-approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev compaction-prompt-parity`
