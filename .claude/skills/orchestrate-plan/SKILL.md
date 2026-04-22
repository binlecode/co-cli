---
name: orchestrate-plan
description: Orchestrate the planning phase. TL drafts the plan, then spawns Core Dev (implementation risk) and PO (scope + first principles) as parallel subagents to critique it. All roles share docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md as the workbench. Use when starting a new major feature, doc restructuring, or refactoring.
argument-hint: "<slug>"
---

# Plan Orchestration Workflow

**TL is the orchestrator and the planning gate.** Plan will create or refine `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` (use today's date) — creating it for new work, refining an existing one before dev. TL validates current state before drafting — review is not a separate prerequisite. Two subagents are spawned after each TL draft: **Core Dev** critiques from an implementation and risk perspective; **PO** challenges scope, first principles, and over-engineering. All roles share `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` as the convergence workbench — every role reads from it and appends its output to it.

**Slug for this delivery: `$ARGUMENTS`** — use this value wherever `<slug>` appears below.

**Consumes:** docs/reference/RESEARCH-<scope>.md (if exists), source. **Produces:** docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md (created or refined)

---

## Task Classification

**Before writing anything**, TL classifies the task as one of:

| Type | Description | TL extras |
|------|-------------|-----------|
| `code-feature` | New functionality, new tools, schema additions | Standard |
| `doc-restructure` | Reorganizing or rewriting non-DESIGN docs (reference/, TODO, CLAUDE.md) | Add Code Accuracy Verification step |
| `doc+code` | Non-DESIGN doc update + corresponding code changes (reference/, TODO, CLAUDE.md) | Both extras |
| `refactor` | Code reorganization without behavior change | Standard |

State the task type at the top of the TL draft (e.g. "Task type: code-feature").

---

## Workbench File Structure

`docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` is the single shared artifact. Every role appends to it in order:

```
# Plan: <Feature Name>
... plan content: Context, Problem & Outcome, Scope, Behavioral Constraints, Failure Modes (conditional), High-Level Design, Tasks, Testing, Open Questions ...

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

Note: the Gate 1 final section is appended first, then the `---` separator and `# Audit Log` section are stripped — leaving only tasks, plan content, and the Final section.

---

## Phase 1 — TL: Draft

**Before writing, do:**
1. Search the codebase for relevant modules, patterns, and tests related to the feature.
2. Read related DESIGN and TODO docs in `docs/`. **Current-state validation:** Before drafting, do two checks:
   - **Doc/source accuracy:** Scan relevant source files and DESIGN docs for accuracy against the planned scope. Flag phantom features, schema mismatches, and stale module names.
   - **Workflow artifact hygiene:** Check for stale TODO files with no remaining unshipped work (all tasks `✓ DONE` and no pending review). Note any hygiene issues in the Context section.
   If the current state is too inconsistent to plan safely, stop and surface it:
   ```
   ✗ Current state is too inconsistent to plan safely.
   Describe the specific inaccuracies found. Run /sync-doc to fix docs first.
   ```
   This check happens inline — no separate pre-planning step is required.
3. Check if a plan already exists by globbing `docs/exec-plans/active/*-<slug>.md`. If found, read it. **Shipped-work check:** For each section or phase, spot-check one key file it names in `files:`. If that file already implements the described behavior, mark the section as "shipped — skip" in your notes and call it out in the Context section. Do not draft tasks for already-implemented work.
4. For each open question you intend to list, first try to answer it by reading existing source files. An open question answerable by inspection weakens the plan and will be flagged by Core Dev.
5. **For `doc-restructure` and `doc+code` tasks**: Run a **Code Accuracy Verification** pass — read each source file referenced by the target docs and check every factual claim against the code. List inaccuracies explicitly in the Context section before proposing structure changes.
6. **For AI behavioral features** (new agents, personality changes, tool-chain modifications affecting model output): before drafting, run N representative inputs through the current system and annotate observed failure modes. List findings in `## Failure Modes` in the TODO before writing `## High-Level Design`. Do not write criteria against imagined failure space — only against observed behavior.

**Draft `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`** filling all sections:
- **Context, Problem & Outcome, Scope, Behavioral Constraints, Failure Modes (conditional for AI behavioral features), High-Level Design, Implementation Plan, Testing, Open Questions**
  - **Problem & Outcome** must include a `Failure cost:` line immediately after `Problem:` — what the user cannot do / what silently breaks as a result of the problem.
- Each task must be atomic (single agent session, ≤5 files touched) with:
  - Stable ID (TASK-1, TASK-2…)
  - `files:` list of paths to create or modify
  - `done_when:` a single, verifiable output (e.g. "test X passes", "file Y exists with field Z", "doc section X matches code behavior"). When `done_when` includes test stub code that captures library output (Rich console, custom fixtures, patched modules), note that the stub is a behavioral spec — Dev is responsible for adapting it to the runtime context. Do not assume plain stdlib output from a project that uses a custom theme or console wrapper. **`done_when` must reflect what the test literally checks** — if you write "registers X tools", provide a concrete check command (e.g. `assert len(agent._function_tools) == 2`). If you cannot write the check, use the simpler assertion the test actually validates.
    **For tasks with a non-N/A `success_signal`** (user-facing behavior): `done_when` must be a test run or behavioral command — not only a grep or file-exists check. A grep confirms structure; it does not confirm the feature works. Prefer: `uv run pytest tests/test_<feature>.py`, `uv run co <command> succeeds`, or a specific assertion that exercises the runtime behavior. Core Dev will flag grep-only `done_when` on user-facing tasks as a minor issue.
    **Integration boundary, not module boundary:** `done_when` must verify that the deliverable is wired into its consumer — not just that the module imports. For tools: assert the tool appears in the agent's toolset or is callable via the agent. For config: assert the field is read by the loader or affects runtime behavior. A clean import proves the file exists; it does not prove the feature is reachable. Prefer assertions at the point where the feature meets its caller.
  - `success_signal:` one sentence — what a user observes when this works correctly in production. Optional for refactor tasks with no user-visible behavior change (use N/A).
  - `prerequisites: [TASK-1, TASK-2]` — optional; tasks that must complete before this one.
    Omit if there are no dependencies. Always use list syntax, even for a single dependency.
  - For `code-feature`: Red-Green-Refactor test requirement
  - For `doc-restructure`: Accuracy check requirement (grep/read the code, confirm claim is correct)
- **No spec update tasks:** specs in `docs/specs/` are updated automatically by `sync-doc` post-delivery — they are outputs of delivery, not inputs. Any task whose `files:` list includes a `docs/specs/` path is invalid and must be removed before implementation begins.
- **Guard condition parity:** For each new tool that mirrors an existing one, list its guard conditions (e.g. `max_requests < 1`, empty-string check) and compare against the nearest existing peer tool. Note intentional divergences inline in the task — do not leave them implicit for Core Dev to catch.
- Decisions must include rationale and alternatives considered.

> **Output contract:** `files:` and `done_when:` on every task are mandatory — they are consumed by `/orchestrate-dev` to drive implementation and verification. A task missing either field will block the dev phase. Core Dev must also verify that `prerequisites:` chains form a DAG — flag any task that transitively depends on itself as a blocking issue.

**Append to `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`** (after a `---` separator and `# Audit Log` heading on first cycle):
```
## Cycle C1 — Team Lead
Submitting for Core Dev review.
```

---

## Phase 2 — Core Dev + PO: Critique

Spawn Core Dev and PO as parallel subagents. Both read `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` and append their critiques — Core Dev under `## Cycle C1 — Core Dev`, PO under `## Cycle C1 — PO`.

### Core Dev

Core Dev critiques from an implementation and risk perspective.

Before critiquing, read the full checklist now:

> Read: .claude/skills/orchestrate-plan/references/core-dev-checklist.md

Apply every item in that file to your critique.

**For `doc-restructure` and `doc+code` tasks, also check:**
- Are all inaccuracies identified in TL's Code Accuracy Verification addressed by tasks?
- Navigability: can a new contributor find what they need without reading more than 2 docs?
- Cross-references: are links between docs consistent and non-circular?
- Scope creep: is the plan restructuring more than intended without justification?
- Are deleted/merged docs properly retired (no dangling links in CLAUDE.md or other DESIGN docs)?

**On C2+:** Before raising new issues, explicitly verify each blocking item from the previous cycle is resolved. For each `adopt` or `modify` entry in TL's decision table, read the specific plan section cited in the `Change` column — not just the decision table — and confirm the concern is substantively addressed. A superficial or missing change re-raises the original issue. Call out any that are still unaddressed — these remain blocking regardless of new findings.

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

Before critiquing, read the full checklist now:

> Read: .claude/skills/orchestrate-plan/references/po-checklist.md

Apply every item in that file to your critique.

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

**Append to `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`**:
```
## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1   | adopt    | Added migration rollback step to TASK-3 | Added `rollback: drop column X` to TASK-3 files: |
| CD-M-2   | modify   | Added test stub; kept scope narrow | Added done_when stub to TASK-2 |
| CD-m-1   | reject   | Style preference — matches existing codebase convention | — |
| PO-M-1   | adopt    | Removed speculative caching layer — not needed for MVP | Removed caching task from Implementation Plan |
| PO-m-1   | reject   | Abstraction is justified by 3 existing callers | — |
```

For every **adopt** or **modify** decision, the `Change` column must describe the specific change with enough detail for Core Dev to locate and verify it in the plan (e.g. which task, which field, what was added). A vague summary like "updated plan" is not acceptable. `reject` entries use `—`.

**Update the plan section** of `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` applying all adopted and modified changes.

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

1. **Append the final section** to `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`:
```
## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev <slug>`
```

2. **Strip the Audit Log from the plan** — remove the `---` separator and everything from
   `# Audit Log` to (but not including) the `## Final — Team Lead` section just appended.
   The plan file must be clean after this step: only tasks, plan content, and the Final section remain.

---

## Iteration Protocol

```
C1: TL Draft → spawn Core Dev + PO (parallel) → TL Decisions → update plan
C2:            spawn Core Dev + PO (parallel) → TL Decisions → update plan
C3:            spawn Core Dev + PO (parallel) → TL Decisions → update plan
    → if still blocking: escalate to human
```

Each new cycle increments the `cycle_id` (C2, C3…).
