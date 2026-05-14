---
name: orchestrate-plan
description: Orchestrate the planning phase. TL drafts the plan, then spawns Core Dev (implementation risk) and PO (scope + first principles) as parallel subagents to critique it. All roles share docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md as the workbench. Use when starting a new major feature, doc restructuring, or refactoring.
argument-hint: "<slug>"
---

# Plan Orchestration Workflow

**TL is the orchestrator and the planning gate.** Creates or refines `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`. After each TL draft, Core Dev (implementation risk) and PO (scope + first principles) critique in parallel. TL decides and updates. Stop when both return `Blocking: none`.

**Slug: `$ARGUMENTS`**

**Consumes:** `docs/reference/RESEARCH-<scope>.md` (if exists), source. **Produces:** `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`

---

## Phase 1 — TL: Draft

**Before writing:**
1. Read relevant source, tests, and `docs/reference/RESEARCH-<scope>.md` if it exists. Glob `docs/exec-plans/active/*-<slug>.md` — if found, read it and skip already-implemented work.
2. **Current-state check:** scan source and specs for accuracy against the planned scope. If too inconsistent to plan safely: `✗ Current state inconsistent — run /sync-doc first.`
3. **For doc tasks** (restructure or doc+code): run a Code Accuracy Verification pass — read each source file referenced by the target docs and confirm every factual claim. List inaccuracies in Context before proposing changes.
4. For each open question you intend to list, try to answer it from the codebase first. An open question answerable by inspection will be flagged by Core Dev.
5. **For AI behavioral features** (new agents, personality changes, tool-chain modifications affecting model output): run representative inputs through the current system and annotate observed failure modes. List findings in `## Failure Modes` before writing `## High-Level Design`. Do not write criteria against imagined failure space.

**Write `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`** with sections:

**Context, Problem & Outcome, Scope, Behavioral Constraints, High-Level Design, Tasks, Testing, Open Questions**

- `Problem & Outcome` must include a `Failure cost:` line — what silently breaks without this fix.
- Each task must have: stable ID (TASK-1…), `files:` list, `done_when:` (single verifiable criterion), `success_signal:` (one sentence; N/A for pure refactors), `prerequisites:` (if any).
- `done_when` for user-facing tasks (non-N/A `success_signal`) must exercise the runtime path — a test run, CLI command, or assertion at the integration boundary. Grep-only is insufficient.
- No task may list `docs/specs/` in `files:` — specs are updated by `sync-doc` post-delivery.

**Append to the plan** (after `---` / `# Audit Log` heading on first cycle):
```
## Cycle C1 — Team Lead
Submitting for Core Dev review.
```

---

## Phase 2 — Core Dev + PO: Critique

Spawn Core Dev and PO as **parallel subagents**. Both read the full plan and append their output.

### Core Dev

Read and apply every item in: `.claude/skills/orchestrate-plan/references/core-dev-checklist.md`

**On C2+:** Before raising new issues, verify each prior-cycle blocker is substantively resolved — read the specific plan section cited in the `Change` column, not just the decision table. Unresolved blockers carry forward.

Output under `## Cycle C1 — Core Dev`:
```
**Assessment:** approve | revise
**Blocking:** CD-M-1, CD-M-2  (or "none")
**Summary:** <1–3 sentences>

**Major issues:**
- **CD-M-1** [TASK-N or Section]: <what is wrong>. Recommendation: <concrete change>

**Minor issues:**
- **CD-m-1** [TASK-N or Section]: <issue>. Recommendation: <tweak>
```

### PO

Read and apply every item in: `.claude/skills/orchestrate-plan/references/po-checklist.md`

Does not re-raise implementation issues already flagged by Core Dev.

Output under `## Cycle C1 — PO`:
```
**Assessment:** approve | revise
**Blocking:** PO-M-1  (or "none")
**Summary:** <1–3 sentences>

**Major issues:**
- **PO-M-1** [Section]: <scope/value/first-principles concern>. Recommendation: <change>

**Minor issues:**
- **PO-m-1** [Section]: <minor concern>. Recommendation: <tweak>
```

---

## Phase 3 — TL: Decisions & Plan Update

Process every issue. Decide: **adopt**, **modify**, or **reject** with rationale.

Append to the plan:
```
## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1   | adopt    | <rationale> | <specific location + what changed> |
| PO-M-1   | reject   | <rationale> | — |
```

`Change` must name the specific task or section and what was added or removed. "Updated plan" is not acceptable. `reject` entries use `—`.

Update the plan applying all adopted and modified changes.

---

## Stop Conditions

Stop when **both** subagents return `Blocking: none`. If both return `Blocking: none` on C1, stop immediately — no C2 needed.

**Iteration cap:** After 3 cycles without convergence:
```
## Escalation — Cycle C3 limit reached
Unresolved blocking: <list>
Human decision required.
```

When stopping normally:
1. Append to the plan:
```
## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev <slug>`
```
2. Strip the Audit Log — remove `---` and everything from `# Audit Log` to (but not including) `## Final`. Leave only plan content and the Final section.
