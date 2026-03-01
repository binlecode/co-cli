---
name: orchestrate-plan
description: Orchestrate the planning phase. TL drafts the plan, then spawns Core Dev as a parallel subagent to critique it. Both roles share docs/TODO-<slug>.md as the workbench. Use when starting a new major feature, doc restructuring, or refactoring.
---

# Plan Orchestration Workflow

**TL is the orchestrator.** Core Dev is a subagent spawned after each TL draft to critique the plan from an implementation and risk perspective. Both roles share `docs/TODO-<slug>.md` as the convergence workbench — every role reads from it and appends its output to it.

**Derive the slug** from the feature name: lowercase, hyphenated (e.g. `sqlite-fts`, `auth-refresh`, `knowledge-docs-restructure`).

---

## Task Classification

**Before writing anything**, TL classifies the task as one of:

| Type | Description | TL extras |
|------|-------------|-----------|
| `code-feature` | New functionality, new tools, schema additions | Standard |
| `doc-restructure` | Reorganizing or rewriting design/TODO docs | Add Code Accuracy Verification step |
| `doc+code` | Doc update + corresponding code changes | Both extras |
| `refactor` | Code reorganization without behavior change | Add regression surface check |

Announce the type at the top of the TL draft:
```
Task type: code-feature
```

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
...critique JSON...

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
2. Read related DESIGN and TODO docs in `docs/`.
3. If `docs/TODO-<slug>.md` already exists, read it first.
4. For each open question you intend to list, first try to answer it by reading existing source files. An open question answerable by inspection weakens the plan and will be flagged by Core Dev.
5. **For `doc-restructure` and `doc+code` tasks**: Run a **Code Accuracy Verification** pass — read each source file referenced by the target docs and check every factual claim against the code. List inaccuracies explicitly in the Context section before proposing structure changes.

**Draft `docs/TODO-<slug>.md`** filling all sections:
- **Context, Problem & Outcome, Scope, High-Level Design, Implementation Plan, Testing, Open Questions**
- Each task must be atomic (single agent session, ≤5 files touched) with:
  - Stable ID (TASK-1, TASK-2…)
  - `files:` list of paths to create or modify
  - `done_when:` a single, verifiable output (e.g. "test X passes", "file Y exists with field Z", "doc section X matches code behavior")
  - For `code-feature`: Red-Green-Refactor test requirement
  - For `doc-restructure`: Accuracy check requirement (grep/read the code, confirm claim is correct)
- Decisions must include rationale and alternatives considered.

> **Output contract:** `files:` and `done_when:` on every task are mandatory — they are consumed by `/orchestrate-dev` to drive implementation and verification. A task missing either field will block the dev phase.

**Append to `docs/TODO-<slug>.md`** (after a `---` separator and `# Audit Log` heading on first cycle):
```
## Cycle C1 — Team Lead
Submitting for Core Dev review.
```

---

## Phase 2 — Core Dev: Critique

Spawn Core Dev as a subagent. It reads `docs/TODO-<slug>.md` and appends its critique as strict JSON under `## Cycle C1 — Core Dev`.

**Core Dev checklist — implementation quality:**
- Missing or ambiguous steps
- Hidden coupling / migration gotchas
- Tasks too large for a single agent session or missing `done_when`
- "Hallucinated" success (outcomes assumed without validation steps)
- Test coverage gaps

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

Output appended to the workbench:
```json
{
  "cycle_id": "C1",
  "overall_assessment": "approve|revise",
  "summary": "<1–3 sentences>",
  "major_issues": [
    {
      "id": "CD-M-1",
      "location": "<Section X.Y or TASK-N>",
      "description": "<what is wrong or missing>",
      "recommendation": "<concrete, actionable change>"
    }
  ],
  "minor_issues": [
    {
      "id": "CD-m-1",
      "location": "<Section X.Y or TASK-N>",
      "description": "<clarity or style issue>",
      "recommendation": "<suggested tweak>"
    }
  ],
  "blocking_items": ["CD-M-1"]
}
```

---

## Phase 3 — TL: Decisions & Plan Update

TL reads the updated workbench and processes every Core Dev issue. Decide: **adopt**, **modify**, or **reject** with brief rationale.

**Append to `docs/TODO-<slug>.md`**:
```
## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | adopt    | Added migration rollback step to TASK-3 |
| CD-M-2   | modify   | Added test stub; kept scope narrow |
| CD-m-1   | reject   | Style preference — matches existing codebase convention |
```

**Update the plan section** of `docs/TODO-<slug>.md` applying all adopted and modified changes.

---

## Stop Conditions

Stop iterating when **either** condition is met:
1. **`blocking_items` is empty** in the latest Core Dev JSON, OR
2. **Diminishing returns**: `blocking_items = []` AND all remaining issues are `CD-m-*` (minor only) AND TL rejected fewer than 2 items in the last cycle.

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
C1: TL Draft → spawn Core Dev → TL Decisions → update plan
C2:            spawn Core Dev → TL Decisions → update plan
C3:            spawn Core Dev → TL Decisions → update plan
    → if still blocking: escalate to human
```

Each new cycle increments the `cycle_id` (C2, C3…).
