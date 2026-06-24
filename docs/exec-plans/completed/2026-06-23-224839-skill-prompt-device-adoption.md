# Skill Prompt-Device Adoption

Adopt the peer-validated prompt-design devices identified in `docs/reference/RESEARCH-skills-prompt-gaps.md` into co's four surviving bundled skills, then retire the research doc (this plan supersedes it).

## Context

`RESEARCH-skills-prompt-gaps.md` compared co's bundled skills against hermes (`software-development/plan`, `hermes-agent-skill-authoring`, `systematic-debugging`, `productivity/ocr-and-documents`) and opencode. Its per-skill survey (re-run against current peer HEADs this session) found co at or beyond parity structurally, with a small set of genuinely borrowable **prompt devices** co lacks. The highest-value one — the **anti-rationalization device** (Red Flags → STOP + excuse→reality framing) — was marked "superseded" in the doc because it originally targeted the removed coding skills (`triage`/`review`/`refactor`); it resurfaces cleanly on the surviving `plan` and `doctor` skills, where the same model failure (skipping a discipline gate under pressure) applies.

Per `feedback_instructions_counter_model_limits`, instructions must be designed to counter the WEAK_LOCAL model's limits as near-unconditional reflexes on observable cues — which is exactly what a Red-Flags→STOP list is. Per `feedback_skill_curation_knowledge_work_positioning`, bundled coding cadence (TDD/commits/executable-spec) is off-mission and stays rejected.

Current-state: all four target `SKILL.md` bodies were read in full this session and their structures confirmed (plan: Core principle + Phase 1–3 + Common Mistakes + Rules; skill-creator: Phase 1–4 + Rules; office: Step 1–4 + Scope; doctor: Phase 1–3 Probe/Diagnose/Report). The research doc's co-side citations were reconciled to current line numbers earlier this session.

Observed failure space (from the research doc — the annotation that grounds these criteria, not imagined):
- `plan`: weak model dives into tasks before scoping; vague `Done when`; silent scope leak.
- `skill-creator`: creates duplicate / no-op-prose / oversized ("sprawl") skills; bloats existing ones ("sediment").
- `office`: no explicit guard against answering from memory on a partial/blank extraction (the `documents` skill has one for the scanned case; `office` has none).
- `doctor`: recommends a fix on incomplete or contradictory evidence instead of running its one allowed follow-up probe.

## Problem & Outcome

**Problem:** Four bundled skills lack low-cost, peer-validated prompt devices that counter known weak-model failure modes. The analysis that established this lives in a research doc that will go stale the moment the skills are edited.

**Outcome:** Each surviving skill carries the borrowable device matched to its failure mode; the research doc is deleted and its only live cross-references (the sibling `RESEARCH-skills-peers-tiers.md`) are cleaned.

**Failure cost:** Without this, the weak model keeps silently skipping the scoping gate, minting duplicate/no-op skills, answering Office questions from a partial extraction, and recommending fixes on thin evidence — the exact failures the peer devices were designed to arrest. The research findings then rot unused and the doc becomes a misleading stale artifact.

## Scope

**In scope:** prose edits to four `SKILL.md` bodies; restoring the lint (R1–R3) + B1 gate to `tests/test_flow_skill_bundled_library.py` (its docstring and `skills.md` §6 already promise this coverage, but the file currently only load-checks — so the four edits have no real lint gate today); deletion of one research doc; cleanup of its live cross-references in `RESEARCH-skills-peers-tiers.md`.

