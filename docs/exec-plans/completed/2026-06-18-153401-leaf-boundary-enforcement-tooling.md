# Leaf-Boundary Enforcement — workflow-skill + rule tooling

**Slug:** `leaf-boundary-enforcement-tooling` · **Created:** 2026-06-18 15:34:01
**Layer:** build-time (`.claude/skills/`) — NOT runtime agent behavior. All deliverables are skill md edits only; no new committed source files.

## Context

The `/audit-conformance` run on 2026-06-18 found a **recurring violation class**:
agent-loop / prompt-assembly concerns accreting into the `context/` leaf package and
reaching into `tools`/`session` (`docs/exec-plans/active/2026-06-18-152625-rules-conformance-cleanup.md`).
Instances: the `skill_manifest → skills/` move (just shipped) and the turn-loop
`context/orchestrate.py` (in that cleanup plan). The rule that's being violated —
`review.md:40` "Module home = owning domain… domain logic under a generic `context/`
layer is a modular-structure finding" — **already exists and is correct**. The
spec's stale/contradictory wording was the standing license, now fixed in
`01-system.md` (separate `/sync-doc`).

So this is not a missing-rule problem. It is an **enforcement-cadence** problem: the
rule bites only at the periodic whole-tree audit (`review.md:29` two-scopes model),
months after the violation lands. Per `feedback_rule_enforcement_tiers`, a rule
re-litigated ≥3× must graduate its enforcement — but `review.md:25` and
`project_architecture_erosion_tension` forbid structural/fitness-function tests. The
only legitimate graduations are: (1) make the audit's detector cheap + repeatable,
and (2) add a per-PR *judgment prompt* (not a frozen allowlist test) so the next
instance is caught at the diff, not the audit.

**Hard constraint:** nothing here may become a CI-gating structural test. The
import-edge script is a manual audit *aid* producing an inventory for human
judgment — not a test in `tests/`, not a build gate. State this explicitly so review
does not reject it as a fitness function.

## Tasks

### ✓ DONE Task 1 — Embed the import-edge detector verbatim in the `audit-conformance` skill

`audit-conformance` currently instructs re-authoring `tmp/import_edges.py` (AST
builder tagging each cross-package edge `MODULE`/`TYPE_CHECKING`/`LOCAL` + `PRIVATE`)
from scratch every run. Embed the proven version as a verbatim code block in the
`audit-conformance` skill Pass 0 so the agent pastes it into `tmp/` and runs it
without re-deriving. No committed source file — all deliverables stay in `.claude/skills/`.

- **done_when:** the `audit-conformance` skill Pass 0 contains the full script as a
  code block (with a comment that it is a manual audit aid, never a CI gate or
  `tests/` member); the agent runs it via `uv run python tmp/import_edges.py`.
- No behavior change to shipped agent.

### ✓ DONE Task 2 — `review-impl`: per-diff leaf-boundary judgment prompt

Add a review-time check (judgment prompt, NOT a test) to the `review-impl` skill:
when a diff adds an import edge **from** a leaf package (`context`/`tools`/`memory`/
`session`) **into** `tools`/`agent`/`bootstrap`, or places loop/prompt-assembly logic
inside a leaf, flag it against `review.md:40` and ask whether the module is
correctly homed. This catches *introduction* at the PR — the gap diff-scoped review
has by design for *accretion* but need not have for *new* edges.

- **done_when:** `review-impl` skill text includes the leaf-boundary diff check with
  the `review.md:40` citation; framed as judgment, explicitly not a fitness test.

### ✓ DONE Task 3 — `orchestrate-plan`: layer-home prompt at planning time

Add one doctrine line to `orchestrate-plan`: when a plan proposes new code in a leaf
package, the TL/Core-Dev review asks "does this concern belong in this layer, or is
it a loop/agent concern that belongs at the `agent` layer?" Cheapest upstream catch —
stops mis-homing before code is written.

- **done_when:** `orchestrate-plan` skill includes the layer-home question in its
  critique checklist.

## Out of scope / deferred

- The actual orchestrate.py relocation — owned by the sibling
  `rules-conformance-cleanup` plan.
- Any change to `tests/` (forbidden — no structural/fitness tests).
- Re-litigating the `tools → memory/session` blessed exception (settled:
  intentional public surface).

## Verification

Build-time only — no agent runtime change, no pytest impact.
- Task 1: re-read the `audit-conformance` skill; confirm Pass 0 contains the full
  script code block with the audit-aid comment; dry-run by pasting and running it.
- Tasks 2–3: re-read the edited skill files; confirm the new checks are present,
  cite `review.md:40`, and are phrased as judgment prompts. Dry-check: the next
  `/review-impl` on a leaf-crossing diff should surface the flag.

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `audit-conformance` Pass 0 contains full script code block with audit-aid comment; run via `uv run python tmp/import_edges.py` | ✓ pass |
| TASK-2 | `review-impl` skill text includes leaf-boundary diff check with `review.md:40` citation, framed as judgment | ✓ pass |
| TASK-3 | `orchestrate-plan` skill includes the layer-home question in its critique checklist | ✓ pass |

**Tests:** build-time only — no pytest impact (skill `.md` edits, no source changes)
**Doc Sync:** N/A — no shared modules or public API touched

**Overall: DELIVERED**
All three tasks shipped as `.claude/skills/` edits only; no source files, no test impact, no CI gate introduced.

## Implementation Review — 2026-06-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `audit-conformance` Pass 0 contains full script code block with audit-aid comment; run via `uv run python tmp/import_edges.py` | ✓ pass | `audit-conformance/SKILL.md:53` — prose instruction says "manual audit aid — never a CI gate or `tests/` member"; `SKILL.md:56` — script header comment `# tmp/import_edges.py — manual audit aid; NOT a CI gate or tests/ member`; `SKILL.md:126` — `uv run python tmp/import_edges.py > tmp/edges.txt` |
| TASK-2 | `review-impl` skill text includes leaf-boundary diff check with `review.md:40` citation, framed as judgment | ✓ pass | `review-impl/SKILL.md:95-96` — "**Leaf-boundary judgment** (`review.md:40`): … Frame as judgment, not a structural test — the goal is a conscious siting decision, not an allowlist gate." |
| TASK-3 | `orchestrate-plan` skill includes the layer-home question in its critique checklist | ✓ pass | `orchestrate-plan/references/core-dev-checklist.md:29-31` — "## Module layer home (`review.md:40`) … This is a judgment prompt, not a structural test."; wired in `orchestrate-plan/SKILL.md:52` — "Read and apply every item in: `.claude/skills/orchestrate-plan/references/core-dev-checklist.md`" |

### Issues Found & Fixed

No issues found.

### Tests
- Build-time only (skill `.md` edits, no source changes) — pytest not applicable.

### Behavioral Verification
- No user-facing surface changed — skipped per plan's own verification section.

### Overall: PASS
All three skill edits deliver their spec exactly; no source changes, no test impact, no CI gate introduced.
