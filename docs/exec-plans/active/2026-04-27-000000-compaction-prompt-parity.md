# Plan: Compaction Prompt Parity (hermes-agent best practices)

**Task type: code-feature**

---

## Context

Cross-repo audit compared co-cli's compaction prompts against hermes-agent
(`hermes-agent/agent/context_compressor.py`). Three gaps were found where
hermes-agent's prompt design is materially better. (A fourth gap — dynamic token
budget injection — is deferred pending post-ship eval validation; see Open Questions.)
No stale plans or already-shipped work found in active exec-plans for this scope.

**Current-state validation:**

- `co_cli/context/summarization.py` — source is authoritative; `_SUMMARIZE_PROMPT`,
  `_SUMMARIZER_SYSTEM_PROMPT`, `_build_summarizer_prompt`, `summarize_messages` all
  exist and match the code reviewed.
- `co_cli/context/_compaction_markers.py` — `summary_marker()` and `static_marker()`
  exist; `SUMMARY_MARKER_PREFIX` is the stable sentinel.
- `docs/specs/compaction.md` — references `_SUMMARIZE_PROMPT` and the marker prefix;
  spot-checked against source; accurate.
- Tests in `tests/context/test_context_compaction.py` cover `_build_summarizer_prompt`,
  section ordering assertions, iterative branch, and marker content — these are the
  tests most affected by TASK-1, TASK-2, and TASK-3.

---

## Problem & Outcome

**Problem:** co-cli's compaction prompts have three behavioural gaps relative to
hermes-agent's validated design:

1. The summarizer LLM is not told to suppress output preamble, so models often prepend
   `"Here is the summary:"` before the structured sections, polluting the handoff.
2. The `summary_marker` injected into the conversation does not tell the continuation
   model that environment state already reflects completed work, so the model may
   re-attempt completed actions.
3. `## Progress` conflates completed actions, in-flight work, and remaining work in one
   unstructured blob; no tool attribution or numbering — the continuation model cannot
   distinguish what is done from what is pending.

**Failure cost:** Compaction continuations lose task state (re-executed completed
steps, verbose pre-summary noise, unclearable in-flight/remaining confusion), degrading
the agent's usefulness precisely when context pressure is highest.

**Outcome:** After this delivery:

- Summarizer output never contains preamble text before the first `##` section heading.
- Continuation model is explicitly told to avoid repeating work already reflected in
  session state.
- Completed actions are a numbered list with tool attribution; in-flight and remaining
  work are separate named sections, each explicitly framed.

---

## Scope

**In scope:**

- `co_cli/context/summarization.py` — `_SUMMARIZER_SYSTEM_PROMPT`, `_SUMMARIZE_PROMPT`
- `co_cli/context/_compaction_markers.py` — `summary_marker()` body
- `tests/context/test_context_compaction.py` — update / add tests for all three changes

**Out of scope:**

- Changing trigger thresholds, token accounting, or boundary planner.
- Changing hermes-agent's own prompts.
- Any `docs/specs/` edits (auto-handled by sync-doc post-delivery).
- `## Active State` and `## Blocked` sections (lower priority; hermes overlap with
  `## Working Set` and `## Errors & Fixes` is sufficient for this cycle).
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
5. `SUMMARY_MARKER_PREFIX` string value must not change (it is the stable sentinel used
   in `_gather_prior_summaries` and `test_gather_compaction_context_*` tests).
6. `_build_summarizer_prompt()` public signature must not change.
7. When `context=None` and `personality_active=False`, the assembled prompt must equal
   `_SUMMARIZE_PROMPT` (the constant itself — the test is self-referential, not a
   hardcoded string copy, so it remains valid after TASK-3 rewrites the constant).
8. Personality addendum must remain after context addendum in the assembled prompt
   (`test_build_summarizer_prompt_keeps_personality_after_context` must pass).
9. `## Completed Actions` must appear before `## In Progress`, which must appear before
   `## Remaining Work`, which must appear before `## Working Set` in `_SUMMARIZE_PROMPT`.
10. Only `## Progress` and `## Working Set` are replaced/restructured by TASK-3.
    `## Active Task`, `## Goal`, `## Key Decisions`, `## Errors & Fixes`, `## Pending
    User Asks`, `## Resolved Questions`, `## Next Step`, and `## Critical Context` are
    not moved, removed, or reordered.

---

## High-Level Design

### TASK-1: Suppress preamble in `_SUMMARIZER_SYSTEM_PROMPT`

Append one sentence to `_SUMMARIZER_SYSTEM_PROMPT`:

> Output only the structured summary body — no preamble, greeting, or prefix before the
> first section heading.

