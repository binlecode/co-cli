# Personality Static-Prefix Refactor — measure-first thinning of seed · mindsets · rules

Task type: core-loop refactor (prompt assembly) + instrumentation + ablation eval, eval-gated

## Context

co assembles one role's static system prefix once at agent construction via
`build_base_instructions(config)` (`co_cli/context/assembly.py:83`) — `seed + mindsets +
build_rules_block()` — joined by the orchestrator's `static_instruction_builders` alongside
`_toolset_guidance_provider` and `_personality_critique_provider` (`co_cli/agent/orchestrator.py:53-59`).
For `tars` this is ≈3,760 words / ~5k tokens carried **every turn**, flat and unmeasured:

- The **rules** block is seven files (`rules/01_identity.md` … `07_memory_protocol.md`), several of
  which are *episodic procedural protocols* (skill, memory) that don't apply on a turn with no such
  operation.
- The **mindsets** block loads all six task-type files for the role into one `## Mindsets` section
  (`load_soul_mindsets`, `co_cli/personality/prompts/loader.py:98-124`), selected only by emergent
  attention — no nudge, no gating.

The diagnosis is `docs/reference/RESEARCH-personality-architecture.md` §11.0 (rules over-specified) and
§11.2 / §2.2 (mindsets: do they earn their real estate; does flat all-six load distract). Both are the
**same over-specification thesis**, partitioned by which part of the prefix they target. This plan is
the single, measure-first thinning of the whole static prefix.

> **History.** This plan merges two prior active plans (distilled into one, 2026-06-13): the
> rules-thinning plan (`personality-self-model` P0) and the mindset-ablation plan
> (`2026-05-27-165621-mindset-stance-selection`). The mindset plan's `build_rules_block()` extraction
> already landed (see Preconditions); its ablation harness was built then **deleted** in `59a698b9`
> before ever running — rebuilt here as TASK-3. The two plans' conditional editorial steps are merged
> into TASK-4. Stale artifacts dropped: the `build_static_instructions` naming (real entry is
> `build_base_instructions`) and the `docs/REPORT-eval-mindset-selection.md` markdown report (eval
> output is now per-run JSONL).

### Preconditions already landed (not tasks)

