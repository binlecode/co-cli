# instruction-floor-audit

## Context

The agent's **fixed prefill floor** — the uncompactable bytes that ride every model request and every
post-compaction state — has two halves: the **tool-schema half** (ALWAYS-visibility tool schemas) and the
**instruction half** (soul seed + mindsets + numbered rules + toolset guidance + critique). The
context-stability plan (`2026-06-02-...-context-stability-sizing-control`) audited and trimmed the
**schema half** rigorously (A1 report → A2: defer 4 tools, 20,581 → 17,224 chars) but left the
**instruction half** with only a size pin.

An instruction-floor audit (2026-06-07; its durable rule distilled into `docs/specs/prompt-assembly.md`
§2.2, the signature-coherence invariant — the standalone audit report was removed post-ship) gave the
instruction half the equivalent first-principles review. Measured live (tars, native toolset):

| Floor component | Chars | ~Tok |
|---|---:|---:|
| `build_static_instructions` (seed + mindsets + rules) | 23,473 | ~5,868 |
| toolset guidance (`MEMORY_GUIDANCE` + `CAPABILITIES_GUIDANCE`) | 985 | ~246 |
| critique (`## Review lens`) | 162 | ~41 |
| **Instruction half — total** | **24,624** | **~6,156** |
| Schema half (post-A2) | 17,224 | ~4,306 |

The instruction half is **59% of the floor and got the least rigor**; the rules block alone (17,134) is
larger than the entire post-A2 schema bucket. The audit surfaced six findings:

- **F1** — verbatim duplicate sentence across `02_safety.md:27` and `07_memory_protocol.md:5`.
- **F2** — memory save anti-patterns stated twice (`02_safety.md:32-34` ≈ `07_memory_protocol.md:69-70`).
- **F3** — recall guidance triplicated (`07` Recall + `MEMORY_GUIDANCE` + `02_safety` memory constraints).
- **F4** — "retrying is a loop" stated 3× (`04_tool_protocol.md:48`, `:53`, `05_workflow.md:31`).
- **F5** — **deferred-tool signature leakage**: A2 moved `session_search`/`session_view`/`skill_patch`/
  `skill_edit` schemas off the floor, but the rules/guidance still hard-code their call signatures
  (`06:36-37`, `07:14,18`, `guidance.py:14`). Two harms: (a) re-encodes the deferred signature on the floor
  every turn (claws back part of the A2 saving); (b) **internally contradicts** `04_tool_protocol.md:60-66`,
  which says deferred tools must be `tool_view`-loaded before calling.
- **F6** — `test_instruction_budget.py` measures only `build_static_instructions`; the guidance + critique
  ride the floor **unguarded**. (Originally ~1,147 chars; **post-TASK-1 the gap is ~578 chars** — guidance
  dropped 985→416 when Option A deleted MEMORY_GUIDANCE, critique unchanged at 162. TASK-4 closes the rest.)

This plan formalizes the report's four proposals (P1–P4).

## Problem & Outcome

**Problem:** The larger half of the fixed floor carries duplicated rules (F1–F4), re-encodes deferred-tool
signatures the schema-half defer was supposed to remove (F5), and is only partially guarded against
regression (F6). The floor is internally inconsistent: it tells the model to directly call tools it also
says are not loaded.

**Outcome:** Each behavioral concept has one floor owner; deferred-tool call signatures are removed from the
floor (behavioral triggers retained); a coupling guard prevents deferred-tool signatures from re-entering;
the budget guard covers the full delivered floor. The floor shrinks modestly (~250–375 tok) and — more
importantly — becomes internally coherent.

**Failure cost:** Without this fix, every turn pays for duplicated rules and re-encoded deferred-tool
signatures (a direct, recurring prefill tax that partially negates A2), and the model receives
contradictory guidance — "call `session_search` directly" vs "deferred tools must be loaded first" — which
on a small local model can produce a failed direct call followed by a recovery round-trip. Nothing trips
CI today, so the contradiction silently persists and any future defer repeats the mistake.

## Scope

**In:** `co_cli/context/rules/{02,04,05,06,07}_*.md`, `co_cli/context/guidance.py`,
`tests/test_instruction_budget.py`, `tests/test_orchestrator_schema_budget.py` (TASK-4 lockstep
`expected` update), `co_cli/bootstrap/core.py` (runtime `static_floor_tokens` full-floor fix), one new guard
test. Dedup (P1), signature-decouple (P2), coupling guard (P3), full-floor accounting — test guard + runtime
measurement (P4). TASK-1 additionally deleted the stale `tests/test_flow_prompt_assembly.py` (MEMORY_GUIDANCE
emission tests, obsolete after Option A) and synced `docs/specs/prompt-assembly.md` (dropped MEMORY_GUIDANCE
references).

**Out:**
- Per-rule deferral mechanism — does **not** port to rules (no per-rule loader; behavioral rules must be
  present to shape behavior). Proposing one would be over-engineering.
- The schema half — A1/A2 already closed it.
- Personality voice rewrites in `seed.md`/`mindsets/` — separate concern from floor redundancy.
- Verbosity trim of `03_reasoning`/`04_tool_protocol` beyond dedup — documented next lever, not this plan.
- Enlarging the operational window — separate eval-gated decision.

## Behavioral Constraints

- **Dedup removes the duplicate statement, never the only statement of a rule.** Every behavioral concept
  must remain stated exactly once on the floor after the trim. A trim that drops a behavior is a regression.
