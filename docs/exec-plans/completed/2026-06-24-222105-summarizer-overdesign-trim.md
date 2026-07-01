# Summarizer schema granularity — measure before trimming

## Context
A peer survey (`docs/reference/RESEARCH-summarization-prompting-peer-survey.md`) plus a source-grounded review observed that co's conversation-compaction summarizer uses the **finest-grained summary schema of all five surveyed systems** — 14 sections in `_SUMMARIZE_PROMPT` (`co_cli/context/summarization.py:158–226`) — on its **weakest target model** (local qwen). Peers: codex 4-bullet, openclaw 5, opencode 7, hermes 13.

**This is a hypothesis, not a known defect — and deliberately framed neutrally.** The same survey (§6/§9) reads co's heavy scaffolding (caps, examples, `(none)` discipline, fine sections) as plausibly *deliberate weak-model adaptation* — more explicit structure can give a weak model more to populate, not just more chances to emit `(none)`. There is **no observed evidence** either way. The user's own standing guidance warns that fewer-sections-for-its-own-sake / peer-parity is an anti-signal, and that section count is not a defect absent evidence. So this plan **measures whether the granularity helps or hurts the configured model** before anyone trims. It does not trim.

Verified facts:
- `_SUMMARIZE_PROMPT` → `_build_summarizer_prompt` → `summarize_messages` is a closed private chain called only from `compaction.py`, reaching both the proactive path and `/compact` (`co_cli/commands/compact.py:34` → `compact_messages`). Single behavioral surface.
- `eval_context_stability.py` already drives real summarizer passes and extracts per-pass summary text (`_summary_text_from_output`, `_read_summarizer_passes`) and the load-bearing sections (`_LOAD_BEARING_SECTIONS = ("## Active Task", "## Next Step")`, `:128`). Measurement can reuse these signals **as-is** — no new prompt variant, no production param.

The separable, source-verified over-design — **double todo injection** — shipped independently as its own atomic plan (`2026-06-24-222105b-summarizer-todo-dedup.md`, now in `completed/`, v0.8.488). Its sibling language-preservation plan (`2026-06-24-133357-summarizer-language-preservation.md`) also shipped (`completed/`, v0.9.4) and **added the language-preservation preamble to `_SUMMARIZE_PROMPT` (`:168–172`)** — this is why the schema now spans `158–226` (still 14 sections). The "current schema" this plan measures therefore includes that preamble; all three plans stayed separate as designed.

## Problem & Outcome
We do not know whether co's 14-section schema helps or hurts the configured local model. Deciding to trim (or keep) on assertion risks either removing scaffolding the weak model relies on, or carrying dead surface that dilutes signal. Both are guesses. The fix is evidence.

**Outcome:** quantified evidence on the configured model — do the hypothesized failure modes occur, and at what rate — sufficient to decide whether a trim is warranted at all. If warranted, a pre-registered, test-safe trim design is handed to a follow-up plan. If not, the schema is kept and the question is closed.

**Failure cost (of not measuring):** decisions about the summarizer schema continue to be made on intuition and peer comparison rather than on how the configured model actually behaves — risking both directions (trimming away useful scaffolding, or leaving silent dead surface). Nothing crashes; the cost is decision quality.

## Hypotheses to measure (neutral — confirm OR refute)
- **H1 (duplication):** on a just-completed-task turn, `## Active Task` and `## Next Step` carry near-identical verbatim quotes of the same user line.
- **H2 (none-spam):** a non-trivial fraction of the 14 sections render `(none)` on typical compactions (dead surface).
- **H3 (load-bearing signals):** the current schema's drift-anchor presence, planted-fact recall, and prior-summary carry across ≥2 passes — the baseline any future trim must not regress.
- **H4 (tool-name hallucination):** the `## Completed Actions` mandate to end every entry with `[tool: name]` may induce invented tool names on a weak model (observed in a real trace). Measure invented-vs-actual tool names in that section.

