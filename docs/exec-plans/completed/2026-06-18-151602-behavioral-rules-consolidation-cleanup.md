# Behavioral Rules Consolidation + Cleanup — act on the shipped audit's deferred findings AND the new dups/inconsistencies surfaced in the G1-review + per-rule peer-justification pass: one ablation-gated tightening, peer-confirmed content/tone dedups, two newly-found cross-file/structural cleanups, and a clean hand-off of the heavy/unmeasurable items

Task type: evidence-gated core-prompt refactor (token-floor hygiene + structural cleanup) + explicit hand-off. Direct follow-up to the shipped `behavioral-rules-audit` (`docs/exec-plans/completed/2026-06-17-224304-behavioral-rules-audit.md`) and its two evidence artifacts: `docs/reference/RESEARCH-behavioral-rules-peer-survey.md` (COMPARE) and the audit's section-ablation VALIDATE run (verdicts recorded in that completed plan; the per-run JSONL is transient and no longer on disk). The audit's ≤3-section ACT cap deferred the original findings; a later G1-review + per-rule peer-justification pass surfaced two more concrete cleanups (a cross-file dup and a structural inconsistency) and one same-file overlap. This plan acts on the honest subset of all of them.

## Context

co's behavioral core is 7 numbered rule files (`co_cli/context/rules/01_identity.md` … `07_memory_protocol.md`), assembled verbatim by `build_rules_block` (`co_cli/context/assembly.py`) into the static prefix; the loader globs `*.md` and orders by numeric prefix, so a file rename is safe at runtime as long as the prefix is preserved and stale references elsewhere are fixed. Personality is off by default, so these rules ARE the agent's behavioral prompt. The audit produced two evidence artifacts and shipped exactly one edit (C1, the `04 ## Memory` stub — already gone). This plan acts on the deferred set plus the newly-found items — but only where action is honest given the evidence.

**Inherited evidence (do not re-derive):**
- **COMPARE** (`RESEARCH-behavioral-rules-peer-survey.md`): topic × {co, opencode, hermes-agent, codex, openclaw} matrix, gap list G1–G4, consolidation candidates C1–C7, all source-cited. Reviewed PASS (12 citations confirmed at exact lines).
- **VALIDATE** (section-ablation, N=20/arm; verdicts in the completed audit plan): only `03 Verification` cleanly **STEERS** (Δ+0.55, against `STEER_DELTA = 0.5`). `07 Recall` DEAD-WEIGHT (0.75/0.80 — model recalls regardless), `05 Execution` DEAD-WEIGHT but floored (0.05/0.00), and **3 NON-DISCRIMINATING** (`03 Two kinds of unknowns`, `04 Deferred tools`, `06 Discovery`). Inventory: 6 PROBED of 31 sections; 4 OBSERVABLE-OUT-OF-HARNESS (multi-turn / saturating); 21 OUT-OF-REACH (content/tone).