- **`build_rules_block()`** — public rules assembler at `assembly.py:66`, called at `:124`. Its live
  consumer is the static-floor test `tests/test_instruction_floor_coupling.py:41`
  (`build_rules_block() + build_toolset_guidance(...)`). **Frozen** — do not change what it returns
  (Constraint #5); the gated-rules work adds `build_invariant_rules_block()` rather than mutating it.
- **`judge_pairwise` / `PairwiseVerdict`** — pairwise judge primitive at `evals/_judge.py:88,204`
  (winner ∈ {A,B,tie} + rationale; signature `judge_pairwise(target_stance, response_a, response_b, *,
  deps, model)`). Built for the deleted ablation, currently **orphaned**; TASK-3 rewires it.

### Code accuracy (claims checked against HEAD)

- **`CoCapabilityState` / `deps.capabilities` do NOT exist** (repo-wide grep: 0 hits). The real,
  bootstrap-set, never-mutated (∴ session-scoped) gating signal is `deps.tool_catalog: dict[str,
  ToolInfo]` and `deps.skill_catalog: dict[str, SkillInfo]` (`co_cli/deps.py:308-310`). This plan gates
  on the catalogs; it does NOT build `CoCapabilityState`.
- Static builder signature is `Callable[[CoDeps], str | None]` (`co_cli/agent/spec.py:31`) — deps only,
  cached. `co_cli/context/guidance.py:26` already gates a block on `"capabilities_check" in
  deps.tool_catalog` — the precedent to mirror.
- `estimate_text_tokens(text) -> int` exists (`co_cli/context/tokens.py:6`); bootstrap calls
  `build_base_instructions` for the static floor (`co_cli/bootstrap/core.py:485`, catalogs already
  built by ~466). Observability spans (`@trace`, `current_span().add_event`, `co trace <id>`;
  `_NoOpSpan` off-stack at `tracing.py:142-165`) are the inspection path.
- Current eval infra (post-`59a698b9` prune): shared `response_text(turn_result)` (`evals/_trace.py:80`)
  replaced the per-eval `_response_text`; `prepend_report` / `_report.py` are gone — run records are
  per-run JSONL via `open_eval_run` (`evals/_observability.py:137`). `CALL_TIMEOUT_S`
  (`evals/_timeouts.py:42`), `judge_model_annotation` (`evals/_judge.py:33`) are present.

## Problem & Outcome

**Problem.** The ~5k-token static prefix is carried every turn, flat and unmeasured: nothing
distinguishes load-bearing text from inertial weight. The rules block carries procedural protocols even
in sessions where the capability is absent; the mindsets block carries all six task-type files with no
evidence the always-on load beats a focused subset, or that it changes behavior at all.

**Failure cost:** without measurement the prefix stays a black box — every future personality/doctrine
change flies blind, and any "thinning" (or the deferred style resolver) would be built and judged
against an unmeasured, over-weight baseline.

**Outcome.**
1. **Per-section measurement** (TASK-1) — a maintainer sees, via the existing trace path, exactly how
   the ~5k tokens split across seed / mindsets / each rule. The baseline every later step is judged
   against. Zero behavioral risk.
2. **Capability-gated rules** (TASK-2) — `06_skill_protocol` / `07_memory_protocol` emitted only when
   the relevant capability is registered. Byte-identical to today when both are present; a
   skills-disabled session never carries the skill protocol.
3. **A measured verdict on the mindsets block** (TASK-3) — does all-six load change behavior vs its
   absence (Pair 1, the real-estate question), and does flat-load cost us vs a focused subset (Pair 2,
   distraction). One of: *mindsets don't earn their tokens* (surface to TL), *work and flat-load is
   fine* (ship nothing on mindsets; router dead), or *work but a distraction gap exists* (→ TASK-4).
4. **Conditional editorial** (TASK-4) — sharper anchors (rules or mindsets) + a mindset selection nudge,
   only where measurement shows the verbose/flat form isn't carrying its weight; behavioral-eval-gated.

**Worst-case committed deliverable:** if TASK-1 shows capabilities are effectively never absent and
TASK-3 shows mindsets+flat-load is fine, the shipped value is the **measurement + ablation surface**
plus a settled §11.0/§11.2 — no common-case token savings. Gate 2 readers should not assume token
reduction from this cycle.

## Scope

### In scope
- **TASK-1 — instrumentation.** Per-section word+token emission from `build_base_instructions` (seed,
  mindsets, each `NN_rule`), debug-only, via the existing observability span. Reuses
  `estimate_text_tokens`.
- **TASK-2 — capability-gate procedural rules.** Partition `build_rules_block`'s content:
  - *Invariant* (always, stay in `build_base_instructions`): `01_identity`, `02_safety`, `03_reasoning`,
    `04_tool_protocol`, `05_workflow`.
  - *Gated* (assembled inside the base provider, never as trailing builders — see Design / CD-M-1):
    `06_skill_protocol` (gate `bool(deps.skill_catalog)`), `07_memory_protocol` (gate: a memory write
    tool in `deps.tool_catalog`).
  - Add `build_invariant_rules_block()` + `build_gated_rule(rule_id)`; `build_rules_block()` unchanged.
  - Gate-correctness verification is woven into the `done_when` (functional, not a heavy ablation — see
    Constraint #4).
- **TASK-3 — mindset real-estate ablation.** Rebuild `evals/eval_mindset_selection.py` (recover from
  git `a34f3c79`, reconcile to current infra) and **run it** (the diagnostic that was never executed).
  Arms differ only in the mindsets block; pairwise judge over both orders; decision tree below.
- **TASK-4 — conditional editorial (default NOT executed).** Sharper anchors (one over-weight rule
  *or* the six mindset trigger lines) + the mindset selection nudge in `load_soul_mindsets`. Gated on a
  measured gap from TASK-1/TASK-3; one file at a time; behavioral-eval-gated; doctrine-level review.

### Out of scope (deferred — see `## Deferred`)
- **Mindset selection router**, static/dynamic split, manifest, triage object, `risk_level`.
- **`04_tool_protocol` / `05_workflow` gating** (apply on ~every session; gating saves ~nothing).
- **Editing rule/mindset *content*** beyond TASK-4's conditional, eval-gated, one-file editorial.
- **Building `CoCapabilityState`** — gate on existing catalogs.
- **P1 (style schema / resolver / `/style`) and P2 (typed preference memory)** — additive frontier
  follow-ons, a different thesis from this subtractive plan.

## Behavioral Constraints
1. **Gate on existing catalogs, not a new registry.** The base provider passes `deps.skill_catalog` /
   `deps.tool_catalog` (`deps.py:308-310`) into `build_base_instructions`, mirroring `guidance.py:26`.
   No `CoCapabilityState`, no new deps field, no new spec tuple entries.
2. **Session-scoped, not turn-scoped.** Gating lives in `static_instruction_builders` (cached once),
   not `per_turn_instructions` — the protocol must be present before the model decides to use the
   capability. The win is cross-session, by design.
3. **Byte-identical when present, proven at the full-prefix level.** With both skills and memory tools
   registered, the full assembled static prefix — *all* `static_instruction_builders` joined via the
   real `build.py:39` path — must be byte-for-byte identical before and after TASK-2. Gated rules are
   emitted inside the base provider so `01-07` stay contiguous (CD-M-1). The check is
   full-prefix-before vs full-prefix-after, NOT base-output vs `seed+mindsets+rules` (CD-M-2).
4. **Verification regime is split by what is removed.** *Capability-gating (TASK-2)* removes a block
   only when the capability is absent — the protocol was inapplicable anyway — so its safety argument
   is **structural**, verified by a functional gate-correctness + byte-identical test, not a behavioral
   ablation. *Mindset ROI (TASK-3)* and *content editorial (TASK-4)* remove/change text that **is**
   applicable, so they require a **behavioral pairwise ablation**. State this so the structural path
   isn't mistaken for skipping rigor.
5. **`build_rules_block()` output is frozen.** Its live consumer is `test_instruction_floor_coupling.py:41`
   — NOT the deleted `eval_mindset_selection.py`. Do not change what it returns; add
   `build_invariant_rules_block()`. After TASK-2 the floor test exercises a *superset* of the live
   floor (it still includes `06`/`07` when production gates them out) — acceptable; state it.
6. **Arms compose from public seams; no mindset toggle in production (TASK-3).** The eval assembles each
   arm as `load_soul_seed(role)` + an arm-specific mindsets string + `build_rules_block()`. It adds no
   on/off switch or arm parameter to production assembly (`feedback_no_eval_test_driven_api`).
7. **Pairwise over absolute (TASK-3).** Verdict is a preference win-rate, never an absolute 0-10 score.
   Run each comparison in both orders ((A,B) and (B,A)); disagreement = tie. Pin and record the judge
   model (`deps.judge_model`).
8. **Anchors are additive, role-neutral (TASK-4).** One trigger line per file under the existing
   heading, above existing bullets — no deletion/rewrite of soul content; task-intrinsic wording,
   identical across roles. The nudge stays in the static (cached) prefix; the core loop is untouched.
9. **Rule & mindset files are core doctrine.** TASK-4 edits alter intrinsic agent behavior →
   core-level review + behavioral eval gate, one file at a time, never automated.
10. **Real everything in evals** (`feedback_eval_real_world_data`): real `make_eval_deps()`, config
    `llm.host`, real model; `ensure_ollama_warm()` **outside** any `asyncio.timeout`; centralized
    settings (`evals/_settings.py` / `_deps.py` / `_timeouts.py`). No caps, no test stores.
11. **No hardcoded paths** — `USER_DIR` / `CO_HOME` derived constants only.

## High-Level Design

**TASK-1 — instrumentation.** Add a private `_section_token_report(parts: list[tuple[str, str]]) ->
None` in `assembly.py` that, when an observability span is active, emits one structured event per
section with word + `estimate_text_tokens` counts keyed by label (`seed`, `mindsets`, `rule:01_identity`,
…) via `current_span().add_event(...)`. Off-stack, `current_span()` returns `_NoOpSpan` so it is a
no-op and the pure return value is unchanged. `build_base_instructions` labels each part as it appends
and calls the reporter before returning. Bootstrap already runs inside a trace → `co trace <id>`
surfaces the breakdown.

**TASK-2 — gating (emitted INSIDE the base provider — CD-M-1).** A trailing provider cannot preserve
order: `build.py:39` joins `static_instruction_builders` with `\n\n` in tuple order, and toolset-guidance
+ critique are non-empty for `tars`, so trailing gated providers would push `06`/`07` behind them. So
the gated rules are assembled within the base provider's single string. In `assembly.py`:
- `build_gated_rule(rule_id: str) -> str` — one rule's `.strip()`-ed text by id (same strip as
  `build_rules_block`, `assembly.py:77`).
- `build_invariant_rules_block()` — `01-05` via `_collect_rule_files` filtered by `rule_id`.
- `build_base_instructions(config, *, skill_catalog, tool_catalog)` assembles invariant `01-05` **plus**
  `build_gated_rule("skill_protocol")` iff `skill_catalog` **plus** `build_gated_rule("memory_protocol")`
  iff `_has_memory_write_tool(tool_catalog)` — joined with the same `"\n\n"` as `build_rules_block`
  (`assembly.py:80`), so `01-07` stay contiguous and byte-identical when both capabilities are present.
- `_base_instructions_provider(deps)` passes `deps.config, deps.skill_catalog, deps.tool_catalog`; the
  bootstrap floor call (`bootstrap/core.py:485`) passes the same. `ORCHESTRATOR_SPEC.static_instruction_builders`
  is unchanged.
- `_has_memory_write_tool(tool_catalog)` = any of `memory_create` / `memory_append` / `memory_replace`
  / `memory_delete` in `tool_catalog` (all `VisibilityPolicyEnum.ALWAYS`), via `name in tool_catalog`
  (mirrors `guidance.py:26`).

**TASK-3 — mindset ablation.** Rebuild `evals/eval_mindset_selection.py` from `a34f3c79`, reconciled:
use shared `response_text(turn_result)` (drop the recovered `_response_text`); write per-run JSONL via
`open_eval_run("mindset_selection")` (drop `prepend_report` / the markdown REPORT); reuse the unchanged
`judge_pairwise`. Mirror a current eval's structure (`eval_skills.py`).
- **Arms** (differ only in the mindsets block): **A0** seed+rules (no `## Mindsets`); **A1** all-six
  (production); **A2** relevant-only (the matching mindset(s) for the case — two for composites); **A3
  (opt)** a single plausible-but-wrong mindset.
- **Cases:** one prompt per task shape (technical, exploration, debugging, teaching, emotional, memory)
  + 2 composites (debug+teach, technical+emotional), each with a target-stance description. Role
  **tars**; trigger lines (if later authored) are role-neutral so finch/jeff transfer is assumed, not
  measured — state in the run record.
- **Metric:** per-case winner requires both order-swapped judgments to agree (else tie); an arm is
  "reliably preferred" if it wins a clear majority of *decisive* (non-tie) cases and decisive cases are
  themselves a majority. Report per-case winners + decisive/tie counts + judge model.
- **Decision tree:**
  - **Pair 1 (A1 vs A0) not reliably preferred** → block is inert; surface to TL as a doctrine
    decision (cut/rebuild). TASK-4 mindset branch moot.
  - **Pair 1 positive, Pair 2 (A1 vs A2) ≈** → mindsets work and flat-load isn't hurting. Ship nothing
    on mindsets; router dead. Update §11.2.
  - **Pair 1 positive, Pair 2 A2 > A1** → distraction gap; run **Pair 3 (A2 vs A3)**: A2 ≈ A3 → lift is
    prose-presence not content (structural — surface to TL, skip authoring); A2 > A3 → content steers →
    **TASK-4 mindset branch**.

**TASK-4 (conditional).** Editorial only, gated on measurement; no design committed up front. Two
independent triggers:
- *Rules:* if TASK-1 + a behavioral eval show a specific verbose rule isn't carrying its weight, sharpen
  it to an anchor (one file).
- *Mindsets:* if TASK-3 reaches the A2>A3 branch, add the six role-neutral trigger lines + the selection
  nudge in `load_soul_mindsets` (`"## Mindsets\n\n" + "Identify which task shape(s) this turn is and
  lead with the matching mindset; treat the others as background.\n\n" + …`), then **rerun the ablation**
  with a fourth arm **A1′ (all-six + anchors + nudge)** judged A1′ vs A2: A1′ ≈ A2 → authoring closed
  the gap, ship; A2 still > A1′ → residual gap justifies+scopes the router (surface to TL, do NOT
  auto-build).

## Tasks

**TASK-1 — Per-section instrumentation of `build_base_instructions`**
- files: `co_cli/context/assembly.py`, `tests/test_prompt_section_instrumentation.py` (new)
- done_when: a `pytest` test wraps the assembly call in a real span (the spans-logger fixture used by
  `test_flow_observability_spans.py`) and asserts one count event per section (`seed`, `mindsets`, one
  per rule id) each carrying a positive token count; and `build_base_instructions` return is
  byte-identical to before (off-span no-op via `_NoOpSpan`).
- success_signal: `co trace <bootstrap-trace-id>` shows the static prefix broken down per section.
- prerequisites: none

**TASK-2 — Capability-gate `06_skill_protocol` and `07_memory_protocol`**
- files: `co_cli/context/assembly.py`, `co_cli/agent/orchestrator.py`, `tests/test_instruction_floor_coupling.py`
- done_when: a parametrized test drives the **full** static prefix through the real
  `static_instruction_builders` join (`build.py`) and asserts: (a) with non-empty `skill_catalog` and a
  memory write tool in `tool_catalog`, the full prefix is **byte-identical** to the pre-change full
  prefix; (b) `skill_catalog={}` → `06_skill_protocol` text absent; (c) no memory write tool →
  `07_memory_protocol` text absent; (d) `build_rules_block()` output unchanged. Gated-rule join/strip
  must match `build_rules_block` exactly (`"\n\n"`, per-rule `.strip()`).
- success_signal: a skills-disabled session's static prefix is measurably lighter (TASK-1 report shows
  `rule:06_skill_protocol` gone) with the full-prefix byte-identical guarantee when present.