Measurement instruments the **current** schema only. No trimmed variant is built or run here. **Scenario-validity caveat:** a hypothesis is only meaningfully measured if the scenario actually exercises it — H1 needs a just-completed-task turn, which the existing seeded scenario may not contain. An un-exercised hypothesis is recorded as **"not exercised (inconclusive)," never as "weak."**

## Scope
**In:**
- Measure H1/H2/H3/H4 on real summarizer passes over the existing seeded compaction scenario, on the configured model, reusing `eval_context_stability.py`'s existing extraction signals.
- Record the numbers; decide warranted/not-warranted.

**Out:**
- Any schema trim (deferred to an evidence-gated follow-up — see Decision gate).
- A two-variant ablation harness or a permanent `docs/REPORT-*.md` artifact — not built unless the numbers are decision-grade and worth persisting (would otherwise be a measurement rig heavier than the prompt it measures).
- Todo de-duplication — split to its own plan (`2026-06-24-222105b-summarizer-todo-dedup.md`).
- `extract_summary_body` prose re-parsing, budget-as-output-cap, prior-summary filter+slot, marker prose, the degradation ladder (breaker / anti-thrash / fit guard / overflow recovery) — all justified or separate concerns, unchanged.
- `docs/specs/` edits (owned by `sync-doc` post-delivery).

## Behavioral Constraints
- **Measurement-only — no production change.** TASK-1 touches no production code and ships no prompt variant. The summarizer prompt is observed, not modified.
- **Reuse existing signals; measure what they don't gate.** Drift-anchor presence is hard-gated (CS.B, `:734` gate / `:749` reason); planted-fact recall is **soft-gated only** (CS.A coherence probe, SOFT_FAIL, `~:578` — never a hard assertion, see `:37–38`); **prior-summary carry is logged, never gated** (`:597–625`), so the measurement must compute carry itself rather than rely on the eval to enforce it.
- **Functional-tests-only.** No structural assertion that a section string is present/absent in the prompt.

## High-Level Design
Add measurement-only instrumentation (in `eval_context_stability.py` or a small sibling analysis under `evals/`) that, over the existing seeded compaction scenario on the configured model, reads the already-extracted per-pass summary text and computes:
- **H1** — textual overlap between the `## Active Task` body and the `## Next Step` body (same quote on a just-completed-task turn?).
- **H2** — per-section `(none)` rate across the 14 sections.
- **H3** — drift-anchor presence, planted-fact recall, and prior-summary carry across ≥2 passes (carry computed directly, not assumed from the eval).
- **H4** — invented-vs-actual `[tool: name]` entries in `## Completed Actions`.

Numbers go to the eval log under `.pytest-logs/`. A `docs/REPORT-*.md` is produced **only if** the numbers are decision-grade and warrant persisting.

## Tasks

✓ DONE **TASK-1 — Measure the current schema's behavior on the configured model**
- files: `evals/eval_context_stability.py` (measurement-only additions) or a small sibling analysis under `evals/`
- done_when: a run over the existing seeded compaction scenario on the configured model (`llm.host`, `noreason_model_settings()`, Ollama warm outside any asyncio.timeout) records, for the **current** 14-section schema: H1 Active-Task/Next-Step quote overlap, H2 per-section `(none)` rate, H3 (drift-anchor presence, planted-fact recall, prior-summary carry across ≥2 passes computed directly), and H4 (invented-vs-actual `[tool: name]` in `## Completed Actions`). The run also records whether the scenario exercised each hypothesis; any un-exercised one is logged "not exercised (inconclusive)." Numbers emitted to the run log under `.pytest-logs/`, LLM-call timing tailed per testing policy. No production code changed; no trim variant built or run.
  - **H1 scenario:** the existing eval fixture is ack-only (no just-completed-task turn), so H1 is structurally inconclusive there. Measure H1 against a scenario that *does* carry a completed-task turn — reuse the seeded scenario in `tests/test_flow_compaction_summarization.py:test_summarize_preserves_anchors_when_task_completed_with_correction` (`:363`) rather than emitting a guaranteed "not exercised" for H1.
  - **H4 ground truth:** invented-vs-actual detection needs the *actual* tool set from the transcript to diff the summary's `[tool: name]` entries against — the existing `_read_summarizer_passes`/`_summary_text_from_output` helpers extract summary text only, so H4 requires one added extraction of actual tool-call names from the spans/transcript.
