---
name: orchestrate-plan
description: Orchestrate the planning phase. TL drafts the plan, then spawns Core Dev (implementation risk) and PO (scope + first principles) as parallel subagents to critique it. All roles share docs/TODO-<slug>.md as the workbench. Use when starting a new major feature, doc restructuring, or refactoring.
---

# Plan Orchestration Workflow

**TL is the orchestrator.** Two subagents are spawned after each TL draft: **Core Dev** critiques from an implementation and risk perspective; **PO** challenges scope, first principles, and over-engineering. All roles share `docs/TODO-<slug>.md` as the convergence workbench — every role reads from it and appends its output to it.

**Derive the slug** from the feature name: lowercase, hyphenated (e.g. `sqlite-fts`, `auth-refresh`, `knowledge-docs-restructure`).

**Consumes:** REVIEW-<scope>.md, RESEARCH-<scope>.md (if exists), source. **Produces:** docs/TODO-<slug>.md

---

## Task Classification

**Before writing anything**, TL classifies the task as one of:

| Type | Description | TL extras |
|------|-------------|-----------|
| `code-feature` | New functionality, new tools, schema additions | Standard |
| `doc-restructure` | Reorganizing or rewriting design/TODO docs | Add Code Accuracy Verification step |
| `doc+code` | Doc update + corresponding code changes | Both extras |
| `refactor` | Code reorganization without behavior change | Add regression surface check |

State the task type at the top of the TL draft (e.g. "Task type: code-feature").

---

## Workbench File Structure

`docs/TODO-<slug>.md` is the single shared artifact. Every role appends to it in order:

```
# TODO: <Feature Name>
... plan content: Context, Problem & Outcome, Scope, Design, Tasks, Testing, Open Questions ...

---

# Audit Log

## Cycle C1 — Team Lead
Submitting for Core Dev review.

## Cycle C1 — Core Dev
...critique (assessment, blocking list, major/minor issues)...

## Cycle C1 — PO
...scope + first-principles critique (assessment, blocking list, major/minor issues)...

## Cycle C1 — Team Lead Decisions
...decision table...

## Cycle C2 — Team Lead
Submitting for Core Dev review.

## Cycle C2 — Core Dev
...
```

Note: the `---` separator and `# Audit Log` section are stripped at the end before the Gate 1 section is appended.

---

## Phase 1 — TL: Draft

**Before writing, do:**
1. Search the codebase for relevant modules, patterns, and tests related to the feature.
2. Read related DESIGN and TODO docs in `docs/`. If `docs/REVIEW-<scope>.md` exists for this feature area, read it — carry forward any unresolved findings into the plan's Context section. A REVIEW verdict of `ACTION_REQUIRED` is a blocking pre-condition — stop and surface it rather than drafting over it:
   ```
   ✗ REVIEW-<scope>.md verdict is ACTION_REQUIRED.
   Resolve P0 items before planning. Run /sync-doc or fix TODO as indicated.
   ```
3. If `docs/TODO-<slug>.md` already exists, read it first. **Shipped-work check:** For each section or phase, spot-check one key file it names in `files:`. If that file already implements the described behavior, mark the section as "shipped — skip" in your notes and call it out in the Context section. Do not draft tasks for already-implemented work.
4. For each open question you intend to list, first try to answer it by reading existing source files. An open question answerable by inspection weakens the plan and will be flagged by Core Dev.
5. **For `doc-restructure` and `doc+code` tasks**: Run a **Code Accuracy Verification** pass — read each source file referenced by the target docs and check every factual claim against the code. List inaccuracies explicitly in the Context section before proposing structure changes.

**Draft `docs/TODO-<slug>.md`** filling all sections:
- **Context, Problem & Outcome, Scope, High-Level Design, Implementation Plan, Testing, Open Questions**
- Each task must be atomic (single agent session, ≤5 files touched) with:
  - Stable ID (TASK-1, TASK-2…)
  - `files:` list of paths to create or modify
  - `done_when:` a single, verifiable output (e.g. "test X passes", "file Y exists with field Z", "doc section X matches code behavior"). When `done_when` includes test stub code that captures library output (Rich console, custom fixtures, patched modules), note that the stub is a behavioral spec — Dev is responsible for adapting it to the runtime context. Do not assume plain stdlib output from a project that uses a custom theme or console wrapper. **`done_when` must reflect what the test literally checks** — if you write "registers X tools", provide a concrete check command (e.g. `assert len(agent._function_tools) == 2`). If you cannot write the check, use the simpler assertion the test actually validates.
  - `prerequisites: [TASK-1, TASK-2]` — optional; tasks that must complete before this one.
    Omit if there are no dependencies. Always use list syntax, even for a single dependency.
  - For `code-feature`: Red-Green-Refactor test requirement
  - For `doc-restructure`: Accuracy check requirement (grep/read the code, confirm claim is correct)
- **Guard condition parity:** For each new tool that mirrors an existing one, list its guard conditions (e.g. `max_requests < 1`, empty-string check) and compare against the nearest existing peer tool. Note intentional divergences inline in the task — do not leave them implicit for Core Dev to catch.
- Decisions must include rationale and alternatives considered.

> **Output contract:** `files:` and `done_when:` on every task are mandatory — they are consumed by `/orchestrate-dev` to drive implementation and verification. A task missing either field will block the dev phase. Core Dev must also verify that `prerequisites:` chains form a DAG — flag any task that transitively depends on itself as a blocking issue.

**Append to `docs/TODO-<slug>.md`** (after a `---` separator and `# Audit Log` heading on first cycle):
```
## Cycle C1 — Team Lead
Submitting for Core Dev review.
```