- prerequisites: TASK-1 (its report reads ROI/regression)

**TASK-3 — Rebuild and run the mindset real-estate ablation**
- files: `evals/eval_mindset_selection.py` (rebuilt), `evals/_judge.py` (reuse only)
- done_when: harness recovered from `a34f3c79` and reconciled to current infra (shared `response_text`;
  JSONL via `open_eval_run`; no markdown REPORT); `uv run python evals/eval_mindset_selection.py` runs
  on real warm 35B, produces the per-run JSONL with per-case winners, decisive/tie counts, judge model,
  and the decision-tree branch reached. The **A1-vs-A0 headline** ("do the mindset tokens pay rent") is
  stated first in the run record. `judge_pairwise` is no longer orphaned.
- success_signal: a reproducible measured branch (INERT / FLAT_OK / AUTHOR / STRUCTURAL) the TL reads at
  the gate.
- prerequisites: TASK-1 (token baseline for interpreting arm cost). Watch warm-call durations
  (`feedback_llm_call_timing`); bump `_timeouts` only if warm timing demands it.

**TASK-4 — Conditional editorial: anchors + mindset nudge (default NOT executed)**
- files: one of `co_cli/context/rules/*.md` *or* `co_cli/personality/prompts/souls/{finch,jeff,tars}/mindsets/*.md`
  + `co_cli/personality/prompts/loader.py` (nudge), behavioral `evals/eval_*` gate
