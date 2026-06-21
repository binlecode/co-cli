# Model-profile 1b — Shared overlay mechanism (append-only base + dual overlay, profile-agnostic infra)

Task type: profile-agnostic prompt-composition mechanism. Build the append-only base + dual-overlay seam, make the eval harness and floor guards overlay-aware, and remove the superseded subtractive code — all shipping with the assembled prompt **byte-identical to the (cleaned) base from Plan 1a** (overlays empty). This is the shared infra both profile plans depend on. Plan 1b of the model-profile group (sits between Plan 1a — the base content cleanup — and the per-profile Plans 02/03). The base output-format floor that an earlier draft placed here has moved to Plan 1a TASK-4, so 1b is now a pure inert refactor with an **unconditional** byte-identity guarantee.

## Architecture (user-decided — not open for re-litigation)
```
PROMPT(profile) = BASE  +  OVERLAY(profile)
```
- **BASE** = shared intersection rule sections, assembled by `build_rules_block()` (no-arg) from `co_cli/context/rules/`.
- **OVERLAY(profile)** = a separate appended content file `co_cli/context/overlays/<profile>.md`, returned by `_model_profile_overlay_provider` and appended **after** base.
- **Append-only**: an overlay only ADDS; nothing is filtered or removed. No subtraction, no membership markers — both rejected.

This plan builds the mechanism with **both overlays empty** and base unchanged, so it lands byte-identical to Plan 1a's cleaned base. The per-profile *content* is Plans 02 (gemini) and 03 (ollama).