- success_signal: the run answers, with measured numbers, whether H1/H2/H4 occur and at what rate (or that a hypothesis was not exercised), and records the H3 baseline.
- prerequisites: Ollama warm; configured model.

## Decision gate / Follow-up (pre-registered, NOT executed here)
After TASK-1:
- **If the exercised failure modes (H1/H2/H4) are weak** — measured at a low rate on turns that actually exercised them: there is nothing to trim. Record the outcome and close. No trim plan, no harness ever built. An **un-exercised** hypothesis is NOT "weak" — re-run with a scenario that exercises it (e.g. a just-completed-task turn for H1) before closing on that hypothesis.
- **H2 interpretation guard:** a high `(none)` rate is NOT prima facie dead surface. Explicit `(none)` discipline is convergent peer design (survey §7 — co, hermes, and opencode all mandate it) and a fixed slot structure plausibly *helps* the weak model populate consistently. Treat H2 as evidence about section utility only in combination with H3 (does dropping a section regress a load-bearing signal?), never as a standalone trim trigger.
- **If any of H1/H2/H4 occur materially**: open a follow-up trim plan carrying this **pre-registered, test-safe single-branch design** (resolved from review — no hedge):
  - Collapse the dual verbatim-quote mandate: **retain `## Next Step` as a header** — a hard assertion requires it (`tests/test_flow_compaction_summarization.py:329`) and the eval's `_LOAD_BEARING_SECTIONS` gates it (`eval_context_stability.py:734,128`) — and drop **only** its verbatim-quote mandate (`_SUMMARIZE_PROMPT` lines ~180–185); `## Active Task` stays the sole mandatory verbatim anchor. (The language-preservation preamble at `:168–172` refers generically to "every mandated verbatim quote"; after this trim it still governs `## Active Task`'s quote — no clause edit needed, and it must NOT be mistaken for a second verbatim mandate to remove.)
  - Merge future-work (`## In Progress` / `## Remaining Work`) and course-change (`## User Corrections` / `## Errors & Fixes`) clusters — repo grep confirms **zero** test references, so these are test-safe.
  - The trim's done_when must protect the actual at-risk assertions: `## Active Task` (`:327`), `## Completed Actions` (`:328`), `## Next Step` (`:329`), and anchor survival via `test_summarize_preserves_anchors_when_task_completed_with_correction` (`:415`); the gate is `uv run pytest tests/test_flow_compaction_summarization.py` (real-LLM integration boundary), logged under `.pytest-logs/`.

## Testing
- TASK-1 is the evidence; logged under `.pytest-logs/`. No production change, so no suite-gate beyond the eval run itself.
- No structural/fitness tests on prompt content (functional-tests-only).

## Open Questions
- **Breaker + anti-thrash dual-counter** (`compaction.py`): two counters (`compaction_skip_count` for failures, `consecutive_low_yield_proactive_compactions` for low-yield successes) both terminate in a static marker. Possibly collapsible, but they guard distinct failure modes. **Deferred** — out of scope. Re-raise trigger: a future degradation-ladder simplification pass, with trace evidence of how often each counter fires.

## Decisions

Consolidated ledger across both critique cycles (reject/modify rows are the over-engineering-avoidance record).

