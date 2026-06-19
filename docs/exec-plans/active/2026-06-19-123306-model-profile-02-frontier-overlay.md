# Model-profile 02 — Frontier prompt overlay (evidence-gated content for the gemini-3.1-pro profile)

Task type: evidence-gated core-prompt content — fill the `FRONTIER` profile overlay hook built by Plan A with prompt blocks that measurably help a frontier model, each shipped only on measured lift. Plan B of the 3-plan model-profile split.

## Plan group (model-profile)
- **01** (`2026-06-19-114937-model-profile-01-seam`) — the profile seam + gemini budget (mechanism). **Prerequisite for this plan.**
- **02 (this plan)** — frontier overlay content. Live-gemini measurement gate.
- **03** (`2026-06-19-123307-model-profile-03-weak-local-reflexes`) — weak-local reflexes + base Recall fix + KEEP record.

## Context
Plan A built one `ModelProfile` resolver and a `static_instruction_builder` overlay hook that currently returns `None`. This plan fills the `FRONTIER` branch. The frontier model (`gemini-3.1-pro-preview`) is the polar opposite of the weak local model co's rules are tuned for: its prompt needs are different *and* it has a long-context pricing cliff (>200k) that makes verbosity a cost lever, not just style.

Two candidate divergences, from `RESEARCH-behavioral-rules-peer-survey.md`:
- **G1 — output-formatting/verbosity rule.** The strongest sourced gap (4/4 peers instruct it; codex heavily, `gpt_5_2_prompt.md:160-242`). co's only coverage is `05 ## When NOT to over-plan` (planning effort, not output shape). Lands hardest on the frontier profile: gemini is verbose by default and the cliff makes length a cost.
- **Reflex-stripping.** co's low-inference reflexes (`03 ## Verification` enumeration, `07 ## Recall` cascade, the persistence hammering) are tuned to counter weak-model limits. On a frontier model they are dead tokens at best, over-constraint at worst. Candidate to omit from the `FRONTIER` overlay.

Discipline: this is measure-first (`feedback_eval_real_world_data`, `feedback_instructions_counter_model_limits`). No block ships on faith.

## Problem & Outcome
**Problem.** With the seam in place but the `FRONTIER` branch empty, gemini runs on the qwen-tuned shared base — over-constrained by reflexes it does not need and missing the verbosity control it most needs (verbose default + pricing cliff).

**Failure cost.** (1) Leave it empty → gemini wastes tokens on counterproductive reflexes and over-runs the cliff. (2) Fill it speculatively → carry per-model prose with no measured lift, busting the lean-prompt advantage.

**Outcome.** The `FRONTIER` overlay diverges from the shared base ONLY where measured to help: a verbosity/output block if it lifts, reflex omissions where ablation shows gemini behavior unchanged/improved without them. Net floor delta ≤ 0 per shipped block.

**Shippable contract:** the measured verdict per candidate block + whichever blocks clear the gate. A block with no measured lift is dropped (recorded) — a valid outcome.

## Behavioral Constraints
- **Evidence-gated**, same bar as `per-model-prompt-calibration` / `eval_rule_compliance.py`: measured behavior delta on the **live gemini path**, never faith. Needs `GEMINI_API_KEY` (`~/env-secrets/`).
- Editing any rule/overlay `.md` trips floor guards (`test_instruction_floor_coupling` F5 + `test_instruction_budget` ceiling); no `tool_name(` syntax in prose; net floor delta ≤ 0 per block; preserve `##` heading text verbatim (eval `_INVENTORY` keyed `(stem,title)` — `--inventory` after any base-heading touch, though the overlay should be profile-layer-additive, not base retitles).
- Rule/overlay prose = core-level review (platform core).
- Tail the log; RCA-first on slow gemini calls; never fold cold-start into a call budget.

## Scope
### In scope
- Probe + ship-or-drop each `FRONTIER` candidate block: (a) G1 output/verbosity block; (b) reflex-stripping omissions.
- Per-block live-gemini measurement arms in `evals/eval_rule_compliance.py`.

### Out of scope
- The seam itself (Plan A). Weak-local content (Plan C). Vision (gemini plan). Base consolidation (C4 shipped via `2026-06-18-151602`; C2 → future persistence-split plan). Making gemini the default.

## Tasks

**TASK-1 — G1 output/verbosity block (ship-or-drop)**
- files: the `FRONTIER` overlay source (location pinned by Plan A's wiring), `evals/eval_rule_compliance.py`
- done_when: a verbosity/output-formatting block is drafted (cliff-aware length guidance + final-answer shape), measured on the live gemini path via `eval_rule_compliance.py` arms (with/without the block) at the agreed N; **ships** only on a measured behavior lift (more compact/structured output, fewer tokens) at net floor delta ≤ 0 with floor guards passing — else **dropped** and recorded. If the probe shows qwen also lifts (OQ-1), the model-agnostic part moves to the shared base (Plan C / consolidation) and only the cliff-aware length cap stays frontier-scoped.
- success_signal: gemini's output is measurably tighter/structured, or the block is dropped with evidence.
- prerequisites: Plan A delivered; `GEMINI_API_KEY`

**TASK-2 — Reflex-stripping for the frontier profile (ship-or-drop)**
- files: the `FRONTIER` overlay source, `evals/eval_rule_compliance.py`
- done_when: for each reflex candidate (`03` Verification enumeration, `07` Recall cascade, persistence hammering), an ablation on the live gemini path measures whether gemini's target behavior is unchanged/improved without it; reflexes that prove dead/counterproductive are **omitted from the `FRONTIER` overlay** (recorded with the Δ); reflexes that still steer gemini are KEPT; net floor delta ≤ 0; floor guards pass; `--inventory` unaffected. Any candidate also measured frontier-counterproductive that Plan C would otherwise put in the shared base is flagged to Plan C as `weak_local`-scoped.
- success_signal: gemini's prompt sheds the reflexes it does not need, measured; the ones it does need stay.
- prerequisites: TASK-1 (shared harness), Plan A

## Testing
- Live-gemini measurement = evals (UAT smoke) via `eval_rule_compliance.py` arms; artifacts under `evals/_outputs/`. No pytest against the live API.
- Floor guards on every overlay `.md` edit; `--inventory` if base headings touched.

## Open Questions
1. **G1: base or frontier-only?** qwen may also benefit from an output-format rule. Default: measure both arms; if qwen lifts, the model-agnostic part goes to the shared base and only the cliff-aware cap stays frontier-scoped.
2. **Reflex-stripping granularity.** Strip whole sections, or specific high-inference enumerations within them? Default: ablate at the section level first; only sub-split if a section is mixed.

## Final — Team Lead
> Gate 1 — PO + TL review required before proceeding.
> Right problem (fill the FRONTIER overlay only where measured)? Correct scope (G1 + reflex-stripping, both ship-or-drop; seam and weak-local content out)?
> Prerequisite: Plan A delivered. Once approved, run: `/orchestrate-plan 2026-06-19-123306-model-profile-02-frontier-overlay` then `/orchestrate-dev`.
