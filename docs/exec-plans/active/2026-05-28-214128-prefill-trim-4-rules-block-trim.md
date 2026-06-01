# rules-block-trim

> **Child 4 of** `2026-05-28-141854-prefill-trim.md`. Closes the family's largest blind
> spot: the rules block is **35% of the prefill (~4,863 tok)** and the family so far trims only
> ~175 tok of it (child 2's rule↔docstring dedup). This plan conservatively trims the three fat,
> non-safety, non-child-2 rule files, eval-gated for adherence.

## Context

The prefill breakdown (parent §"Measured ground truth"): tool schemas 46%, **rules `01`–`07` 35%
(~4,863 tok)**, mindsets 8%, rest <10%. The family pours its effort into tool schemas (children 2,
3, and child 1b `2026-05-28-164327-deferred-tool-stubs.md`) and the mindset block is owned by a
separate ablation plan (`2026-05-27-165621-mindset-stance-selection.md`) — leaving the rules block,
the *second-largest* component, almost untouched.

### Per-file sizes (verified, chars → ~tok @ 4 chars/tok)

| File | chars | ~tok | Disposition |
|---|---:|---:|---|
| `04_tool_protocol.md` | 4,711 | ~1,180 | **owned by child 2** (dedup) — out of scope here |
| `07_memory_protocol.md` | 3,820 | ~955 | **target** |
| `06_skill_protocol.md` | 3,197 | ~800 | **target** (unblocked — see below) |
| `03_reasoning.md` | 3,094 | ~775 | **owned by child 2** (stale-data cue) — out of scope |
| `05_workflow.md` | 2,567 | ~640 | **target** |
| `02_safety.md` | 1,622 | ~405 | out (load-bearing safety) |
| `01_identity.md` | 538 | ~135 | out (too small to be worth the risk) |

Targets `05`+`06`+`07` ≈ **2,395 tok**; a conservative dedup/inert-prose trim of ~15–30% yields
**~−400–700 tok** off the prefill without touching any load-bearing injunction.

### Current-state facts (verified against source)

- Composition seam: `build_rules_block()` (`co_cli/context/assembly.py:66`) concatenates all rule
  files; `_collect_rule_files()` enforces **unique, contiguous-from-01** numbering
  (`assembly.py:56-61`). → No renumbering, no whole-file deletion (would break contiguity). **Trim
  within files only.**
- `06` is **trimmable regardless of `skill_manage` visibility**. Child 1b's per-tool deferred
  stubs (`build_deferred_tool_awareness_prompt`, `co_cli/tools/deferred_prompt.py`, wired at
  `orchestrator.py:62`) now seed a name+one-liner for every DEFERRED tool from live `tool_index`.
  So the discovery anchor is the auto-generated stub, **not** rule `06`'s literal
  `skill_manage(action=…)` mentions — those are explanatory, not the anchor. `skill_manage` is
  ALWAYS by Standing Decision (child 1b — re-test failed 0/3; `skills.py:306`), so its mentions in
  `06` are doubly non-load-bearing for discovery. Trimming `06`'s redundant manifest-scan
  repetition is therefore safe.
- The mindset plan established the eval seam for prompt-real-estate changes: `build_rules_block` is
  the one production seam, and `evals/eval_mindset_selection.py` composes arms through it.

## Failure Modes (the risk this plan must not trigger)

- **FM-1 — cut a load-bearing injunction.** Removing an explicit routing/when-to-use cue (e.g.
  "`skill_view` before edit", "explicit saves are synchronous", kind-selection) regresses adherence
  on the 3B-active model. The parent's principle holds: *this model needs more explicit guidance,
  not less* — so the trim is dedup/inert-prose only, never injunction removal.
- **FM-2 — silent drift.** A prose cut that subtly changes behavior won't be caught by unit tests
  (rule text has no unit assertions today) — only by behavioral evals. Hence the eval gate.

## Problem & Outcome

**Problem.** The rules block is 35% of every cold prefill and the trim family barely touches it.
Within `05`/`06`/`07` there is genuine redundancy — the *same* injunction repeated 2–3× and
reference-enumerations that duplicate what the model already sees in tool results.

**Failure cost.** Left alone, the family lands ~11–16% prefill reduction against the parent's
16–20% target and leaves its second-biggest lever unpulled — every cold turn keeps paying for
duplicated guidance that also dilutes signal for a 3B-active attention budget.

**Outcome.** Conservatively trim `05`/`06`/`07` — collapse repeated injunctions to one canonical
statement, cut reference-enumerations that duplicate tool-result content, keep **every** load-bearing
cue — gated on no adherence regression in the domain evals, and locked with a rules-block budget
test.

### Concrete trim candidates (grounding, not exhaustive)

- **`06` — repetition.** "scan the `<available_skills>` manifest before any multi-step task" is
  stated **3×** (intro para, manifest para, `## Discovery`). Collapse to one. `## Background review`
  is a long mechanism explanation; the only behavioral cue is "don't double up" — compress.
- **`05` — repetition.** "if attempts don't progress, surface the blocker, don't loop" appears in
  both `## Execution` and `## Completeness`. State once. `## When NOT to over-plan` overlaps
  `## Execution`'s "only stop at a plan when asked".
- **`07` — reference-enumeration.** `## Curation` "Dedup awareness" enumerates all four
  `SaveResult.action` outcomes (`saved/skipped/merged/appended`) — the model reads `action` in the
  return. Keep "dedups; don't retry rephrasings", cut the 4-way list. `## Recall`'s "Triggers:" line
  restates the section's opening triggers.

## Scope

In: conservative prose trim of `05_workflow.md`, `06_skill_protocol.md`, `07_memory_protocol.md`; an
adherence regression gate; a rules-block budget pytest. Out: `02_safety` (load-bearing), `03`/`04`
(owned by child 2), `01` (too small), the mindset block (separate plan), any tool-schema work,
renumbering or whole-file removal.

## Behavioral Constraints

- **Trim = dedup + inert-prose only.** Collapse repeated injunctions to one canonical home; cut
  reference-enumerations duplicating tool-result content and pure mechanism-pedagogy. **Never**
  remove an explicit routing/when-to-use cue or a safety/correctness injunction.
- **Load-bearing injunctions that MUST survive** (grep-checked in TASK-2): `06` — `skill_view`
  before edit/patch, "skill body is your procedure, not reference", drift→`patch`/`edit`
  immediately, create bar (3+ steps / reusable), confirm before create-on-behalf; `07` — recall
  before answering, `memory_view` not `file_read` for bodies, explicit saves synchronous (not
  dream), declarative-not-imperative phrasing, the four `kind` definitions, replace-on-correction,
  skills-not-memory for procedures; `05` — decompose-then-execute, completeness before done,
  `todo_read` when a list is active, surface-blocker-don't-loop (kept once).
- **Contiguous-from-01 invariant.** `assembly.py:56-61` must still pass — no renumbering, no
  file deletion.
- **Eval-gated.** Adherence (skill protocol, memory protocol, workflow) must not regress vs the
  TASK-1 baseline.
- **Coordinate with child 2 — the cross-file blocker-loop cue.** `03`/`04` are child 2's; do not
  touch them. One specific overlap: the "multiple attempts not progressing = blocked, surface it,
  retrying is a loop" cue lives in `04`'s `## Strategy` ("Track convergence" paragraph) / `## Error recovery`
  (child 2's file) AND `05`'s `## Execution` / `## Completeness` (this plan). This plan dedups it
  *within* `05` (keep once), but a cross-file canonical home must be picked so it isn't left stated
  in both `04` and `05`. **Decision needed:** keep the goal-level convergence cue in `05` (workflow
  altitude) and have child 2 drop the near-identical "Track convergence" paragraph from `04`'s
  `## Strategy`, while `04 ## Error recovery` retains only the tool-call-level "don't repeat the
  exact failed call" retry guidance (a distinct altitude). Flag at
  Gate 1 so both children agree before either lands.
- **Sequence AFTER `deferred-discovery-diagnostic`** (`2026-05-28-233704-deferred-discovery-diagnostic.md`,
  standalone — not a family child). That spike probes the skill-vs-tool namespace confusion rooted
  partly in rule `06`'s text. Read its TASK-3 verdict **before** trimming `06`: if it recommends
  reinforcing rule `06`'s tool-vs-skill distinction, this plan must not thin that signal. The
  `skill_manage` injunctions are already on this plan's must-survive list, so this is "read the
  verdict first," not a hard block.
- **Coworker-maintained assets** (`souls/`, `_profiles/`, `evals/judges/`, `memories/`) untouched.

## High-Level Design

Edit three markdown rule files in place. For each: identify repeated injunctions (keep one canonical
statement, delete the echoes) and reference-enumerations that duplicate tool-result content (cut to
the behavioral cue). Preserve section headings and the load-bearing set above. No code change beyond
the new budget test; `build_rules_block` already reads whatever the files contain.

## Tasks

### TASK-1 — adherence baseline

**Files:** none (measurement only; produces a recorded baseline).

**done_when:** `evals/eval_skills.py`, `evals/eval_memory.py`, and `evals/eval_mindset_selection.py`
run to completion on the **current** rules and their pass/verdict + scores are recorded (in the
Delivery Summary) as the regression reference.

**success_signal:** A concrete before-state exists to compare the trim against.

**prerequisites:** none.

### TASK-2 — conservative trim of 05 / 06 / 07

**Files:** `co_cli/context/rules/05_workflow.md`, `co_cli/context/rules/06_skill_protocol.md`,
`co_cli/context/rules/07_memory_protocol.md`.

**done_when:**
- Combined size of the three files drops **≥ 1,500 chars** (~−375 tok) from the 9,584-char baseline.
- `grep` confirms every load-bearing injunction (Behavioral Constraints list) still present — a
  scripted grep of the must-survive cues returns all hits.
- `uv run pytest tests/test_flow_prompt_assembly.py -x` passes (contiguity + assembly intact).

**success_signal:** The rules read once-not-thrice with no behavioral cue lost.

**prerequisites:** TASK-1.

### TASK-3 — eval-gated adherence verification

**Files:** none (runs the gate; records result).

**done_when:** Re-run the three TASK-1 evals on the trimmed rules; **no behavioral regression** vs
the TASK-1 baseline (skill discovery/use/drift/create, memory recall/save/kind/curation, workflow
decompose-execute-complete all still pass). Record the final char/tok reduction. If any domain
regresses, restore the cut cue in that file and re-run.

**success_signal:** Adherence held; reduction realized.

**prerequisites:** TASK-2.

### TASK-4 — rules-block budget guard

**Files:** `tests/test_flow_rules_block_budget.py` (NEW).

**done_when:** `uv run pytest tests/test_flow_rules_block_budget.py -x` passes — asserts
`len(build_rules_block())` ≤ measured-post-trim + ~400-char headroom, and that the contiguous-from-01
set is intact. Mirrors child 3's schema-budget guard.

**success_signal:** A future verbose rule addition fails CI.

**prerequisites:** TASK-3 (ceiling set from the measured post-trim number).

## Testing

- `scripts/quality-gate.sh full` (lint + full pytest).
- `tests/test_flow_prompt_assembly.py` + new `tests/test_flow_rules_block_budget.py`.
- `evals/eval_skills.py`, `evals/eval_memory.py`, `evals/eval_mindset_selection.py` — the adherence
  gate (UAT diagnostics; stochastic on a 3B-active model, so compare against the TASK-1 baseline,
  not an absolute bar).
- **`/sync-doc` follow-up:** if any spec quotes the trimmed rule prose, reconcile post-delivery.

## Open Questions

- **Eval sensitivity.** The domain evals are stochastic UAT diagnostics — can they reliably catch a
  subtle adherence regression from a prose cut? Mitigation: trim only dedup/reference content, keep
  all injunctions; if signal is noisy, run each domain eval ≥2× and compare.
- **Reduction target realism.** ~−375–700 tok is modest per-file but it closes the 35% blind spot
  and de-duplicates signal for the 3B model (adherence benefit independent of token count).
- **Sequence vs child 2.** Independent (disjoint files), but if child 2 reshapes `04`'s cross-tool
  framing, re-check `05`/`06` references to it before this ships.

## Next step

Gate 1 — PO + TL review (right problem? correct scope?). Optionally run `/orchestrate-plan
rules-block-trim` first for Core Dev + PO critique; otherwise approve and run
`/orchestrate-dev rules-block-trim`.
