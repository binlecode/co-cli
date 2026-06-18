# Orchestrate skills: close overdesign/drift gaps

## Context

A review of the last ~50 ships shows release work is dominated by subtraction/cleanup that re-litigates earlier overdesign: 4 `rules-conformance-cleanup` plans in ~4 weeks, a wall of `drop-*` (capability-api, reported-realtime-trigger, segment-term, eval-markdown-reports, web-research, ToolInfo write-only fields, dead memory settings, config-surface), and multiple `rename-*` plans. This matches `project_architecture_erosion_tension` (cleanup ≈42% of commits, accelerating).

The `.claude/skills/orchestrate-plan/SKILL.md` and `.claude/skills/orchestrate-dev/SKILL.md` workflows structurally permit this. Cross-checking the two skills against `.agent_docs/{review,testing,spec-conventions,code-conventions,tools}.md` surfaced five concrete misalignments and two overdesign points in the skills themselves.

Build-time scope: these are workflow tooling under `.claude/skills/`, not shipped runtime (`co_cli/`) — no version bump, no spec sync.

## Problem & Outcome

The skills' overdesign defenses (PO/Core Dev critique, subtraction rules, dead-code cleanup, full-suite verification for renames) are either discarded, missing from the spawn contract, or deferred to a later human-gated step. The result is that drift ships and is removed later as a separate cleanup plan class.

**Failure cost:** without these edits, the cleanup-commit treadmill continues — overdesign decisions are made invisibly at plan time (no audit trail), one-sided members and dead code pass dev review, and rename/drop plans are marked DONE on scoped tests that cannot catch cross-file ripple. Each is a future `rules-conformance-cleanup` plan.

**Outcome:** the two skills retain the rejection ledger, enforce subtraction/dead-code checks inline, and verify renames/drops against the full suite — moving overdesign prevention from after-the-fact audit to plan/dev time.

## Scope

In scope — edits to exactly two files:
- `.claude/skills/orchestrate-plan/SKILL.md`
- `.claude/skills/orchestrate-dev/SKILL.md`

Out of scope: the PO/Core Dev reference checklists (`references/*.md`) unless an edit below requires a pointer there; `/review-impl`, `/audit-conformance`, `/ship` skills; any `co_cli/` source; any `docs/specs/`.

## Behavioral Constraints

- No new ceremony that increases planning cost for small plans (the skills are already heavy relative to actual use — OD1/OD2). Net change should reduce or hold workflow weight, not add.
- Edits are prose/workflow-rule changes to skill markdown; they alter agent workflow behavior, not runtime code.
- Keep each skill's existing section structure and voice; surgical insertions/edits only.

## High-Level Design

Five gap fixes (G1–G5 from the review) plus two overdesign trims (OD1–OD2), mapped to tasks:

