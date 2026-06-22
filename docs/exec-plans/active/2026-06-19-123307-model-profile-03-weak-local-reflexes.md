# Model-profile 03 — weak-local overlay (`overlays/weak_local.md`) + BASE neutralization

## Goal
Two coupled outcomes:
1. **Neutralize BASE.** Today's BASE is calibrated to the weak local model — it carries compensatory scaffolding (verify-first reflexes, decompose-into-todos, recall reminders, act-this-turn, thoroughness nags) that a strong model does not need. Move that weak-specific scaffolding **out of BASE into `overlays/weak_local.md`**, leaving BASE model-agnostic (works for heavy or weak — model-wisdom-agnostic).
2. **Author `overlays/weak_local.md`.** It holds the scaffolding relocated from BASE **plus** additional weak-model techniques co lacks, borrowed from the peer-converged parity.

Result: **BASE = neutral core; `overlays/weak_local.md` = everything weak-specific.** Mechanism is shipped (Plan 1b). Content only.

**Absorbs Plan 03b (deleted).** The measure-then-author split is gone; this is one authoring plan. Peer convergence is the evidence — not co's own ablation.

## Peer sources (converged parity)
- hermes weak-enforcement guidance (`GOOGLE_MODEL_OPERATIONAL_GUIDANCE`, `prompt_builder.py`)
- opencode `gemini.txt` (flash/weak tier, `system.ts:25-39`)
- codex `prompts/`, openclaw overlay (where they special-case a less-steerable model)
- **co's own weak-tuned BASE content** — already weak-specific; it relocates into the overlay.

The peer 4-form rubric for a weak-model reflex (borrowed as form): imperative not descriptive; observable trigger first; concrete tool named (backtick bare name, no `tool_name(`); quantified limit where applicable; bad-phrase citation for output/conciseness reflexes; hard block (NEVER/MUST) only on critical paths.

## Scope
**In:** (a) identify which of today's BASE `##` sections are weak-model-specific scaffolding vs genuinely universal, and relocate the weak-specific ones into `overlays/weak_local.md` — whole-section where clean, or split where a universal is embedded (the weak half relocates, the universal half re-homes within BASE per the split table); (b) borrow weak-model techniques co lacks from peer parity (e.g. a conciseness/no-chitchat reflex with bad-phrase citation) into the same overlay.
**Out:** the frontier overlay (Plan 02); the mechanism (1b); the heavy model. Genuinely universal rules (safety, approval, core tool/memory mechanics, output format) **stay in BASE** — they are not weak-specific.

## Proposed split (recorded for G1)

**Test:** relocate a section only if it is **dead-weight for a strong reasoner** (it does that natively). If a strong model still benefits → universal → stays in BASE. A weak model needing something *more* does not make it weak-*specific*. Because overlays are additive, over-keeping in BASE is harmless (frontier carries minor dead weight); under-keeping risks stripping a universal — so **when in doubt, keep in BASE.**

**Whole-section relocation is the default, NOT a hard rule (revised at G1).** Section boundaries were drawn for a weak-tuned BASE, so three sections mix a relocatable weak scaffold with an embedded *universal* rule. Where the weak portion is substantial enough to be worth removing from frontier AND a genuine universal is bundled with it, **split the section**: relocate the weak half verbatim to the overlay, keep/re-home the universal half in BASE. Splitting preserves weak parity (weak_local = base + overlay still contains both halves) and only changes frontier (it keeps the universal, sheds the weak scaffold) — exactly the intent. Where the weak portion is merely *minor dead weight*, keep the section whole in BASE (the over-keep-is-harmless principle wins; no split).

**→ Relocate WHOLE to `overlays/weak_local.md` (verbatim — clearly compensatory, no embedded universal):**
| Section | Why dead-weight for a strong model |
|---|---|
| `04 Error recovery` | loop-prevention; strong models don't repeat identical failed calls |
| `05 Execution` | act-this-turn / don't-end-on-intent; strong models act in-turn natively |
| `05 When NOT to over-plan` | over-planning calibration; strong models calibrate length natively |

