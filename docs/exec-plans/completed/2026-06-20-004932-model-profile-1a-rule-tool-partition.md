# Model-profile 1a — Rule↔tool-surface partition correction + base content cleanup (profile-agnostic)

Task type: profile-agnostic prompt-content/structure work on the BASE rule layer. Correct the partition between the always-injected general rules (`co_cli/context/rules/`) and the per-tool descriptions/schemas (`co_cli/tools/`), consolidate the cross-tool persistence/completion duplication, and add the base output-format floor. Ships a leaner, intersection-shaped base for Plan 1b to make composable. Plan 1a of the model-profile group (sits between shipped Plan 01 and the mechanism Plan 1b).

## Architecture (user-decided — not open for re-litigation)
```
PROMPT(profile) = BASE + OVERLAY(profile)
```
1a only touches **BASE** content. It does not build the overlay mechanism (that is 1b) and it does not author any per-profile content (02/03). The goal is to make BASE a clean cross-tool intersection so 1b can freeze it byte-identically and 02/03 can add profile content against it.

## Plan group (model-profile) — DAG: `01 → 1a → 1b → { 02, 03 }`
- **01** (`2026-06-19-114937-model-profile-01-seam`) — `ModelProfile` resolver + per-profile budget (shipped). **Prerequisite.**
- **1a (this plan)** — base content/partition cleanup. **Prerequisite for 1b.** Profile-agnostic, measurement-gated, in-place edits to `rules/`.
- **1b** (`2026-06-20-001146-model-profile-1b-overlay-mechanism`) — the append-only overlay mechanism + overlay-aware harness/floor guards + seam-B removal. Depends on 1a; freezes 1a's cleaned base byte-identically (the G1 output-format floor moves OUT of 1b into 1a TASK-4, so 1b becomes a pure inert refactor with an unconditional byte-identity guarantee — no "except the new section" carve-out, CD-m-3 retired).
- **02** (frontier overlay content) / **03** (ollama overlay content) — depend on 1b. **03 additionally absorbs the Bucket-B relocations 1a defers** (see Scope/Defer): rule mechanics that a weak model relies on but a frontier model does not, relocated from BASE into `overlays/weak_local.md`.

## Context

### Why a partition correction (peer-grounded)
A full review of the 28 base rule sections against every tool's actual description/schema (`docs/reference/RESEARCH-behavioral-rules-peer-survey.md` + a fresh 2026-06-20 tool-surface read) found a large slice of `04`/`06`/`07` is **the same operational mechanics at the same altitude in both the always-injected rule AND the tool description** — accidental duplication, and in one case a contradiction.

Peer best practice (4-peer survey, this session):
- **codex** — strict single-source: tool desc = *what it is* + arg/format constraints; system prompt = *when/why/prefer/recover*. No duplication.
- **hermes** — altitude split (strategy in prompt, mechanics in schema) **plus tool-gated injection**: prompt guidance is injected only when its tool is loaded (`if "memory" in valid_tool_names: inject MEMORY_GUIDANCE`).
- **opencode** — deliberate duplication for weak-model robustness; tool descs short and self-contained.
- **openclaw** — prompt-centric sections; provider overlays *replace* `tool_call_style`/`execution_bias` per model.

co is in none of these positions: same content, same altitude, in both places, and (unlike hermes) the rule copy is NOT tool-gated — it injects every turn even when the tool is deferred or absent. The target is the codex/hermes altitude split: cross-tool *strategy/trigger* stays in rules; tool *mechanics* ride the tool description (which co already loads only when the tool is visible). The opencode lesson + `feedback_tool_split_small_model` is the guardrail: some duplication is deliberate weak-local reinforcement, so it RELOCATES to the weak_local overlay (Plan 03), it is not deleted — which is why 1a is measurement-gated, not a free deletion pass.

