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

> **Scope check first.** For atomic/single-file changes, use Claude Code's built-in plan flow directly — no skill needed (per CLAUDE.md). This skill's full critique loop is for major features, doc restructuring, or refactoring. Do not coin a divergent threshold.

**Before writing:**
1. Read relevant source, tests, and `docs/reference/RESEARCH-<scope>.md` if it exists. Glob `docs/exec-plans/active/*-<slug>.md` — if found, read it and skip already-implemented work.
2. **Current-state check:** scan source and specs for accuracy against the planned scope. If too inconsistent to plan safely: `✗ Current state inconsistent — run /sync-doc first.`
3. **For doc tasks** (restructure or doc+code): run a Code Accuracy Verification pass — read each source file referenced by the target docs and confirm every factual claim. List inaccuracies in Context before proposing changes.
4. **Resolve open questions before listing them.** For each open question, in order:
   a. **Try the codebase first.** If a source-of-truth file answers it, read it and record the answer with `path/file.py:LINE` — do not ask.
   b. **If only the user can answer it** (product intent, preference, risk appetite — not derivable from code), interview one question at a time. No batching. Each question states the decision space in one sentence, a recommended default with a one-line rationale, and one or two alternatives. Move to the next immediately after each answer.
   c. **If the user defers**, record it under `## Open Questions` with any re-raise trigger they gave.
   Only genuinely-deferred items survive into `## Open Questions`; codebase- and user-resolved decisions are settled inline and feed `## High-Level Design`. Do not invent questions beyond what the planned scope requires.
5. **For AI behavioral features** (new agents, personality changes, tool-chain modifications affecting model output): run representative inputs through the current system and annotate observed failure modes. List findings in `## Failure Modes` before writing `## High-Level Design`. Do not write criteria against imagined failure space.
6. **Resolve every implementation decision to one concrete approach — no hedged branches.** A design or task that reads "do X if condition C holds, otherwise Y", "adopt the subclass only if it can own …", or "the dev settles this during dev" is unfinished planning, not a plan — the hedge hides an unmade decision inside what looks like settled design. Check condition C against source *now* and write the single surviving branch. The plan that reaches Gate 1 must carry exactly one concrete approach per task. A genuine branch that truly cannot be collapsed until runtime belongs in `## Open Questions` with a re-raise trigger (rule 4c), never buried in `## High-Level Design` or a task body.
7. **Ground decisions in the actually-installed source, including third-party deps.** Current-state grounding is not limited to this repo. When a decision turns on how a library, SDK, or module behaves internally — subclassing, overriding, private `_attr` coupling, populated state, contract shape — read its installed source under `.venv/lib/python*/site-packages/<pkg>/` and cite `path/file.py:LINE`. Memory of an API is not grounding; assertion from the public surface is not grounding. The decision is unresolved until the implementing source is read. (Planning-time counterpart of `.agent_docs/review.md`'s evidence-based-verdict discipline — same "cite source, never assert from memory" principle, applied to plan decisions instead of review verdicts.)

**Slug suffix (multi-phase work).** If this plan is a **milestone metaplan** spanning multiple independently-shippable phases, suffix the slug `-milestone` (`...-<slug>-milestone.md`) and re-enter `/orchestrate-plan <slug>-phaseN` to detail each phase plan (`-phaseN`). Single-phase work stays bare `<slug>`. Name by tier, singular — never `-milestones`/`-phases`. See `.agent_docs/spec-conventions.md` (Artifact Lifecycle).

**Write `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`** with sections:

**Context, Problem & Outcome, Scope, Behavioral Constraints, High-Level Design, Tasks, Testing, Open Questions**

- `Problem & Outcome` must include a `Failure cost:` line — what silently breaks without this fix.
- Each task must have: stable ID (TASK-1…), `files:` list, `done_when:` (single verifiable criterion), `success_signal:` (one sentence; N/A for pure refactors), `prerequisites:` (if any).
- `done_when` for user-facing tasks (non-N/A `success_signal`) must exercise the runtime path — a test run, CLI command, or assertion at the integration boundary. Grep-only is insufficient.
- `done_when` for rename / drop / refactor tasks must require a **repo-wide stale-reference grep** AND the full test suite, per `review.md` line 19 ("Done only when grep finds zero stale references AND tests pass"). Scoped tests cannot catch cross-file ripple; grep-only cannot prove nothing broke.
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
2. Condense to a Decisions ledger — collapse the Audit Log (`---` and everything from `# Audit Log` to, but not including, `## Final`) into a single `## Decisions` table above `## Final`. First migrate every row from the per-cycle `## Cycle Cn — Team Lead Decisions` tables into that one table (one row per issue: adopt / modify / **reject** + rationale — the reject rows are the overdesign-avoidance record and must survive). Then delete the verbose critique sections (`## Cycle Cn — Core Dev`, `## Cycle Cn — PO`, and the `## Cycle Cn — Team Lead` submission stubs). Leave plan content, the consolidated `## Decisions` table, and the Final section.