**→ SPLIT (relocate weak half; keep universal half in BASE):**
| Section | Relocate to overlay (weak scaffold) | Keep in BASE (embedded universal) |
|---|---|---|
| `05 Intent classification` | directive/deep/shallow taxonomy + "default to Shallow" + "act directly for shallow" — strong models infer intent | the state-mutation gate ("for Deep Inquiry, do not modify files or persist state until an explicit Directive; exception: durable memory saves always permitted"). Universal behavioral guardrail — a frontier model needs it or it writes files mid-inquiry. Re-home to `02_safety` (approval-adjacent: governs when state mutation is permitted); dev's call, `03_reasoning` is the alternative. |
| `05 Completeness` | sub-goal verification + the 5-point validation checklist (Correctness/Grounding/Format/Side-effect/Blockers) — strong models self-verify | the `todo_read` gate ("if todo_write was called this session, call todo_read and confirm no pending/in_progress items before responding done"). Core tool mechanics — the keep-rule retains those in BASE. Re-home to `04_tool_protocol`. |

**→ Keep WHOLE in BASE (universal — a strong model still benefits):** all of `01` (stances), `02` (safety), `06` (skill mechanics), `07` (memory mechanics); plus `03 Verification` (verify-state / tool>training / deps / stale-facts — universal correctness), `03 Resolving contradictions` (surface conflicts, don't flatten — universal quality), `03 Two kinds of unknowns` (discover-before-asking is minor frontier dead weight, but the section embeds universal interaction style — "present 2-4 options with a recommended default", "state assumptions explicitly"; over-keep is harmless, not worth a split), `04 Responsiveness` (preamble style), `04 Strategy` (depth-over-breadth / prerequisites / parallelization — universal good practice).

Net: `05 workflow` empties entirely (taxonomy + Execution + Completeness-self-verify + When-NOT relocate; the two embedded universals re-home to `02`/`04`); `03 reasoning` keeps Verification + Resolving contradictions + Two kinds of unknowns; `04 tool_protocol` drops Error recovery and gains the re-homed `todo_read` gate. Two splits each add one `##` heading, so the weak_local profile inventory goes **25 → 27** sections (content preserved; an embedded universal and its weak scaffold are now separate headings) — TASK-1's `--inventory` count is **27 for weak_local**, not unchanged. `06 Discovery` / `07 Recall` firing reflexes stay in BASE; TASK-2 may sharpen weak firing in the overlay if peer parity supports it.

**Ordering consequence (accepted):** relocated sections move from their interleaved numbered position to the trailing overlay block (base first, overlay appended). weak_local *content* is preserved; *order* is not. Inherent to additive overlays, accepted as-is.

**Plan 02 coordination:** Plan 02 borrows frontier "keep-going/persistence" for `overlays/frontier.md` while this plan relocates co's act-this-turn/thoroughness (`05 Execution`) into `overlays/weak_local.md`. Independent files, no mechanical conflict — but the same "don't stop early" concept will live in both overlays. Author them so frontier and weak don't drift into contradictory versions of it.

## Tasks

**✓ DONE — TASK-1 — Neutralize BASE: relocate weak-specific sections into `overlays/weak_local.md`**
- files: `co_cli/context/rules/*.md` (remove relocated sections), `co_cli/context/overlays/weak_local.md` (create + add them), this plan (record the keep-in-BASE vs relocate decision per `##` section)
- done_when: the relocations in the recorded split above are applied — 3 sections moved **whole** and 2 sections **split** (weak half → `overlays/weak_local.md` verbatim; universal half re-homed in BASE: state-mutation gate → `02_safety`, `todo_read` gate → `04_tool_protocol`); BASE retains the recorded keep-whole set; `uv run python evals/eval_rule_compliance.py --inventory` passes with the **weak_local profile at 27 sections** (25 + 2 split headings; content preserved, not byte-identical); floor guards pass (`tests/test_instruction_budget.py`); full suite passes.
- success_signal: BASE reads as model-agnostic but still carries every universal (the two split-out gates re-homed, not lost); `overlays/weak_local.md` holds the relocated weak scaffolding; weak_local's assembled prompt (base+overlay) preserves all of today's content (reordered, not byte-identical); frontier's BASE-only prompt sheds the weak scaffolding while keeping both universal gates.
- prerequisites: Plan 1b shipped.

**✓ DONE — TASK-2 — Borrow weak-model techniques co lacks from peer parity**
- files: `co_cli/context/overlays/weak_local.md` (append), `evals/eval_rule_compliance.py` (one `_INVENTORY` row per net-new section; `_PROBES` entry if single-turn observable), this plan (record peer source per section)
- done_when: weak-specific techniques the peers converge on that co lacks are authored as append-only `##` sections in `overlays/weak_local.md` in the 4-form rubric (imperative, observable-cue-first, concrete backtick tool, quantified where applicable, hard-block only on critical paths, no `tool_name(`); each records its peer source(s); `--inventory` passes; floor guards pass (base+overlay ≤ `INSTRUCTION_BLOCK_CEILING`); full suite passes. If a candidate technique co already covers in BASE/relocated content, it is recorded as already-covered, not duplicated.
- success_signal: `overlays/weak_local.md` adopts the weak-model techniques co was missing; no duplication of relocated content.
- prerequisites: TASK-1.

## Testing
- `--inventory` + floor guards + full suite on any rules/overlay change.
- Optional (not a gate): behavioral smoke on the weak ollama path to spot-check an authored section steers the weak model. Peer convergence is the authoring basis; smoke is a spot-check.
- No structural/fitness tests on rule/overlay files.

## Decisions
**Reset 2026-06-22 (first-principle rewrite).** Supersedes the prior measure-only framing (ablation Δ → "live-on-weak set" → 03b authoring → joint re-partition). That split was over-engineered: ablation only tests whether co's *existing* rules under-fire, and is structurally blind to *missing* peer techniques — so it can never tell you what to *add*. The real job is (1) make BASE neutral by relocating weak scaffolding into the weak overlay, and (2) borrow the weak-model techniques the peers converged on. Plan 03b folds in here and is deleted. The earlier ablation run (Verification steers weak Δ+0.68, Recall saturated/inconclusive) is a discarded artifact, not a gate.

## Delivery Summary — 2026-06-22

### TASK-1 — relocation record (per `##` section)
Applied exactly as recorded in the G1 split:

**Relocated WHOLE → `overlays/weak_local.md` (verbatim):**
| Section | From | Inventory key now |
|---|---|---|
| Execution | `05_workflow` | `(weak_local, Execution)` |
| When NOT to over-plan | `05_workflow` | `(weak_local, When NOT to over-plan)` |
| Error recovery | `04_tool_protocol` | `(weak_local, Error recovery)` |

**SPLIT (weak half → overlay verbatim; universal half re-homed in BASE, reworded to drop the relocated taxonomy vocabulary so it stands alone for a frontier model):**
| Section | Weak half → overlay | Universal half → BASE |
|---|---|---|
| Intent classification | taxonomy + default-Shallow + act-directly-for-Shallow + research-thoroughly-for-Deep → `(weak_local, Intent classification)` | state-mutation gate → new `## State mutation` in `02_safety` (reworded: "When a request is for analysis or information only, do not modify files or persist state until the user explicitly asks…" — no longer references Directive/Deep Inquiry) |
| Completeness | verify-sub-goals + 5-point validation checklist → `(weak_local, Completeness)` | `todo_read` gate → new `## Todo completion` in `04_tool_protocol` |

**Kept WHOLE in BASE (universal):** all of `01`, `02` (+ new State mutation), `06`, `07`; `03` Verification / Resolving contradictions / Two kinds of unknowns; `04` Responsiveness / Strategy (+ new Todo completion). `05_workflow.md` is now empty (kept as a file so `_collect_rule_files` contiguity 01–07 holds; stripped-empty content is skipped by `build_rules_block`).

Net: base 22 sections + weak_local overlay 5 = **27** (`--inventory` green for the weak_local profile, the configured Ollama backend). Frontier composition (base only) = 22, sheds all 5 weak reflexes while keeping both re-homed universals.

### TASK-2 — peer-source record (per authored section)
One net-new overlay section authored (`overlays/weak_local.md`), bringing weak_local to **28**:

| Section | Technique | Peer convergence (verified file:line) | Observable? |
|---|---|---|---|
| `## Conciseness` | no preamble/postamble (bad-phrase citations), few-sentence density ceiling, don't-repeat-prompt, lead-with-outcome for simple requests | opencode `gemini.txt:40-43` (Concise & Direct / Minimal Output <3 lines / No Chitchat); openclaw `gpt5-prompt-overlay.ts:32,70,78` (avoid preambles/restatement, don't narrate routine tool calls, dense replies); hermes `prompt_builder.py:417-429` (`GOOGLE_MODEL_OPERATIONAL_GUIDANCE` Conciseness, gated to Gemini/Gemma); codex `gpt_5_2_prompt.md:220` (concise & factual, no filler) | No — response content/tone only, no tool-call signal → `OUT-OF-REACH`, no probe |