Pure string append; no logic changes. Follows hermes preamble pattern
`"Do NOT include any preamble, greeting, or prefix"` but placed in the system prompt
(co-cli's correct location) rather than the user message.

### TASK-2: Strengthen `summary_marker()` with session-state directive

Add two sentences to `summary_marker()` after the existing `resume from there` clause
(line 74 in `_compaction_markers.py`) and before the `f"The summary covers..."` line:

> Respond ONLY to the most recent user message that appears after this summary.
> The current session state (files, code, environment) may already reflect work
> described here — do not repeat or redo it.

Both sentences are present verbatim in hermes `SUMMARY_PREFIX`. The `SUMMARY_MARKER_PREFIX`
sentinel is not touched.

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
navigating nested structure; a single section with subsections risks the model treating
all subsections as part of the same scope. `## Working Set` is kept last (and its name
unchanged) because the iterative-update instructions in `_build_iterative_template`
already reference `'Completed Actions'` and `'In Progress'` by name — matching the new
sections with no further changes needed to the iterative template.

The new sections replace only `## Progress` and the current position of `## Working Set`
in the prompt. All other sections (`## Active Task`, `## Goal`, `## Key Decisions`,
`## Errors & Fixes`, `## Pending User Asks`, `## Resolved Questions`, `## Next Step`,
`## Critical Context`) remain in their current positions, unchanged.

---

## Implementation Plan

### TASK-1 — Suppress preamble in system prompt
- **files:** `co_cli/context/summarization.py`, `tests/context/test_context_compaction.py`
- **done_when:**
  ```
  uv run pytest tests/context/test_context_compaction.py -x -k "system_prompt"
  ```
  New test `test_summarizer_system_prompt_no_preamble_instruction` asserts
  `"no preamble"` appears in `_SUMMARIZER_SYSTEM_PROMPT` (case-insensitive match).
- **success_signal:** Summarizer output no longer begins with prose before the first
  `##` section heading when tested against a real LLM call (observable in `/compact` output).
- **prerequisites:** none

### TASK-2 — Strengthen `summary_marker()` with session-state directive
- **files:** `co_cli/context/_compaction_markers.py`, `tests/context/test_context_compaction.py`
- **done_when:**
  ```
  uv run pytest tests/context/test_context_compaction.py -x -k "summary_marker"
  ```
  Updated/new test `test_summary_marker_session_state_directive` asserts:
  - `"Respond ONLY to the most recent user message"` in marker content
  - `"current session state"` in marker content
  - `SUMMARY_MARKER_PREFIX` still starts the content (sentinel unchanged)
  - New sentences appear after `"resume from there"` and before `"The summary covers"`
- **success_signal:** After compaction, the continuation model does not re-attempt
  actions already reflected in the working directory.
- **prerequisites:** none

### TASK-3 — Restructure Progress sections in `_SUMMARIZE_PROMPT`
- **files:** `co_cli/context/summarization.py`, `tests/context/test_context_compaction.py`
- **done_when:**
  ```
  uv run pytest tests/context/test_context_compaction.py -x
  ```
  All pre-existing section-ordering tests pass (Behavioral Constraints 1–4, 7–8).
  New test `test_summarize_prompt_progress_structure` asserts:
  - `"## Completed Actions"` in prompt
  - `"## In Progress"` in prompt
  - `"## Remaining Work"` in prompt
  - `"## Working Set"` in prompt
  - ordering: index(`Completed Actions`) < index(`In Progress`) < index(`Remaining Work`)
    < index(`Working Set`)
  - `"## Progress\n"` NOT in prompt (old section removed)
  - `"[tool: name]"` hint text in prompt (tool attribution instruction present)
  - `"## Active Task"` and `"## Goal"` positions unchanged from baseline (Constraint 10)
  Note: `test_build_summarizer_prompt_variants` no-extras baseline (`result ==
  _SUMMARIZE_PROMPT`) remains valid — the test compares against the constant itself,
  not a hardcoded string copy, so it auto-tracks the updated value.
- **success_signal:** Compaction summaries list actions as a numbered sequence with
  tool names; continuation model can unambiguously distinguish done/doing/todo.
- **prerequisites:** none

---

## Testing

All three tasks follow the Red-Green-Refactor pattern:

1. Write the new / updated test first (it fails on current code).
2. Apply the code change.
3. Confirm the file suite passes: `uv run pytest tests/context/test_context_compaction.py -x`.
4. Full suite gate before ship: `scripts/quality-gate.sh full`.

No evals are updated in this cycle — the changes are prompt-text and marker-text only.
`evals/eval_compaction_quality.py` can be run manually post-ship to validate no
regression in summary quality. After shipping, run `/compact` manually with a multi-turn
conversation to observe preamble suppression (TASK-1), session-state directive (TASK-2),
and action numbering (TASK-3).

---

## Open Questions

**Deferred: Dynamic token budget injection** — `_build_summarizer_prompt()` could accept
`target_tokens: int | None` and prepend `"Target ~{N} tokens for the summary."` (computed
as `estimate_message_tokens(dropped) // 5` by the caller `_summarize_dropped_messages()`
in `compaction.py`). Deferred because: (a) the formula is ported from hermes without
validation against co-cli usage patterns; (b) there is no mechanism to confirm the hint
actually improves summary density; (c) the signature change adds complexity. Revisit
after running `evals/eval_compaction_quality.py` on post-ship data to confirm whether
budget guidance produces measurably tighter/denser summaries.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev compaction-prompt-parity`
