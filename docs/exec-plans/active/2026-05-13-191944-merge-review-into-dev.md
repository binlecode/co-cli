# merge-review-into-dev

## Problem
The `orchestrate-dev` and `review-impl` skills overlap in several areas (lint, done_when verification, doc sync, convention checklist). The question is whether `review-impl` should be merged into `orchestrate-dev` as a final phase, kept separate, or restructured to eliminate redundancy while preserving the adversarial stance that makes review-impl valuable.

## Status
Open decisions resolved — ready for /orchestrate-plan merge-review-into-dev.

## Open Decisions Resolved — 2026-05-13

### D1. Degree of integration (what "merge" means)
- Question: Full collapse, auto-chain, or overlap trim only?
- Recommended: overlap trim only
- Chosen: overlap trim — keep review-impl separate, simplify orchestrate-dev, remove deliver
- Why: full collapse destroys the adversarial cold-read stance that makes review-impl valuable; context contamination from the build phase undermines any review done in the same invocation
- Constraint: the adversarial subagents in review-impl must start cold — they cannot be spawned from the same orchestration context that just built the code

### D2. Should deliver route through review-impl?
- Question: Does deliver go through review-impl before shipping?
- Recommended: no — deliver exists to avoid the review gate
- Chosen: moot — deliver is removed entirely (see D3)

### D3. Deliver skill fate
- Question: Keep deliver as-is, route through review-impl, or remove?
- Recommended: keep as-is
- Chosen: remove completely
- Why: for atomic/simple changes, Claude Code's built-in plan flow is sufficient — deliver added ceremony without earning it; its quality checklist duplicated orchestrate-dev and its "no review gate" stance made it a weaker path for anything non-trivial

### D4. Is orchestrate-dev still needed?
- Question: With today's models, is the TL+Dev subagent pattern still earning its keep?
- Recommended: yes for multi-task parallel plans; borderline for short sequential plans
- Chosen: stays, but simplified
- Why: genuine throughput benefit for plans with independent parallel workstreams; the formal task tracking (done_when, delivery summary) is still valuable; simplification removes the ceremony that was duplicating review-impl's job

### D5. New standalone code-quality skill (originally "D5 / code-hygiene")
- Question: Create a new path-based code quality skill separate from review-impl?
- Recommended: drop it — extend review-impl to path mode instead
- Chosen: dropped
- Why: a dual-mode review-impl (slug OR path) covers the use case with one skill to maintain; a separate skill would duplicate the convention checklist and auto-fix logic

### D6. review-impl becomes dual-mode
- Question: How does review-impl serve the "standing post-impl checklist" use case without a plan?
- Chosen: /review-impl accepts a slug (plan-bound, full 9-phase) OR a path (code quality only)
- Path mode runs: convention checklist (naming, visibility, dead code, API shape, modular structure, anti-patterns), hybrid auto-fix/ask, scoped tests, final lint re-scan
- Path mode skips: spec fidelity, done_when, behavioral verification, full test suite, doc sync
- Auto-fix tier: mechanical (dead code, stale imports) auto-fixed; functional/logical/uncertain — ask before applying; architectural — report only

### D7. test-hygiene renamed
- Question: test-hygiene is noun/state-based; rename to action verb
- Chosen: clean-tests
- Constraint: same rename applies to any future hygiene-suffixed skills — use action verbs throughout

### D8. review-impl path mode — test depth
- Question: Full test suite, scoped tests, or no tests in path mode?
- Recommended: scoped tests
- Chosen: scoped tests only — pytest on files under the reviewed path after fixes
- Why: proportional to scope; full suite stays exclusive to slug mode (where the full delivery must be verified)

### D9. CLAUDE.md workflow after deliver removal
- Question: How to document the gap left by removing deliver?
- Chosen: one line added to the workflow section — "For atomic/single-file changes, use Claude Code's built-in plan flow directly — no skill needed."
- The binary workflow: atomic → built-in plan; multi-task → orchestrate-plan → orchestrate-dev → review-impl <slug> → ship; standing code check → review-impl <path>

---

### Resolved from codebase (no interview)
- D0. Overlaps identified: lint (3×), done_when (2×), doc sync (identical prose copied), convention checklist (3 layers) — established by reading orchestrate-dev/SKILL.md and review-impl/SKILL.md directly

### Deferred
_(none)_

---
Summary: 8 open → 8 resolved · 0 deferred · 1 from codebase

## Deliverables

1. **review-impl**: no mode change — stays plan-bound, context-driven; add to Phase 2C convention checklist: naming (per `agent_docs/code-conventions.md`), visibility boundaries (`_prefix` convention), API surface/shape (parameter order, return types, signature width), software modular structure (cohesion/coupling, code in wrong module), code anti-patterns (global state, speculative abstractions); add same items to Phase 7 final re-scan; remove Phase 6 doc sync (dev cycle owns it)
2. **orchestrate-dev**: trim Step 4 self-review to lint-fix only (`scripts/quality-gate.sh lint --fix`) — convention checklist moves to review-impl; keep Phase 3 doc sync (mandatory, part of dev cycle)
3. **deliver**: delete skill entirely
4. **clean-tests**: rename from test-hygiene (SKILL.md frontmatter, name, description + any references in CLAUDE.md and agent_docs/)
5. **CLAUDE.md**: remove deliver entry from skill reference block; add one line — "For atomic/single-file changes, use Claude Code's built-in plan flow directly — no skill needed."; clarify doc sync ownership (dev cycle, not review)