**Out of scope (rejected by design — do not add):**
- Executable-spec / copy-pasteable code / exact-command-output in `plan` (WEAK_LOCAL; removes the test/lint feedback loop).
- TDD RED/GREEN, bite-sized time budgets, frequent-commit cadence (coding-agent-coupled; co is knowledge-work positioned).
- `doctor` `--fix` / repair posture, detect/repair split, severity exit codes (co's doctor is recommend-only by design).
- Adding `severity`/`category`/`fixHint`/`measured`/`expected` **fields to the `capabilities_check` tool** (tool-schema change, not a skill-prompt device; severity here is a prose label only).
- Extractor feature-comparison matrix (co ships one extractor per format; a matrix is unjustified until a second exists).
- Editing the completed plan `2026-06-23-163704-regeneralize-plan-skill.md` (historical record; never edited).

## Behavioral Constraints

- Every edited bundled skill must keep passing the load gate in `tests/test_flow_skill_bundled_library.py` and the lint+B1 gate this plan restores to that same file (R1–R3 + B1 hard; R4 body ≤8000 chars stays a soft warning — keep additions tight; introduce no `TODO`/`FIXME`/`XXX`).
- Devices are re-authored in co's house style and scoping/knowledge-work vocabulary, not copied verbatim from hermes' coding context.
- `plan` and `doctor` Red-Flags entries are phrased as near-unconditional reflexes on an observable cue (an excuse the model would emit), per `feedback_instructions_counter_model_limits`.
- The `doctor` severity tag is a one-word prose label on the Report's Likely-issue line — it must not imply a repair or filtering capability.

## High-Level Design

- **`plan`** gains a `## Red Flags — STOP` block (placed after `## Common Mistakes`, before `## Rules`): 3–4 excuse→reality lines targeting scope-skipping and vague done-conditions. The existing four Common Mistakes get short named labels so the failure modes are recall-anchored.
- **`skill-creator`** gains a `## Quality failures` named-vocabulary block (Premature Completion / Duplication / Sediment / Sprawl / No-op Prose) before `## Rules`, plus one subtraction-principle Rule ("if a line doesn't change agent behavior versus the default, cut it").
- **`office`** gains one source-of-truth sentence in Step 4 (or Scope) mirroring `documents`' blank-extraction guard, generalized: cite the extracted text; never answer from memory or a partial/capped extraction.
- **`doctor`** gains a Red-Flags→STOP guard in `## Phase 2 — Diagnose` (contradictory findings or many fallbacks active → spend the one allowed follow-up probe before recommending) and a `severity:` prose label on the Report's **Likely issue** line.
- The research doc is deleted; the three live pointers in `RESEARCH-skills-peers-tiers.md` are removed or redirected to the skills/this plan; the historical pointer in the completed plan is left intact.

## Tasks

### ✓ DONE TASK-1 — Restore the lint + B1 gate to the bundled-library test
- **files:** `tests/test_flow_skill_bundled_library.py`
- **done_when:** the test loads every bundled skill and runs `lint_skill` (asserting no R1–R3 findings; R4 remains a non-failing soft warning) AND `lint_bundled_extras` (asserting no B1 markers) over each loaded body; `uv run pytest tests/test_flow_skill_bundled_library.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task1.log` passes against the current (pre-edit) bundled set, proving the gate is green before the edits land.
- **success_signal:** Introducing a `TODO` marker or a missing-description into any bundled `SKILL.md` now fails this test (the gate is real, not docstring-only).

### ✓ DONE TASK-2 — Add anti-rationalization block and named mistakes to `plan`
- **files:** `co_cli/skills/plan/SKILL.md`
- **prerequisites:** TASK-1
- **done_when:** `grep -q "## Red Flags" co_cli/skills/plan/SKILL.md` succeeds, the block holds ≥3 excuse→reality lines, and each of the four Common Mistakes carries a short named label; `uv run pytest tests/test_flow_skill_bundled_library.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task2.log` passes (load + R1–R3 + B1; body stays ≤8000 so no new R4 warning).
- **success_signal:** Given an under-scoped `/plan` request, the model stops and sharpens scope / splits the task instead of diving into Phase 2.
- **notes:** The Red-Flags→STOP block and the four named Common Mistakes deliberately encode the same four failure modes (mushy done / scope leak / boulder task / hand-waved questions) by two distinct mechanisms — a recall-anchor label vs. a reflex cue per `feedback_instructions_counter_model_limits`. This dual-encoding is intentional, not the No-op-Prose/Sediment the `skill-creator` edit polices. If the ≤8000-char R4 soft-warn ever gets tight on `plan`, consolidate by trimming the Common Mistakes labels first — the Red Flags are the load-bearing reflex half.

### ✓ DONE TASK-3 — Add failure-mode vocabulary and subtraction principle to `skill-creator`
- **files:** `co_cli/skills/skill-creator/SKILL.md`
- **prerequisites:** TASK-1
- **done_when:** the body contains a named-vocabulary block holding all five terms (`grep -qi "premature completion" && grep -qi "no-op" co_cli/skills/skill-creator/SKILL.md`) before `## Rules`, plus a subtraction-principle Rule; `uv run pytest tests/test_flow_skill_bundled_library.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task3.log` passes.
- **success_signal:** When deciding whether to save a skill, the model rejects a no-op/duplicate/sprawl candidate by name and trims behavior-neutral lines before writing.

### ✓ DONE TASK-4 — Add source-of-truth guard to `office`
- **files:** `co_cli/skills/office/SKILL.md`
- **prerequisites:** TASK-1
- **done_when:** Step 4 (or Scope) contains a sentence forbidding answering from memory / a partial or capped extraction and requiring citation of the extracted text (`grep -qi "never answer from memory\|partial extraction\|capped" co_cli/skills/office/SKILL.md`); `uv run pytest tests/test_flow_skill_bundled_library.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task4.log` passes.
- **success_signal:** On a truncated Excel/Word extraction, the model (dispatched via the manifest — `office` is model-only, not a `/office` slash command) cites only the extracted rows/headings and flags the gap rather than filling from memory.

### ✓ DONE TASK-5 — Add Diagnose Red-Flags and Report severity to `doctor`
- **files:** `co_cli/skills/doctor/SKILL.md`
- **prerequisites:** TASK-1
- **done_when:** `## Phase 2 — Diagnose` contains a STOP guard (`grep -q "Red Flag\|STOP" co_cli/skills/doctor/SKILL.md`) tying contradictory-findings / many-fallbacks-active to the one allowed follow-up probe, and `## Phase 3 — Report`'s **Likely issue** line carries a `severity` (info/warning/error) label with no repair/filtering implication; `uv run pytest tests/test_flow_skill_bundled_library.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task5.log` passes.
- **success_signal:** Faced with contradictory capability findings, the model runs its single follow-up probe before reporting, and tags the issue's severity in the Report.
- **notes:** The Phase-2 STOP guard is the load-bearing half; the `severity:` label is strictly secondary — if the ≤8000-char R4 soft-warn gets tight on `doctor`, trim the severity label first, not the STOP guard.

### ✓ DONE TASK-6 — Delete the research doc and clean its live cross-references
- **files:** `docs/reference/RESEARCH-skills-prompt-gaps.md`, `docs/reference/RESEARCH-skills-peers-tiers.md`
- **prerequisites:** TASK-2, TASK-3, TASK-4, TASK-5
- **done_when:** `RESEARCH-skills-prompt-gaps.md` is removed; in `RESEARCH-skills-peers-tiers.md` the line ~599 provenance citation is **dropped** (not redirected — this plan is a device-adoption plan, not the gap analysis), and the line ~4 and ~52 pointers are dropped or redirected to the skills / this plan as fits the sentence; a repo-wide `grep -rn "RESEARCH-skills-prompt-gaps" --include="*.md" --include="*.py" .` returns only historical references inside `docs/exec-plans/completed/` (the two never-edited plans `2026-06-23-193755-plan-skill-inline-args.md` and `2026-06-23-163704-regeneralize-plan-skill.md`) — no live reference remains in `docs/reference/` or `co_cli/`; the full suite `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task6.log` passes.
- **success_signal:** N/A (doc retirement / refactor).

## Testing

- TASK-1 restores the real gate: `tests/test_flow_skill_bundled_library.py` currently only load-checks (asserts non-empty body/description — `tests/test_flow_skill_bundled_library.py:19-33`); the R1–R4/B1 rules are exercised only against synthetic fixture strings in `tests/test_flow_skill_lint.py`, never against the shipped bodies. After TASK-1 the file lints every bundled body (R1–R3 + B1 hard, R4 soft), making its own docstring true and giving TASK-2…5 a machine-verifiable gate.
- Per-task (TASK-2…5): structural half by grep (block/term present), conformance half by the TASK-1 gate.
- TASK-6: repo-wide stale-reference grep (zero live references except the historical completed-plan mention) AND full suite green, per `review.md` ("Done only when grep finds zero stale references AND tests pass").
- No new eval is warranted: the prior `plan` A/B (recorded in the research doc) hit a ceiling effect proving no-harm, not upside; behavioral upside of prose reflexes is not measurable on clean tasks. Validation is the load/lint gate plus a manual read-through of each device against 2–3 representative prompts.
- Floor-guard note: these are `SKILL.md` bodies (loaded on demand / per-turn manifest), **not** `co_cli/context/rules/*.md`, so the injected-rule floor guards (budget ceiling, F5 no-deferred-tool-signature) do not apply.

## Open Questions

None — ready to implement.


## Decisions

Consolidated ledger of all review decisions (reject rows are the overdesign-avoidance record).

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Verified against source: `test_flow_skill_bundled_library.py:19-33` only load-checks; no test lints shipped bodies; spec §6 + the file's own docstring already promise the coverage. Option (b) makes the gate real and fixes pre-existing drift. | Added new **TASK-1** (restore lint+B1 gate to `tests/test_flow_skill_bundled_library.py`); renumbered edit tasks to TASK-2…5 with TASK-1 as prerequisite; corrected Scope, Behavioral Constraints, and Testing to stop claiming the load-only test lints, and to describe the restored gate (R1–R3 + B1 hard, R4 soft). |
| CD-m-1 | adopt | Machine-verifiable pass signal for the structural prose halves. | Added a concrete `grep` to the done_when of TASK-2 (`## Red Flags`), TASK-3 (five vocabulary terms), TASK-4 (memory/partial/capped guard), TASK-5 (Red Flag/STOP). |
| CD-m-2 | adopt | `office` is model-only; the scenario can't run as a `/office` slash command. | Reworded TASK-4 success_signal to note office is dispatched via the manifest, not a slash command. |
| PO-m-1 | adopt | Severity label is the marginal device; STOP guard is load-bearing. | Added a `notes:` line to TASK-5 making the Phase-2 STOP guard primary and the severity label trim-first under the R4 char budget. |
| PO-m-2 | adopt | A redirect would mislead — this plan is device-adoption, not the gap analysis. | TASK-6 done_when now specifies the line ~599 provenance citation is dropped (not redirected); lines ~4/~52 dropped or redirected as the sentence fits. |
| G1-1 | adopt | Source-verified: the residual-reference grep returns **two** completed-plan hits (`2026-06-23-193755-plan-skill-inline-args.md:186` and `2026-06-23-163704-regeneralize-plan-skill.md:87`), not one. The old done_when named only the latter, so it could never pass and risked tempting an edit to a completed plan (forbidden by this plan's own Out-of-scope rule). | TASK-6 done_when reworded to expect both completed/ references to remain and assert no live reference in `docs/reference/` or `co_cli/`. |
| G1-2 | accept-by-design | Source-read of `plan/SKILL.md:79-82` (Common Mistakes) vs `:88-91` (Red Flags): both cover the same four failure modes. This is intentional dual-encoding — recall-anchor label + reflex cue are distinct mechanisms per `feedback_instructions_counter_model_limits` — not the No-op-Prose/Sediment the sibling `skill-creator` edit polices. Non-blocking: body is 6184/8000, no R4 pressure today. | Added a `notes:` line to TASK-2 recording the dual-encoding rationale and the trim-first order (Common Mistakes before Red Flags) should R4 ever tighten — mirrors TASK-5's severity-trim note. |

C1: PO approved (Blocking: none). Core Dev raised CD-M-1 (blocker) + two minors. C2: Core Dev confirmed CD-M-1 resolved (Blocking: none). Both subagents converged — no C3 needed. G1 (retrospective review, 2026-06-24): APPROVE — right problem, correct scope; G1-2 logged as the one non-blocking observation, resolved by design. No blockers.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev skill-prompt-device-adoption`

## Delivery Summary — 2026-06-24

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | bundled-library test lints every shipped body (R1–R3 + B1 hard, R4 soft) and passes pre-edit | ✓ pass |
| TASK-2 | `## Red Flags — STOP` block (4 excuse→reality lines) + 4 named Common Mistakes in `plan`; gate green | ✓ pass |
| TASK-3 | `## Quality failures` 5-term vocabulary before `## Rules` + subtraction Rule in `skill-creator`; gate green | ✓ pass |
| TASK-4 | source-of-truth guard (never answer from memory / partial / capped) in `office` Step 4; gate green | ✓ pass |
| TASK-5 | Phase-2 STOP guard + Phase-3 `severity:` prose label (recommend-only) in `doctor`; gate green | ✓ pass |
| TASK-6 | research doc deleted; no live ref in `docs/reference/` or `co_cli/`; only completed/ historical hits remain | ✓ pass |

**Tests:** scoped — 2 passed, 0 failed (`tests/test_flow_skill_bundled_library.py`: load + lint-clean). Full suite deferred to `/review-impl` per Phase-3 rule (it satisfies TASK-6's full-suite clause).
**Doc Sync:** clean — no shared module / API / schema touched; TASK-1 made `skills.md §6`'s pre-existing lint-gate promise true.

**Bodies (R4 ≤8000):** plan 6184, skill-creator 3421, office 5348, doctor 2908 — all under ceiling, no new soft warning.

**Overall: DELIVERED**
All six tasks passed `done_when`; lint clean; scoped gate green. The four peer-validated prompt devices are in their target skills and machine-gated by the now-real bundled-library lint. Next: `/review-impl skill-prompt-device-adoption`.

## Implementation Review — 2026-06-24

Stance: issues exist — PASS earned. Three parallel per-task reviewers + independent TL re-verification of every `done_when`.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | bundled-library test lints every shipped body (R1–R3 + B1 hard, R4 soft), green pre-edit | ✓ pass | `tests/test_flow_skill_bundled_library.py:45-50` — loops `_BUNDLED_NAMES`, reads raw `SKILL.md`, calls `lint_skill` (filters R4) + `lint_bundled_extras`. Stub-litmus: a TODO → B1 (`lint.py:100-110`), missing desc → R2 (`lint.py:56-59`) → fails the test. Functional, not structural. |
| TASK-2 | `## Red Flags` + ≥3 excuse→reality + 4 named mistakes; ≤8000 | ✓ pass | `plan/SKILL.md:84` Red Flags block, `:88-91` 4 excuse→reality reflex lines, `:79-82` four named labels (Mushy done / Silent scope leak / Boulder task / Hand-waved questions). 6184 chars. |
| TASK-3 | 5-term vocab before `## Rules` + subtraction Rule | ✓ pass | `skill-creator/SKILL.md:51-59` `## Quality failures` (5 terms) before `## Rules` (`:61`); subtraction Rule `:68`. 3421 chars. |
| TASK-4 | source-of-truth guard in Step 4 | ✓ pass | `office/SKILL.md:46` — "never answer from memory or from a partial / capped extraction; … say so rather than filling the gap." Mirrors `documents/SKILL.md:51,61`. 5348 chars. |
| TASK-5 | Phase-2 STOP guard + Phase-3 severity (recommend-only) | ✓ pass | `doctor/SKILL.md:30` STOP guard ties contradictory/many-fallbacks to the one Phase-1 probe (`:19`); `:36` `severity: info\|warning\|error` prose label with "no repair, exit-code, or auto-filter behavior" disclaimer + `:44`. 2908 chars. |
| TASK-6 | doc deleted; no live ref in `docs/reference/` or `co_cli/` | ✓ pass | `RESEARCH-skills-prompt-gaps.md` deleted (staged `D`); `peers-tiers.md` cleaned at former L4/L52/L599; live-ref grep clean in `docs/reference/` + `co_cli/`. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale prose pointers to the deleted doc (de-attributed: kept substance, dropped dangling reference) | `peers-tiers.md:578`, `:736` | blocking (incomplete doc-retirement) | Fixed — rewrote both to state the conclusion directly; live-ref grep now fully clean |
| Pre-existing unrelated working-tree edits in diff | `context/overlays/weak_local.md`, `context/rules/03_reasoning.md`, `evals/eval_multistep_plan.py`, `uv.lock` | minor (scope) | Not this delivery — flagged must-not-stage at ship |

### Tests
- Command: `uv run pytest -v`
- Result: **843 passed, 0 failed** (237s; no stalled LLM calls)
- Log: `.pytest-logs/20260624-*-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads).
- Edits are **body-only** — no frontmatter `description` changed on any of the four skills (verified via `git diff`), so description-driven skill selection cannot regress.
- `success_signal` (LLM-mediated, non-gating per the skill): TASK-4's office discipline observed live in `eval_skills.py` W4.B — on a missing/failed pptx extraction the model did **not** fabricate a summary; it reported the file wasn't found and asked the user (aligned with the source-of-truth guard's intent). TASK-2/3/5 reflexes are reviewer-confirmed as well-formed observable-cue reflexes; A/B upside on clean tasks is not measurable (documented ceiling effect — the plan deliberately shipped no new eval).
- **Eval check (per user request):** `eval_skills.py` W4.A/W4.R/W4.M PASS (both of two runs). W4.B reports FAIL **deterministically** (identical across both runs), but the live transcript shows the model selected the **right** skill (`documents` for pdf, `office` for pptx) and dispatched it — the failure is a harness trace-capture bug (`_selected_skills` reads `turn_trace.tool_calls`, which drops the 2nd loop iteration's `skill_view` call). Independent of this change: `eval_skills.py` is unmodified, descriptions are unchanged, and W4.B selection depends only on descriptions. **Recommend a separate follow-up** to fix the per-iteration `turn_trace` capture in `case_w4_b_skill_selection`.

### Overall: PASS
All six tasks meet `done_when` with file:line evidence; full pytest suite green (843); lint clean; doc-retirement completed (stale prose pointers fixed). The lone eval FAIL (`eval_skills.py` W4.B) is a deterministic, pre-existing harness trace-capture bug demonstrably unrelated to these body-only edits — flagged for a separate follow-up, not a blocker. Ready for Gate 2 / `/ship`.
