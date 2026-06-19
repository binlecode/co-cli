# Model-profile 03 — Weak-local reflex calibration + base Recall fix + KEEP record (absorbed from the deleted rules-reflex-migration-backlog)

Task type: evidence-gated core-prompt content — probe the 3 judgment-call reflex candidates the prior audit flagged, rewrite only those that under-fire on the configured local model, scope them to the `weak_local` profile, apply the model-agnostic Recall fix, and record the KEEP decisions. Plan C of the 3-plan model-profile split. Absorbs the live content of the never-implemented `rules-reflex-migration-backlog` (now deleted).

## Plan group (model-profile)
- **01** (`2026-06-19-114937-model-profile-01-seam`) — profile seam + gemini budget (mechanism). **Prerequisite.**
- **02** (`2026-06-19-123306-model-profile-02-frontier-overlay`) — frontier overlay content.
- **03 (this plan)** — weak-local reflexes + base Recall fix + KEEP record.

## Context
The prior reflex-migration audit (increment 1) classified all 28 rule sections — 19 reflex / 9 judgment-call — and found **no judgment-call section carries live failure evidence today.** The genuine reflex-rewrite candidates are **three**, each lacking evidence:
- `01 ## Relationship`
- `06 ## Create`
- `07 ## Curation` (promotion)

The honest move is evidence-first: probe whether each under-fires on the configured local model, rewrite only what does. Under the new profile architecture (Plan A), these reflexes are **`weak_local`-scoped** — they strengthen the calibration for the model whose limits they counter, and do NOT enter the shared base (where they would re-widen the gemini gap that Plan B is stripping). If Plan B measures any of them frontier-counterproductive, this plan keeps them out of the frontier overlay.

Also folded from the deleted backlog:
- **07 Recall stale-phrasing fix** — drop "this user's specific setup or preferences" from the `07 ## Recall` trigger; the always-injected USER.md profile owns that now (shipped `402203e1`). Model-agnostic accuracy fix.
- **KEEP decisions** — 4 sections are deliberately not reflexes and stay as-is.
- The backlog's `04 Strategy` split + persistence-dedup hand-offs are NOT here — they belong to the C2 persistence consolidation, handed off by the DELIVERED `2026-06-18-151602` to a future `behavioral-rules-persistence-consolidation` plan.

## Problem & Outcome
**Problem.** Three judgment-call sections may under-fire on the local model, but none has been measured; bulk-rewriting on faith is the rejected over-build, and leaving them un-probed stalls the calibration with its backlog un-actioned.

**Failure cost.** (1) Skip → the 3 keep under-firing silently. (2) Over-act → rewrite stances/scaffolded sections that should stay, or rewrite candidates without evidence, destabilizing the core prompt with no proven lift.

**Outcome.** A probe verdict per candidate (under-fires / fine / unmeasurable); evidence-gated `weak_local`-scoped reflex rewrites for only the under-firing ones; explicit recorded KEEP decisions for the 4 stay-sections; the 07 Recall stale-phrasing fix.

**Shippable contract:** the probe verdicts + the reflex rewrites that clear their gate + the Recall fix + the KEEP record. A candidate measured fine/unmeasurable is KEPT (not rewritten) and reported — a valid outcome.

## Behavioral Constraints
- **Evidence-gated:** ablation-gated via `eval_rule_compliance.py` (N=40, threshold+1SE ≈0.59 per the noise floor) where the section is probeable; behavioral-smoke-gated (`tmp/weather_smoke.py` pattern) where multi-turn; review-gated rubric-conformance (token-neutral, NO behavioral claim) only where neither fits — and never described as measured-safe.
- **Stances are not defects.** Do not "fix" value anchors into reflexes.
- Rule prose = core-level review; floor guards (`test_instruction_floor_coupling` F5 + `test_instruction_budget`) on every rule edit; no `tool_name(` in prose; net floor delta ≤ 0 per edit; preserve `##` heading text verbatim (`_INVENTORY` keyed `(stem,title)`, `--inventory` after any edit); rubric = `.agent_docs/rule-authoring-standard.md`.
- All eval data real; centralized eval settings; `ensure_ollama_warm` outside `asyncio.timeout`; tail the log + RCA-first on slow calls.

## Scope
### In scope
- Probe campaign for `01 Relationship`, `06 Create`, `07 Curation`-promotion → per-candidate verdict.
- Evidence-gated `weak_local`-scoped reflex rewrites for the under-firing ones (rubric-satisfying, body-only).
- Recorded KEEP decisions for `01 Thoroughness-over-speed`, `05 When-NOT-to-over-plan` (value-anchor stances), `01 Anti-sycophancy`, `05 Intent-classification` (scaffolded-mild).
- `07 ## Recall` stale-phrasing fix (model-agnostic base accuracy).

