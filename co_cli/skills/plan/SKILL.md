---
description: Planning — turn a request (a feature, a document, a research question, a project) into a scoped, ordered plan with acceptance criteria and surfaced open questions.
argument-hint: "[what to plan]"
user-invocable: true
---

# Plan

**Invocation:** `/plan [what to plan]`

Turn a request — a feature, a document, a research question, a project — into a scoped, ordered plan with acceptance criteria and open questions surfaced before the work begins.

**Core principle:** A good plan makes the work obvious. If a task leaves the doer guessing, it is incomplete — sharpen the `Done when` or split the task.

---

## Phase 1 — Scope

Clarify the problem and its boundaries before writing any tasks.

1. Restate the request in one sentence. If a *discoverable* fact is missing, find it; ask only when the ambiguity is a preference or constraint that genuinely changes the plan.
2. Identify what is **in scope**: the specific change, the parts affected, and the outcome someone will observe.
3. Identify what is **out of scope**: adjacent improvements that could be done but are not required by this request. List them briefly so they are not lost, but do not plan them.
4. State the acceptance criteria: what observable state confirms the work is done?
   - Code: "The user can do X and sees Y."
   - Research: "The question is answered with cited sources."
   - Writing: "The draft covers sections A–C."
5. Read the relevant material / current state to understand what you are working with. Note any constraints (existing structure to preserve, external contracts, sources or paths to respect).

## Phase 2 — Tasks

Draft an ordered task list. Each task must be independently executable and verifiable.

For each task, write:
```
### Task N — <verb phrase>

**Touches:** <what the task changes or produces — files for code; sections, sources, or deliverables otherwise>
**Done when:** <one-sentence verifiable condition>
**Notes:** <any hint or constraint — omit if none>
```

Task ordering rules:
- Prerequisites before the tasks that depend on them.
- Foundational pieces before what builds on them.
- Verification or review steps after the work they check.

Keep tasks atomic: a task that spans many moving parts is a signal to split it.

After listing tasks, add a one-line **Estimated scope** statement (e.g. "4 tasks, low risk") so the reviewer can calibrate effort at a glance.

When the plan has more than one task, write each task to the session todo list as one item — its content the task's verb phrase plus its `Done when`. (Skip this for a single-task or purely informational request.)

## Phase 3 — Open questions

Surface blockers and decisions that must be resolved before or during the work.

List each open question as:
```
- [ ] <Question> — <why it blocks or what decision it drives>
```

Categories to check:
- **External dependencies or contracts**: does this change affect something others rely on — a public interface, a shared format, an upstream service?
- **Backward compatibility / reversibility**: does the plan break something others depend on, and can it be undone?
- **Gaps in what's known**: is there a part the author does not understand well enough to plan confidently?
- **Resources or access needed**: does the plan require a tool, source, permission, or service not yet available?

If an open question is a preference or tradeoff you are deferring, record it with your recommended default so the decision is teed up rather than reopened from scratch.

If there are no open questions, write "None — ready to implement." Do not leave this section empty.

## Common Mistakes

Each is a real failure mode for this skill — the contrast is the fix.

- **Vague `Done when`.** Bad: "the section is solid" / "the feature works." Good: "the draft answers the three review questions with a cited source each" / "`pytest tests/test_auth.py::test_login` passes (3 cases)." A condition you cannot confirm by eye or with one check is not a done-when.
- **Scope leak.** Bad: a task that quietly fixes an adjacent thing the request never asked for. Good: list that improvement under out-of-scope in Phase 1 and leave it unplanned.
- **Oversized task.** Bad: one task bundling several distinct deliverables ("do the whole pipeline"). Good: split at the seams — one cohesive change per task.
- **Open questions skipped.** Bad: "None" written without checking. Good: walk every Phase 3 category; write "None — ready to implement" only after they all clear.

## Rules

- The plan names the steps and their done-conditions, not the finished work product.
- Every `Done when` must be verifiable by inspection or a single check.
- Scope creep is a bug: if a task exceeds its stated scope during the work, the plan must be updated before continuing.