---

## Phase 2 — Core Dev + PO: Critique

Spawn Core Dev and PO as parallel subagents. Both read `docs/TODO-<slug>.md` and append their critiques — Core Dev under `## Cycle C1 — Core Dev`, PO under `## Cycle C1 — PO`.

### Core Dev

Core Dev critiques from an implementation and risk perspective.

**Core Dev checklist — implementation quality:**
- Missing or ambiguous steps
- Hidden coupling / migration gotchas
- Tasks too large for a single agent session or missing `done_when`
- "Hallucinated" success (outcomes assumed without validation steps)
- Test coverage gaps
- All `done_when:` criteria are machine-verifiable. Acceptable: `grep/test/file/doc-match`.
  Not acceptable: subjective phrases like "code is clean", "developer is satisfied",
  "feature works as expected" with no concrete check command.

**Core Dev checklist — operational risk:**
- Schema or data model changes without migration or rollback path
- Irreversible operations (deletes, overwrites, publishes, prunes) without safeguards
- External API integrations or third-party side effects without error handling
- Tools marked `requires_approval=True` missing approval wiring

**For `doc-restructure` and `doc+code` tasks, also check:**
- Are all inaccuracies identified in TL's Code Accuracy Verification addressed by tasks?
- Navigability: can a new contributor find what they need without reading more than 2 docs?
- Cross-references: are links between docs consistent and non-circular?
- Scope creep: is the plan restructuring more than intended without justification?
- Are deleted/merged docs properly retired (no dangling links in CLAUDE.md or other DESIGN docs)?

**On C2+:** Before raising new issues, explicitly verify each blocking item from the previous cycle is resolved. Call out any that are still unaddressed — these remain blocking regardless of new findings.

Output appended to the workbench under `## Cycle C1 — Core Dev`:

```
**Assessment:** approve | revise
**Blocking:** CD-M-1, CD-M-2  (or "none")
**Summary:** <1–3 sentences>

**Major issues:**
- **CD-M-1** [<Section X.Y or TASK-N>]: <what is wrong or missing>. Recommendation: <concrete, actionable change>

**Minor issues:**
- **CD-m-1** [<Section X.Y or TASK-N>]: <clarity or style issue>. Recommendation: <suggested tweak>
```

### PO

PO critiques from a scope, value, and first-principles perspective. Does not re-raise implementation issues already flagged by Core Dev.

**PO checklist:**
- **Right problem?** Does the plan address the actual user need, or a proxy/assumed version of it?
- **Correct scope?** Is the scope the minimum needed to solve the problem — no more, no less?
- **First principles?** Does the design start from fundamentals, or does it layer complexity on top of existing complexity without necessity?
- **Non-over-engineering?** Are any tasks, abstractions, or design choices more elaborate than the problem warrants? Flag gold-plating, premature generalization, and speculative future-proofing.
- **Effectiveness?** Will this plan, if fully executed, actually solve the stated problem for the user?

Output appended to the workbench under `## Cycle C1 — PO`:

```
**Assessment:** approve | revise
**Blocking:** PO-M-1, PO-M-2  (or "none")
**Summary:** <1–3 sentences>

**Major issues:**
- **PO-M-1** [<Section X.Y or TASK-N>]: <scope/value/first-principles concern>. Recommendation: <concrete change>

**Minor issues:**
- **PO-m-1** [<Section X.Y or TASK-N>]: <minor concern>. Recommendation: <suggested tweak>
```

---

## Phase 3 — TL: Decisions & Plan Update

TL reads the updated workbench and processes every Core Dev and PO issue. Decide: **adopt**, **modify**, or **reject** with brief rationale.

**Append to `docs/TODO-<slug>.md`**:
```
## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | adopt    | Added migration rollback step to TASK-3 |
| CD-M-2   | modify   | Added test stub; kept scope narrow |
| CD-m-1   | reject   | Style preference — matches existing codebase convention |
| PO-M-1   | adopt    | Removed speculative caching layer — not needed for MVP |
| PO-m-1   | reject   | Abstraction is justified by 3 existing callers |
```

**Update the plan section** of `docs/TODO-<slug>.md` applying all adopted and modified changes.

---

## Stop Conditions

Stop iterating when **both** conditions are met:
1. **`Blocking: none`** in the latest Core Dev output, AND
2. **`Blocking: none`** in the latest PO output.

**Diminishing returns shortcut:** If both are `Blocking: none` and all remaining issues are minor (`CD-m-*` / `PO-m-*`) and TL rejected fewer than 2 items total in the last cycle — stop immediately.

**C1 fast-path:** If both Core Dev and PO return `Blocking: none` on the first cycle, stop conditions apply immediately — no C2 needed. Proceed directly to the stop sequence below.

**Iteration cap:** If neither condition is met after **3 cycles**, stop and escalate:
```
## Escalation — Cycle C3 limit reached

Unresolved blocking items: <list CD-M-* ids>
Human decision required before proceeding.
```

When stopping normally:

1. **Strip the Audit Log** — remove the `---` separator and everything from `# Audit Log` to the end of the file. The TODO file must be clean: only tasks and plan content remain.

2. **Append the final section** to `docs/TODO-<slug>.md`:
```
## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev <slug>`
```

---

## Iteration Protocol

```
C1: TL Draft → spawn Core Dev + PO (parallel) → TL Decisions → update plan
C2:            spawn Core Dev + PO (parallel) → TL Decisions → update plan
C3:            spawn Core Dev + PO (parallel) → TL Decisions → update plan
    → if still blocking: escalate to human
```

Each new cycle increments the `cycle_id` (C2, C3…).