### Current-state findings (verified 2026-06-20, with source)
- **Tool visibilities:** `tool_view`, `shell_exec`, `memory_search`, `session_search` are all `VisibilityPolicyEnum.ALWAYS` (present every turn). `deferred_tool_awareness_prompt` is a per-turn instruction (`orchestrator.py:87`). So a rule duplicating an ALWAYS-visible tool's description loses no information when removed.
- **Contradiction — `04 ## Paths`.** The rule says "Construct **absolute** paths for all file operations… never rely on cwd." The native file tools are workspace-relative-primary: `file_read`/`file_write`/`file_patch` take paths "relative to the workspace root" and `file_search`/`shell_exec` take a relative `work_dir`. A model obeying the rule literally fights the tool contract. (`co_cli/context/rules/04_tool_protocol.md` `## Paths` vs `co_cli/tools/files/read.py`, `write.py`, `read.py::file_search`.)
- **Triple-redundant — `04 ## Deferred tools`.** The whole section restates `tool_view`'s description (ALWAYS-visible) AND the per-turn `deferred_tool_awareness_prompt`. Three copies of the same mechanics.
- **Subset duplication — the dedicated-tool-over-shell list in `04 ## Strategy`.** `shell_exec`'s description carries the canonical, more complete "Do not use shell for: file_read instead of cat… file_search instead of grep…" list; the rule's version is a strict subset.
- **Cross-tool duplication cluster (orphaned survey C2).** The persistence/completion idea is restated ≥4× across `01 ## Thoroughness over speed`, `04 ## Strategy` (Follow through), `04 ## Execute, don't promise`, `05 ## Execution`. The survey flagged this as the strongest consolidation target, deferred to "its own Gate-1 plan; GATE = whole-assembly re-ablation" — a plan never created. `05 ## Completeness`'s validation checklist is genuinely unique (KEEP).
- **Orphaned output-format gap (survey G1).** No base `##` section governs final-answer shape; 4/4 peers instruct it (codex `gpt_5_2_prompt.md:160-242` heavy). The completed `behavioral-rules-audit` + `behavioral-rules-consolidation-cleanup` plans landed the cuts (C1/C3/C5/C6) but never the G1 gap-fill — it is now unowned. 1a adopts it (relocated from the 1b draft's TASK-3).
- **Weak-local-sensitive duplications (Bucket B — DEFERRED to Plan 03, not touched here).** `07 ## Recall` cascade (near-verbatim in the ALWAYS-visible `session_search` desc), `06 ## Create`/`## Drift` mechanics (in `skill_create`/`skill_patch` descs), `05 ## Completeness` todo mechanics (in `todo_write`/`todo_read`). These are ALWAYS-present in their tool desc too, but recall is the reported struggle area and small models under-read descriptions — removing the rule copy risks a weak-local regression. The correct move is relocate-to-overlay, which requires 1b's mechanism + a harness measurement → Plan 03.

## Problem & Outcome
**Problem.** co's base rule layer duplicates tool mechanics at the same altitude as the tool descriptions, injects them unconditionally even when the tool is absent, contradicts the file tools on path construction, restates one cross-tool idea ≥4×, and lacks the output-format floor all peers carry. The result is a bloated, partly-wrong, non-intersection base that 1b would otherwise freeze as-is.

**Failure cost.** Without 1a: 1b freezes a base that is known mis-partitioned and self-contradictory; the always-injected floor carries dead duplicate tokens against `INSTRUCTION_BLOCK_CEILING`; the `04 Paths` contradiction keeps emitting conflicting signals on every profile; and co keeps the 4/4-peer output-format gap.

**Outcome.** A leaner, contradiction-free, intersection-shaped BASE: the `04 Paths` contradiction resolved against the real tool contract, the triple/subset duplications of ALWAYS-visible tools removed, the persistence/completion cluster consolidated to one cross-tool section (validation checklist preserved), and the base output-format floor added — all verified by the rule-compliance harness on the weak-local arm so no removal silently drops a behavior the weak model relied on.

**Shippable contract:** partition corrections (TASK-1/2) + C2 consolidation (TASK-3) + base output-format floor (TASK-4), with the rule-compliance `--inventory` updated, the floor budget guard green, the weak-local rule-compliance arm showing no compliance regression on any removed/changed behavior, and the full suite green. Any removal whose behavior the weak arm shows it relied on is NOT deleted — it is recorded as a Bucket-B relocation candidate for Plan 03.

## Behavioral Constraints
- Rule prose = **core-level review** (platform core; `souls/`+rules are built-in platform core, intrinsic agent traits). No `tool_name(` call syntax in rule prose (per `.agent_docs/rule-authoring-standard.md` + the F5 floor guard). Preserve surviving `##` heading text verbatim (the harness `_INVENTORY` is keyed `(stem, title)`). Run `--inventory` after any base change.
- **Gate proportional to risk (not a uniform live-eval pass).** Live weak-local rule-compliance measurement is reserved for **TASK-3** (the C2 consolidation genuinely rewrites behavioral prose — the survey's "whole-assembly re-ablation" gate). **TASK-1/2 do NOT carry a live gate**: TASK-1 removes a *factually wrong* instruction (a contradiction, no behavior to preserve), and TASK-2 deletes content with ≥2 surviving always-on copies (no behavior to lose) — both are justified by the logical argument + the full pytest suite, matching how peers curate single-source by convention rather than by harness. The weak-local-reinforcement risk (`feedback_tool_split_small_model`, `feedback_recall_fix_must_generalize`) bites on Bucket B, which 1a defers to Plan 03; see the visibility-keyed test in HLD for which case each edit is.
- The base output-format floor is **review-gated output hygiene**, NOT a fire-rate reflex — justification is 4/4 peer parity (survey G1), not measured lift. It adds bounded base chars; net section change must keep the assembled floor under `INSTRUCTION_BLOCK_CEILING = 25_000` chars (`tests/test_instruction_budget.py:52,72`). 1a is net-negative on base size before the floor (deletions + consolidation), so headroom should improve.
- **Profile-agnostic only.** 1a edits BASE in place for ALL profiles. No overlay files, no `ModelProfile` branching, no per-profile content — those are 1b/02/03. The harness is base-only at 1a time (1b makes it overlay-aware); 1a only updates the section-count literal + `_INVENTORY`.
- **Do NOT touch Bucket B or C7.** `07 Recall` cascade, `06`/`05` tool-mechanics, and the `07` Curation/Anti-patterns simplification are out of scope (Plan 03 / separate measurement-gated pass).

## High-Level Design
**Partition rule applied (codex/hermes altitude split).** What stays in BASE: cross-tool behavioral invariants and the trigger to engage a capability before any tool is loaded. What leaves BASE: mechanics of operating a specific tool, when its ALWAYS-visible description already carries them. What relocates later (03): mechanics a weak model relies on despite the description.

**Visibility-keyed redundancy (the precise test for what is safe to remove).** Whether a rule duplicates the tool surface depends on the tool's *visibility*, verified against `co_cli/tools/deferred_prompt.py`: ALWAYS tools carry their **full** description in the prompt every turn; DEFERRED tools carry only a **name + one-line stub** (`build_deferred_tool_awareness_prompt`, ≤100 chars), with full mechanics absent until `tool_view` loads them. This yields three cases:
- **Rule mechanics of an ALWAYS tool** (e.g. `04 ## Deferred tools` ↔ `tool_view`; the shell-vs-dedicated list ↔ `shell_exec`; `07 ## Recall` cascade ↔ `session_search` — all ALWAYS) → the full mechanics are already in-prompt every turn → **fully redundant, delete** (still measure when weak-sensitive, e.g. recall).
- **Rule *trigger* for a DEFERRED tool** (e.g. `06 ## Create`/`## Drift` ↔ `skill_create`/`skill_patch`; profile-save routing ↔ `user_profile_write` — all DEFERRED) → the one-line stub does **not** teach *when* to engage, so the trigger has no other always-on home → **keep in BASE** (lean). Deleting it means the weak model never reaches for the deferred capability.
- **Rule *mechanics* of a DEFERRED tool** (args, §6 conformance, merge-rewrite) → present only after `tool_view` loads the tool, so redundant post-load — but a weak model may not read even the loaded desc → **Bucket B relocation candidate (Plan 03)**, not a 1a deletion.

So the operative test is sharper than the altitude split alone: **delete rule mechanics an ALWAYS tool already carries; keep rule triggers for DEFERRED tools; relocate (don't delete) mechanics a weak model relies on.** This is what makes TASK-2's deletions safe (ALWAYS tools) while TASK-3 and the deferred triggers stay in BASE.

Skills are the **third progressive-disclosure surface** under the same rule: the always-present `<available_skills>` manifest (`co_cli/skills/manifest.py::render_skill_manifest`) carries only each skill's name + description (the skills-equivalent of the deferred-tool stub), with the full `SKILL.md` body loaded on demand via the ALWAYS-visible `skill_view` — so `06_skill_protocol.md`'s *engagement triggers* (Discovery scan, Create-promotion reflex, Drift-fix reflex, Use) **stay in BASE** because neither the manifest line nor the DEFERRED `skill_create`/`skill_patch` stubs teach *when* to engage, while their *mechanics* (read-before-edit, already in the ALWAYS `skill_view` desc; §6 conformance + offer-to-save, in the DEFERRED create/patch descs) are removable or Bucket-B-relocatable on the same visibility test.

**TASK-1 — `04 Paths` contradiction.** Resolve against the real tool contract. The native file tools own path semantics (workspace-relative, with absolute permitted under a configured root); the rule's "absolute for all file ops" is both redundant with and contradictory to that. Default: delete the `## Paths` section (tools own it); fallback if the weak arm regresses on path handling: rewrite it to *match* the workspace-relative contract rather than mandate absolute.

**TASK-2 — Remove duplications of ALWAYS-visible tools.** Delete `04 ## Deferred tools` (triple-redundant with `tool_view` desc + per-turn deferred prompt) and the dedicated-tool-over-shell list inside `04 ## Strategy` (subset of `shell_exec`'s canonical list). These carry zero unique cross-tool content; the always-present tool surface is the single source.

**TASK-3 — Consolidate the persistence/completion cluster (survey C2).** Merge the ≥4 overlapping spans into ONE cross-tool section (one home; candidate `05` or `04`), preserving `05 ## Completeness`'s unique validation checklist. This is rule-layer cross-tool content, not a tool mechanic — it stays in BASE, just de-duplicated.

**TASK-4 — Base output-format floor (survey G1).** A new BASE `## Output format` section (headers / bullets / monospace for code+identifiers / `file:line` refs / no fabricated citations), all profiles, in the single best-fit existing rule file. Cite survey G1 + the completed audit plan as justification; no behavioral/fire-rate claim.

**Harness + budget.** 1a changes the base section count (deletions + consolidation − ; new floor +). Update the `eval_rule_compliance.py:646` count literal + `_INVENTORY` to the new base count, and assert `build_base_instructions(config)` stays under `INSTRUCTION_BLOCK_CEILING`. (1b later re-pins this baseline and makes the harness overlay-aware; 1a leaves it base-only.)

## Tasks

✓ DONE **TASK-1 — Resolve the `04 ## Paths` contradiction**
- files: `co_cli/context/rules/04_tool_protocol.md`, `evals/eval_rule_compliance.py`
- done_when: the `## Paths` section no longer mandates absolute paths in contradiction to the file tools — either deleted (tools own path semantics) or rewritten to match the workspace-relative-primary contract of `file_read`/`file_write`/`file_patch`/`file_search`; no live gate (this removes a factually wrong instruction, not behavior to preserve); `_INVENTORY` + the section-count literal updated; `--inventory` passes; full suite passes.
- success_signal: the rule no longer conflicts with the tool contract on path construction.
- prerequisites: Plan 01 delivered

✓ DONE **TASK-2 — Remove rule mechanics duplicated by ALWAYS-visible tools**
- files: `co_cli/context/rules/04_tool_protocol.md`, `evals/eval_rule_compliance.py`
- done_when: `04 ## Deferred tools` deleted (its content is triply present in `tool_view`'s ALWAYS-visible description and the per-turn `deferred_tool_awareness_prompt` — ≥2 always-on copies survive, no behavior lost); the dedicated-tool-over-shell enumeration inside `04 ## Strategy` removed (strict subset of `shell_exec`'s canonical "Do not use shell for…" list, ALWAYS-visible), leaving any genuinely cross-tool strategy in `## Strategy` intact; no live gate (deletions leave surviving always-on copies); `_INVENTORY` + count literal updated; `--inventory` + full suite pass.
- success_signal: the always-injected floor no longer carries duplicate tool mechanics; the surviving always-on copies are the single source.
- prerequisites: TASK-1

✓ DONE **TASK-3 — Consolidate the persistence/completion cluster (orphaned survey C2)**
- files: `co_cli/context/rules/01_interaction.md`, `co_cli/context/rules/04_tool_protocol.md`, `co_cli/context/rules/05_workflow.md`, `evals/eval_rule_compliance.py`
- done_when: the ≥4 overlapping persistence/completion spans (`01 ## Thoroughness over speed`, `04 ## Strategy` Follow-through, `04 ## Execute, don't promise`, `05 ## Execution`) consolidated into ONE cross-tool section with a single home, with **`05 ## Completeness`'s validation-pass checklist preserved verbatim** (it is unique, not redundant); a weak-local rule-compliance arm confirms persistence/completion behavior is not weakened by the consolidation (the survey's "whole-assembly re-ablation" gate); `_INVENTORY` + count literal updated; `--inventory` + full suite pass; the deferred standalone-C2 intent is recorded as superseded by this task.
- success_signal: one cross-tool persistence section instead of ≥4 scattered restatements; behavior preserved on the weak arm.
- prerequisites: TASK-2

✓ DONE **TASK-4 — Base output-format floor (orphaned survey G1; review-gated, all profiles)**
- files: `co_cli/context/rules/` (a `## Output format` section in the single best-fit file — likely `01_interaction.md` or `04_tool_protocol.md`; no new `NN_` file unless ordering demands it), `evals/eval_rule_compliance.py`, `tests/test_instruction_budget.py`
- done_when: a concise `## Output format` BASE section instructs the unconditional formatting floor (section headers, bullets, monospace/backticks for code and identifiers, `file:line` reference syntax, no fabricated citations), **~6–10 lines, no verbosity/compactness-tier machinery** (match opencode's lean `anthropic.txt:14` end, NOT codex's heavy `gpt_5_2_prompt.md:160-242` tiers), authored per `.agent_docs/rule-authoring-standard.md` (no `tool_name(`; no fire-rate claim — justified by survey G1 / 4-of-4 peers, citing the completed `behavioral-rules-audit` plan); it is BASE (all profiles); the assembled `build_base_instructions(config)` stays under `INSTRUCTION_BLOCK_CEILING = 25_000` chars with the net char delta recorded (1a should be net-negative overall after TASK-1/2/3 deletions); `_INVENTORY` + base section count updated; `--inventory` + floor guards + full suite pass; core-level review recorded.
- success_signal: co gains the base output-format floor all peers carry, on every profile, with no unmeasured behavioral claim, and the net base size is smaller than before 1a.
- prerequisites: TASK-3

## Testing
- All pytest, fail-fast (`-x`), piped to a timestamped `.pytest-logs/` file; tail the log to watch LLM-call timing live (per testing.md policy).
- Rule-compliance harness: `uv run python evals/eval_rule_compliance.py --inventory` after every base change (count + `_INVENTORY` must agree) — for ALL tasks (static check, no live model). The **live weak-local arm runs for TASK-3 only** — ablate the consolidated persistence/completion section and confirm the behavior still fires on the configured local model (warm model only, per call-timeout policy); a regression there means the consolidation lost steer, not a relocation. TASK-1/2 are validated by logical argument + the full suite (no live arm — see Behavioral Constraints).
- Floor budget guard (`test_instruction_budget`) at the 25k-char `INSTRUCTION_BLOCK_CEILING`; record the net char delta (expected negative pre-floor, modestly positive after TASK-4 but still net-negative vs the pre-1a base).
- No overlay/byte-identity tests here — those are 1b. No per-profile live runs — those are 02/03.

## Open Questions
1. **`04 Paths` — delete vs rewrite.** Default delete (tools own path semantics). Settle at TASK-1 by the weak-local arm: if path handling regresses on deletion, rewrite to the workspace-relative contract instead.
2. **C2 consolidation home.** `05` (workflow/completeness) vs `04` (tool protocol). Default: fold into `05` near `## Completeness` so persistence + completion sit together. Settle at TASK-3 by topical fit.
3. **Output-format floor home.** `01_interaction.md` vs `04_tool_protocol.md` vs a new `08_output.md`. Default: fold into an existing file to avoid renumbering; settle at TASK-4 by best topical fit.

## Notes for downstream plans
- **1b:** with G1 now in 1a TASK-4, 1b's TASK-3 (base output-format floor) is removed and its byte-identity guarantee becomes unconditional (no "except the new section", CD-m-3 retired). 1b captures its baseline from 1a's *cleaned* base.
- **Plan 03:** inherits the Bucket-B relocation candidate set (any TASK-1/2/3 span the weak arm showed is load-bearing) plus the standing Bucket-B list — `07 ## Recall` cascade, `06 ## Create`/`## Drift` mechanics, `05 ## Completeness` todo mechanics — to relocate from BASE into `overlays/weak_local.md` once 1b's mechanism exists. C7 (`07` Curation/Anti-patterns simplification) remains its own separate measurement-gated pass, not folded into 03.

## Gate 1 — PO + TL review required before `/orchestrate-plan` critique or `/orchestrate-dev`
> Right problem (make BASE a clean cross-tool intersection — correct the rule↔tool-description partition, kill the `04 Paths` contradiction, consolidate the orphaned C2 cluster, add the orphaned G1 floor — so 1b freezes a correct base and 02/03 build on it)?
> Correct scope (BASE-only, profile-agnostic, measurement-gated; partition corrections + C2 + G1; Bucket-B relocations and C7 explicitly deferred)?
> Prerequisites: Plan 01 delivered (confirmed). **This plan is now the prerequisite for 1b** — the model-profile DAG root shifts from 1b to 1a.
> Source-grounded, peer-checked, and right-sized in drafting (claims verified against `assembly.py`/`orchestrator.py`/`deferred_prompt.py`/`manifest.py`; scope checked against the 4-peer survey; the disproportionate live-eval gate already trimmed to TASK-3 only). The `/orchestrate-plan` critique is not required — go straight to Gate 1.

## Delivery Summary — 2026-06-20

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `04 ## Paths` no longer mandates absolute paths in contradiction to the file tools | ✓ pass — section **deleted** (not rewritten) |
| TASK-2 | `04 ## Deferred tools` deleted; shell-vs-dedicated echo removed; `## Strategy` intact | ✓ pass |
| TASK-3 | persistence/completion cluster consolidated to one home; `05 ## Completeness` checklist preserved | ✓ pass (lossless-content basis; see note) |
| TASK-4 | concise base `## Output format` section added, all profiles, under budget | ✓ pass |

**Implementation notes (two plan inaccuracies corrected in dev):**
- **TASK-1 → delete, not rewrite.** The ALWAYS-visible file tools already carry path semantics (`write.py:286` "relative to the workspace root", `read.py:411` "relative … or absolute under a configured root"). A rewrite would re-introduce the exact redundancy TASK-2 removes, so the partition-consistent move is deletion. Open Question 1 settled: **delete**.
- **TASK-2 → "Strategy shell-list" did not exist.** The plan's "dedicated-tool-over-shell enumeration inside `04 ## Strategy`" is not present; the canonical list lives only in `shell_exec`'s description (`execute.py:31-33`), and the rule's sole echo was a one-line clause *inside* `## Deferred tools`, removed with that section. `## Strategy` was left untouched.
- **TASK-3 home (Open Q2 settled):** `05 ## Execution` (keeps its PROBED `todo_write` key, sits beside `## Completeness`). Folded in `01 ## Thoroughness over speed`, `04 ## Execute, don't promise`, and the `04 ## Strategy` "Follow through" bullet. Deleted the two now-empty `##` sections. Every distinct idea preserved (decompose+execute-now · act-in-same-response/no-intent-turns · thoroughness · don't-stop-while-more-helps/partial-is-failure · blocked-detection · stop-after-final). `05 ## Completeness` validation checklist untouched.
- **TASK-4 home (Open Q3 settled):** `01_interaction.md` (response-shape fits interaction; no renumbering). 6-line lean section; no `tool_name(` syntax; no fire-rate claim.

**Harness:** base section count `28 → 25` (−Paths −Deferred-tools −Thoroughness −Execute-don't-promise +Output-format); `_INVENTORY` updated, `Deferred tools` probe + now-orphaned `_TOOL_VIEW` constant removed; docstring `28→25`. `--inventory` green (parser self-test, span uniqueness, reassembly).

**TASK-3 live-arm RCA (gate substituted with PO+TL approval):** the prescribed weak-local re-ablation arm is structurally uninformative here — (1) each "sample" is a full multi-turn agentic episode (~30-60s × 40), and (2) the only probe-able facet (`todo_write` decompose) is floor-pinned on the configured model (full arm 2 True / 17 False ≈ 0.11, below `STEER_DELTA = 0.5` → verdict predetermined non-steering), while the consolidated persistence behavior is multi-turn / OUT-OF-REACH by the harness's own classification. Killed the run after RCA (no timeout widened, per policy). PO+TL approved gating TASK-3 on the **lossless-content audit + static harness + budget** instead — same basis as TASK-1/2. This is a plan-accuracy finding for Gate 2.

**Budget:** assembled rules block 17,185 chars (< `INSTRUCTION_BLOCK_CEILING = 25_000`); net **−504 chars** vs pre-1a base (net-negative after the new floor, as predicted).

**Tests:** scoped — 11 passed, 0 failed (`test_instruction_budget`, `test_instruction_floor_coupling` [F5 guard green], `test_personality_disabled`, `test_profile_rules_composition`). Lint clean.
**Doc Sync:** clean — no spec enumerates the changed rule sections (spec hits are the deferred-prompt *mechanism* + unrelated "Paths" anchors).

**Overall: DELIVERED**
All four tasks pass. TASK-3's live gate was found unsatisfiable in dev and substituted with the lossless-content basis under PO+TL approval — flag for Gate 2.

## Implementation Review — 2026-06-20

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `04 ## Paths` no longer contradicts the file tools | ✓ pass | `04_tool_protocol.md` — `## Paths` section deleted; tools own path semantics (`write.py` "relative to the workspace root", `read.py` "relative … or absolute under a configured root"). |
| TASK-2 | `04 ## Deferred tools` deleted; shell echo removed; `## Strategy` intact | ✓ pass | `04_tool_protocol.md` — `## Deferred tools` removed (was triply present in `tool_view` ALWAYS desc + per-turn `deferred_tool_awareness_prompt`); the one-line shell echo lived inside that section and went with it; `## Strategy` parallelism/sequencing text untouched. |
| TASK-3 | persistence/completion consolidated to one home; `05 ## Completeness` checklist preserved | ✓ pass | Consolidated into `05 ## Execution`. Lossless-content audit confirmed: decompose-then-execute, act-in-same-response, no-intent-turns, thoroughness, don't-stop-while-helpful, partial-is-failure, blocked-detection, stop-after-final all preserved. Original `05` "did this move closer to the goal?" folded into "evaluate progress." `## Completeness` validation checklist verbatim. `01 ## Thoroughness over speed`, `04 ## Execute, don't promise` deleted (now empty). |
| TASK-4 | concise base `## Output format` section, all profiles, under budget | ✓ pass | `01_interaction.md` `## Output format` — 6 lines (headers/bullets/backticks/`file:line`/no-fabricated-citations); no `tool_name(` syntax; no fire-rate claim. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Scope creep: ollama-warm gating + `eval_agent_uses_ollama` helper bundled into 1a's declared `eval_rule_compliance.py` | `eval_rule_compliance.py:64,648` + `evals/_settings.py:113` | minor (out-of-scope, not a defect) | NOT auto-fixed — see scope note below; ship must isolate 1a's hunks |
| Working tree mixes 1a with ≥3 other in-flight plans (config/llm.py, assembly.py, eval_skills/user_model W4.R/W4.M cases, dream.md, RESEARCH, 02/03 plan files, ship/SKILL.md, uv.lock) | working tree | minor (cross-plan contamination) | Flagged for ship-time staging discipline |

**Scope note (the one finding that matters for ship).** 1a's *substance* is clean, but the working tree is a shared multi-plan workspace. `eval_rule_compliance.py` (a 1a-declared file) carries two concerns: (a) **1a-scope** — docstring `28→25`, `_INVENTORY` updates, `Deferred tools` probe + `_TOOL_VIEW` removal, count assertion `28→25`; (b) **out-of-scope** — a gemini-frontier-path enablement (gate `ensure_ollama_warm()` on `eval_agent_uses_ollama(deps)`, new helper in `_settings.py`, backend prints in `_deps.py`). The helper is used only here; `eval_skills.py`/`eval_user_model.py` still warm unconditionally (their diffs are unrelated new W4.R/W4.M cases). The frontier-path enablement is Plan 02 territory, not 1a. Not reverted here — it is deliberate in-flight work, not a defect. **`/ship` must `git add -p` to stage only 1a's hunks** (the three rule `.md` files + the inventory/count/probe hunks of `eval_rule_compliance.py`), leaving the warm-gating, `_settings.py`/`_deps.py`, and all other plans' files out of the 1a commit.

### Harness & Budget
- `--inventory`: ✓ green — 25 sections, parser self-test (count/uniqueness/clean reassembly) passes; PROBED 5 / OBSERVABLE-OUT-OF-HARNESS 4 / OUT-OF-REACH 16.
- Budget: assembled rules block well under `INSTRUCTION_BLOCK_CEILING = 25_000` (`test_instruction_floor_within_budget` green); net-negative vs pre-1a base as predicted.

### Tests
- Command: `uv run pytest tests/test_instruction_budget.py tests/test_instruction_floor_coupling.py tests/test_profile_rules_composition.py tests/test_personality_disabled.py -v`
- Result: 11 passed, 0 failed (F5 no-deferred-tool-signature floor guard green; budget guard green; composition + personality green).
- Log: `.pytest-logs/<ts>-review-impl-1a.log`
- Full-suite run **deferred to ship**: the working tree mixes ≥3 other plans, so a whole-suite verdict here would conflate 1a with unrelated in-flight work. 1a's surface is prose-only rule files + a static inventory (no runtime code path) — the scoped floor/composition/personality tests are its complete behavioral surface. Ship runs the full suite against the isolated 1a commit.
- Lint: `scripts/quality-gate.sh lint` ✓ (393 files formatted, all checks pass).

### Behavioral Verification
- `uv run co --help`: ✓ boots — import + bootstrap graph loads, so the cleaned base rules parse and assemble into the static prompt.
- `success_signal`s: TASK-1 (rule no longer conflicts with tool contract) ✓ via deletion; TASK-4 (base output-format floor on every profile) ✓ via `--inventory` showing the `Output format` section present in the assembled base. No LLM-mediated chat turn gated (rule-prose effect on model behavior is OUT-OF-REACH per the harness's own classification; TASK-3 live arm unsatisfiable per delivery RCA).

### Cross-review with Plan 1b (working-tree ownership)
Cross-checked the flagged "creep" against `2026-06-20-001146-model-profile-1b-overlay-mechanism` (NOT yet delivered — no `✓ DONE` marks; its Gate 1 says *"Do NOT start 1b before 1a ships"*). The dirty files split into **three** ownership buckets, not one:

- **Bucket A — 1a (commit with this plan):** `rules/01_interaction.md`, `rules/04_tool_protocol.md`, `rules/05_workflow.md`, and the inventory/count/probe hunks of `eval_rule_compliance.py`.
- **Bucket B — superseded "seam B" (1b TASK-1 will DELETE; must NOT ride 1a):** `co_cli/context/assembly.py` carries the *subtractive* seam — `_FRONTIER_EXCLUDED_SECTIONS` + `_drop_sections` + `build_rules_block(profile)`, with `build_base_instructions` calling `build_rules_block(resolve_model_profile(config.llm))`. `tests/test_profile_rules_composition.py` (untracked) is its test. This is the "delivered-then-superseded" code 1b Context §24-25 charters 1b to remove (CD-m-2: revert `build_rules_block()` to no-arg + delete the exclusion machinery). It is the *input* 1b consumes, owned by neither 1a (base-only, never touches `assembly.py`) nor a clean 1b — 1b's job is to delete it. **Leftover from the earlier Plan 02 draft, not premature 1b work** (1b genuinely hasn't started).
- **Bucket C — other plans:** the `eval_rule_compliance.py` warm-gating + `eval_agent_uses_ollama`/`_deps.py` prints, `config/llm.py` (drops `gemini-2.5-flash-lite` settings), `eval_skills.py`/`eval_user_model.py` (W4.R/W4.M cases), `02`/`03` plan drafts, `dream.md`, RESEARCH, `ship/SKILL.md`, `uv.lock`.

**Does seam B invalidate 1a's verdict? No.** Seam B is uncommitted and live, so production `build_base_instructions` currently runs the *subtractive* path — but `_FRONTIER_EXCLUDED_SECTIONS` is **empty**, so `build_rules_block(FRONTIER) ≡ build_rules_block()` byte-for-byte. The assembled base is identical to the clean no-arg base 1a's plan assumes, so **1a's 25-section inventory, the 17,185-char budget, and byte-identity all remain valid.** `test_profile_rules_composition.py` passed because it monkeypatches `("03_reasoning", "Verification")` — a section 1a never touched — so 1a's deletions don't break it.

**Net effect on ship guidance:** the `git add -p` discipline is now sharper — Bucket B (`assembly.py`, `test_profile_rules_composition.py`) is **not merely "another plan's file" to leave alone; it is code 1b will delete and must stay out of 1a's commit entirely.** 1a's clean commit = Bucket A only.

### Overall: PASS
1a's four tasks are verified clean (Paths contradiction deleted, ALWAYS-tool duplications removed, persistence/completion consolidation lossless, output-format floor added; inventory 25-section green, budget net-negative, scoped tests + boot smoke pass), and the cross-review confirms seam B's presence does not perturb any 1a measurement (empty exclusion set → byte-identical base). The only carry-forward is staging hygiene: `/ship` must `git add -p` to commit **Bucket A only** — the three rule files + the inventory hunks of `eval_rule_compliance.py` — leaving seam B (Bucket B, 1b's removal target) and all other plans' files (Bucket C) out of the 1a commit.