- **P2 removes signatures, not triggers.** The behavioral trigger ("recall before answering", "fix a
  drifting skill immediately — don't wait to be asked") must survive verbatim-in-spirit; only the literal
  `tool_name(args...)` call syntax of *deferred* tools is removed.
- **ALWAYS-tool signatures stay.** `memory_search`, `memory_view`, `memory_create`, `skill_view`,
  `todo_*`, etc. are ALWAYS — their references are correct and must not be touched. Only the four
  A2-deferred tools (`session_search`, `session_view`, `skill_patch`, `skill_edit`) are in P2 scope.
- **Conservative small-model bias preserved** — these edits improve floor coherence and size within the
  existing `0.50` ceiling; they do not relax it.
- **Re-pin downward only** — when a trim lowers a measured ceiling, re-pin to the new measurement; never
  raise a ceiling to accommodate growth (mirrors `test_instruction_budget.py` and
  `test_orchestrator_schema_budget.py` discipline).
- **No backward-compat shims** (`feedback_zero_backward_compat`).

## High-Level Design

**Organizing principle (ported from A1):** the floor carries **WHEN/WHY** (behavioral triggers — legitimately
uncompactable); the loaded schema carries **HOW** (call signatures). A signature on the floor is redundant
for ALWAYS tools and incoherent for DEFERRED ones.

- **P1/P2 are content edits** to rule `.md` files plus the `guidance.py` constants — single-owner each
  concept, strip deferred-tool signatures. P1 migrates each concept's unique content into its surviving owner
  before deleting the duplicate (migrate-then-dedup), and applies **Option A**: fold the recall content into
  `07` and delete `MEMORY_GUIDANCE` (its `tool_index`-presence gate is near-vacuous and can't detect
  deferral — see TASK-1/TASK-2). **✓ P1 (Option A) delivered in TASK-1 (2026-06-07);** the F4 done_when↔detail
  gate contradiction (`is a loop` 3→1 vs "`05` keeps a reference") was resolved by rewording `05`'s reference
  to drop the literal phrase — no plan change needed. **✓ P2 (TASK-2) delivered 2026-06-07** (deferred-tool
  signatures stripped from `06`/`07`; floor confirmed signature-clean; static trimmed 23,192 → 23,129). P3
  (TASK-3) and P4 (TASK-4) remain.
- **P3 is a new guard test** modeled on `tests/test_orchestrator_schema_budget.py`: it iterates the live
  `deps.tool_index` for `VisibilityPolicyEnum.DEFERRED` tools and asserts none of their names appears with a
  call-signature pattern (`\bname\s*\(`) in the **assembled** floor text (`build_rules_block()` +
  `build_toolset_guidance(deps.tool_index)`). It exercises the real assembly path, not raw file greps, so it
  guards the integration boundary and auto-covers any future defer (no hardcoded tool allowlist).
- **P4 extends** `test_instruction_budget.py` to measure the full delivered floor —
  `build_static_instructions + build_toolset_guidance + load_soul_critique` — closing the ~578-char gap that
  remains after TASK-1 (was ~1,147 pre-TASK-1; Option A's MEMORY_GUIDANCE deletion already closed half).

The guard (P3) is authored after the content fix (P2) so the committed test is green, not red.

## Tasks

### ✓ DONE TASK-1 — Dedup to single-owner (P1 / F1–F4)

- **files:** `co_cli/context/rules/02_safety.md`, `co_cli/context/rules/04_tool_protocol.md`,
  `co_cli/context/rules/05_workflow.md`, `co_cli/context/rules/07_memory_protocol.md`,
  `co_cli/context/guidance.py`
- **Method — migrate-then-dedup, NOT delete.** Each dedup first migrates any *unique* content into the
  surviving owner, then deletes only what is now a true duplicate. Net behavioral content is conserved; only
  repetition is removed. The `02_safety` "Memory constraints" section and `MEMORY_GUIDANCE` each carry
  content with **no other home** (verified: NOT present in `07`) — dropping it is a regression, so it must
  land in `07` before the source is removed.
- **Detail:**
  - F1+F2: memory save-policy owned by `07_memory_protocol.md`. Before removing `02_safety.md` "Memory
    constraints", migrate into `07` the content unique to `02` — the **never-save safety list** (credentials,
    health, financial, **workspace-specific paths**, **transient errors** — into `07` Anti-patterns) and the
    **"err on the side of saving — dedup catches redundancy"** save-bias line. The value statement and the
    ephemeral list are already in `07` (dedup removes `02`'s copies, keeping `07`'s ephemeral wording as the
    union). Proactive-save survives in `05_workflow.md` (the Directive-exception clause). After migration,
    `02_safety` retains only its security domain (credential protection, source control, approval, injected
    content) — no memory section.
  - F3 → **Option A**: fold the recall content into `07` "Recall" and **DELETE `MEMORY_GUIDANCE` entirely**
    (remove the constant and its branch in `build_toolset_guidance`). Migrate into `07` the two items unique
    to `MEMORY_GUIDANCE`: the **"recognize the topic but lack this user's setup"** recall trigger and the
    **bounded-retry-on-miss** rule ("at most one broader retry, then surface the miss"). Rationale: the
    `MEMORY_GUIDANCE` gate keys on `tool_index` *registry membership* (`"memory_search" in tool_index`), which
    is always true and **cannot detect deferral** — so it is near-vacuous defensive gating, not a live filter.
    Un-gating only happens via a source edit (removing the tool from `TOOL_REGISTRY`), at which point the rules
    get edited anyway. Folding into the unconditional `07` owner loses no behavior that can occur at runtime.
    (Supersedes the earlier PO-m-2 "preserve gating" framing: the gate is registry-keyed, so what it would
    protect cannot occur at runtime — verified against `_build_native_toolset`, `co_cli/agent/toolset.py:101`.)
  - F4: anti-loop owned by `04_tool_protocol.md` "Error recovery"; collapse its two adjacent restatements of
    the `"is a loop"` clause into one, keeping both distinct *scenarios* (tool-error vs empty/partial
    results) with their distinct surrounding guidance; `05_workflow.md` keeps a single-clause reference, not
    a full restatement.
- **done_when:** `python -c` invoking the assembly path
  (`build_static_instructions(deps.config) + build_toolset_guidance(deps.tool_index)`) asserts BOTH:
  1. **Dedup landed** — each deduped rule collapses 2+→1. Line-wrap-robust substrings (normalize `\n`→space
     before counting): F1 value-statement → `"prevents the user from having to correct or remind you again"`
     (today 2 → after 1); F4 anti-loop → `"is a loop"` (today 3 → after 1).
  2. **Unique content survived** (migrate-then-dedup, not data loss) — the assembled floor still contains,
     each ≥1×: `"credentials"`+`"health"`+`"financial"`, `"workspace-specific paths"` (or migrated
     equivalent), `"transient errors"`, `"err on the side of saving"`, `"broader retry"`/`"surface the
     miss"`, and the "recognize the topic" recall trigger.
  AND `build_toolset_guidance` no longer emits a memory block (`MEMORY_GUIDANCE` deleted), AND
  `uv run pytest tests/test_instruction_budget.py` passes after re-pinning `INSTRUCTION_BLOCK_CEILING` down
  to the new measurement. Note: `test_static_floor_tokens_measured_at_bootstrap` is **dynamically** measured
  (computes `expected` live from `build_static_instructions`) — a rules trim lowers it and `expected` in
  lockstep, so it needs no re-pin and will not show a phantom stale-assertion break (CD-m-1).
- **success_signal:** The assembled floor states each memory/recall/anti-loop rule once and retains every
  unique safety/recall constraint; `MEMORY_GUIDANCE` is gone with no behavioral loss.
- **prerequisites:** none

### ✓ DONE TASK-2 — Decouple deferred-tool signatures from triggers (P2 / F5)

- **files:** `co_cli/context/rules/06_skill_protocol.md`, `co_cli/context/rules/07_memory_protocol.md`
- **Detail:** Remove the literal call signatures of the four A2-deferred tools from the floor, retaining the
  behavioral trigger. (`guidance.py` is **not** in scope here — TASK-1's Option A already deleted
  `MEMORY_GUIDANCE`; the recall content now lives solely in `07`, so the `session_search`/`session_view`
  decoupling happens in `07`, not the guidance layer.)
  - `06` "Drift": keep "if a skill drifted, fix it immediately — don't wait to be asked"; drop
    `skill_patch(name=…, old_string=…, new_string=…)` and `skill_edit(name=…, content=…)` literals.
  - `07` "Recall"/"Anti-patterns" (including the recall content folded in by TASK-1): keep "search past
    sessions before answering"; drop the `session_view(session_id, start, end)` literal and the direct-call
    framing of `session_search`.
- **Current surface (post-TASK-1, verified 2026-06-07):** the only deferred-tool *call-signature* (regex
  `\bname\s*\(`) remaining in `07` is `session_view(session_id, start, end)` at `07:20`. The
  `session_search` references in `07` (Recall `:13-15`, Anti-patterns ephemeral bullet) are **bare names, not
  call signatures**, so the TASK-3 regex does not flag them — but the TASK-2 detail still asks to soften the
  Recall *direct-call framing* ("call … `session_search` … before answering") so the floor stops implying a
  direct invocation of a deferred tool. `06` "Drift" still carries both `skill_patch(...)`/`skill_edit(...)`
  literals. TASK-1's Option A already removed the `MEMORY_GUIDANCE` `session_search` signature, so the
  guidance layer is clean.
- **Gating-layer note (forward-looking principle).** The F5 defect's root cause is that
  `build_toolset_guidance` gates on `tool_index` *registry membership* — the coarsest layer, which can't tell
  a DEFERRED tool from a loaded one (a deferred tool stays in `tool_index`; only the per-turn
  `_tool_visibility_filter` at `co_cli/agent/toolset.py:62` hides it). So a presence-keyed gate keeps emitting
  "call X" after X is deferred. **Rule going forward:** any tool-*usage* guidance that survives in a gated
  layer must key on **per-turn visibility** (loaded vs deferred), not `tool_index` presence — otherwise it
  reproduces F5. This plan removes the only offender (`MEMORY_GUIDANCE`, via TASK-1 Option A); the surviving
  `CAPABILITIES_GUIDANCE` is presence-keyed too but names only the ALWAYS `capabilities_check` (never
  deferred), so it is not an active F5 risk — flagged here so a future defer of any tool it names triggers a
  re-gate, not a silent stale instruction.
- **done_when:** Self-contained `python -c` (does **not** depend on TASK-3's test): assert
  `re.search(r"\b(session_search|session_view|skill_patch|skill_edit)\s*\(", floor)` is `None` where
  `floor = build_rules_block() + build_toolset_guidance(deps.tool_index)`, AND the two behavioral triggers
  survive in `floor` (the skill-drift phrase "don't wait to be asked" and the recall phrase "before
  answering" are still present). The durable guard test is layered on top in TASK-3, not used as this gate.
- **success_signal:** The assembled floor no longer instructs a direct call to any deferred tool; the
  drift/recall behaviors still fire.
- **prerequisites:** ✓ TASK-1 (DELIVERED 2026-06-07 — `MEMORY_GUIDANCE` deleted, recall content folded into
  `07`; the surface TASK-2 decouples is now in `07`/`06` as described above)

### ✓ DONE TASK-3 — Coupling guard test (P3)

- **files:** `tests/test_instruction_floor_coupling.py` (new)
- **Detail:** Model on `tests/test_orchestrator_schema_budget.py`. Bootstrap headless deps with the full
  required signature — `create_deps(on_status=lambda _s: None, stack=None, theme_override=None)`
  (`on_status` is keyword-only and required; `create_deps(stack=None)` raises `TypeError`) — collect
  DEFERRED tool names
  (`[n for n,i in deps.tool_index.items() if i.visibility == VisibilityPolicyEnum.DEFERRED]`), assemble the
  floor text (`build_rules_block()` + `build_toolset_guidance(deps.tool_index)`), and assert no deferred
  name matches `re.search(rf"\b{re.escape(name)}\s*\(", floor)`. No hardcoded allowlist — the guard derives
  the deferred set live, so any future defer is auto-covered. Failure message names the offending tool and
  points at this plan's F5.
- **Verified (post-TASK-1):** `build_rules_block()` exists as a public function in
  `co_cli/context/assembly.py:66` (extracted by the mindset-stance-selection plan's TASK-0) — the done_when
  reference is real, not phantom. **Note:** TASK-1 deleted `tests/test_flow_prompt_assembly.py` (the only
  test of `build_toolset_guidance` emission — it pinned the now-removed `MEMORY_GUIDANCE` gating). After this
  task, this new coupling test plus `test_instruction_budget.py` (TASK-4, full-floor) are the only guards on
  the assembled instruction floor; there is no longer a guidance-emission unit test to update.
- **Post-TASK-2 (DELIVERED 2026-06-07) — floor confirmed signature-clean:** both the TASK-2 done_when and the
  review-impl pass ran `re.search(r"\b(session_search|session_view|skill_patch|skill_edit)\s*\(", floor)` over
  `build_rules_block() + build_toolset_guidance(deps.tool_index)` → `None`. So this guard is **green on commit**
  (authored after the content fix per the High-Level Design), not red — TASK-3 codifies a state that already
  holds rather than driving a new fix.
- **done_when:** `uv run pytest tests/test_instruction_floor_coupling.py` passes (green on the post-TASK-2
  floor — already confirmed clean above), and a temporary re-introduction of a deferred-tool signature makes
  it fail (verified manually, noted in delivery summary).
- **success_signal:** A future defer that forgets the paired floor sweep, or a floor edit that re-adds a
  deferred-tool signature, fails CI.
- **prerequisites:** ✓ TASK-2 (DELIVERED 2026-06-07 — floor confirmed signature-clean; TASK-3 ready to run)

### ✓ DONE TASK-4 — Extend the full-floor accounting: test guard AND runtime measurement (P4 / F6)

- **files:** `tests/test_instruction_budget.py`, `co_cli/bootstrap/core.py`,
  `tests/test_orchestrator_schema_budget.py` (lockstep `expected` update — see done_when)
- **Detail:** F6 is two faces of the same under-count, and fixing only the test guard while leaving the
  runtime floor wrong contradicts the plan's coherence thesis — so fix both with **one shared full-floor
  definition** (the existing `measure_always_schema_budget` single-source pattern is the model):
  1. **Runtime (`co_cli/bootstrap/core.py:440-442`, `deps.static_floor_tokens`):** today it sums only
     `estimate_text_tokens(build_static_instructions(config))` + ALWAYS schema, excluding guidance + critique.
     **Re-measured post-TASK-2 (2026-06-07):** guidance is **416 chars** (CAPABILITIES_GUIDANCE only — TASK-1's
     Option A deleted MEMORY_GUIDANCE, which was the bulk of the old 985) and critique **162 chars** (tars) —
     **both unchanged by TASK-2**, which only edited rule prose. So the compaction trigger still under-counts
     the real floor by **~578 chars (~145 tok)** — down from the report's ~1,147/287 because TASK-1 already
     removed half the gap. Fold guidance + critique into the measured floor so the trigger reflects what
     actually rides every request.
  2. **Test guard (`tests/test_instruction_budget.py`):** measure the same full delivered floor —
     `build_static_instructions(config) + build_toolset_guidance(tool_index) + load_soul_critique(config.personality)`
     (`load_soul_critique` requires the role arg — pass the configured default, as the orchestrator does) —
     and re-pin `INSTRUCTION_BLOCK_CEILING` to that measurement. **Re-pin DIRECTION note (critical):** the
     ceiling currently sits at **23,600** pinning the *static-only* surface — re-measured **post-TASK-2 the
     static surface is 23,129** (TASK-1 left it at 23,192; TASK-2 trimmed −63 by removing the deferred-tool
     signatures). TASK-4 changes the *measured quantity* from static-only to full-floor (static 23,129 +
     guidance 416 + critique 162 = **23,707 chars; 23,711 once `\n\n`-joined**), so the ceiling must move
     **UP** to ~24,200 (full-floor measurement + ~490 headroom). **Note the full-floor (23,711) already
     EXCEEDS the current 23,600 ceiling** — so once the measured quantity expands, the upward re-pin is
     mandatory, not discretionary (the static-only value happens to still pass today only because guidance +
     critique aren't yet counted). This is **not** a "downward only" violation — the rule forbids raising a
     ceiling to accommodate *growth of the same surface*; here the surface definition itself expands to
     include two components that were always on the floor but previously unmeasured. Update the module
     docstring to state it now guards the full delivered floor (static + guidance + critique), and supersede
     TASK-2's static-only 23,129 / 23,600 note.
  3. **OQ-1 resolution (inline) — critique sizes pre-measured 2026-06-07:** tars **162**, jeff **178**, finch
     **185** (the max, +23 over the tars pin). Pinning the full-floor to the configured default (tars) with a
     ~24,200 ceiling leaves ~490 chars of headroom over the tars full-floor (23,711) — comfortably absorbing
     finch's +23-char critique, so no per-personality pin matrix is needed. Confirm this margin holds at
     implementation time (seed/mindsets also vary per role, but the pin tracks the configured default per
     existing discipline) and record it in the delivery summary. Do **not** build a per-personality pin
     matrix — pin to the configured default (tars).
- **done_when:** `uv run pytest tests/test_instruction_budget.py` passes; a `python -c` confirms BOTH (a) the
  test-asserted total ≈ static+guidance+critique (not static alone), and (b) a freshly bootstrapped
  `deps.static_floor_tokens` equals the token-count of static+guidance+critique+ALWAYS-schema (not
  static+schema alone). **Lockstep fix (required, not conditional):** `test_static_floor_tokens_measured_at_bootstrap`
  (`tests/test_orchestrator_schema_budget.py:73-84`) computes `expected` from `build_static_instructions` +
  schema **only** (lines 78-81); once the runtime adds guidance+critique, this test breaks unless `expected`
  also adds `estimate_text_tokens(build_toolset_guidance(deps.tool_index))` +
  `estimate_text_tokens(load_soul_critique(deps.config.personality))`. Update it in the same task and verify
  green.
- **success_signal:** Growth in `CAPABILITIES_GUIDANCE` or the critique now trips CI (MEMORY_GUIDANCE no
  longer exists — deleted in TASK-1), and the runtime compaction trigger accounts for the full floor.
- **prerequisites:** ✓ TASK-1 (DELIVERED 2026-06-07), ✓ TASK-2 (DELIVERED 2026-06-07) — both prereqs met;
  TASK-4 ready to run (independent of TASK-3)

## Testing

- `tests/test_instruction_floor_coupling.py` (new, TASK-3) — the durable F5 guard.
- `tests/test_instruction_budget.py` (extended, TASK-4) — full-floor size guard.
- `scripts/quality-gate.sh full` at ship.
- No eval required: this is a prompt-assembly content/coherence change; the assembly functions are the
  integration boundary and the two guard tests exercise them. (Behavioral confirmation that the small model
  no longer mis-calls a deferred tool is a soft win, observable in existing eval runs — not a gate here.)

## Open Questions

- **OQ-1 (resolved → measured 2026-06-07, folded into TASK-4):** Other shipped souls (finch, jeff) may carry
  a longer critique that would change the P4 re-pin. Sizes now measured: tars 162, jeff 178, finch 185 (max,
  +23 over the tars pin) — the ~24,200 ceiling's ~490-char headroom over the tars full-floor absorbs it.
  Resolution: pin to the configured default (tars), no per-personality pin matrix. No open decision remains.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev instruction-floor-audit`

## Delivery Summary — 2026-06-07 (TASK-1 only)

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | dedup landed (F1 1×, F4 1×) + unique content survived + `MEMORY_GUIDANCE` deleted + `test_instruction_budget.py` passes after downward re-pin | ✓ pass |
| TASK-2 | — | — not run (scoped to `task 1`) |
| TASK-3 | — | — not run |
| TASK-4 | — | — not run |

**What was done:**
- **F1+F2** — deleted the entire `## Memory constraints` section from `02_safety.md`. Migrated its unique content to `07_memory_protocol.md`: the never-save safety list (workspace-specific paths / transient errors / session-only context / credentials, health, financial) and the "err on the side of saving" bias → `07` Anti-patterns. The value statement and ephemeral-state list were already in `07` (dedup kept `07`'s copy). Proactive-save trigger already covered by `05_workflow.md:16-18`.
- **F3 (Option A)** — deleted `MEMORY_GUIDANCE` constant + its gate branch in `build_toolset_guidance`. Folded its two unique items into `07` Recall: the "recognize the topic but lack this user's setup" trigger and the bounded-retry-on-miss rule ("at most one broader retry, then surface the miss").
- **F4** — collapsed the two adjacent `is a loop` clauses in `04_tool_protocol.md` to one (kept both distinct scenarios: tool-error vs empty/partial results); reduced `05_workflow.md`'s restatement to a reference without the literal phrase.

**G1-flagged gate contradiction resolved:** the plan's done_when (`is a loop` 3→1) conflicted with its detail ("`05` keeps a single-clause reference"). Resolved via review option (a) — `05`'s surviving reference reworded to drop the literal `is a loop` phrase ("Repeating a failed action unchanged is not persistence"), satisfying both. No plan revision required.

**Measurement:** `build_static_instructions` 23,352 → **23,192 chars** (−160 net after migrations). `INSTRUCTION_BLOCK_CEILING` re-pinned 23,750 → **23,600** (downward only).

**⚠ Extra file (beyond TASK-1 `files:`):** `tests/test_instruction_budget.py` — TASK-1's done_when mandates the ceiling re-pin; file is nominally TASK-4's. TASK-4 will redefine this test for the full delivered floor.

**Stale tests dropped:** deleted `tests/test_flow_prompt_assembly.py` (both tests verified the now-deleted `MEMORY_GUIDANCE` gating — one broke outright since `"at most one broader retry"` moved to rule `07`, the other became vacuously-true). The recall content is now guarded by `test_instruction_budget.py` (static floor) rather than by guidance-emission gating tests. No new tests added (clean-tests: remove structural/wiring, don't replace).

**Tests:** scoped — 3 passed (`test_instruction_budget` + `test_orchestrator_schema_budget`'s 2 tests, incl. dynamically-measured `test_static_floor_tokens_measured_at_bootstrap` — confirms CD-m-1: no stale-assertion break), 0 failed; 2 stale tests deleted. Lint clean.
**Doc Sync:** fixed — `docs/specs/prompt-assembly.md`, dropped 3 stale `MEMORY_GUIDANCE` references (§2.1, §4 contract, §5 Files).

**Overall: DELIVERED** (TASK-1 only — invocation scoped to `task 1`). TASK-2–4 remain; TASK-2 depends on TASK-1 (now complete) and is ready to run.

## Delivery Summary — 2026-06-07 (TASK-2 only)

| Task | done_when | Status |
|------|-----------|--------|
| TASK-2 | no `(session_search\|session_view\|skill_patch\|skill_edit)\s*\(` in assembled floor + both behavioral triggers survive | ✓ pass |
| TASK-3 | — | — not run (scoped to `task 2`) |
| TASK-4 | — | — not run |

**What was done (P2 / F5 — signature/trigger decoupling):**
- **`06_skill_protocol.md` "Drift"** — dropped the `skill_patch(name=…, old_string=…, new_string=…)` and `skill_edit(name=…, content=…)` literal signatures; reworded to "a surgical patch for a localized fix, a full rewrite for a structural overhaul." Trigger ("fix it immediately … Don't wait to be asked") and the surgical-vs-structural WHY both retained. The deferred tools are loaded by name via the `04_tool_protocol.md` "Deferred tools" `tool_view` mechanic.
- **`07_memory_protocol.md` Recall ¶1** — removed the direct-call framing of the deferred `session_search` ("call `memory_search` … or `session_search` … before answering" → "search before answering: `memory_search` … and past conversations for prior exchanges"). `memory_search` (ALWAYS) keeps its direct-call reference; "before answering" retained.
- **`07_memory_protocol.md` Recall ¶2** — dropped the `session_view(session_id, start, end)` literal signature ("pull verbatim turns from a past session when you need the exact wording"). `memory_view(name)` (ALWAYS) untouched.
- **`07_memory_protocol.md` Anti-patterns** — softened the bare `session_search` direct-call reference ("recall them later via `session_search`" → "recall them later by searching past sessions") for floor coherence — not regex-flagged (no paren), but it framed a direct call to a deferred tool.

**Coherence result:** the floor no longer instructs a direct call to any of the four A2-deferred tools, resolving the F5 contradiction with `04_tool_protocol.md:60-66` ("deferred tools must be `tool_view`-loaded before calling"). ALWAYS-tool signatures (`memory_search`, `memory_view`, `skill_view`, `skill_create`) untouched per the plan's behavioral constraint.

**Tests:** scoped — `tests/test_instruction_budget.py` 1 passed (floor trim only lowers the measured value; ceiling pin holds — no re-pin in TASK-2 scope; TASK-4 redefines this test for the full floor). Lint clean. done_when verified via the self-contained assembly-path `python -c`.
**Doc Sync:** clean — TASK-2 edited only rule-prose content (no shared module / API / schema change); `prompt-assembly.md` documents the mechanism, not literal rule signatures, and was synced in TASK-1.

**Overall: DELIVERED** (TASK-2 only — invocation scoped to `task 2`). TASK-3 (coupling guard, prereq TASK-2 ✓) and TASK-4 (full-floor accounting) remain.

## Implementation Review — 2026-06-07 (TASK-1 + TASK-2)

Focus dimensions (per invocation): code smell · visibility · boundary · API surface/shape · dead code.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | F1 1× + F4 1× dedup; unique safety/recall content survives; `MEMORY_GUIDANCE` deleted; budget test green after downward re-pin | ✓ pass | Live assembly path: `is a loop`=1, value-statement=1; `credentials`/`health`/`financial`/`transient errors`/`err on the side of saving`/`broader retry`/`surface the miss`/`recognize the topic` all present; `MEMORY_GUIDANCE` absent from `co_cli/`+`tests/` (grep); `guidance.py:21-29` emits no memory block |
| TASK-2 | no deferred-tool call signature in assembled floor; both behavioral triggers survive | ✓ pass | `re.search(r"\b(session_search\|session_view\|skill_patch\|skill_edit)\s*\("` over `build_rules_block()+build_toolset_guidance(tool_index)` → `None`; `06_skill_protocol.md:33-38` retains "Don't wait to be asked"; `07_memory_protocol.md:9-21` retains "before answering" |

### Findings by focus dimension
| Dimension | Verdict | Evidence |
|-----------|---------|----------|
| **Dead code** | clean | `MEMORY_GUIDANCE` fully removed — zero references in `co_cli/`/`tests/` (only historical docs). `build_toolset_guidance` retains a live job (emit `CAPABILITIES_GUIDANCE`, gated on ALWAYS `capabilities_check`); single-branch `parts` accumulator is pre-existing shape, not an orphan my changes created. |
| **Visibility (`_prefix`)** | clean | `guidance.py:10` imports only public `co_cli.deps.ToolInfo`; no private-symbol import or leak introduced. |
| **Boundary** | clean | No deferred-tool call signature in *any* rule file (grep over `co_cli/context/rules/`); rules now carry WHEN/WHY, the per-turn deferred block (`04_tool_protocol.md:60-66`) carries the names+HOW — boundary held. |
| **API surface/shape** | clean | `build_toolset_guidance(tool_index: dict[str, ToolInfo]) -> str` unchanged; sole caller `orchestrator.py:36-38` via the established lazy-import provider — no signature/return regression. |
| **Code smell** | clean | `guidance.py` module docstring verified *accurate* (not stale): `CAPABILITIES_GUIDANCE` was extracted from rules in commit `dc7418cf`, so "content previously in rule files, emitted when the tool is present" still describes the surviving constant. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `docs/REPORT-eval-memory.md`, `docs/REPORT-eval-skills.md` carry uncommitted insertions | — | scope note | **Not touched.** Outside both tasks' `files:`; unrelated to this plan (coworker-maintained eval reports). Must **not** be staged with this plan's ship. |

_No blocking findings. No code-smell, visibility, boundary, API-shape, or dead-code defects in the delivered surface._

### Tests
- Deterministic blast radius: `uv run pytest tests/test_instruction_budget.py tests/test_orchestrator_schema_budget.py` → 3 passed, 0 failed.
- **Full suite (re-run on request): `uv run pytest -x -q` → 623 passed, 0 failed, 1 warning in 159s.** All real-LLM call timings healthy (slowest 23.3s on `test_length_retry_completes_truncated_noreason_response`; compaction summarization 8.6s — no stalls). The 3 `status=ERROR` log lines are an intentional surrogate-`\ud800` error-handling assertion, not failures.
- Both done_whens re-executed live (not assumed): TASK-1 + TASK-2 PASS.
- Logs: `.pytest-logs/<ts>-review-impl.log` (scoped), `.pytest-logs/<ts>-review-impl-full.log` (full).

### Behavioral Verification
- Bootstrap-assembles-floor: ✓ live `create_deps` + `build_static_instructions`/`build_rules_block`/`build_toolset_guidance` produced a coherent 23,129-char floor (< 23,600 ceiling) with no errors — the user-facing surface (prompt floor rides every request) assembles cleanly.
- `co status` not run — no such command in this CLI; `co chat` interaction would test LLM behavior, which the plan's Testing section explicitly scopes as a non-gating soft win.
- `success_signal` (TASK-2: "floor no longer instructs a direct call to any deferred tool"): ✓ verified — regex confirms zero deferred-tool signatures in the assembled floor.

### Overall: PASS
TASK-1 and TASK-2 are correctly delivered against their `done_when`; the focus dimensions (code smell, visibility, boundary, API surface/shape, dead code) are all clean; lint green; deterministic blast-radius tests pass. The two uncommitted `REPORT-eval-*.md` files are unrelated coworker work and must be excluded from this plan's ship.

## Delivery Summary — 2026-06-07 (TASK-3 + TASK-4)

| Task | done_when | Status |
|------|-----------|--------|
| TASK-3 | `test_instruction_floor_coupling.py` green; deferred-tool signature re-intro makes it fail (verified + reverted) | ✓ pass |
| TASK-4 | `test_instruction_budget.py` green at full-floor pin; `static_floor_tokens` == full-floor + ALWAYS-schema (not static+schema); lockstep `expected` fixed | ✓ pass |

**TASK-3 (P3 / F5 — coupling guard):**
- New `tests/test_instruction_floor_coupling.py` — derives the DEFERRED set live from `deps.tool_index` (no hardcoded allowlist), assembles `build_rules_block() + build_toolset_guidance(deps.tool_index)`, asserts no deferred name matches `\bname\s*\(`. Any future defer is auto-covered; failure message names the tool and cites F5.
- **Guard caught a live F5 leak on first run** the TASK-2 scope missed: `skill_create` is `VisibilityPolicyEnum.DEFERRED` (verified `co_cli/tools/system/skills.py:299,303`) but its `skill_create(name=…, content=…)` signature was still hard-coded at `06_skill_protocol.md:45` (TASK-2's review at plan line 360 misclassified it as ALWAYS). Applied the same TASK-2 decouple: dropped the literal signature, kept the promote-to-skill trigger + §6 conformance. The tool loads by name via the `04_tool_protocol.md` `tool_view` mechanic.
- Red-on-regression verified manually (temp `session_view(...)` inserted → red → reverted → green).
- **⚠ Extra file (beyond TASK-3 `files:`):** `co_cli/context/rules/06_skill_protocol.md` "Create" hunk — the only way to keep the guard green on commit (the leak it exposed). Same F5 class as TASK-2; surgical, trigger+§6 preserved.

**TASK-4 (P4 / F6 — full-floor accounting):**
- **Runtime** (`co_cli/bootstrap/core.py`): `deps.static_floor_tokens` now folds in `build_toolset_guidance(tool_index)` + `load_soul_critique(personality)` (guarded on `config.personality`) alongside `build_static_instructions`. Compaction trigger no longer under-counts the floor by ~144 tok.
- **Test guard** (`tests/test_instruction_budget.py`): measures the full delivered floor (base + guidance + critique); renamed `test_instruction_floor_within_budget`; `INSTRUCTION_BLOCK_CEILING` re-pinned **23,600 → 24,200**. This is a *surface-definition expansion* (the two always-on components were previously unmeasured), not a downward-only violation — full-floor already exceeds the old static-only pin, so the upward move is mandatory. Docstring updated to state it guards the full floor.
- **Lockstep** (`tests/test_orchestrator_schema_budget.py`): `test_static_floor_tokens_measured_at_bootstrap` `expected` now adds the guidance + critique token terms to match the widened runtime measurement; green.
- **Measurement (live, post-TASK-3 `06` trim):** base 23,078 + guidance 416 + critique 162 = **23,656 chars** (24,200 ceiling → 544 headroom, absorbs finch's +23 critique). `static_floor_tokens` 10,075 → **10,219 tok** (+144). OQ-1 margin confirmed.

**Tests:** scoped — `test_instruction_budget.py` + `test_orchestrator_schema_budget.py` (2) + `test_instruction_floor_coupling.py` → **4 passed, 0 failed** (`.pytest-logs/<ts>-scoped.log`). Lint clean.
**Doc Sync:** fixed — `docs/specs/compaction.md`, 4 stale `static_floor_tokens`-composition descriptions corrected to the full-floor definition (§1.5, §2.5 field def, Files table, Tests table). Narrow scope; `prompt-assembly.md` already documented the three providers correctly.

**Overall: DELIVERED** — all four tasks (P1–P4) complete. The instruction-floor coupling defect (F5) is closed and CI-guarded; the full delivered floor is now both size-guarded (test) and correctly counted by the compaction trigger (runtime).

**Next step:** `/review-impl instruction-floor-audit` — full suite + evidence scan + behavioral verification → verdict at Gate 2.

## Implementation Review — 2026-06-07 (entire plan: TASK-1 through TASK-4)

Re-review of all four `✓ DONE` tasks (logic + tests), one cold-read evidence subagent per task. Default stance: issues exist; every PASS earned with a re-executed `done_when` + file:line.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | F1 1× + F4 1× dedup; unique safety/recall content survives; `MEMORY_GUIDANCE` gone; budget test green | ✓ pass | Live assembly: `"prevents…remind you again"`=1, `"is a loop"`=1; all 9 migrated markers present (credentials/health/financial/workspace-specific paths/transient errors/err on the side of saving/broader retry/surface the miss/recognize the topic); `MEMORY_GUIDANCE` zero non-doc hits; `guidance.py:21-29` emits only `CAPABILITIES_GUIDANCE` |
| TASK-2 | no deferred call-sig in floor; both triggers survive | ✓ pass | `re.search(r"\b(session_search\|session_view\|skill_patch\|skill_edit)\s*\(", floor)`→`None`; drift trigger `06:37`, recall trigger `07:16`; ALWAYS `memory_search`/`memory_view` references intact |
| TASK-3 | coupling guard green; red on re-introduced signature (verified+reverted) | ✓ pass | `test_instruction_floor_coupling.py` PASS; injected `session_view(...)`→FAIL naming tool+F5→reverted; live deferred set (17 tools) covers session_search/session_view/skill_patch/skill_edit/**skill_create**; derived live (no allowlist); real deps, no mocks |
| TASK-4 | full-floor budget test green; `static_floor_tokens`==full-floor+ALWAYS-schema (not static+schema); lockstep fixed | ✓ pass | Runtime `core.py:446-451` sums 3 instruction terms (critique guarded on `config.personality`) + schema; `static_floor_tokens`=10,217 == independent reconstruction (static-only=10,073, proves full-floor); lockstep `test_orchestrator_schema_budget.py` mirrors runtime exactly (both 10,217); char-pin full=23,649 ≤ 24,200, > old 23,600 (up-move mandatory) |

### Cross-task / logic correctness
- **Runtime ↔ lockstep test compute the SAME quantity** (token-sum of base+guidance+critique+ALWAYS-schema) — verified numerically equal (10,217). No double-count: single `static_floor_tokens` assignment; guidance/critique each added once; same `tool_index` reference passed to deps and `build_toolset_guidance`.
- **Char-pin vs token-count are distinct, internally consistent guards** — `test_instruction_budget` pins chars of the concatenated floor (≤24,200); runtime/lockstep pin summed token estimates. Neither claims to equal the other.
- **F4 invariant holds across the whole floor** — assembled `"is a loop"`=1 (in `04:50`); `05`/`07` carry the principle as non-duplicate rephrasings, not dropped.

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| **F5 leak the guard caught on first run:** `skill_create` is DEFERRED (`skills.py:299,303`) but its `skill_create(name=…, content=…)` signature was still on the floor at `06_skill_protocol.md:45` (TASK-2 review misclassified it ALWAYS) | `06_skill_protocol.md` | blocking | Fixed in TASK-3 delivery — literal signature dropped, promote-to-skill trigger + §6 conformance kept; loads via `tool_view`. Guard now green. |
| **Review-process incident (not a code defect):** TASK-3 evidence subagent reverted an injected test signature with `git checkout`, discarding TASK-1's unstaged F4 edit in `04_tool_protocol.md`; reconstructed line 53 from the delivery summary | `04_tool_protocol.md:53` | verified-repaired | Confirmed current diff vs HEAD is line-53-only (`"is a loop"`→`"is not"`); reconstruction is coherent (parallel ellipsis "is not [persistence]") and satisfies F4 (floor `"is a loop"`=1). 7-char shorter than the lost original, so live floor is now 23,649 chars (delivery summary's 23,656 is pre-incident). Both pins still pass. |
| `static_floor_tokens` composition stale in spec | `compaction.md` ×4 | minor | Fixed via sync-doc (full-floor definition: base+guidance+critique+ALWAYS-schema) |

_Minor/by-design (no action): TASK-1 folded the "debugging notes" example into the broader ephemeral-state bullet (rule preserved). TASK-4 measures raw `load_soul_critique`, not the `## Review lens` wrapper (~16 chars, per plan; absorbed by 551-char headroom). Personality guard divergence is dormant — runtime and test guard identically; bootstrap default `tars` is truthy._

### Tests
- Command: `uv run pytest -x -q`
- Result: **624 passed, 0 failed**, 1 warning in 155.53s. Real-LLM timings healthy (consistent with the 159s baseline). +1 over the prior 623 = the new coupling guard.
- Log: `.pytest-logs/<ts>-review-impl-full.log`. Lint: `scripts/quality-gate.sh lint` PASS (330 files).
- All four `done_when` re-executed live (not assumed): PASS.

### Behavioral Verification
- Bootstrap surface: ✓ `create_deps` completed cleanly across every review run; `deps.static_floor_tokens`=10,217 computed correctly; instruction floor (23,649 chars) assembles without error — the prefill floor that rides every request is coherent.
- No `co status` command in this CLI; no chat-loop/tool-output behavior changed, so `co chat` would only exercise LLM behavior (plan's Testing section scopes that as a non-gating soft win).
- `success_signal` TASK-3 ("a future defer / floor edit that re-adds a deferred-tool signature fails CI"): ✓ verified via red-on-regression. `success_signal` TASK-4 ("guidance/critique growth trips CI; runtime accounts for full floor"): ✓ verified — budget test now measures all three builders; `static_floor_tokens` 10,073→10,217.

### Overall: PASS
All four tasks (P1–P4) correctly delivered against their `done_when`; logic verified (runtime ↔ lockstep agree numerically, no double-count, F4 invariant holds); tests clean (real deps, functional assertions, no mocks); full suite green; lint clean. The F5 coherence defect is closed and CI-guarded.

**Two ship-time notes for the TL at Gate 2:**
1. **Confirm the `04_tool_protocol.md:53` phrasing** — `"One varied retry is persistence; a second unchanged retry is not."` is a review-reconstruction of TASK-1's lost unstaged wording; it is coherent and satisfies F4, but is not byte-for-byte the original. Adjust if a different phrasing was intended.
2. **Staging hygiene:** ship ONLY this plan's surface (the 5 rule files, `guidance.py`, `bootstrap/core.py`, the 3 test files, the deleted `test_flow_prompt_assembly.py`, `compaction.md`, `prompt-assembly.md`, and this plan + its REPORT). EXCLUDE the unrelated untracked files: `floor-naming-renames.md` (separate peer plan, gated behind this ship) and `RESEARCH-pydantic-ai-sdk-usage.md` (coworker research).