| Cycle | Issue | Decision | Rationale | Change |
|-------|-------|----------|-----------|--------|
| C1 | PO-M-1 | adopt | Don't hold the safe todo-dedup hostage to the uncertain schema work; bundling muddies the Gate-2 verdict. | Todo-dedup split to atomic plan `…222105b`; removed from this plan. |
| C1 | PO-M-2 | adopt | A two-variant LLM ablation rig + permanent REPORT for a silent, once-per-compaction prompt with no observed evidence is a cure heavier than the disease. | Restructured to measurement-only; trim deferred to a gated follow-up. |
| C1 | CD-M-1 | modify | Unimplementable two-variant injection seam dissolves under measurement-only (nothing to inject). | Two-variant ablation deleted; TASK-1 measures the current schema via the eval's existing extraction. |
| C1 | CD-M-2 | adopt | Hedge removed by deferring the trim; resolved single branch pre-registered. | Decision gate fixes one branch: retain `## Next Step` header, drop only its quote mandate. |
| C1 | CD-M-3 | adopt | Factually wrong done_when gone with the trim task; corrected at-risk assertions preserved. | Decision gate records real at-risk tests (327/328/329/415) + correct pytest gate. |
| C1 | CD-m-3 | adopt | Prior-summary carry is logged-not-gated in the eval. | TASK-1 computes carry directly. |
| C1 | PO-m-1 | adopt | Pre-committing to "bloat" would author the measurement to confirm a foregone conclusion. | Context/Problem reframed neutral; hypotheses are confirm-OR-refute, "keep" is first-class. |
| C1 | CD-m-1 | adopt (→ dedup plan) | Verified-clean removal details belong in the split-out plan. | `…222105b`: grep `_gather_session_todos` too; keep `_active_todos`/`_format_active_todos`. |
| C1 | CD-m-2 | adopt (→ follow-up) | Anchor-survival best gated by the existing real-LLM test. | Decision gate cites `test_summarize_preserves_anchors_when_task_completed_with_correction`. |
| C1 | PO-m-2 | acknowledge | Out-of-scope calls sound (extract_summary_body, dual-counter, degradation ladder). | — |
| C2 | CD-m-1 / PO-m-2 | adopt | The ack-only fixture has no just-completed-task turn, so an H1-weak result is inconclusive, not a refutation. | Added scenario-validity caveat; Decision gate: un-exercised ≠ weak (re-run before closing). |
| C2 | PO-m-1 | modify | A real trace showed hallucinated tool names — a failure mode H1/H2 miss; title already reads "measure before trimming" (trim is conditional, not foregone). | Added H4 (tool-name hallucination) to hypotheses + TASK-1; title kept. |

## Delivery Summary — 2026-06-30

Added a measurement-only case **CS.D — summarizer schema-granularity measurement** to
`evals/eval_context_stability.py` (the plan's first sanctioned option). H2/H3 re-read the
summarizer passes CS.A already produced (zero extra LLM cost — the CS.B precedent); H1 and
H4 each fire **one** direct `summarize_messages` call on an inlined realistic fixture that
CS.A's ack-only text loop cannot produce (H1 = completed-task bcrypt→Argon2; H4 = tool-bearing
auth transcript with known ground-truth tools). H3 planted-fact recall is measured
schema-attributably as needle-survival into the summary text (spans-only, no coupling to CS.A's
end-to-end probe). No production code changed; no trim variant built.

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | records H1/H2/H3/H4 on the configured model over the seeded compaction scenario, un-exercised logged inconclusive, numbers to `.pytest-logs/`, no production change | ✓ pass (CS.D PASS) |

**Measured numbers (two runs, configured model qwen3.6:35b-a3b-agentic):**

