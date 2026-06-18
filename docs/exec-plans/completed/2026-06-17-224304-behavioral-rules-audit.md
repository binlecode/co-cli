# Behavioral Rules Audit — compare co's 7 rules against open-source peers, validate each rule section-by-section on the configured model, then consolidate / clean / fill gaps on evidence (LLM-eval-gated)

Task type: audit + measurement initiative (peer semantic comparison + section-level instruction-following ablation) → conditional, evidence-scoped rule edits, eval-gated. Whole-codebase-audit class (`project_architecture_erosion_tension`) applied to the prompt's behavioral core. Builds directly on `per-model-prompt-calibration` (delivered this session): that plan validated rules at FILE level for `03`/`07` only; this plan validates every SECTION of all 7 rules and adds the peer-comparison axis.

## Context

co's behavioral core is 7 numbered rule files (`co_cli/context/rules/01_identity.md` … `07_memory_protocol.md`),
assembled verbatim by `build_rules_block` (`co_cli/context/assembly.py`) into the static prefix. With
personality off by default, these rules ARE the agent's behavioral prompt. They were authored as
descriptive prose and never systematically validated against (a) what peer agents actually instruct, or
(b) whether the configured local model follows each section.

Two prior measurements frame this plan:
- `per-model-prompt-calibration` (delivered): file-level ablation showed `03_reasoning` and
  `07_memory_protocol` are HONORED (Δ+0.75, Δ+0.50) — but only ONE behavior slice of each was probed
  (arithmetic-via-tool for `03`, recall-before-answering for `07`). The other sections of those two
  rules, and all of `01/02/04/05/06`, are unvalidated. The reusable harness is
  `evals/eval_rule_compliance.py` (file-level ablation via a custom `OrchestratorSpec` that swaps in a
  rules block with one file removed; deterministic tool-call scoring; NON-DISCRIMINATING saturation guard
  for tasks that can't separate the rule's effect).
- `summarizer-fidelity-measure` (delivered): the same model IGNORES a demanding 13-section template
  (REVISE) while honoring light behavioral rules. So rule effectiveness is not uniform — it depends on
  how heavy/structured the instruction is. Section-ablation will place each rule section on that spectrum.

### Peer sources (verified present on disk this session)
- opencode — `~/workspace_genai/opencode/packages/core/src/session/prompt.ts` (+ per-model prompt files);
  the hand-tuned-per-model exemplar (`docs/reference/RESEARCH-opencode-context-conciseness-architecture.md`).
- hermes-agent — `~/workspace_genai/hermes-agent/agent/system_prompt.py` (use `hermes-agent`, not the
  absent `hermes`; `reference_hermes_repos`).
- codex — `~/workspace_genai/codex/codex-rs/core/*_prompt.md` (multiple per-model prompts).
- openclaw — `~/workspace_genai/openclaw/docs/concepts/system-prompt.md` + prompt-overlay sources.
- Background: `docs/reference/RESEARCH-context-management-peer-survey.md` and peers' existing survey docs.
- Claude Code excluded — full system prompt not available to diff.

## Failure Modes (observed, not imagined)

- **Coverage blind spot.** We have never diffed co's rules against peers, so we cannot say whether a
  reported struggle (reasoning, recall) is a missing instruction co's peers include, or a co-specific
  design choice. Gaps are currently invisible.
- **Validation blind spot.** ~85% of the rule word count (5 whole rules + the untested sections of
  `03`/`07`) has zero evidence it changes the configured model's behavior. The "are the rules bloated?"
  question is currently unanswerable — bloat and dead weight are indistinguishable from load-bearing prose.
- **Saturation trap (already hit once).** Naive probes that command the behavior ("compute the hash")
  saturate both arms and falsely read as IGNORED. The harness now guards this (NON-DISCRIMINATING), but
  every new section probe must be implicit or it measures noise.
- **Untestable-section trap.** Some sections steer *response content/tone* (e.g. `03` source-conflict
  surfacing, `01` identity), not *tool calls*. A tool-call-only signal cannot score them; scoring them
  needs a judged property with a stated threshold, or they are honestly marked out of harness reach.