### Out of scope
- The seam (Plan A); frontier content (Plan B); `04 Strategy` split + persistence dedup (future `behavioral-rules-persistence-consolidation` / C2 plan); vision; the 19 already-reflex sections; any candidate that is unmeasurable AND not smoke-reproducible (deferred, recorded).

## Tasks

**TASK-1 — Probe campaign for the 3 candidates (ASSESS / measure; no rule edits)**
- files: `evals/eval_rule_compliance.py` (probe additions only if a single-turn probe genuinely fits), this plan (append verdict table)
- done_when: each of `01 Relationship`, `06 Create`, `07 Curation`-promotion has a recorded verdict — **under-fires** (ablation Δ or reproduced behavioral failure), **fine** (steers / scaffolding suffices), or **unmeasurable** (no single-turn probe fits, no smoke reproduces); any new probe runs at N=40 and does not change the section set or `_INVENTORY`; no rule `.md` edited. An `unmeasurable` candidate is deferred, not promoted.
- success_signal: a per-candidate evidence verdict exists; TASK-2 acts only on `under-fires`.
- prerequisites: Plan A delivered (so a rewrite can be `weak_local`-scoped)

**TASK-2 — Evidence-gated weak-local reflex rewrites (only `under-fires` candidates)**
- files: the specific rule/overlay file(s) for the `under-fires` candidates (`01_interaction.md` / `06_skill_protocol.md` / `07_memory_protocol.md` or the `weak_local` overlay, per Plan A's mechanism)
- done_when: each `under-fires` candidate is rewritten as a rubric-satisfying low-inference reflex (observable cue, imperative, concrete tool named, no `tool_name(`), body-only with `##` heading preserved, scoped to `weak_local`; net floor delta ≤ 0; floor guards pass; `--inventory` count unchanged; the TASK-1 gate re-runs and confirms the rewrite steers (≥ threshold+1SE) or the smoke shows corrected behavior; core-level review recorded. If TASK-1 found zero `under-fires`, this is a no-op recorded as such. Any candidate Plan B flags frontier-counterproductive is confirmed `weak_local`-only here.
- success_signal: measured-under-firing sections now steer on the local model; no faith rewrites; gemini gap not re-widened.
- prerequisites: TASK-1 (acts only on `under-fires`)

**TASK-3 — Recorded KEEP decisions + 07 Recall stale-phrasing fix (review-gated)**
- files: `co_cli/context/rules/07_memory_protocol.md` (stale-phrasing fix only), this plan (KEEP rationale)
- done_when: KEEP decisions for `01 Thoroughness-over-speed`, `05 When-NOT-to-over-plan`, `01 Anti-sycophancy`, `05 Intent-classification` recorded with one-line rationale each; AND the `07 ## Recall` trigger drops "this user's specific setup or preferences" — body-only, `##` heading unchanged, net floor delta ≤ 0, floor guards pass, `--inventory` unaffected, stale-anchor grep clean; full suite passes; core-level review recorded (value: accuracy; NO behavioral claim).
- success_signal: stay-sections explicitly decided; the stale Recall trigger corrected.
- prerequisites: none (file-disjoint from TASK-2 unless TASK-1 verdicts `07 Curation` as `under-fires` → then serialize on `07_memory_protocol.md`, floor-account once)

## Testing
- Floor guards on every rule edit; `--inventory` after any edit (28-count + span reassembly). NOT `test_orchestrator_schema_budget` (tool-schema scoped).
- TASK-1 ablation N=40/arm only where a single-turn probe fits; smokes follow `tmp/weather_smoke.py`. Tail the log; RCA-first on slow calls.
- No new structural/fitness tests on rule files; behavioral pytest (if any) asserts observable behavior only (`feedback_functional_tests_only`).

## Open Questions
1. **07 Recall fix — here or standalone `/sync-doc`?** Default: include in TASK-3 (it touches `07`, which TASK-2 may also touch if `07 Curation` is `under-fires` — single-owner).
2. **Probeability of 06 Create / 07 Curation-promotion.** Both gate an inference ("is it reusable" / "is it useful") behind an observable sub-cue. Default: attempt ablation; fall back to smoke; accept `unmeasurable` if neither fits.

## Final — Team Lead
> Gate 1 — PO + TL review required before proceeding.
> Right problem (evidence-first migration of the audited backlog, weak-local-scoped)? Correct scope (3 candidates probed; 4 keeps; Recall fix; structural split stays with consolidation)?
> Prerequisite: Plan A delivered. Once approved, run: `/orchestrate-plan 2026-06-19-123307-model-profile-03-weak-local-reflexes` then `/orchestrate-dev`.