| Hypothesis | Run 1 (1 pass) | Run 2 (3 passes) | Reading |
|---|---|---|---|
| **H1** Active Task ↔ Next Step quote duplication | word-Jaccard 0.20, longest shared run 3 words | Jaccard 0.19, run 3 words | **refuted** — no near-identical verbatim quote across the two sections on a just-completed-task turn |
| **H2** per-section `(none)` rate | 4/14 = 0.29 | 6/14 = 0.43; always-`(none)`: User Corrections, Errors & Fixes, In Progress, Remaining Work, Pending User Asks, Resolved Questions | measured (interpret only with H3 per the H2 guard — high `(none)` is convergent peer discipline, not prima facie dead surface) |
| **H3** load-bearing baseline | anchors 1/1, needle survived 1/1, carry not exercised (<2 passes) | anchors 3/3, needle 3/3, **prior-summary carried across passes ✓ (1794 chars)** | healthy baseline any trim must not regress |
| **H4** invented-vs-actual `[tool: name]` | 3 annotations, 0 invented | 3 annotations, 0 invented | **refuted** — no hallucinated tool names against known ground truth {file_read, file_edit, shell_exec} |

**Decision-gate reading (NOT executed — TL's call at Gate 2 / follow-up):** the two trim-triggering
failure modes H1 and H4 are **refuted** on the turns that exercised them; H2's high `(none)` rate is
expected `(none)`-discipline (peer-convergent, survey §7), not dead surface, and H3's baseline is
healthy (anchors + needle + carry all intact). By the pre-registered gate — "if the exercised failure
modes are weak, there is nothing to trim; record and close" — the measured evidence does **not**
warrant a trim. The final keep/trim decision remains the TL's at Gate 2.

**Tests:** eval run is the evidence (no pytest test files touched). CS.D PASS both runs. CS.B PASS
(3 summarizer passes, no truncation). Lint clean.
**Doc Sync:** not run — no shared module, public API, or schema changed (measurement-only eval addition).

**Logs:** `.pytest-logs/20260630-233801-cs-schema-measure.log` (run 1),
`.pytest-logs/*-cs-schema-measure-rerun.log` (run 2). No `docs/REPORT-*.md` produced — the delivery
summary + logs persist the numbers, and the plan gates a REPORT only if worth persisting beyond this.

**Pre-existing issue (out of measurement scope, flagged not fixed):** CS.A FAILED both runs on a
transient LLM stall (turn 4, then turn 8 — non-deterministic, different turn each run) past the 180s
per-turn budget, the model rambling into reasoning-style output on a noreason ack turn. This is
pre-existing pressure-loop flakiness with the local model (the eval's own comments flag the
prefill-latency hazard), independent of the CS.D addition. Not fixed here: fixing it is out of the
measurement-only scope and would touch the per-turn timeout, which is barred without approval
(RCA-first). CS.D is robust to it — it reads whatever passes fired. **Follow-up candidate:** a
separate plan to harden CS.A against model reasoning-drift under pressure (e.g. a shorter ack cap or
noreason enforcement), or accept it as known local-model variance.

**Overall: DELIVERED** — TASK-1's done_when met; the measurement records all four hypotheses on the
configured model with the multi-pass carry baseline exercised. CS.A's transient stall is a
pre-existing, orthogonal flakiness flagged for a separate follow-up.

## Final — Team Lead

Plan approved — converged at C2 (Core Dev + PO both `Blocking: none`).

This plan is **measurement-only**: it produces evidence on whether co's 14-section summarizer schema helps or hurts the configured model. It ships no prompt change. The schema trim is a pre-registered, evidence-gated follow-up (see Decision gate). The separable todo-dedup over-design ships independently as `2026-06-24-222105b-summarizer-todo-dedup.md`.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev summarizer-overdesign-trim` (and `/orchestrate-dev summarizer-todo-dedup` for the sibling).

## Implementation Review — 2026-07-01

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | records H1/H2/H3/H4 on the configured model, un-exercised logged inconclusive, numbers to `.pytest-logs/`, no production change, no trim variant | ✓ pass | `evals/eval_context_stability.py:1047` `case_measure_schema_granularity` records all four hypotheses; `main():1511` runs it after CS.A/CS.B |
| TASK-1 | H1 measured on a just-completed-task turn (not the ack-only loop) | ✓ pass | `:832` `_H1_COMPLETED_TASK_MESSAGES` (bcrypt→Argon2 correction, inlined from the cited test); `:1139` fires one direct `summarize_messages` call |
| TASK-1 | H4 invented-vs-actual diffed against the actual tool set | ✓ pass | `:866` `_H4_ACTUAL_TOOLS = {file_read, file_edit, shell_exec}`; `:1184` diffs `[tool:]` annotations against it |
| TASK-1 | H2 parses the current 14-section schema | ✓ pass | `_SUMMARY_SCHEMA_SECTIONS` (`:809`) matches `summarization.py:173–223` exactly (14 headers, same order) — verified against installed prompt, not just names |
| TASK-1 | H3 computes prior-summary carry directly (logged-not-gated in eval) | ✓ pass | `:1121` calls `_carried_prior_summary_slot(passes)` directly rather than reading an eval gate |
| TASK-1 | no production code changed | ✓ pass | only `evals/eval_context_stability.py` among relevant diff; `summarize_messages(deps, msgs)` signature unchanged (`summarization.py:359`) |

### Issues Found & Fixed
No issues found.

⚠ Scope note (not blocking): `git diff HEAD` also shows `co_cli/agent/loop.py`, `docs/specs/core-loop.md`, `docs/specs/pydantic-ai-integration.md`, `tests/test_flow_model_request_cap.py`, `tests/test_flow_usage_tracking.py`, `docs/reference/RESEARCH-*.md`, `uv.lock` — none in TASK-1's `files:`. These are pre-existing WIP from concurrent active plans (loop-terminal-answer / pydantic-ai-v2), not this delivery. TASK-1 touched only `evals/eval_context_stability.py`. Verify staging excludes them before `/ship`.

### Tests
- No pytest files touched (measurement-only) — the eval run is the evidence, per the plan's Testing section.
- Command: `uv run python evals/eval_context_stability.py` (independent re-run at review time)
- Result: CS.A PASS, CS.B PASS, **CS.D PASS**, CS.C SKIPPED — full run green. Confirms the delivery's diagnosis that CS.A's earlier stall was transient local-model flakiness (non-deterministic, orthogonal to CS.D — CS.A runs and fails before CS.D executes).
- Log: `.pytest-logs/20260701-*-review-impl-cs-d.log`; lint clean (`scripts/quality-gate.sh lint`).

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads).
- CS.D measurement (LLM-mediated) verified via the eval run itself — the done_when instrument. `success_signal` confirmed: the run answers all four hypotheses with numbers. Across three independent runs (2 delivery + 1 review) the readings are stable:
  - **H1 refuted** — Active Task ↔ Next Step word-Jaccard 0.19–0.20, longest shared run 3 words (no duplicated verbatim quote).
  - **H2 measured** — 0.29–0.43 `(none)` rate; always-`(none)`: User Corrections, Errors & Fixes, In Progress, Remaining Work, Pending User Asks, Resolved Questions (interpret only with H3 per the plan's H2 guard — convergent `(none)`-discipline, not prima facie dead surface).
  - **H3 baseline healthy** — anchors 3/3, planted needle survived 3/3, prior-summary carried across passes (1794–1873 chars).
  - **H4 refuted** — 3 `[tool:]` annotations, 0 invented against ground truth {file_read, file_edit, shell_exec}.

### Overall: PASS
TASK-1's done_when is met: the measurement records all four hypotheses on the configured model with the multi-pass carry baseline exercised, numbers logged, no production change and no trim variant built. The measurement is stable across three runs. Decision-gate reading (TL's call at Gate 2): the two trim-triggering failure modes H1 and H4 are refuted on the turns that exercised them, so the pre-registered gate does not warrant a trim.