## Plan group (model-profile) — DAG: `01 → 1a → 1b → { 02, 03 }`
- **01** (`2026-06-19-114937-model-profile-01-seam`) — `ModelProfile` resolver + per-profile budget (shipped). **Prerequisite.** This plan generalizes its inert overlay-builder seam.
- **1a** (`2026-06-20-004932-model-profile-1a-rule-tool-partition`) — base content/partition cleanup + the base output-format floor. **Prerequisite for 1b** — 1b freezes the base 1a produces. Profile-agnostic, in-place edits to `rules/`.
- **1b (this plan)** — the shared overlay mechanism (append-only seam + overlay-aware harness/floor guards + seam-B removal). **Prerequisite for 02 and 03.** Depends on 1a.
- **02** (`...-model-profile-02-frontier-overlay`) — gemini/frontier profile content. Depends on 1b.
- **03** (`...-model-profile-03-weak-local-reflexes`) — ollama/weak-local profile content + the base→overlay relocation join (inherits 1a's Bucket-B relocation candidates). Depends on 1b and (for the join) on 02's recorded Δ.

## Context

### Delivered-then-superseded (this plan REMOVES it)
The first Plan 02 draft shipped a **subtractive** seam in `co_cli/context/assembly.py`: `build_rules_block(profile)` + `_drop_sections` (`##`-span parser) + `_FRONTIER_EXCLUDED_SECTIONS` (frozenset), plus `tests/test_profile_rules_composition.py`. The decided architecture is append-only, so this subtractive code is removed: `build_rules_block()` reverts to no-arg base-only, and the exclusion machinery + its test are deleted. (TASK-A, the gemini eval lever, was delivered separately and lives in Plan 02 — untouched here.)

### Current-state findings (verified 2026-06-19/20)
- **Builders join append-only.** `build.py:35-39` joins `ORCHESTRATOR_SPEC.static_instruction_builders` with `\n\n`, skipping `None`. Order (`orchestrator.py:77-83`): `_base_instructions_provider`, `_user_profile_provider`, `_toolset_guidance_provider`, `_model_profile_overlay_provider`, `_personality_critique_provider`. The overlay slot exists and already appends after base; this plan fills it + reorders it to immediately follow `_base_instructions_provider`.
- **`_model_profile_overlay_provider(deps)` returns `None`** (`orchestrator.py:53`). Generalize it to resolve `resolve_model_profile(deps.config.llm)` and return that profile's overlay block.
- **Base today = full rules.** `build_base_instructions(config)` (`assembly.py:127`) → `build_rules_block()` walks 7 `NN_*.md` (25 `##` sections) in `co_cli/context/rules/`.
- **The eval harness is a second, overlay-BLIND parser.** `_build_arm_agent` (`eval_rule_compliance.py:436`) hand-rolls a 2-builder spec whose `_rules_builder` returns `build_rules_block()` and **never invokes the overlay** → an arm measures base-only. `_all_sections` (`:153`)/`_INVENTORY` (25-count asserted at `:616`) and `_rules_block_drop_section` (`:162`) read `_RULES_DIR` directly. All must become profile-aware (span base + overlay) or measurement post-relocation is invalid.
- **Floor guards read base only.** `test_instruction_floor_coupling.py:41` reads `build_rules_block()`; `test_instruction_budget.py:67` reads `build_base_instructions(config)`. Neither sees overlay content.
- **The base output-format floor is Plan 1a's job (TASK-4), not this plan's.** 1b consumes whatever base 1a produces; it adds no rule sections of its own.

## Problem & Outcome
**Problem.** co's prompt has no profile-composition structure. The overlay slot is inert, the only per-profile code shipped is the rejected subtractive seam, and the eval harness + floor guards are blind to any overlay — so no profile content can land safely and no measurement post-relocation would be valid.

**Failure cost.** Without this: Plans 02/03 have nowhere to put profile content; if they add it anyway, the overlay-blind harness measures the wrong prompt and the floor guards under-count, silently shipping over-budget or mis-measured prompts.

**Outcome.** The append-only base + dual-overlay mechanism, an overlay-aware harness + floor guards, and removal of the subtractive code — all landing **byte-identical to Plan 1a's base** (overlays empty), unconditionally (no carve-out). Both profile plans can then add content against a verified, measurable seam.

**Shippable contract:** the mechanism + overlay-aware harness/guards + seam-B removal, with **unconditional** byte-identity proven against Plan 1a's base (empty overlays) and the full suite + `--inventory` green.

## Behavioral Constraints
- Rule/overlay prose = **core-level review** (platform core). No `tool_name(` in prose. Preserve `##` heading text verbatim (`_INVENTORY` keyed `(stem, title)`). `--inventory` after any base/overlay change.
- The floor guards assert the `test_instruction_budget` `INSTRUCTION_BLOCK_CEILING = 25_000` chars guard (a char-count ceiling on the assembled floor, `test_instruction_budget.py:52,72`; distinct from Plan 01's 64k-*token* context budget — do not conflate the two). 1b adds no base chars itself (the output-format floor is Plan 1a); it only makes the guard measure base + `overlay(weak_local)`.
- Append-only only: overlay ADDS, never filters. The mechanism must make removal *structurally* impossible to express, not merely unused.

## High-Level Design
**Overlay source.** One file per profile: `co_cli/context/overlays/<profile>.md` (e.g. `overlays/weak_local.md`, `overlays/frontier.md`), parsed into `##` sections by the existing parser. Absent/empty → overlay empty → no append. Escalate to `overlays/<profile>/NN_*.md` only if a profile reaches ≥2 sections (not built speculatively).

**Builder.** `_model_profile_overlay_provider(deps)` resolves the profile and returns its assembled overlay block (or `None`). Reorder it to immediately follow `_base_instructions_provider` in `ORCHESTRATOR_SPEC` so overlay sits adjacent to base.

**`build_rules_block()` is no-arg base-only**; `build_base_instructions(config)` builds base only. All per-profile divergence is the overlay builder.

**Overlay-aware harness.** `_build_arm_agent` assembles `base + overlay(resolve_model_profile(deps.config.llm))` via the production overlay builder, not a fixed `build_rules_block()`. `_all_sections(profile)`/`_rules_block_drop_section(target, profile)` locate+ablate a target in base OR the profile overlay. The `:616` count assertion becomes `len(base)+len(overlay(profile))`; `_INVENTORY` carries each section's home (base vs overlay).

**Floor guards measure the worst case.** Both guards append `overlay(weak_local)`: `test_instruction_budget` asserts `len(build_base_instructions(config) + overlay(weak_local) + guidance + critique) <= INSTRUCTION_BLOCK_CEILING` (25_000 chars, `test_instruction_budget.py:52`); the F5 guard (`test_instruction_floor_coupling`) concatenates `build_rules_block() + overlay(weak_local) + guidance`. This is the char-ceiling guard, NOT the 64k-token context budget (a separate Plan 01 concern, not asserted here).

**Byte-identity (unconditional).** With overlays empty, the assembled prefix (both profiles) equals a baseline string captured from Plan 1a's base — **with no carve-out** — the guard compares the reordered `ORCHESTRATOR_SPEC` against the baseline, not re-deriving inertness from overlay-is-None. (Since 1b adds no base content, there is no section to except; the earlier "EXCEPT the output-format section" carve-out and its re-pin coupling are retired with G1's move to Plan 1a.)

## Tasks

✓ DONE **TASK-1 — Append-only dual-overlay seam (generalize the builder; remove seam B)**
- files: `co_cli/context/assembly.py`, `co_cli/agent/orchestrator.py`, `tests/test_profile_rules_composition.py`, `tests/test_flow_model_profile.py`
- done_when: `_model_profile_overlay_provider` assembles and returns `overlays/<profile>.md` content (empty/absent → `None`) appended after base, reordered to immediately follow `_base_instructions_provider`; `build_rules_block()` reverted to no-arg base-only **AND its call-site `build_base_instructions` (`assembly.py:169`, currently `build_rules_block(resolve_model_profile(config.llm))`) reverted to the no-arg call** (CD-m-2); `_FRONTIER_EXCLUDED_SECTIONS` + `_drop_sections` deleted and `test_profile_rules_composition.py` removed/rebuilt; with overlays empty the assembled static prefix for BOTH profiles is **byte-identical, unconditionally, to a baseline string captured from Plan 1a's base** (comparing the reordered `ORCHESTRATOR_SPEC`, not overlay-is-None; no carve-out — 1b adds no base content); a fixture `overlays/<profile>.md` section appears in that profile's assembled prompt and is absent from the other (functional guard); a **repo-wide grep for `_FRONTIER_EXCLUDED_SECTIONS` and `_drop_sections` returns zero references** AND the **full suite passes**.
- success_signal: the prompt assembles as base + per-profile overlay, append-only, default prompt unchanged, no subtractive code left.
- prerequisites: Plan 01 delivered

✓ DONE **TASK-2 — Overlay-aware eval harness + floor guards**
- files: `evals/eval_rule_compliance.py`, `tests/test_instruction_floor_coupling.py`, `tests/test_instruction_budget.py`
- done_when: `_build_arm_agent` assembles `base + overlay(resolve_model_profile(deps.config.llm))` via the production overlay builder (NOT a fixed `build_rules_block()`); `_all_sections(profile)`/`_rules_block_drop_section(target, profile)` locate+ablate a target in base OR the profile overlay; the hardcoded `len(_INVENTORY) == len(sections) == 25` literal at `eval_rule_compliance.py:616` is **removed and replaced** by `len(base)+len(overlay(profile))` (the literal `25` is a stale-assertion hazard, CD-m-1) and `_INVENTORY` carries each section's home (base vs overlay); the floor guards append `overlay(weak_local)` and assert against the `INSTRUCTION_BLOCK_CEILING = 25_000` chars guard (`test_instruction_budget.py:52`), not a token budget; `uv run python evals/eval_rule_compliance.py --inventory` passes; an assertion proves ablating an overlay-resident fixture section on a profile arm removes it from that arm's prompt; full suite passes.
- success_signal: the harness measures base+overlay per profile and floor guards bound the worst case, so post-relocation measurement (Plans 02/03) is valid.
- prerequisites: TASK-1

_(TASK-3 — the base output-format floor — has moved to Plan 1a TASK-4. 1b adds no base content.)_

## Testing
- All pytest: **unconditional** byte-identity guard (empty overlays, no carve-out), functional overlay presence/absence guard, overlay-aware `--inventory`, floor guards at the 25k-char `INSTRUCTION_BLOCK_CEILING` worst case (base + `overlay(weak_local)`), full suite. TASK-1 removes shared surface → repo-wide grep + full suite mandatory.
- No live-API tests here (measurement is Plans 02/03).

## Open Questions
1. **Overlay layout — RESOLVED.** Single `overlays/<profile>.md`; escalate to a dir walk only at ≥2 sections per profile.
2. **Output-format floor home — moved to Plan 1a (OQ3 there).** No longer a 1b question.

## Decisions

Critique loop converged in 2 cycles (C2: Core Dev approve, PO approve C1, Blocking none) across all three plans of the restructure. The decisive C1 catches were source-grounded factual corrections: the floor guard is a 25k-CHAR ceiling (not the 64k-token budget the plans conflated it with), the "persistence reflex" is unmeasurable + C2-owned (dropped), and the cited ≈0.59 gate doesn't exist (`STEER_DELTA = 0.5`). The 4-plan split (mechanism / gemini / ollama) was PO-affirmed as the cleaner cut.

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | verified: the floor guard is a 25k-CHAR ceiling, not a 64k-token budget — conflating them makes the sizing claim unverifiable | 1.5 Behavioral Constraints + HLD "Floor guards" + TASK-2/TASK-3 done_when: name `INSTRUCTION_BLOCK_CEILING = 25_000` chars; all 64k refs in 02/03 → char-ceiling |
| CD-M-2 | adopt | verified: no "persistence" section exists; `04 Strategy` is C2-owned + out-of-scope | shared candidate set reduced to `03 Verification` + `07 Recall` in Plan 02 TASK-1, Plan 03 TASK-1/TASK-3, HLD/Scope |
| CD-M-3 | adopt | verified: `STEER_DELTA = 0.5` in source; ≈0.59 is invented | gate → `STEER_DELTA = 0.5` across Plan 02/03 Behavioral Constraints + tasks |
| CD-m-1 | adopt | the `==25` literal is a stale-assertion hazard once a section is added/moved | 1.5 TASK-2 done_when: remove+replace the literal, not just "re-express" |
| CD-m-2 | adopt | `build_base_instructions` (`assembly.py:169`) calls `build_rules_block(profile)` — the revert must touch the call-site | 1.5 TASK-1 done_when names the `:169` call-site revert |
| CD-m-3 | ~~adopt~~ **RETIRED** | superseded post-critique: the base output-format floor (the section that required re-pinning) moved to Plan 1a TASK-4, so 1b adds no base content and its byte-identity is unconditional — no re-pin coupling exists | removed from 1b TASK-1/TASK-3 done_when; byte-identity is now unconditional |
| CD-m-4 | adopt | the join's point is gemini ABSENCE, not weak_local non-growth | 03 TASK-3 done_when: gemini-absence is the load-bearing functional guard |
| CD-m-5 | adopt | a hand-transcribed Δ table across plan files is non-verifiable | 02 TASK-1 writes Δ to `evals/_outputs/` artifact; 03 TASK-1/TASK-3 read it |
| PO-m-1 | adopt | prevent reading Plan 02's empty overlay as under-delivery | 02 Shippable contract: expected landed diff = "recorded Δ artifact + zero source change" |
| PO-m-2 | adopt | only the join needs Plan 02; ollama work is independent | 03 HLD: TASK-1/2/4 may run parallel to Plan 02; only TASK-3 gates |
| PO-m-3 | adopt (no-op) | output-format home settled at TASK-3 by topical fit | unchanged (OQ2) |

## Final — Team Lead

Plan approved (TL). Critique loop converged C2 — Core Dev approve, PO approve, Blocking none.

> Gate 1 — PO review required before proceeding.
> Right problem (extract the shared, profile-agnostic overlay mechanism so the per-profile plans build on verified, measurable infra)? Correct scope (append-only seam + overlay-aware harness/floor guards + seam-B removal; ships **unconditionally** byte-identical to Plan 1a's base)?
> Prerequisites: Plan 01 delivered (confirmed) **and Plan 1a delivered** (`2026-06-20-004932-model-profile-1a-rule-tool-partition` — the DAG root shifted to 1a; 1b freezes 1a's cleaned base). Do NOT start 1b before 1a ships.
> Then Plan 02 (gemini) and Plan 03 (ollama) build on 1b. Once 1a is delivered and this is approved, run: `/orchestrate-dev 2026-06-20-001146-model-profile-1b-overlay-mechanism`.
>
> _Note: the Decisions table below is the historical converged-critique log from when this plan was "1.5"; CD-m-3 is marked RETIRED to record G1's relocation of the output-format floor to Plan 1a. Other `1.5`/`TASK-3` references in that table are preserved as the record of what was decided then._

## Delivery Summary — 2026-06-21

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | overlay builder generalized + reordered after base; `build_rules_block()` no-arg + `:169` call-site reverted; seam B (`_FRONTIER_EXCLUDED_SECTIONS`/`_drop_sections`) deleted; byte-identity to base; functional per-profile overlay guard; zero grep refs; full suite | ✓ pass |
| TASK-2 | `_build_arm_agent` composes base + `overlay(resolve_model_profile(deps.config.llm))`; `_all_sections(profile)`/`_rules_block_drop_section(target, profile)` overlay-aware; `==25` literal removed → `len(_INVENTORY)==len(sections)`; inventory carries home; floor guards append `overlay(weak_local)` at the 25k-char ceiling; `--inventory` green; overlay-resident ablation guard | ✓ pass |

**Mechanism.** `build_rules_block()` reverted to no-arg profile-agnostic base. New `build_profile_overlay(profile)` reads `overlays/<profile>.md` (absent/empty → `None`) — append-only by construction (reads a profile's own file, never touches base, so removal is structurally inexpressible). `_model_profile_overlay_provider` now resolves the profile and returns its overlay, reordered to immediately follow `_base_instructions_provider` in `ORCHESTRATOR_SPEC`. Both overlays ship empty, so the assembled prefix is byte-identical to Plan 1a's base.

**Harness.** `Section` gained a `home` field (base vs overlay); home is sourced authoritatively from the parsed section (single source of truth — not duplicated into the `_INVENTORY` literal, avoiding drift) and carried into the emitted inventory record + `--inventory` table. `_eval_profile()` resolves the configured backend's profile deps-free for `--inventory`.

**Floor guards.** Both append `build_profile_overlay(WEAK_LOCAL)` (the worst-case profile) to the measured floor; `INSTRUCTION_BLOCK_CEILING = 25_000` chars unchanged (overlays empty → floor unchanged, guard now measures the right surface).

**Tests:** scoped — 19 passed; full suite — 805 passed. Grep for `_FRONTIER_EXCLUDED_SECTIONS`/`_drop_sections` → zero. `--inventory` green.
**Doc Sync:** narrow — `docs/specs/prompt-assembly.md` §2.1 updated (four→five builders, overlay provider documented as #2, append-only semantics; replaced a dangling anchor with the resolution rule). No shared-schema change warranting full sync.

**Extra file:** `tests/test_rule_compliance_overlay_ablation.py` — TASK-2 done_when requires a deterministic assertion that overlay-resident ablation works; evals are real-data smoke runs (no monkeypatch/tmp), so this guard belongs in pytest.

**Overall: DELIVERED**
Append-only base + dual-overlay mechanism landed byte-identical to Plan 1a's base; harness + floor guards are overlay-aware; subtractive seam B removed. Plans 02 (gemini) and 03 (ollama) can now add overlay content against a verified, measurable seam.

## Implementation Review — 2026-06-21

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | overlay builder generalized + reordered; `build_rules_block()` no-arg + call-site reverted; seam B deleted; byte-identity; functional per-profile guard; zero grep refs | ✓ pass | `orchestrator.py:53-64` provider → `build_profile_overlay`; `orchestrator.py:81-82` overlay at base-index+1; `assembly.py:70` no-arg `build_rules_block()`, `:149` no-arg call-site; `assembly.py:100-104` append-only by construction (reads only `_OVERLAYS_DIR/<profile>.md`); grep `_FRONTIER_EXCLUDED_SECTIONS`/`_drop_sections` → zero; `test_flow_model_profile.py:82-99` byte-identity; `test_profile_rules_composition.py:57-73` functional present/absent |
| TASK-2 | `_build_arm_agent` composes base+overlay via prod builder; `_all_sections(profile)`/`_rules_block_drop_section(target,profile)` overlay-aware; `==25` literal removed; inventory carries home; floor guards append `overlay(weak_local)` at 25k chars; `--inventory` green; overlay-resident ablation guard | ✓ pass | `eval_rule_compliance.py:493-494` resolves profile → `_full_block`; `:158-172`/`:200-223` base-or-overlay ablation; `:673-675` `len(_INVENTORY)==len(sections)` (no `==25`); `home` from `Section.home` (`:653`); `test_instruction_budget.py:55,71,76`; `test_rule_compliance_overlay_ablation.py:41-45` |

### Cross-review (Plans 02 & 03)
The seam satisfies both downstream plans: single-file `overlays/frontier.md`/`overlays/weak_local.md` slots (`build_profile_overlay` keys on `profile.value`); `_build_arm_agent` resolves the live backend's profile so Plan 02 ablates base sections on the gemini path; `_full_block(profile)`/`_all_sections(profile)`/`_rules_block_drop_section(target, profile)` are profile-parameterized (not deps-locked), so Plan 03's relocation join can assert a moved reflex is absent from `_full_block(FRONTIER)` and present in `_full_block(WEAK_LOCAL)` (CD-m-4 gemini-absence guard); the `len(_INVENTORY)==len(sections)` count guard forces an inventory row whenever 02/03 add an overlay section; floor guards measure base+overlay. No conflicts found.

### Issues Found & Fixed
No issues found. Both per-task evidence subagents returned PASS with file:line evidence. The deliberate decision to source each section's `home` from the parsed `Section.home` (single source of truth) rather than duplicating it into the `_INVENTORY` literal was reviewed and endorsed as superior to the literal done_when wording (avoids literal-vs-file drift; the count guard already forces inventory rows for overlay sections).

### Tests
- Command: `uv run pytest -q -p no:randomly`
- Result: 805 passed, 0 failed
- Log: `.pytest-logs/<ts>-review-impl.log`
- `uv run python evals/eval_rule_compliance.py --inventory`: green (25 sections, `home` column populated)

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads; all subcommands listed)
- Overlay mechanism is static-prompt/LLM-mediated; both overlays ship empty so the default prompt is byte-identical (no user-observable change). Verified via `--inventory` (overlay-aware) + byte-identity/functional tests — chat interaction non-gating.
- `success_signal` (TASK-1: prompt = base + per-profile overlay, append-only, default unchanged, no subtractive code): verified via tests + zero-ref grep. (TASK-2: harness measures base+overlay per profile, floor guards bound worst case): verified via `--inventory` + floor guard tests.

### Overall: PASS
Append-only dual-overlay seam, overlay-aware harness + floor guards, and seam-B removal all land byte-identical to Plan 1a's base; full suite green, lint clean, downstream Plans 02/03 contracts satisfied. Ready to ship.