## Problem & Outcome

**Problem.** co's 7 behavioral rules are unvalidated against peers (coverage gaps unknown) and largely
unvalidated against the configured model (effectiveness/bloat unknown). Edits to date have been prose
judgment, not evidence. Without this audit, the rule-set drifts: dead weight accretes (token cost, the
erosion tension) and real gaps persist (reported reasoning/recall struggles may be missing instructions).

**Failure cost:** two ways to lose. (1) Keep editing rule prose on opinion → bloat accretes and gaps
persist invisibly; the prompt's behavioral core erodes the way the architecture does (42%-cleanup-commit
pattern). (2) Rewrite/cut rules speculatively from the peer diff alone, without the per-section model
evidence → cut a section that was quietly load-bearing on THIS model, or import a peer instruction the
model ignores — regressing behavior while believing it improved.

**Outcome (measure + compare first).**
1. **A peer coverage map (COMPARE).** A research artifact diffing co's rules vs the four peers per topic:
   what co covers that peers don't, what peers cover that co lacks (gap candidates), where co is heavier
   or redundant (consolidation candidates). Decisive deliverable on its own.
2. **A per-section effectiveness map (VALIDATE).** For every rule section that maps to an observable
   behavior, a measured STEERS / DEAD-WEIGHT verdict on the configured model at a sample size large
   enough to be more than directional; sections with no observable signal explicitly marked out-of-reach.
3. **Only on evidence — scoped rule edits (ACT, conditional).** Consolidate measured DEAD-WEIGHT, rewrite
   IGNORED-but-load-bearing sections (prose → few-shot), fill peer-confirmed gaps — each change re-validated
   by re-running the section-ablation (GATE) before it lands. "Rules are well-calibrated, change nothing"
   is a valid, shippable outcome.

**This plan's shippable contract is the two evidence artifacts (the COMPARE coverage map + the VALIDATE
per-section effectiveness map).** ACT (lane 3) is opportunistic and bounded — it lands only the small set
of edits the evidence directly justifies (≤3 sections in-plan); a large change-set escalates to its own
Gate-1 plan. It is expected and acceptable that ACT may land little or nothing in this plan.

## Scope

### In scope
- Semantic comparison of all 7 co rules against opencode, hermes-agent, codex, openclaw system prompts.
- Section-level ablation harness extension covering all 7 rules; per-section effectiveness verdict where a
  behavior is observable.
- Conditional, evidence-scoped edits to specific rule sections (consolidate / rewrite / gap-fill), each
  re-validated.

### Out of scope
- The summarizer/compaction prompt (owned by the summarizer plans).
- Re-introducing personality/persona (orthogonal; persona stays off).
- Per-model routing `switch` (deferred unless a second configured model forces it — same stance as
  `per-model-prompt-calibration`).
- Any `docs/specs/` edit as a task (sync-doc handles spec drift post-delivery).
- Tone/identity sections that have no observable behavioral signal — diagnosed and reported, not edited
  blind.

## Behavioral Constraints
- All eval data real (`feedback_eval_real_world_data`); centralized eval settings only
  (`feedback_evals_centralized_settings`); `llm.host` + `reasoning_model_settings()` /
  `noreason_model_settings()` per call type (`feedback_tests_use_config_model_settings`).
- Editing any rule `.md` trips the instruction-floor guards (budget ceiling + F5 no-deferred-tool
  signature) — run them during ACT; keep `tool_name(` call syntax out of rule prose
  (`feedback_instruction_floor_guards_on_rule_edits`).
- Rule prose is core-prompt change — it alters intrinsic agent behavior; treat ACT edits with core-level
  review.
- Deterministic-first scoring; any judged property carries a stated threshold up front or it does not
  count toward a verdict. One section ablated at a time, all else fixed.
- Section probes must be implicit (never command the target behavior) or the arms saturate
  (NON-DISCRIMINATING).