- done_when: undertaken only if TASK-1 + a behavioral eval (rules) or TASK-3's A2>A3 branch (mindsets)
  show the verbose/flat form isn't carrying its weight. Rules branch: sharpen one file, eval shows no
  regression. Mindset branch: add six trigger lines + nudge, rerun ablation (A1′ vs A2); ship iff A1′≈A2.
  Otherwise explicitly closed as "not executed — measurement did not justify."
- success_signal: N/A (conditional; prompt-weight reduction with eval parity)
- prerequisites: TASK-1, TASK-2, TASK-3

## Testing
- `tests/test_prompt_section_instrumentation.py` (new) — per-section emission under a real span (TASK-1).
- `tests/test_instruction_floor_coupling.py` — add full-prefix capability-config assertions incl.
  byte-identical-when-present (TASK-2); it already drives `build_rules_block()` + assembly.
- Re-run `tests/test_instruction_budget.py` and `tests/test_orchestrator_schema_budget.py` (they import
  from `assembly`) to confirm the floor holds with the new signature.
- TASK-3 is an eval (`uv run`), not pytest: real warm 35B, JSONL output, `ensure_ollama_warm` outside
  any `asyncio.timeout`.
- TASK-4's behavioral eval is conditional and out of the committed test set.
- All pytest runs piped to a timestamped `.pytest-logs/` file; tail the log to watch LLM timing.