- **G1 (audit trail):** replace `orchestrate-plan` Phase 3 / Stop-Conditions "Strip the Audit Log" step with "retain a condensed Decisions ledger" — keep the adopt/modify/**reject** rows (the reject rows are the overdesign-avoidance record), drop only the verbose per-cycle critique bodies.
- **G2 (subtraction gate):** add to `orchestrate-dev` Step 4 self-review a concise pointer to `review.md`'s "Clarity by Subtraction" rules (one-sided members, minimum abstraction) — a pointer, not a per-field grep ritual, so the self-review stays light. (Scoped to `orchestrate-dev/SKILL.md` only; no `references/*.md` edit.)
- **G3 (dead-code in contract):** add `review.md`'s subagent dead-code-cleanup rule (line 14: "each subagent cleans up dead code before returning") to the `orchestrate-dev` Dev spawn contract block — a one-line pointer. The contract currently says "Apply Engineering Rules from CLAUDE.md in full," but the subagent dead-code rule lives in `review.md`, not CLAUDE.md's Engineering Rules, so it is not currently in the contract.
- **G4 (rename verification):** for rename/drop tasks, require full-suite or repo-wide stale-ref grep in `done_when`, aligning `orchestrate-plan` line ~32 with `review.md` line 19.
- **G5/OD2 (small-plan path):** add a single-independent-task solo path to `orchestrate-dev` (skip team announcement + topo-sort).
- **OD1:** reinforcement (not a gap) — surface the carve-out CLAUDE.md already states ("For atomic/single-file changes, use Claude Code's built-in plan flow directly — no skill needed") inside `orchestrate-plan` so the heavy path isn't the skill's implicit default. Point to the canonical CLAUDE.md line; do not coin a divergent threshold.

## Tasks

### ✓ DONE TASK-1 — orchestrate-plan: retain Decisions ledger instead of stripping (G1)
- `files:` `.claude/skills/orchestrate-plan/SKILL.md`
- `done_when:` The "Stop Conditions" final step no longer instructs deleting the decision table; it instructs retaining a condensed Decisions ledger (adopt/modify/reject + rationale) and removing only verbose critique bodies. `grep -n "Strip the Audit Log" SKILL.md` returns nothing AND `grep -n "Decisions ledger" SKILL.md` returns a match (pinned to the exact inserted string).
- `success_signal:` A plan produced by the skill keeps the rejection rationale rows in its archived form.
- `prerequisites:` none

### ✓ DONE TASK-2 — orchestrate-plan: align rename/drop done_when with full-suite rule (G4)
- `files:` `.claude/skills/orchestrate-plan/SKILL.md`
- `done_when:` The `done_when` authoring rule (the N/A-for-refactors clause at SKILL.md line ~31) is amended so rename/drop/refactor tasks require a repo-wide stale-reference grep AND the full suite, citing `review.md` line 19's "grep finds zero stale references AND tests pass". `grep -n "repo-wide stale-reference grep" SKILL.md` returns a match (pinned to the exact inserted string).
- `success_signal:` A rename task's `done_when` can no longer be grep-only.
- `prerequisites:` none

### ✓ DONE TASK-3 — orchestrate-plan: surface built-in-flow carve-out for small/atomic plans (OD1, reinforcement)
- `files:` `.claude/skills/orchestrate-plan/SKILL.md`
- `done_when:` Phase 1 (or a new short preamble) states that atomic/single-file changes should use Claude Code's built-in plan flow rather than this skill, pointing to the canonical CLAUDE.md carve-out ("For atomic/single-file changes, use Claude Code's built-in plan flow directly — no skill needed") without coining a divergent threshold. `grep -n "built-in plan flow" SKILL.md` matches.
- `success_signal:` N/A
- `prerequisites:` none
- Note: this reinforces an existing CLAUDE.md rule for skill-local visibility; it is not closing a gap.

### ✓ DONE TASK-4 — orchestrate-dev: add dead-code rule + subtraction pointer (G2, G3)
- `files:` `.claude/skills/orchestrate-dev/SKILL.md`
- `done_when:` (a) The Dev spawn-contract block contains a "clean up dead code before reporting" line (citing `review.md`); (b) Step 4 — Self-review contains a one-line pointer to `review.md`'s "Clarity by Subtraction" rules (one-sided members, minimum abstraction) — a pointer, not a per-field grep procedure. Both strings present via `grep -n`.
- `success_signal:` A Dev subagent introducing a write-only field is reminded (via the review.md pointer) to catch it before reporting.
- `prerequisites:` none
- Note: `done_when` is grep-only because the artifact is a workflow markdown rule with no runtime integration boundary to exercise — no pytest path exists. This is the accepted verification for skill-md edits, not a missing behavioral assertion (applies to all tasks in this plan).

### ✓ DONE TASK-5 — orchestrate-dev: single-task solo path (G5, OD2)
- `files:` `.claude/skills/orchestrate-dev/SKILL.md`
- `done_when:` Phase 1 contains a branch: when the plan has a single independent task (or a trivial chain), skip the Team announcement block and topological-sort step and run the solo path. `grep -nE "solo|single independent task" SKILL.md` matches.
- `success_signal:` A one-task plan no longer requires a Team announcement.
- `prerequisites:` none

## Testing

These are skill-markdown workflow edits — no pytest target. Verification is per-task `done_when` greps against the edited files, plus one read-through of each full skill to confirm the inserted rules cohere with surrounding sections and introduce no contradiction. Two specific coherence checks:
- TASK-1's ledger retention must not contradict the "Final — Team Lead" section.
- TASK-5's solo path must flow cleanly into Phase 3 — a single-task plan skipping the Team-announcement block and topo-sort must still reach Phase 3 step 1 integration without contradicting Phase 2's parallel-spawn / "collect all Dev results" prose.

No `co_cli/` code changes, so the suite is unaffected; full suite is review-impl/ship's gate per workflow.

## Open Questions

- TASK-1: keep the condensed ledger inside the plan body under `## Final`, or as a collapsed `## Decisions` section above it? (Default: a short `## Decisions` table retained above `## Final`; verbose cycle bodies removed.)

## Resolved

- TASK-4 checklist mirror: **No — out of scope, not deferred.** The one-sided-member / subtraction pointer stays in `orchestrate-dev/SKILL.md` Step 4 only. It will NOT be mirrored into `references/po-checklist.md` or `core-dev-checklist.md` — mirroring is the scope-creep vector this plan exists to avoid. Scope already excludes `references/*.md`.

## Delivery Summary — 2026-06-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `Strip the Audit Log` gone AND `Decisions ledger` present | ✓ pass |
| TASK-2 | `repo-wide stale-reference grep` present | ✓ pass |
| TASK-3 | `built-in plan flow` present | ✓ pass |
| TASK-4 | dead-code line in contract + Clarity-by-Subtraction pointer in Step 4 | ✓ pass |
| TASK-5 | solo/single-independent-task branch in Phase 1 | ✓ pass |

**Tests:** N/A — edits are `.claude/skills/*.md` workflow markdown; no pytest target, no `co_cli/` source touched. Verification = per-task `done_when` greps (all matched) + read-through coherence checks.
**Doc Sync:** N/A — build-time skill tooling, no shared module / public API / schema change, no `docs/specs/` impact (per plan Scope).

**Coherence checks (Testing section):**
- TASK-1 ledger: new Stop-Conditions step preserves `## Final` and places `## Decisions` above it — no contradiction with the Final section.
- TASK-5 solo path: routes "TL takes all tasks → Phase 2 → Phase 3"; Phase 2's "collect all Dev results" is vacuous with zero Dev subagents — flows cleanly into Phase 3.

**Overall: DELIVERED**
All five gap fixes applied to the two skill files; every `done_when` grep matched and both coherence checks hold.

## Implementation Review — 2026-06-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `Strip the Audit Log` gone AND `Decisions ledger` present | ✓ pass | orchestrate-plan/SKILL.md:132 — Stop-Conditions step 2 now condenses to a `## Decisions` ledger; "Strip the Audit Log" removed (grep count 0), "Decisions ledger" present (grep count 1) |
| TASK-2 | `repo-wide stale-reference grep` present | ✓ pass | orchestrate-plan/SKILL.md:35 — new `done_when` bullet for rename/drop/refactor, cites review.md:19; complements (does not contradict) the line-33 user-facing rule |
| TASK-3 | `built-in plan flow` present | ✓ pass | orchestrate-plan/SKILL.md:19 — Phase 1 scope-check blockquote cites CLAUDE.md carve-out, no divergent threshold |
| TASK-4 | dead-code contract line + Clarity-by-Subtraction pointer | ✓ pass | orchestrate-dev/SKILL.md:35 (dead-code, cites review.md:14) + :66 (subtraction pointer; matches review.md:33–37) |
| TASK-5 | solo/single-independent-task branch | ✓ pass | orchestrate-dev/SKILL.md:20 — solo path skips Team announcement + topo-sort, routes TL→Phase 2→Phase 3 |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| TASK-1 ledger instruction self-contradicted: told to keep adopt/modify/reject rows but to delete `## Cycle Cn — *` sections, which include the `## Cycle Cn — Team Lead Decisions` tables those rows live in — could silently destroy the reject rationale (G1's whole point) | orchestrate-plan/SKILL.md:132 | blocking | Reworded: first migrate per-cycle decision rows into the consolidated `## Decisions` table, then delete only the Core Dev / PO critique sections + TL submission stubs. done_when re-verified (grep counts 0/1). |

### Scope
- `git diff --name-only HEAD` confined to the two declared files (orchestrate-plan/SKILL.md, orchestrate-dev/SKILL.md). No extra files. No scope creep.

### Tests
- Skipped — delivery touches zero Python (`.claude/skills/*.md` are Claude Code harness workflow artifacts, not `co_cli/` runtime). Full suite is not a meaningful gate for markdown-only edits and the working tree carries unrelated `co_cli/` WIP. Per plan Testing section. Verification = per-task `done_when` greps (all matched) + coherence read-through.

### Behavioral Verification
- No `co_cli/` user-facing surface changed — skill markdown is consumed by the Claude Code harness, not the `co` runtime. Behavioral verification skipped.

### Overall: PASS
All five gap fixes faithful to spec; one self-contradicting ledger instruction (TASK-1) found and fixed; scope clean; no runtime code touched.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev orchestrate-skills-overdesign-gaps`
