---
description: Implementation plan drafting — translate a feature request or bug into a scoped, task-ordered execution plan with acceptance criteria.
argument-hint: "[feature or task description]"
user-invocable: true
---

# Plan

**Invocation:** `/plan [feature or task description]`

Translate a feature request, bug fix, or refactor goal into a scoped, task-ordered execution plan with acceptance criteria and open questions surfaced before implementation begins.

---

## Phase 1 — Scope

Clarify the problem and its boundaries before writing any tasks.

1. Restate the request in one sentence. If the request is ambiguous, ask one clarifying question before proceeding — do not guess.
2. Identify what is **in scope**: the specific behaviour change, module(s) affected, and user-visible outcome.
3. Identify what is **out of scope**: adjacent improvements that could be done but are not required by this request. List them briefly so they are not lost, but do not plan them.
4. State the acceptance criteria: what observable state confirms the work is done?
   - For features: "The user can do X and sees Y."
   - For bugs: "The reproduction steps from the report no longer reproduce the issue."
   - For refactors: "All existing tests pass; no public API changes."
5. Read the relevant source files and tests to understand the current state. Note any constraints (existing abstractions to preserve, external contracts, config paths).

## Phase 2 — Tasks

Draft an ordered task list. Each task must be independently executable and verifiable.

For each task, write:
```
### Task N — <verb phrase>

**Files:** <file paths affected>
**Done when:** <one-sentence verifiable condition>
**Notes:** <any implementation hint or constraint — omit if none>
```

Task ordering rules:
- Data model / schema changes before logic that depends on them.
- Test additions before implementation changes when the test defines the contract.
- Infrastructure (config, deps) before the code that uses it.
- Cleanup / dead-code removal last.

Keep tasks atomic: a task that requires editing more than ~3 files is a signal to split it.

After listing tasks, add a one-line **Estimated scope** statement (e.g. "4 tasks, ~3 files, low risk") so the reviewer can calibrate effort at a glance.

## Phase 3 — Open questions

Surface blockers and decisions that must be resolved before or during implementation.

List each open question as:
```
- [ ] <Question> — <why it blocks or what decision it drives>
```

Categories to check:
- **External contracts**: does this change affect a public API, CLI argument, config schema, or file format that other code or users depend on?
- **Backward compatibility**: does the plan break existing behavior? If so, is migration needed?
- **Test gaps**: are there code paths that the plan changes but that have no test coverage? Flag them — the implementer must add coverage as part of the plan.
- **Dependencies**: does the plan require a new package, tool, or service?
- **Unknown behaviour**: is there a code path the author does not understand well enough to plan confidently?

If there are no open questions, write "None — ready to implement." Do not leave this section empty.

## Rules

- Do not write implementation code during planning — write file names and done-when conditions only.
- Every task must have a `Done when` condition that is verifiable without running the full test suite.
- Scope creep is a bug: if a task exceeds its stated scope during implementation, the plan must be updated before continuing.