**Already-covered / not duplicated:** output structure (headers/bullets/backticks/`file:line`) is BASE `01 Output format`; per-task completion is BASE `04 Todo completion` + overlay `Completeness`; the "model-family gating" insight (hermes gates to gemini/gemma) is the deferred per-provider axis, not authored here.

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | 3 whole + 2 split relocations applied; `--inventory` weak_local = 27; floor guards pass | ✓ pass |
| TASK-2 | Conciseness reflex authored in 4-form rubric w/ peer sources; `--inventory` passes (28); floor guards pass | ✓ pass |

**Tests:** scoped — 14 (TASK-1) + 7 (TASK-2) passed, 0 failed; floor budget guard 17,732 chars ≤ 25,000 ceiling; F5 deferred-signature guard green. Full suite deferred to `/review-impl`.
**Doc Sync:** fixed (narrow) — `docs/specs/prompt-assembly.md:46` stale "both overlays are empty" claim updated to describe the shipped weak_local overlay + absent frontier overlay.
**⚠ Extra files (beyond TASK-1 `files:`, announced):** `evals/eval_rule_compliance.py` (`_INVENTORY` re-homing + `Execution` probe stem; forced by the `--inventory`/full-suite done_when) and `tests/test_profile_rules_composition.py` (the `test_overlays_empty_for_every_profile` invariant is falsified by shipping the overlay; rewritten to assert the new reality).