## Open Questions
1. **Is capability-gating worth shipping?** Resolved by data: after TASK-1, check how often real
   sessions register zero skills / no memory tools. Skills can be empty (gate `06` likely pays); memory
   tools may be core-always-on (gate `07` may not) — TASK-1 settles it; a never-absent capability's gate
   does not ship.
2. **`04_tool_protocol` / `05_workflow`** — kept invariant (Open: does TASK-1 reveal a procedural-only
   sub-block of `05` that is absent-gateable? If so → TASK-4 candidate, not TASK-2).
3. **Mindset case count (TASK-3)** — coverage by task shape, not a count target
   (`feedback_no_test_count_rule`); arms × cases × order-swap is the call budget — keep N tight, let
   warm-call timing guide it.

## Deferred — follow-ons (documented, not in scope)
- **Mindset selection router** — built only if TASK-4's mindset branch leaves A2 > A1′ (a measured
  distraction gap authoring can't close). Single constrained LLM call, temp=0, structured output limited
  to the 6 labels (multi-label), returning a triage object (`{task_labels, …}`) extensible to
  `risk_level`. Static/dynamic split: static prefix keeps seed + always-on `base` + a 6-line mindset
  *manifest* + rules + critique (cached); dynamic suffix injects only the selected mindset body late
  (recency); manifest is the safety floor (a routing miss degrades to today). Fallback: router error →
  all six.