**Newly-surfaced findings (this session's G1-review + per-rule justification pass — not in the original audit):**
- **D1 — untracked cross-file dup.** "Don't repeat a failed call/action unchanged" is stated twice: `04_tool_protocol.md` `## Error recovery` ("Never repeat the exact same failed call with identical arguments. Retrying unchanged is a loop, not recovery.") and `05_workflow.md` `## Execution` ("Repeating a failed action unchanged is not persistence."). One home suffices; `04 ## Error recovery` is the natural owner (it is literally the error-recovery section). This was NOT in the audit's C1–C7 list.
- **D2 — structural inconsistency in `01_identity.md`.** It is the only rule file with no top-level `#` heading (it opens at `## Relationship`); the other 6 all lead with one (`# Safety`, `# Reasoning`, …). Separately, the filename implies identity content the file does not hold — its three sections (`## Relationship`, `## Anti-sycophancy`, `## Thoroughness over speed`) are interaction + working-values, and actual identity lives in the personality tier (G3).
- **03 same-file overlap (absorbed into C4/TASK-1, not its own task).** `03 ## Verification`:32-34 ("note what could not be confirmed and continue") and `03 ## Two kinds of unknowns`:63-65 ("state the assumption explicitly when proceeding") restate the proceed-with-stated-assumption idea. TASK-1 may absorb the `Verification` half during the C4 tightening, but only without dropping a behavior the ablation covers.

**The decisive lesson the critics forced (the spine of this plan):** the audit's evidence is that **this model is steered by almost none of these sections** — only 1 of 6 observable signals measurably steers, and the summarizer plan separately showed the model ignores heavy/structured prose. Consequences that bound scope:
1. **There is exactly one ablation-gated edit available today** — tightening `03 Verification` (the one section that reliably STEERS, so re-ablation can confirm no regression). Every other observable signal is NON-DISCRIMINATING/floored and has **no working gate**; building one (multi-turn harness) would re-confirm known probe artifacts. **Rejected: the speculative harness lane.**
2. **The consolidations and cleanups (C3/C5/C6, D1, D2) are token-floor hygiene / structural, not behavioral.** They touch OUT-OF-REACH content/tone or pure structure that NO peer instructs differently — by definition no behavioral regression *or* improvement signal; the payoff is tokens saved + one home per concept + structural consistency (the architecture-erosion tension applied to the prompt). They are review-gated (COMPARE peer-diff + core-level review), never measured-safe. **Rejected: any behavioral claim on these.**
3. **The gap-fills (G1/G2/G4) are coding-agent-shaped prose on a prose-ignoring model** — high prior of DEAD-WEIGHT-on-arrival; the only "fix" path is "add to the floor on faith." **Rejected: in-plan gap-fills.** Deferred with rationale (same stance as G3).

## Failure Modes (observed this session, not imagined)

- **Most consolidations have no working gate, and building one is low-ROI.** 5 of 6 observable signals are NON-DISCRIMINATING/floored; `tool_view`/`skill_view` floored at 0.00 both arms because the model never emits those tools at all. A "harder probe" cannot lift a floor the model never approaches.
- **C4's gate sits inside its own noise band.** Baseline Δ+0.55 is only ~0.4 SE above the 0.5 threshold (SE≈0.13 at N=20), so a bare "still ≥0.5" gate cannot separate a real regression from sampling noise — see the C4 noise-floor constraint.
- **Recall is the reported struggle area AND measured DEAD-WEIGHT-at-ceiling** (`07 Recall` 0.75/0.80 on a -0.05 delta inside N=20 noise). Trimming `07` (C7) on that would be acting on noise on the most sensitive rule.
- **Prose this model ignores.** The summarizer plan showed the configured model ignores heavy/structured instructions; a G1/G4 prose rule has a high prior of landing DEAD-WEIGHT.
- **D1 touches a C2-cluster file ahead of the C2 split.** `05 ## Execution` is both the D1 dedup site and a C2 persistence-cluster span. D1 removes only the verbatim duplicate sentence (the rule survives in `04`), so it carries no behavioral claim — but the C2 hand-off's "pre-edit assembly" baseline must be the post-D1 state, not today's. Recorded in TASK-5.
- **Multi-file consolidation (C2) has no single re-ablation target.** Its only honest gate is whole-assembly re-ablation; its blast radius (`01`/`04`/`05`) warrants its own Gate-1.

## Problem & Outcome

**Problem.** The audit identified ~10 rule-set issues but could act on only one; the later review pass found two more (D1, D2) plus a same-file overlap. Of the full set, the evidence says: one is safely tightenable now with a real gate (C4), several are token-floor-hygiene / structural cleanups with no behavioral signal either way (C3/C5/C6, D1, D2), and the remainder (C2, C7, G1/G2/G4) either need their own Gate-1 or should not be done as prose on this model at all.

**Failure cost:** two ways to lose. (1) Sit on the dups/inconsistencies → real redundancy and structural drift keep accreting (the architecture-erosion tension), and the audit's evidence rots. (2) Over-act → build a speculative harness to re-confirm known findings, import coding-shaped prose this model ignores, or restructure the C2 cluster without its whole-assembly gate — spending effort and growing/destabilizing the floor with zero behavioral payoff while believing it improved the agent.

**Outcome (act only where honest; hand off the rest).**
1. **One ablation-gated tightening (C4).** Tighten `03 Verification`'s verbose enumeration (optionally absorbing the `Two kinds of unknowns` assumption-stating overlap); re-ablate to confirm it still STEERS clear of noise. The single edit with a real gate.
2. **Review-gated content/tone + structural cleanups (C3/C5/C6, D1, D2).** Collapse co-unique content/tone redundancy and the cross-file dup to one home each; fix the `01` structural inconsistency. Justified by COMPARE + core review; value stated plainly as tokens-saved / structural consistency (no behavioral claim). Floor-delta tracked per edit.
3. **Explicit hand-off.** C2 → its own Gate-1 (unconditional split, gate spec + post-D1 baseline note provided). C7, G1/G2/G4, G3 → deferred-with-rationale.

**Shippable contract: the C4 tightening + the C3/C5/C6/D1/D2 cleanups that clear core-level review, with a clean hand-off doc for everything else.** It is acceptable for ACT to land only the subset that survives review; any cleanup that fails core review is dropped, not forced.

## Scope

### In scope
- **C4 (ablation-gated):** tighten `03 Verification` prose; optionally absorb the `03 Two kinds of unknowns` assumption-stating overlap if it drops no ablation-covered behavior; re-validate by re-ablation (still STEERS clear of noise) + negative floor delta.
- **C3 (review-gated):** merge `03 Fact authority` + `03 Source conflicts` into one "resolving contradictions" section.
- **C5 (review-gated):** merge `06 Create` + `06 Offer-to-save` into one skill-creation section.
- **C6 (review-gated):** resolve `06 Background review` self-undercut (merge into `## Drift`/`## Create` or remove).
- **D1 (review-gated, NEW):** remove the duplicate "repeat failed call/action unchanged" sentence — keep `04 ## Error recovery` as the single home, trim the `05 ## Execution` restatement (preserving `05`'s blocked-sub-goal / surface-the-blocker framing).
- **D2 (review-gated, NEW; rename + heading):** rename `01_identity.md` → `01_interaction.md` and add a top-level `# Interaction` heading consistent with the other 6 files. The file holds only interaction/working-values; actual identity is owned by the off-by-default personality overlay, so file + heading + content are made to agree (OQ-3 re-resolved to rename — reverses CD-M-1/PO-M-1(C3)). Stem rename ripples the eval `_INVENTORY` (3 rows keyed `01_identity` → `01_interaction`) + `assembly.py:26` docstring + `personality.md` (sync-doc); section count stays 31.
- Per-edit net static-floor token delta; instruction-floor guard tests (budget ceiling + F5) on every rule `.md` edit; repo-wide stale-anchor grep + full suite; **any task that changes the rule-section set updates `eval_rule_compliance.py`'s `_INVENTORY` + count assert** and runs `--inventory`.

### Out of scope (deferred with rationale — not done here)
- **Speculative measurement harness** (multi-turn / state-seeded probe mode) — rejected: re-confirms the audit's known probe-artifact findings; the two truly-floored signals (`tool_view`/`skill_view`) cannot be lifted by a harder probe.
- **C2 persistence/completion cluster** — unconditional **SPLIT** to its own Gate-1 (TASK-5 provides the slug + whole-assembly re-ablation gate spec + the post-D1 baseline note). Multi-file (`01`/`04`/`05`), no single re-ablation target.
- **C7 `07 Curation`/`Anti-patterns` trim** — unconditional defer: no ablation path even with harness work (content/tone), and recall is the reported struggle area; review-gated-or-nothing, not this plan.
- **Gap-fills G1 (output-formatting), G4 (todo-discipline)** — deferred: coding-agent-shaped (COMPARE flags peers as coding-first; co is not) and prose this model ignores.
- **G2 (tool-call-budget)** — deferred; revisit only as a zero-net-floor-growth tightening of existing `04`/`05` prose in a future plan.
- **G3 identity-in-rules** — deliberate design divergence (personality tier owns identity, off by default); assessed, not edited.
- Personality/persona; per-model routing; any `docs/specs/` edit as a task (sync-doc post-delivery); the summarizer/compaction prompt.

## Behavioral Constraints
- Rule prose is core-prompt change — every rule edit carries core-level review.
- Two gate regimes, never conflated: **ablation-gated** (C4 only — the one measurable section, re-validated by re-ablation) vs **review-gated** (C3/C5/C6/D1/D2 — COMPARE + core review only; value is token-floor reduction / structural consistency, NOT behavioral; a review-gated edit is NEVER described as measured-safe).
- C4 re-ablation: one section ablated at a time, all else fixed; implicit probe (the existing `03 Verification` arithmetic probe); deterministic-first scoring; threshold is the eval's `STEER_DELTA = 0.5`; uses the existing single-turn harness unchanged.
- **C4 noise floor (decisive for what TASK-1 can claim).** Baseline Δ+0.55 sits only ~0.4 SE above the 0.5 threshold (SE≈0.13 at N=20), so a bare "still ≥ 0.5" gate cannot distinguish a real regression from noise. Two mitigations, both required: (a) raise the re-ablation to N=40/arm (SE≈0.09 — cheap, one short section), and (b) require the tightened arm to clear threshold by ≥1 SE (Δ ≥ ~0.59 at N=40), not merely ≥0.5. A delta in the noise band (0.5–~0.59) is INCONCLUSIVE → keep the original prose and report, not PASS.
- All eval data real (`feedback_eval_real_world_data`); centralized eval settings only (`feedback_evals_centralized_settings`); `llm.host` + `reasoning_model_settings()`/`noreason_model_settings()`; `ensure_ollama_warm` outside `asyncio.timeout`; tail the log + RCA-first on slow calls.
- Editing any rule `.md` trips the instruction-floor guards (budget ceiling + F5) — run them on every edit; keep `tool_name(` call syntax out of rule prose (`feedback_instruction_floor_guards_on_rule_edits`).
- Net static-floor token delta tracked per edit; a change that grows the floor without measured lift or a clear peer-confirmed / structural value is rejected. **Single-owner per file:** TASK-1 and TASK-2/C3 both edit `03_reasoning.md` — sequence them and floor-account the combined post-edit file once. **Three tasks edit `eval_rule_compliance.py` — serialize all three (TASK-1 → TASK-2 → TASK-4), no parallel:** TASK-1 adds the `--samples` override (sampling plumbing, `:79/:549-565/:689/:719`), TASK-2 rewrites `_INVENTORY` + the `==N` assert (`:199-207`, `:665`), TASK-4 renames the three `01` stems (`:199-202`, `:128`). The edited regions are mostly disjoint but share the file, so integrate in order rather than concurrently. D1 (TASK-3) touches only `04`/`05` and is the one task free to run in parallel; D2 also touches `assembly.py`/`personality.md`. Keep each task's rule files otherwise disjoint except the noted `03` sequencing.
- **D1/C2 interaction:** D1 edits `05 ## Execution`, a C2-cluster span. D1 removes only the verbatim duplicate (the rule survives in `04`), so no behavioral claim — but TASK-5's C2 gate spec must state its pre-edit baseline is the post-D1 assembly.
- **Eval-inventory coupling (the ripple both critics caught):** `eval_rule_compliance.py` hardcodes the section set in `_INVENTORY` (`:199-207`), keys it by live `path.stem`, and asserts `len(_INVENTORY) == len(sections) == 31` (`:665`). Any task that removes/merges a section (TASK-2) or renames a file (TASK-4) must update the eval in the same task and run `--inventory` — this is NOT covered by the pytest "full suite" (the eval is a `uv run python` script) and NOT by sync-doc. TASK-4's rename does not change the section count (it flips 3 `_INVENTORY` stems from `01_identity` to `01_interaction` at `:199-202` plus the `:128` docstring), but it edits the same `_INVENTORY`/assert as TASK-2 — sequence the two and integrate against TASK-2's post-merge count rather than re-asserting a literal 31. TASK-1 leaves the section set untouched, so it does NOT edit `_INVENTORY` or the `==31` assert — but it DOES edit the eval to add a `--samples N` override (the C4 noise floor needs N=40 and `SAMPLES_PER_ARM` is hardcoded 20 at `:79`); that override defaults to 20, so no other eval behavior changes.

## High-Level Design

Five tasks, no speculative scaffolding:

1. **C4 ABLATION-GATED (TASK-1)** — tighten `03 Verification` (optionally absorb the `Two kinds of unknowns` overlap); re-run its single-turn ablation at N=40; confirm STEERS clear of noise + negative floor delta. The one edit with a real gate.
2. **C3/C5/C6 REVIEW-GATED (TASK-2)** — collapse co-unique content/tone redundancy in `03`/`06`; COMPARE + core review; floor delta; no behavioral claim.
3. **D1 REVIEW-GATED (TASK-3)** — cross-file dedup of the repeat-failed-call rule (`04`/`05`), single home in `04 ## Error recovery`.
4. **D2 REVIEW-GATED (TASK-4)** — `01_identity.md` → `01_interaction.md` rename + top-level `# Interaction` heading (file/heading/content agree; identity stays in the personality tier); ripples eval `_INVENTORY` stems + `assembly.py` docstring, count stays 31.
5. **HAND-OFF (TASK-5)** — written assessment: C2 unconditional SPLIT (slug + gate spec + post-D1 baseline), C7/G1/G2/G4/G3 deferred-with-rationale. No edits.

## Tasks

✓ DONE **TASK-1 — Tighten `03 Verification`, re-validate by re-ablation (C4; ablation-gated)**
- files: `co_cli/context/rules/03_reasoning.md`, `evals/eval_rule_compliance.py`
- done_when: `03 Verification` is tightened (the verbose time/system/file/git/versions/dependency/arithmetic enumeration compacted toward hermes' bullet density without dropping a covered category; optionally absorbing the `## Two kinds of unknowns` assumption-stating overlap at the **sentence level only** — trim the redundant `Verification` sentence, do NOT remove the `## Two kinds of unknowns` section itself (it is a PROBED `_INVENTORY` entry; deleting it would change the probe set and the section count, pulling eval edits into this task), and only if no ablation-covered behavior is lost), AND re-running the section's ablation at **N=40/arm** per the C4 noise floor shows it **still STEERS clear of noise**. The eval hardcodes `SAMPLES_PER_ARM = 20` (`:79`) with bare `sys.argv` parsing (no argparse) — TASK-1 adds a `--samples N` (`-n`) override that defaults to the existing 20 (so every future run is unchanged) and is read wherever `SAMPLES_PER_ARM` is consumed (`:549/:555/:559/:563/:565`, plus the run-header/summary echoes at `:689/:719`), then runs `uv run python evals/eval_rule_compliance.py --samples 40` for the `03 Verification` probe. This is the ONLY eval edit TASK-1 makes — it does not touch `_INVENTORY` or the `==31` assert (the section set is unchanged: sentence-level absorption only, `Two kinds of unknowns` retained). The full-minus-ablated Δ must be ≥ `STEER_DELTA` + 1 SE ≈ 0.59 (a delta in the noise band 0.5–~0.59 is INCONCLUSIVE → keep original prose and report, not PASS) AND net static-floor token delta is negative, with instruction-floor guards (budget + F5) passing, a repo-wide grep confirming no stale reference to the section anchor, and the full test suite passing.
- success_signal: the longest `03` section is leaner and still measurably steers the model — tokens saved with no behavioral regression.
- prerequisites: none

✓ DONE **TASK-2 — Review-gated content/tone consolidations (C3, C5, C6; token-floor hygiene)**
- files: `co_cli/context/rules/03_reasoning.md` (C3), `co_cli/context/rules/06_skill_protocol.md` (C5, C6), `evals/eval_rule_compliance.py` (inventory + count assert)
- done_when: C3 merges `## Fact authority` + `## Source conflicts` into one contradiction-resolution section; C5 merges `## Create` + `## Offer-to-save` into one skill-creation section; C6 folds `## Background review`'s non-redundant content into `## Drift`/`## Create` or removes it — each justified by a recorded core-level review note citing the COMPARE peer-diff (explicitly NO ablation/behavioral claim — value is token-floor reduction), with net static-floor token delta non-positive, instruction-floor guards passing, a repo-wide grep confirming no stale reference to any removed/renamed section anchor, and the full test suite passing. **AND** `eval_rule_compliance.py`'s `_INVENTORY` (`:199-207`) and the `len(_INVENTORY) == len(sections) == 31` assertion (`:665`) are updated to the post-merge section set (each merge nets −1; none of the merged sections is a PROBED section, so the probe list is unaffected), verified by `uv run python evals/eval_rule_compliance.py --inventory` passing. (`03_reasoning.md` is touched by both TASK-1 and TASK-2/C3 — integrate and floor-account the combined file once.)
- success_signal: three co-unique redundancies collapse to one home each; the rule set is leaner with no behavioral change claimed or expected.
- prerequisites: none functionally, but **sequence after TASK-1** (single-owner) — both edit `03_reasoning.md` (TASK-1 the `Verification` span, TASK-2/C3 the disjoint `Fact authority`/`Source conflicts` spans); do not parallelize, floor-account the combined post-edit file once.

✓ DONE **TASK-3 — Cross-file dedup of the repeat-failed-call rule (D1; review-gated)**
- files: `co_cli/context/rules/04_tool_protocol.md`, `co_cli/context/rules/05_workflow.md`
- done_when: the "repeat a failed call/action unchanged" rule has a single home in `04 ## Error recovery`; the verbatim restatement in `05 ## Execution` is removed while `05`'s distinct blocked-sub-goal / surface-the-blocker framing is preserved — justified by a recorded core-level review note (NO behavioral claim — value is one home for one rule), with net static-floor token delta negative, instruction-floor guards passing, a repo-wide grep confirming no stale reference, and the full test suite passing.
- success_signal: one rule, one home — the duplicate sentence is gone with `05`'s blocked-sub-goal guidance intact.
- prerequisites: none (files disjoint from TASK-1/TASK-2; may run in parallel with them)

✓ DONE **TASK-4 — Rename `01_identity.md` → `01_interaction.md` + `# Interaction` heading (D2; review-gated)**
- files: `co_cli/context/rules/01_identity.md` (→ `01_interaction.md`), `evals/eval_rule_compliance.py`, `co_cli/context/assembly.py`, `docs/specs/personality.md`
- done_when: the file is renamed `01_identity.md` → `01_interaction.md` (`git mv`, `01_` prefix preserved so the prefix-ordered loader is unaffected) and opens with a top-level `# Interaction` heading consistent with the other 6 rule files; the eval `_INVENTORY`'s 3 `01_identity` stems (`:199-202`) and the `:128` docstring example are updated to `01_interaction`, with `uv run python evals/eval_rule_compliance.py --inventory` passing — the rename does NOT change the section count, so it must integrate with (not overwrite) whatever count TASK-2 left in the `len(...) == N` assert (28 if TASK-4 runs after TASK-2's three merges; 31 if before). The rename touches only the three `01` rows, disjoint from TASK-2's `03`/`06` row edits; the `assembly.py:26` docstring example is updated; instruction-floor guards (budget + F5) pass; a repo-wide grep confirms no stale `01_identity` reference or anchor remains in code (RESEARCH-* historical artifacts left as-is); the full test suite passes. Justified by a recorded core-level review note (value is structural consistency + an honest filename↔content↔heading match; NO behavioral claim).
- success_signal: all 7 rule files share the same top-level-heading structure, and the file's name, heading, and contents all agree on "interaction" rather than mislabeling interaction/working-values as identity.
- prerequisites: none (file disjoint from other tasks; `personality.md` spec edit also covered by post-delivery sync-doc, updated in-task to avoid a transient stale ref)

✓ DONE **TASK-5 — Hand-off assessment: C2 split + deferrals (ASSESS; always)**
- files: `docs/exec-plans/active/2026-06-18-151602-behavioral-rules-consolidation-cleanup.md` (append the assessment)
- done_when: a written assessment in this plan that (a) recommends C2 as an unconditional SPLIT to its own Gate-1 plan, naming the candidate slug, the **5** COMPARE spans (`01 Thoroughness over speed`, `04 Strategy` follow-through, `04 Execute, don't promise`, `05 Execution`, and `05 Completeness` — with the audit's KEEP note that `05 Completeness`'s validation-pass checklist is unique and stays), the single merged-section target, the whole-assembly re-ablation gate spec (aggregate persistence fire-rate unchanged vs the pre-edit assembly), AND the note that C2's pre-edit baseline is the **post-D1** assembly; and (b) records the deferral rationale for C7 (no ablation path + recall-struggle), G1/G4 (coding-shaped + prose-ignored), G2 (zero-growth-only future), and G3 (design divergence). No rule edit in this task.
- success_signal: the heavy/unmeasurable items are cleanly handed off with their gates specified — none bulk-applied unmeasured.
- prerequisites: TASK-3 (so the post-D1 baseline note is accurate)

## Testing
- TASK-1 re-ablation is an eval (UAT smoke), artifact under `evals/_outputs/`; tail the log, RCA-first on slow calls. Single section, **~80 turns (2 arms × N=40)** per the C4 noise floor — still short, not a long-form pass.
- TASK-1/TASK-2/TASK-3/TASK-4 rule edits run the instruction-floor guard tests (budget ceiling + F5) and the full suite per the rename/drop done_when rule.
- No new behavioral pytest (no gap-fill adds a required behavior). No structural/fitness-function tests on rule files (`.agent_docs/review.md` Code Regulation Model).

## Open Questions
1. **C6 disposition.** Does `06 Background review` carry any non-redundant content worth folding into `## Drift`/`## Create`, or is it pure self-undercut to delete? Default: read it cold during TASK-2 and fold only genuinely-unique content; otherwise remove. Resolved in TASK-2.
2. **C5 merge shape.** Merge `Create` + `Offer-to-save` keeping both the autonomous and collaborative-creation triggers, or collapse to one trigger? Default: keep both triggers in one section (they are distinct reflexes), just remove the duplicated framing. Resolved in TASK-2.
3. **D2 heading text.** RE-RESOLVED (G1 user review): **rename `01_identity.md` → `01_interaction.md` + add `# Interaction`.** The C3 resolution (heading-only `# Identity`) was reversed: the file contains zero identity content — only interaction/working-values (`## Relationship`, `## Anti-sycophancy`, `## Thoroughness over speed`) — and actual identity is owned by the off-by-default personality overlay (`souls/{role}/seed.md`). `# Identity` would mislabel the file and overlap the overlay's identity ownership; it only "matched" an equally-wrong filename. The eval blast radius that justified deferring the rename is moot — it is exactly the same `eval_rule_compliance.py` `_INVENTORY` edit TASK-2 already performs (3 stem updates `01_identity`→`01_interaction` at `:199-202` + `:128` docstring), the count stays 31, and the `01_` prefix is preserved so the loader is unaffected. File, heading, and content now all agree. Resolved in TASK-4.

## Decisions (cycles C1–C3)

The C1 critique inverted the original draft: both critics judged the 4-lane plan (speculative measurement harness + gap-fills) over-built on a model the audit showed largely ignores prose. The restructure dropped the harness lane and gap-fills. C3 reopened the plan for the user-directed rescope (act on D1/D2 as real edits, not deferrals); both critics returned one blocker each, converging on the same eval-coupling root cause — both adopted.

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| PO-M-1 | adopt | Re-measuring audit-diagnosed probe artifacts is low-ROI; only `03 Verification` is measurable today. | Dropped the speculative harness lane; ablation-gating opportunistic on C4. |
| PO-M-2 | adopt | G1/G4 are coding-shaped prose on a prose-ignoring model. | Cut G1/G4 gap-fills → deferred; G2 deferred as zero-growth-only future. |
| PO-m-2 | adopt | C2's multi-file blast radius warrants its own Gate-1. | C2 = unconditional SPLIT. |
| PO-m-3 | adopt | Co-unique sections no peer instructs have no behavioral signal. | Review-gated edits framed as token-floor reduction, NOT behavioral. |
| CD-M-2 | adopt | C4 needs nothing from a harness — the guaranteed measurable floor. | C4 = sole ablation-gated edit (TASK-1). |
| CD-m-2 | reject (moot) | No multi-turn seeding remains, so no seed/probe arm-contamination risk. | — |
| CD-m-3 | adopt | C7 has no ablation path even after harness work (curation is content/tone). | C7 = unconditional defer. |
| CD-m-4 | adopt | TASK-1 and TASK-2 both edit `03_reasoning.md`. | Single-owner sequencing + combined floor-accounting. |
| CD-m-5 | adopt | COMPARE names 5 spans with `05 Completeness` KEEP. | TASK-5 names all 5 spans + the KEEP note. |
| G1-review-1 | adopt | Baseline Δ+0.55 only ~0.4 SE above 0.5 threshold (SE≈0.13 at N=20). | C4 noise floor: N=40/arm + clear by ≥1 SE; noise-band = INCONCLUSIVE→keep original. |
| G1-review-2 | adopt | Inherited VALIDATE JSONL transient/gone; threshold number not carried. | Context cites the audit plan; restates `STEER_DELTA = 0.5`. |
| CD-M-1 (C3) | adopt | `eval_rule_compliance.py` hardcodes `_INVENTORY` + `len==31` assert; TASK-2's three merges drop the count → `--inventory` crashes, and the eval is a `uv run python` script the pytest "full suite" never runs. | TASK-2 files: + `evals/eval_rule_compliance.py`; done_when updates `_INVENTORY` (`:199-207`) + the `==31` assert (`:665`) to the post-merge set and runs `--inventory` clean. Eval-coupling added to Behavioral Constraints. |
| PO-M-1 (C3) | adopt | D2 rename payoff is cosmetic; blast radius into the eval's hardcoded `01_identity` keys is under-counted and not sync-doc-covered. Heading fix alone resolves the structural defect. | OQ-3 resolved heading-only; TASK-4 rewritten to add `# Identity` only, no rename, no eval ripple; rename → out-of-scope deferred-cosmetic. Also moots the rename half of CD-M-1. |
| CD-M-1 heading-text | modify | PO suggested `# Interaction`; under heading-only that re-introduces a heading↔filename mismatch. | Chose `# Identity` (matches filename, file stays self-consistent); identity/interaction naming deferred with the rename. |
| G1-review-3 (user) | adopt | File holds zero identity content (interaction/working-values only); real identity is in the off-by-default personality overlay, so `# Identity` mislabels it. Rename eval-ripple is the same `_INVENTORY` edit TASK-2 already makes, count stays 31, `01_` prefix preserved. | Reversed OQ-3 / CD-M-1 / PO-M-1(C3): TASK-4 now renames `01_identity.md`→`01_interaction.md` + `# Interaction`, updates eval `_INVENTORY` stems + `assembly.py`/`personality.md`; filename rename removed from out-of-scope. |
| CD-m-1 (C3) | adopt (via guard) | TASK-1 omitted the `--inventory` cross-check. | TASK-1 scoped to keep the section set at 31 (sentence-level absorption only; `Two kinds of unknowns` retained) → inventory stays valid through TASK-1, no eval edit there. |
| CD-m-2 (C3) | acknowledge | Loader rename safety, D1/C2 baseline handling, task-file disjointness all verified sound. | No change; `assembly.py:26` docstring example left as-is. |
| PO-m-1 (C3) | acknowledge | D1-as-standalone adds the one sequencing cost; defensible since it removes only a verbatim dup. | No change — TASK-5 post-D1 baseline prereq non-negotiable (already in done_when). |
| PO-m-2 (C3) | acknowledge | TASK-1 optional absorption is correctly guarded, not a smuggled behavioral claim. | No change — absorption stays strictly optional. |

## Final — Team Lead

Plan approved (rescoped, C1–C3 converged; both C3 blockers adopted).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev 2026-06-18-151602-behavioral-rules-consolidation-cleanup`

## TASK-5 — Hand-off assessment (C2 split + deferrals)

No rule edit in this task. This is the written hand-off for the items the
evidence says must not be bulk-applied in this plan.

### C2 — persistence/completion cluster → unconditional SPLIT to its own Gate-1

**Recommendation: split C2 into its own plan-then-dev cycle. Do NOT fold it into
this plan.** C2 is multi-file with no single re-ablation target, so it needs its
own Gate-1 design pass and a whole-assembly gate this plan's per-section regime
cannot supply.

- **Candidate slug:** `behavioral-rules-persistence-consolidation`.
- **The 5 COMPARE spans** (the duplicated persistence/completion idea, restated
  across three rule files):
  1. `01_interaction.md ## Thoroughness over speed`
  2. `04_tool_protocol.md ## Strategy` — the "Follow through" paragraph
  3. `04_tool_protocol.md ## Execute, don't promise`
  4. `05_workflow.md ## Execution`
  5. `05_workflow.md ## Completeness`
  **KEEP note (from the audit, carried forward):** `05 ## Completeness`'s
  validation-pass checklist (correctness / grounding / format-schema /
  side-effect safety / blockers) is genuinely unique and is NOT redundant with
  the persistence idea — it stays. C2 consolidates the *persistence/follow-through*
  restatement only; the completeness checklist is preserved verbatim.
- **Single merged-section target:** one block (working name `## Follow through`,
  natural home `04_tool_protocol.md`) carrying the consolidated
  "decompose → execute → don't stop half-done → execute-don't-promise" steer,
  with the unique `05 Completeness` validation checklist left in place as its own
  section. Net: 5 spans → 1 persistence block + the retained checklist.
- **Whole-assembly re-ablation gate spec:** the per-section ablation harness
  cannot gate a 3-file consolidation (no single span to drop). The honest gate is
  a **whole-assembly** comparison: measure the aggregate persistence fire-rate
  (the model's follow-through / execute-don't-promise / continue-until-done
  behavior) of the *pre-edit* assembly vs the *post-consolidation* assembly across
  the persistence-relevant probes, and require the post-edit aggregate fire-rate
  to be **unchanged within noise** (same N=40/arm + ≥1-SE discipline as the C4
  noise floor). A drop beyond noise = regression → keep the original spread.
- **Pre-edit baseline is the POST-D1 assembly.** D1 (TASK-3, delivered this plan)
  already removed the verbatim "Repeating a failed action unchanged is not
  persistence." sentence from `05 ## Execution`. C2's pre-edit baseline must be
  the assembly *after* D1, not today-minus-D1 — otherwise the gate would
  attribute D1's removal to C2.

### Deferred-with-rationale (not done here, not silently dropped)

- **C7 — `07 Curation` / `Anti-patterns` trim.** Deferred unconditionally. No
  ablation path exists even with harness work (curation is content/tone, no
  tool-call signal — `07 Curation` and `Anti-patterns` are both OUT-OF-REACH in
  the inventory), AND recall is the reported struggle area; `07 Recall` measured
  DEAD-WEIGHT-at-ceiling (0.75/0.80, inside N=20 noise). Trimming the most
  sensitive rule on a no-signal basis is acting on noise. Review-gated-or-nothing,
  and not in this plan.
- **G1 (output-formatting/verbosity) + G4 (todo/plan-tool discipline).** Deferred.
  Both are coding-agent-shaped (COMPARE flags the peers that instruct them —
  codex, opencode — as coding-first; co is not), and the summarizer evidence shows
  this model ignores heavy/structured prose. A G1/G4 prose rule has a high prior
  of landing DEAD-WEIGHT-on-arrival; the only path is "add to the floor on faith,"
  which grows the floor with no measured lift. Revisit only with a real signal.
- **G2 (tool-call-budget / explicit stop condition).** Deferred. Revisit only as a
  **zero-net-floor-growth** tightening of existing `04`/`05` prose in a future
  plan — never as net-new added prose.
- **G3 (identity statement in the rule core).** Assessed, not edited — deliberate
  design divergence. Identity is owned by the off-by-default personality tier
  (`souls/{role}/seed.md`); the rule core intentionally carries none. TASK-4's
  rename (`01_identity.md` → `01_interaction.md`) makes the filename honest about
  this: the file holds interaction/working-values, not identity. Not a gap to fill.

**Summary:** C2 hands off cleanly with its slug, 5 spans (+ the `05 Completeness`
KEEP), merged-section target, whole-assembly gate spec, and post-D1 baseline
noted. C7/G1/G2/G4/G3 are deferred with explicit rationale — none bulk-applied
unmeasured.

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 (C4) | `03 Verification` tightened; re-ablation at N=40 STEERS clear of noise; floor delta negative | ✓ pass — STEERS Δ=+0.60 (full 0.95 / ablated 0.35), ≥ threshold+1SE (~0.59); floor smaller |
| TASK-2 (C3/C5/C6) | merge contradiction sections, skill-creation sections, fold Background review; `_INVENTORY` + count → 28; `--inventory` clean | ✓ pass — 31→28 sections; inventory + floor guards clean |
| TASK-3 (D1) | single home for repeat-failed-call rule in `04 ## Error recovery`; `05` restatement removed, blocked-sub-goal framing kept | ✓ pass (Dev-1) — floor guards + grep clean |
| TASK-4 (D2) | `01_identity.md`→`01_interaction.md` + `# Interaction` heading; eval stems + `assembly.py`/`personality.md` updated; count stays 28 | ✓ pass — rename clean, span parser + floor guards clean, no stale `01_identity` |
| TASK-5 (hand-off) | C2 unconditional-SPLIT write-up (slug, 5 spans, KEEP note, whole-assembly gate, post-D1 baseline) + C7/G1/G2/G4/G3 deferrals | ✓ pass — appended above |

**Tests:** scoped — 11 passed, 0 failed (floor guards `test_instruction_floor_coupling`/`test_instruction_budget`, `test_orchestrator_schema_budget`, `test_personality_disabled`); eval `--inventory` validates the span parser at 28 sections; C4 ablation eval 80 samples → STEERS.
**Doc Sync:** fixed — `personality.md` (rule-file rename, in-task); `skills.md` (Offer-to-save folded into the Create reflex bullet; stale "Background review section present" traceability row removed). Flagged for review-impl: `skills.md` traceability table references a **nonexistent** `tests/test_flow_skill_protocol.py` in two surviving rows ("06_skill_protocol.md appears in assembled static instructions", "skill-creator present in manifest") — pre-existing doc rot, not introduced by this plan.

**Overall: DELIVERED**
All five tasks passed `done_when`; the one ablation-gated edit (C4) cleared the noise floor; the four review-gated edits (C3/C5/C6, D1, D2) are token-floor/structural only with no behavioral claim. Section set is 31→28, all 7 rule files now share a top-level H1, and the heavy/unmeasurable items (C2, C7, G1/G2/G4/G3) are handed off with gates specified. One pre-existing doc-rot item flagged for the review-impl pass.