**Overall: DELIVERED**
BASE is now model-agnostic (both split-out universals re-homed, none lost); `overlays/weak_local.md` holds all relocated weak scaffolding plus the peer-converged Conciseness reflex; weak_local composition preserves all prior content (reordered) and frontier sheds the weak scaffold while keeping both universals.

## Implementation Review — 2026-06-22

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | 3 whole + 2 split relocations; `--inventory` weak_local count; floor guards | ✓ pass | Whole relocations verbatim — `overlays/weak_local.md:17-37` (Execution), `:51-53` (When NOT to over-plan), `:55-66` (Error recovery); confirmed byte-equal to the deleted `05_workflow`/`04_tool_protocol` blocks in `git diff`. Splits: Intent taxonomy → `overlays/weak_local.md:3-15` with the state-mutation gate stripped and re-homed standalone to `02_safety.md:25-30` (`## State mutation`, reworded — no Directive/Deep vocabulary); Completeness checklist → `overlays/weak_local.md:39-49` with the `todo_read` gate excised and re-homed to `04_tool_protocol.md:32-35` (`## Todo completion`). `05_workflow.md` is empty (0 bytes). |
| TASK-1 | `--inventory` passes | ✓ pass | `eval_rule_compliance.py --inventory` → 28 sections (22 base + 6 `weak_local` overlay), home column correct (`base`/`overlay`); re-homed `State mutation`→base, `Todo completion`→base, relocated 5→overlay. |
| TASK-2 | Conciseness authored in 4-form rubric, peer sources recorded, no dup | ✓ pass | `overlays/weak_local.md:68-76` — imperative, bad-phrase citations, no `tool_name(` syntax; peer convergence recorded in plan Delivery table (opencode/openclaw/hermes/codex file:line). Already-covered items (output structure, per-task completion) correctly not duplicated. |
| both | floor guards (base+overlay ≤ ceiling) | ✓ pass | `test_instruction_budget.py::test_instruction_floor_within_budget` PASS (17,732 ≤ 25,000); F5 deferred-signature guard green. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale test: `test_assembled_prompt_byte_identical_to_base` asserted the now-falsified "overlay provider returns None for default path" contract — broke the full suite (delivery's "full suite passes" was deferred and untrue). Sibling to the `test_overlays_empty_for_every_profile` invariant the delivery did rewrite. | tests/test_flow_model_profile.py:82-99 | blocking | Rewrote to `test_weak_local_overlay_reaches_assembled_prompt` — asserts the seam injects the weak overlay into the assembled prompt for the default path (overlay sections present in full prefix, absent from base-only). Updated stale module docstring (`:1-7`). RCA: stale test (behavior intentionally changed), not a source bug. |

### Scope (extra files in working tree — NOT part of this plan)
The diff carries files beyond TASK-1/TASK-2 `files:`. **In-scope & announced:** `eval_rule_compliance.py`, `tests/test_profile_rules_composition.py`, `docs/specs/prompt-assembly.md` (narrow doc-sync, verified accurate). Plus my fix to `tests/test_flow_model_profile.py`. **Out of scope — belong to concurrent dream/profile-synthesis & Plan 02 work, must NOT be staged with this ship:**
```
⚠ evals/eval_skills.py          — dream skill-reviewer / merge_skills eval coverage
⚠ evals/eval_user_model.py      — profile-synthesis (W10.D) eval coverage
⚠ docs/specs/dream.md           — dream user_profile tool surface + run_housekeeping signature
⚠ docs/reference/RESEARCH-self-learning-co-vs-hermes.md
⚠ docs/exec-plans/active/2026-06-19-123306-model-profile-02-frontier-overlay.md  (sibling Plan 02)
⚠ uv.lock
```
`/ship` must stage only this plan's files (the 3 rules + `overlays/weak_local.md` + `eval_rule_compliance.py` + the two test files + `prompt-assembly.md` + this plan).

### Tests
- Command: `uv run pytest`
- Result: 805 passed, 0 failed (first run halted at 1 stale-test failure; green after the fix above)
- Log: `.pytest-logs/<timestamp>-review-impl-full.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap + static-prompt assembly graph loads clean)
- Overlay reaches assembled prompt: ✓ verified via `test_weak_local_overlay_reaches_assembled_prompt` (default ollama→WEAK_LOCAL path composes base + weak overlay) and `--inventory` (28 sections, home column correct)
- `success_signal` (TASK-1): ✓ BASE sheds the 5 weak reflexes while keeping both re-homed universals (`State mutation`, `Todo completion`); weak_local composition preserves all prior content; frontier (base-only) keeps both gates. (TASK-2): ✓ `Conciseness` adopted, no duplication.
- LLM-mediated steering (weak model actually obeys the authored reflexes): non-gating — plan basis is peer convergence; behavioral smoke is an optional spot-check, not a gate.

### Overall: PASS
All relocations applied exactly as recorded in the G1 split; both universal gates re-homed (none lost); Conciseness authored to rubric with peer sourcing; one stale test fixed; suite green; lint clean. Ship gate: stage only this plan's files — six unrelated working-tree files (dream/profile-synthesis + Plan 02 + uv.lock) must be excluded.

**G1 review 2026-06-22 (TL + PO) — CHANGES APPLIED.** The original "no section is split — every relocation verbatim" constraint collided with the plan's own "when in doubt, keep in BASE" test: three relocate-targets embed a universal rule. Resolved by relaxing the constraint to allow splitting where a universal is embedded (whole-relocation stays the default). (A) `05 Intent classification` embeds the state-mutation gate (don't persist until a Directive) — a universal guardrail; split, gate re-homes to `02_safety`. (C) `05 Completeness` embeds the `todo_read` tool-mechanic — core tool mechanics belong in BASE; split, gate re-homes to `04_tool_protocol`. (B) `03 Two kinds of unknowns` embeds universal interaction style (2-4 options, assumption disclosure) but its weak portion is only minor frontier dead weight → kept whole in BASE, no split. Splitting preserves weak parity and only changes frontier (which is the intent). Inventory count for weak_local: 25 → 27. Mechanism verified against source at G1: `build_profile_overlay`/`build_rules_block` (`assembly.py:70-104`), `ModelProfile.WEAK_LOCAL` (`config/llm.py:48`), `--inventory` base→overlay re-home (`eval_rule_compliance.py:158-172`).