- Net static-floor token delta tracked on every ACT edit; a change that grows the floor without measured
  lift is rejected.

## High-Level Design

Three sequential lanes, ACT/GATE conditional on the first two:

1. **COMPARE (TASK-1)** — read co's rules + peer prompts, build a topic × source coverage matrix, emit a
   research doc enumerating gap candidates and consolidation candidates with citations.
2. **VALIDATE (TASK-2)** — extend `eval_rule_compliance.py` to parse each rule into `##` sections, ablate
   one section at a time (reusing the custom-spec seam, now removing a section span rather than a whole
   file), run implicit discriminating probes at N≥20, emit a per-section JSONL effectiveness map. Sections
   with no observable signal are recorded as OUT-OF-REACH, not scored.
3. **ACT + GATE (TASK-3, conditional)** — for the specific sections the evidence flags (DEAD-WEIGHT from
   TASK-2, or gaps from TASK-1, or IGNORED-but-load-bearing), apply the scoped edit and re-run that
   section's ablation to confirm the change steers / the gap is now covered, within the floor budget.

## Tasks

✓ DONE **TASK-1 — Peer rules semantic comparison (COMPARE; always)**
- files: `docs/reference/RESEARCH-behavioral-rules-peer-comparison.md` (new)
- done_when: the research doc exists and contains (a) a topic × {co, opencode, hermes-agent, codex,
  openclaw} coverage matrix built by reading each peer's system-prompt source, (b) an enumerated **gap
  list** (topics ≥2 peers instruct that co omits), and (c) an enumerated **consolidation-candidate list**
  (co sections materially heavier than every peer's equivalent, or duplicated across co rules) — every row
  citing the co rule section AND the peer file path it was read from. The matrix is populated from the peer
  sources FIRST, then reconciled against the head-start table below: any head-start hypothesis the peer diff
  does NOT support is recorded as explicitly contradicted, not silently dropped (keeps COMPARE independent).
- success_signal: a peer-grounded map naming concrete, sourced gap and consolidation candidates to feed ACT.
- prerequisites: none

> **TASK-1 head-start — pre-seeded candidates (eyeball pass over all 32 sections, source-read this
> session). HYPOTHESES, not decisions: every one must be confirmed by the peer diff (COMPARE) and, before
> any edit, measured by section-ablation (VALIDATE). Listed so COMPARE starts warm, not cold.**
>
> *Dominant theme — cross-rule duplication:* "persist / don't stop half-done / execute-don't-promise /
> verify-completeness" is restated ≥5× across `01 Thoroughness over speed`, `04 Strategy→Follow through`,
> `04 Execute, don't promise`, `05 Execution`, `05 Completeness`. Strongest consolidation target — one
> "persistence & completion" section. (Restating MIGHT be what drives compliance — ablate each before cutting.)
>
> | Rule · Section | Flag | Note |
> |---|---|---|
> | `04 ## Memory` | cleanup | Cross-ref stub → 07; delete, no behavior lost. |
> | `04 ## Strategy` | simplify | Kitchen-sink (6 ideas bundled); "follow through" duplicates 01/05. |
> | `04 ## Execute, don't promise` | consolidate | Near-duplicate of `05 Execution`. |
> | `03 ## Fact authority` + `## Source conflicts` | consolidate | Both "handling contradictions" — merge. |
> | `03 ## Verification` | simplify | Longest 03 section; verbose time/system/file/git enumeration. |
> | `05 ## Completeness` | partial | Validation-pass checklist is unique — KEEP; "follow through" framing overlaps 04. |
> | `06 ## Create` + `## Offer-to-save` | review | Autonomous vs collaborative creation — reads redundant. |
> | `06 ## Background review` | tension | Tells agent not to double up on Drift/Create — partially undercuts them. |
> | `07 ## Curation`, `## Anti-patterns` | simplify? | Heaviest rule's longest prose; recall is the struggle area — measure hard first. |
> | `01`, `02` (all) | keep | Leanest rules (84 / 142 w); tight, distinct, no dead weight. |
>
> *Gap candidates (weak — COMPARE is authoritative):* (1) no dedicated **output-formatting / verbosity**
> rule (only `05 When NOT to over-plan` grazes it; opencode invests heavily here); (2) no explicit
> **tool-call-budget / when-to-stop-searching** guidance beyond `05`'s blocked-sub-goal note.
>
> *Two gating caveats (C4):*
> - **OUT-OF-REACH rows have no ablation gate.** Most flagged rows (`04 Strategy`/`Execute-don't-promise`,
>   `05 Completeness`, `06 Create`/`Offer-to-save`, `03 Source conflicts`) are content/tone steers TASK-2
>   cannot score. They can be acted on ONLY via the COMPARE peer-diff + core-level review — never an
>   ablation gate (none exists for them). Do not read "consolidate" as "measured-safe to cut."
> - **The dominant persistence/completion consolidation is multi-file (5 spans across 01/04/05).** It
>   exceeds the ≤3-section in-plan ACT cap and has no single "the section" to re-ablate — it almost
>   certainly **escalates to its own Gate-1 plan**, whose GATE is whole-assembly re-ablation of the merged
>   section (aggregate persistence/completion fire-rate unchanged vs the pre-edit assembly), not a
>   single-span re-run.

✓ DONE **TASK-2 — Section-level effectiveness ablation (VALIDATE; always)**
- files: `evals/eval_rule_compliance.py` (extend), `evals/_settings.py` / `_deps.py` (reuse)
- done_when (two sub-deliverables):
  - **(a) Section-observability inventory (produced FIRST, before any probe authoring).** A table of every
    `##` section across all 7 rules (~32 total) classified OBSERVABLE (maps to a tool-call signal — names
    the target tool, e.g. `03 Verification`→`shell_exec`, `07 Recall`→recall tools, `05 Execution`→todo,
    `06`→skill tools) or OUT-OF-REACH (steers response content/tone — no tool-call signal; identity/safety/
    most-of-`04`/`07 Curation`+`Anti-patterns` are expected here, marked from inspection, NOT from eval
    budget). Sections that share one target tool are recorded as a single **distinguishable signal** (they
    cannot be ablation-scored independently). Expected OBSERVABLE subset is ~8-10 distinguishable signals
    of 32 sections — coverage is claimed only over this subset. Pre-identified this cycle:
    `04_tool_protocol.md` `## Memory` is a pure cross-reference stub ("See `07_memory_protocol.md`…") — not
    a behavioral instruction; classify OUT-OF-REACH AND flag it to TASK-1 as a consolidation candidate.
  - **(b) Ablation run over the OBSERVABLE subset.** `uv run python evals/eval_rule_compliance.py` removes
    one `##` section span at a time (all other content held fixed) and records a JSONL per-section verdict:
    STEERS / DEAD-WEIGHT / NON-DISCRIMINATING. Two assertions guard assembly fidelity: the full arm is
    byte-equal to `build_rules_block`, AND the ablated arm equals `full_block.replace(section_span, "")`
    (exactly one span removed, H1 title + inter-section joins intact — catches reassembly drift the
    full-arm guard cannot). Implicit discriminating probes only (commanding the behavior saturates →
    NON-DISCRIMINATING). N≥20 samples/arm for the decisive sections; raw fire-rates always recorded so the
    verdict is transparent at any N. This is a deliberate **long-form eval pass** (~400 turns at the
    observable subset, ~1.7h), NOT bundled into the suite/CI — tail the log, RCA-first on slow calls.
  - **Parser note (verified against source this cycle).** Section count is exactly **32** `##` spans
    (01:3, 02:4, 03:4, 04:7, 05:4, 06:6, 07:4). The span parser MUST split on `^## ` boundaries per file
    and NOT assume a leading `# ` H1 — `01_identity.md` has no H1 and opens directly on `## Relationship`.
    `**bold**` sub-blocks (e.g. `07` Cross-session recall cascade, Kind selection) live INSIDE `##` spans;
    `##`-span is the ablation unit (Open Q1) unless a span bundles unrelated behaviors.
- success_signal: a reproducible per-section map of which rule paragraphs actually steer the configured
  model and which are unvalidated dead weight, plus the honest OBSERVABLE/OUT-OF-REACH split.
- prerequisites: none (TASK-1 may inform probe design but does not block measurement)

✓ DONE **TASK-3 — Evidence-scoped rule edits + re-validation (ACT + GATE; conditional, ≤3 sections in-plan)**
- files: the specific `co_cli/context/rules/0N_*.md` sections flagged by TASK-1/TASK-2; `tests/` behavioral
  test if a gap-fill adds a newly-required behavior
- scope cap: at most **3 sections** edited in-plan (core-prompt change carries core-level review); a larger
  change-set escalates to a separate `rules-conformance-cleanup`-class plan re-entering at Gate 1.
- done_when: every adopted edit is applied to its specific section AND re-validated by a per-edit-class
  machine-checkable criterion, with net static-floor token delta recorded and within the instruction-floor
  budget, instruction-floor guard tests passing, a repo-wide grep confirming no stale reference to any
  removed/renamed rule section anchor, and the full test suite passing:
  - **consolidate DEAD-WEIGHT** → re-run the section's ablation: behavior fire-rate unchanged vs the
    pre-edit full arm (no regression) AND net floor delta is negative (tokens actually saved).
  - **rewrite IGNORED-but-load-bearing (prose→few-shot)** → re-run ablation: the section now STEERS
    (full-arm fire-rate exceeds the ablated floor past the steer threshold).
  - **gap-fill (peer-confirmed)** → a probe for the new behavior now fires under the edited rules where it
    did not before.
- success_signal: each edited section measurably changes the configured model's behavior in the intended
  direction (or provably saves tokens with no regression) without busting the prompt budget.
- prerequisites: TASK-1 + TASK-2 (an adopted gap or DEAD-WEIGHT/IGNORED finding)

## Testing
- TASK-1 is a research artifact — no test; reviewed for citation accuracy at Gate 1 / review-impl.
- TASK-2 and all re-runs are evals (UAT smoke), artifacts under `evals/_outputs/`; tail the log every run,
  RCA-first on slow calls (`feedback_long_llm_call_rca_first`, `feedback_tail_log_every_test_run`).
- TASK-3 behavioral pytest (only if a gap-fill adds a required behavior) asserts observable behavior change,
  never rule-text structure (`feedback_functional_tests_only`); fail-fast `-x`; pipe to `.pytest-logs/`.
- TASK-3 also runs the instruction-floor guard tests (budget ceiling + F5) and the full suite per the
  rename/drop done_when rule.

## Open Questions
1. **Section parsing granularity.** Rules use `##` (and some `**bold**`) sub-headings. Is `##`-span the
   right ablation unit, or do some sections need finer (paragraph) splitting? Default: `##` span; revisit
   if the TASK-2 inventory finds a section bundling unrelated behaviors.

Resolved during C1 review (now folded into the tasks, no longer open):
- *Non-tool-observable sections* — TASK-2(a) inventory reports the OBSERVABLE/OUT-OF-REACH split explicitly;
  coverage is claimed only over the OBSERVABLE subset (~8-10 distinguishable signals); identity/safety/tone
  marked OUT-OF-REACH from inspection, not from eval budget.
- *Probe authoring cost / run sizing* — scoped to the OBSERVABLE subset (~400 turns, ~1.7h long-form pass),
  not all 32 sections; sections sharing a target tool are jointly scored as one distinguishable signal.
- *ACT boundary* — hard-capped at ≤3 sections in-plan; larger change-sets escalate to a separate Gate-1
  plan (TASK-3 scope cap).

---
## Decisions

PO approved at C1 (`Blocking: none`). Core Dev raised 6 issues at C1 (all adopted) and approved at C2
(`Blocking: none`). Convergence reached. C3 refinement (source-grounding re-review): verified the section
count (exactly 32) and folded two source-grounded findings — no new blockers, scope unchanged. C4 (after
the TASK-1 head-start triage was added): both critics approved `Blocking: none`; adopted 3 minors hardening
the head-start gates — no new blockers, scope unchanged.

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | ~22 of 32 sections have no tool-call signal; an implied 32/32 coverage claim is dishonest. | TASK-2: added an up-front **section-observability inventory** sub-deliverable (all 32 `##` sections → OBSERVABLE/OUT-OF-REACH + named target signal) produced BEFORE probe authoring; done_when now claims coverage only over the OBSERVABLE subset (~8-10 sections). |
| CD-M-2 | adopt | 32×N20×2 = 1280 turns (~5-14h) is intractable; observable subset (~10) ≈ 400 turns (~1.7h). | TASK-2: scoped the run to the OBSERVABLE subset, N≥20 only for decisive sections; added explicit "deliberate long-form eval pass, not a suite/CI run; tail-log + RCA-first" note. |
| CD-m-1 | adopt | Full-arm byte-equal guard cannot catch ablated-arm reassembly drift into a neighbor. | TASK-2 done_when: added assertion `ablated == full_block.replace(section_span, "")` (removed exactly one span, H1 title + inter-section joins preserved). |
| CD-m-2 | adopt | Sections sharing one target tool can't be independently ablation-scored. | TASK-2: inventory records **distinguishable signals**, not raw section count; co-signal sections noted as jointly-scored. |
| CD-m-3 | adopt | A consolidated DEAD-WEIGHT section has no "now STEERS" target. | TASK-3 done_when: split by edit class (consolidate-deadweight → no regression + negative floor delta; rewrite-ignored → now-STEERS; gap-fill → gap behavior now fires). |
| CD-m-4 | adopt | Core-prompt edits carry core-level review; unbounded ACT scope at Gate 1 is unsafe. | TASK-3: hard-capped in-plan ACT at ≤3 sections; above that, escalate to a separate Gate-1 plan. |
| PO-m-1 | adopt | Avoid burning UAT runs proving nulls on identity/safety/tone prose. | Merged into CD-M-1: identity/safety/tone sections marked OUT-OF-REACH from the COMPARE + inspection pass, not from eval budget. |
| PO-m-2 | adopt | User should not be surprised ACT may land little/nothing here. | Problem & Outcome: states the shippable contract is the two evidence artifacts (COMPARE map + VALIDATE map); ACT is opportunistic. |
| PO-m-3 | adopt | "no production code touched" is a constraint, not an outcome. | Dropped that clause from TASK-1 and TASK-2 success_signal lines. |
| TL-C3-1 | adopt | Section count was an estimate ("~32"); grounded it. Span parser would break on `01_identity.md` (no H1). | TASK-2(b): added a verified parser note — exactly 32 spans, split on `^## ` per file, do NOT assume a leading `# ` H1; `**bold**` sub-blocks live inside `##` spans. |
| TL-C3-2 | adopt | `04 ## Memory` is a cross-reference stub, not a behavior — would mislead the observability inventory. | TASK-2(a): pre-classified `04 ## Memory` OUT-OF-REACH and flagged it to TASK-1 as a consolidation candidate. |
| CD-m-1 (C4) | adopt | OUT-OF-REACH flagged rows have NO ablation gate; "consolidate" must not read as measured-safe. | Head-start: added caveat that OUT-OF-REACH rows act only on COMPARE diff + core review, never an ablation gate. |
| CD-m-2 (C4) | adopt | The dominant persistence/completion consolidation is 5 spans across 01/04/05 — exceeds the ≤3 cap, no single section to re-ablate. | Head-start: added caveat that it escalates to its own Gate-1 plan, GATE = whole-assembly re-ablation of the merged section. |
| PO-m-4 (C4) | adopt | The head-start verdict words could nudge COMPARE to confirm rather than independently derive. | TASK-1 done_when: matrix populated from peer sources FIRST, then reconciled against head-start; unsupported hypotheses recorded as contradicted. |
| CD-m-3, PO-m-5, PO-m-6 (C4) | acknowledge | Confirmations, not defects (count/parser verified; ablate-each-before-merge endorsed; gap candidates correctly scoped behind the triple gate). | — |

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | peer coverage matrix + gap list + consolidation list + head-start reconciliation, all source-cited | ✓ pass |
| TASK-2 | (a) 31/32-section observability inventory + (b) ablation run over the OBSERVABLE subset, JSONL verdict | ✓ pass |
| TASK-3 | ≤3-section evidence-scoped edit, re-validated, negative floor delta, grep clean, guards + full suite green | ✓ pass (1 section) |

**Artifacts**
- COMPARE: `docs/reference/RESEARCH-behavioral-rules-peer-comparison.md` — 8-topic × 5-source matrix; gaps **G1** output-formatting (4/4 peers, **strong**), G2 tool-call-budget (3 peers, moderate), G3 identity (design divergence), G4 todo-discipline; consolidation **C1** `04 Memory` stub, **C2** persistence cluster (5 spans across 01/04/05), C3–C7. Dominant-duplication hypothesis CONFIRMED; C3 framing corrected (internal-dup, no peer instructs it); nothing contradicted.
- VALIDATE: `evals/_outputs/rule-compliance-20260618-003339-run.jsonl` — section-ablation, N=20/arm, 0 timeouts.

**VALIDATE per-section map (6 OBSERVABLE distinguishable signals of 32)**

| Section | full | ablated | Δ | Verdict |
|---|---|---|---|---|
| `03 Verification` | 1.00 | 0.45 | +0.55 | **STEERS** (load-bearing — keep) |
| `03 Two kinds of unknowns` | 1.00 | 1.00 | 0 | NON-DISCRIMINATING (probe saturated) |
| `04 Deferred tools` | 0.00 | 0.00 | 0 | NON-DISCRIMINATING (probe floored) |
| `05 Execution` | 0.05 | 0.00 | +0.05 | DEAD-WEIGHT (weak — floored probe) |
| `06 Discovery` | 0.00 | 0.00 | 0 | NON-DISCRIMINATING (probe floored) |
| `07 Recall` | 0.75 | 0.80 | −0.05 | DEAD-WEIGHT (model recalls regardless) |

Inventory split: 6 PROBED · 4 OBSERVABLE-OUT-OF-HARNESS (todo_read / skill_create / skill_edit / memory_create — multi-turn or saturating) · 22 OUT-OF-REACH (content/tone). The 3 NON-DISCRIMINATING and 2 floored-DEAD-WEIGHT verdicts are probe-design limits (single-turn could not elicit tool_view / skill_view / todo_write), not confident dead-weight findings — so no section was cut on the ablation gate.

**ACT landed — C1 only.** Removed `04_tool_protocol.md` `## Memory` (pure cross-reference stub to 07; zero behavioral instruction). Justified by COMPARE (no peer ships a stub) + triple pre-classification (plan head-start, TASK-2(a) OUT-OF-REACH, inspection) — the OUT-OF-REACH path (COMPARE + core review), not an ablation gate. Floor delta **−97 chars** (18112→18015, personality=None). Eval inventory synced 32→31. Repo-wide grep: no stale anchor reference. Instruction-floor guards (budget + F5) pass; full suite 784 passed. Ceiling not re-pinned (−97 is below the documented cross-soul critique headroom; ceiling is calibrated against personality=tars, unmeasurable in the default personality=None env).

**Deferred to their own Gate-1 plans (out of the ≤3-section in-plan cap or off the ablation gate):**
- **C2** persistence/completion cluster (5 spans across 01/04/05) — multi-file; GATE = whole-assembly re-ablation of the merged section.
- **G1** output-formatting/verbosity gap-fill — strongest sourced gap (4/4 peers); content/tone, no ablation gate (COMPARE + core review).
- **C7** `07 Curation`/`Anti-patterns` simplify — gate-hard: recall is the reported struggle area; `07 Recall` measured DEAD-WEIGHT here only weakly (high baseline both arms), not license to cut.

**Tests:** floor guards 9 passed; full suite 784 passed. Eval: section-ablation run, 0 timeouts (trailing `RuntimeError` is benign httpx/openai stream-teardown noise after results were written — pre-existing eval-harness teardown pattern, not a measurement failure).
**Doc Sync:** clean — no spec references the removed section anchor (`personality.md:139` lists filenames; the file still exists).

**Overall: DELIVERED** — both evidence artifacts produced; one zero-risk COMPARE-confirmed section cut landed and re-validated; the substantive consolidations correctly escalate to their own Gate-1 plans per the in-plan cap.

## Implementation Review — 2026-06-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | peer matrix + gap list + consolidation list + reconciliation, all cited | ✓ pass | `RESEARCH-behavioral-rules-peer-comparison.md` — 12 spot-checked citations CONFIRMED at exact lines (codex `gpt_5_2_prompt.md:160-242`, hermes `prompt_builder.py:292,317`, codex `:29,111`); C3 correction verified by grepping all 4 peers for fact-authority/source-conflict — none found; (a)(b)(c)(d) all present |
| TASK-2 | (a) section-observability inventory + (b) ablation run with 2 fidelity guards | ✓ pass | `eval_rule_compliance.py` — `--inventory` exits clean at 31 sections; parser handles headerless `01_identity.md` (`_parse_file_sections`); `_rules_block_drop_section` is independent reassembly cross-checked against `full_block.replace(span,"")`; all 6 probes implicit; centralized eval settings (`_deps`/`_settings`/`_timeouts`), no inline ModelSettings |
| TASK-3 | ≤3-section edit, negative floor delta, grep clean, guards + full suite green | ✓ pass | `04_tool_protocol.md` — `## Memory` stub removed, ends clean at `## Deferred tools`; floor 18112→18015 (**−97**); grep: no production stale reference to the removed anchor; floor guards (budget + F5) pass; surgical diff (only 5-line stub removed) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Soft-anchor citation: `system-prompt.md:256` is blank (Skills content at `:53`) | `RESEARCH-behavioral-rules-peer-comparison.md:26,100` | minor | Left as-is — non-load-bearing cell; substantive Skills claim correctly anchored at `:53`; research artifact, not production |
| Latest `_outputs` artifact has 32 inventory records | `evals/_outputs/rule-compliance-20260618-003339-run.jsonl` | minor | Expected chronology — VALIDATE measured 32 sections, ACT removed the stub afterward; JSONL accurately records measurement-time state; re-running the 40-min pass would not change any of the 6 probed verdicts (removed section was OUT-OF-REACH/never-probed) |
| Budget ceiling not re-pinned after −97 trim | `tests/test_instruction_budget.py:52` | minor | Defensible per reviewer — trim widens tars headroom to ~403 (still < 25,000); guard still holds the right invariant; not required by done_when |

### Tests
- Command: `uv run pytest -q` (full suite)
- Result: **784 passed, 0 failed** (exit 0) — confirmed by two independent clean runs (219s; reviewer's 273s)
- RCA: one transient failure (`test_daemon_lifecycle.py::test_stop_daemon_terminates_process`) was a timing race from **two concurrent full-suite pytest runs** (review + reviewer subagent) contending on CPU — real-daemon SIGTERM-termination missed its window under load. Passes in isolation (2.58s), as a file unit (4 passed), and in both clean full runs. Not caused by this change (rule-section deletion cannot affect daemon lifecycle).

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads with the trimmed rules block)
- Rule-steering behavior is LLM-mediated (static-prompt assembly) — verified via the TASK-2 section-ablation eval (`03 Verification` STEERS Δ+0.55); chat interaction non-gating.

### Overall: PASS
All three tasks satisfy their done_when with file:line evidence; full suite green on clean runs; the single suite failure was diagnosed to concurrent-pytest CPU contention, not a defect; three minors recorded, none blocking.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev 2026-06-17-224304-behavioral-rules-audit`