- **P1 — working-style schema + resolver** (`style.yaml`, `load_soul_style`, validator fail-closed,
  `ResolvedStyle` + `resolve_working_style()`, per-turn `## Working Style` provider, read-only `/style`,
  precedence contract). Frontier (✗ peer-backed); resolver is a `per_turn_instructions` provider. Plan
  only after this plan gives a measured baseline. PyYAML is an implicit dep (`co_cli/memory/frontmatter.py`)
  — P1 adds it explicitly to `pyproject.toml`.
- **P2 — typed preference memory.** Optional `type`/`scope`/`strength`/`supersedes`/`superseded_by`/
  `do_not_override` on `MemoryItem` (`co_cli/memory/item.py`) + frontmatter + `save_memory_item`
  (`co_cli/memory/service.py`). Real write tools are `memory_create`/`memory_append`/`memory_replace`/
  `memory_delete` (`co_cli/tools/memory/manage.py`); the dream reviewer (`co_cli/daemons/dream/_reviewer.py`,
  `prompts/memory_review.md`) is the extraction hook. Letta-backed (survey §A), eval-gated.

## Decision record
Lean, evidence-first, **measure-before-fix**, core-flow scoped. One plan thins the whole static prefix:
TASK-1 instruments it (the shared baseline), TASK-2 thins the rules structurally (byte-identical-safe
capability-gating), TASK-3 measures the mindsets block behaviorally (pairwise ablation — the question
that was built then deleted before running), TASK-4 sharpens any over-weight section only on a measured
gap. The two verification regimes are deliberate, not inconsistent: structural for removing inapplicable
protocol, behavioral for removing applicable content (Constraint #4). The router and the P1/P2 self-model
work are downstream of measured signal, not suspicion. Even a "ship nothing" outcome delivers a settled
§11.0/§11.2 and a standing measurement + ablation guard. Full rationale in
`docs/reference/RESEARCH-personality-architecture.md` §11.

---

## Final — Team Lead

Plan re-scoped (merged from two plans, 2026-06-13) — supersedes prior Gate-1 approval; re-approval needed.

> Gate 1 — PO + TL review required before proceeding.
> Right problem? Correct scope? Note TASK-3 carries a rebuild precondition (recover + reconcile the
> deleted harness) and a real warm-35B run; TASK-1/TASK-2 are ready and low-risk.
> Once approved: `/orchestrate-dev personality-static-prefix-refactor`.
