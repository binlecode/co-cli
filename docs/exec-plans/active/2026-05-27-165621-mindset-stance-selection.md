# Mindset Real-Estate — Ablation-First Eval (authoring + router both eval-gated)

Task type: eval script (new pairwise-judge mode) + conditional prompt/doctrine authoring

## Context

co loads all six mindset files for the active role into one static `## Mindsets`
block at agent construction (`co_cli/personality/prompts/loader.py:71-97`,
`load_soul_mindsets` → `"## Mindsets\n\n" + "\n\n".join(parts)`). Each is a
labeled subsection (`## Debugging`, `## Technical`, …). The model self-selects
which stance applies purely by emergent attention — there is **no prompt-level
nudge to select, no system-level gating, and no runtime task/risk state**. This is
the §2.2 gap in `docs/reference/RESEARCH-personality-self-working-style.md`.

The block is *always-on doctrine*: all six mindsets occupy the static prefix every
turn for the life of the session. So the first-order question is not "is the right
mindset selected?" — it is **do the mindsets earn that system-prompt real estate at
all?** A selection metric is conditional on mindsets mattering; if the model would
answer the same with or without the block, selection is a vanity number and the
block is dead weight. You cannot answer the real-estate question without the
counterfactual where the block is absent — i.e. an **ablation**.

On the deployment target (a 35B-A3B model) flat all-six load has two suspected
failure modes: the relevant guidance doesn't actually steer behavior (block is
inert), and the five non-relevant mindsets dilute/distract. Both are **unmeasured**.

**Design decision (settled in dialog, recorded here).** Mindsets are *core agentic
doctrine* — auto-injected every turn, never queryable, not skills or memory.
Core-loop code is the most expensive place to add standing complexity. The peer
survey (`RESEARCH-personality-peer-survey.md`) warns co's stack may already be
over-specified ("context-over-command") yet rates co's mindset-per-task as the
strongest role layer surveyed. Both claims are untested on the target model.
Therefore the lean, **measure-before-fix** path:

1. **Build an ablation eval first.** Run the same prompts through arms that differ
   *only* in the mindsets block and have the judge compare responses pairwise. This
   answers the ROI question (do the tokens pay rent) and the distraction question
   (does flat-load cost us) directly — not the conditional selection question.
2. **Branch on measured signal.** Every downstream action — cut the block, ship
   nothing, author anchors, or build the router — is gated on a specific pairwise
   result. Nothing is built on suspicion.

Authoring (sharper anchors + a selection nudge) and the runtime router are **both
deferred and both eval-gated**: authoring is the cheap first attempt to close a
*measured* selection gap; the router is justified only if a measured distraction gap
survives authoring. Neither is built on the current unmeasured suspicion.

**Cost honesty.** "Lean" describes the *prompt/runtime* footprint, not the build
effort. The real investment is the eval itself: a small ablation harness, a new
pairwise judge mode, the Step 0 refactor, and 4 arms × ~8 cases × order-swap on a
warm 35B (tens of minutes per run). That is justified because the eval is **retained
as a standing real-estate / regression guard**, not a throwaway diagnostic — it
re-answers "do these tokens pay rent" on any future prompt or model change.

## Problem & Outcome

**Problem.** The mindsets block consumes static-prefix real estate every turn, and
we have no measurement that it changes behavior, nor that flat all-six load beats a
focused subset. Selection-quality is the wrong first question; real-estate ROI is the
right one.

**Outcome.**
1. **Ablation eval (built first)** — `evals/eval_mindset_selection.py` + a new
   **pairwise judge mode** in `evals/_judge.py`. Runs the arm-pairs below and reports
   pairwise win-rates. This is the deliverable that decides everything downstream and
   is retained as a standing real-estate/regression guard regardless of verdict.
2. **A measured verdict on the block** — one of: *mindsets don't earn their tokens*
   (cut, surface to TL), *mindsets work and flat-load is fine* (ship nothing, router
   dead), or *mindsets work but selection/distraction gap exists* (→ authoring).
3. **Anchors + nudge (only if a measured selection gap exists)** — sharper trigger
   lines + one preamble, then rerun the ablation to confirm the gap closed. If it
   doesn't close, that residual gap is the evidence that scopes the router.

## Scope

### In scope
- **Rules-block seam (small production refactor — Step 0):** extract a public
  `build_rules_block()` from `co_cli/context/assembly.py` (the rule-walking currently
  inline in `build_static_instructions`, lines ~102-106). Both production and the
  eval call it; `build_static_instructions` becomes `seed + mindsets + build_rules_block()`.
  This is a natural decomposition (rules are a coherent unit), not eval-driven API —
  it exists because arm composition needs `seed + <varied mindsets> + rules` and the
  rules-walking is otherwise locked behind the private `_collect_rule_files`. Without
  it the eval cannot isolate the mindsets variable (setting `personality=None` drops
  seed *and* mindsets together, so it can't produce A0).
- **Pairwise judge mode (new, eval layer):** `evals/_judge.py` — a
  `judge_pairwise(...)` returning a winner ∈ {A, B, tie} + rationale, given two
  responses and a per-case target-stance description. Run each comparison in **both
  orders** (A,B) and (B,A) to cancel position bias; disagreement counts as a tie.
  The existing absolute `judge_with_llm` is untouched.
- **Ablation eval (new):** `evals/eval_mindset_selection.py` + report at
  `docs/REPORT-eval-mindset-selection.md`. Composes arm prompts in the eval layer
  (see Step 1 feasibility note) and judges the arm-pairs pairwise.
- **Anchors (18 files) — conditional on a measured selection gap:** `co_cli/personality/prompts/souls/{finch,jeff,tars}/mindsets/{technical,exploration,debugging,teaching,emotional,memory}.md`
  — one trigger line under each `## <TaskType>` heading. Additive only; do not
  rewrite the co-worker's existing bullets/examples. (Co-worker maintains soul
  assets; edit authorized for all three roles for this change.)
- **Nudge (1 code edit) — conditional on a measured selection gap:** `co_cli/personality/prompts/loader.py` `load_soul_mindsets`
  — insert the selection preamble after `## Mindsets`.
- **Test update:** confirmed no test pins the `## Mindsets` prefix or asserts on
  `load_soul_mindsets` (grep of `tests/` is empty; closest is
  `test_flow_prompt_assembly.py`). Verification-only unless that changes.
- **Doc sync:** record in `RESEARCH-personality-self-working-style.md` §2.2 the
  ablation verdict, whether authoring shipped, and the router's gate (via `/sync-doc`
  or inline).

### Out of scope
- **The runtime router, static/dynamic split, manifest, always-on `base` block,
  triage object, mindset indexing.** All deferred to the contingency below; built
  only on a measured surviving distraction gap.
- **Cutting / restructuring the mindsets block.** If Pair 1 says the block is inert,
  that is a doctrine decision surfaced to TL, not executed in this plan.
- **Changing mindset *content*/voice** beyond the additive trigger line.
- **New runtime state** (`risk_level`, `response_contract`, etc.).

## Behavioural Constraints
1. **Arms compose from public seams; no mindset toggle in production.** The eval
   assembles each arm's prompt as `load_soul_seed(role)` + an arm-specific mindsets
   string + `build_rules_block()` (the Step 0 seam). It does **not** add a mindset
   on/off switch or any arm parameter to `build_static_instructions` — that would be
   eval-driven API (`feedback_no_eval_test_driven_api`). The Step 0 rules-block
   extraction is the *one* production change, and it stands on its own as a clean
   decomposition, independent of this eval.
2. **Pairwise over absolute.** The verdict is a preference win-rate ("which response
   better exhibits the target stance"), never an absolute 0-10 score — preference is
   far more reliable than calibration on a soft behavioral rubric and a 35B judge.
   Pin the judge model (`deps.judge_model`); record it in the report.
3. **Anchors are additive.** One trigger line per file, under the existing heading,
   above the existing bullets. No deletion or rewrite of co-worker content.
4. **Trigger lines are role-neutral.** Task-intrinsic wording, identical across the
   three roles; character-specific behavior stays in the bullets.
5. **Nudge stays in the static (cached) prefix.** One line in the assembled block;
   nothing moves to per-turn. The core loop is untouched.
6. **Eval uses real everything** (per `feedback_eval_real_world_data`): real
   `make_eval_deps()`, config `llm.host`, real model. `ensure_ollama_warm()` called
   **outside** any `asyncio.timeout`. No caps, no test stores.

## ✓ DONE — Step 0 — Extract the rules-block seam

In `co_cli/context/assembly.py`, extract the inline rule-walking (lines ~102-106)
into a public `build_rules_block() -> str` and have `build_static_instructions` call
it: `parts = [seed, mindsets, build_rules_block()]` (skipping empties as today). No
behavioral change to the assembled production prompt — pure decomposition so the eval
can build `seed + <varied mindsets> + build_rules_block()` without importing the
private `_collect_rule_files` or duplicating the walk.

**Verify 0:** `scripts/quality-gate.sh lint`; run `tests/test_flow_prompt_assembly.py`
to confirm the assembled prompt is byte-identical before/after the extraction.

## ◐ PARTIAL — Step 1 — Build the ablation eval and run it (the diagnostic)

> **Build delivered, run deferred** (orchestrate-dev scoped to "build harness, defer run").
> Done: `judge_pairwise`/`PairwiseVerdict` in `evals/_judge.py`; `evals/eval_mindset_selection.py`
> (arms A0–A3, 8 cases, order-swap reconciliation, majority-rule decision tree) — lint-clean
> and import-clean. **Not done in this session:** the actual `uv run` diagnostic, the report,
> and the decision-tree branch (that is a separate real-35B run + TL read at the gate).


`evals/eval_mindset_selection.py`, mirroring `eval_skills.py` structure
(`make_eval_deps`, `run_turn`, `ensure_ollama_warm`, `open_eval_run`,
`prepend_report`, `_timeouts`) + the new `judge_pairwise` from `_judge.py`.

### Arms (differ only in the mindsets block)
- **A0 — none:** seed + rules, no `## Mindsets` block.
- **A1 — all-six:** current production prompt.
- **A2 — relevant-only:** seed + rules + only the mindset(s) matching the case's task
  shape. For single-shape cases that is one mindset; for the composites (debug+teach,
  technical+emotional) it is the *two* matching mindsets — making Pair 2 on composites
  a direct "2 relevant vs all 6" distraction test.
- **A3 — wrong-only (optional):** seed + rules + a single *plausible-but-wrong*
  mindset for the case (e.g. emotional on a debugging task), not an arbitrary or
  incoherent pick — otherwise A3 degrades into "coherent vs irrelevant prose."

### Cases
One representative prompt per task shape (technical, exploration, debugging,
teaching, emotional, memory) + 2 composites (debug+teach, technical+emotional). Each
case carries a **target-stance description** (what the matching mindset should make
the response do, e.g. debugging → "commits a hypothesis then verifies; diagnoses
root cause before proposing fixes"). Coverage by functionality, not count (per
`feedback_no_test_count_rule`). Run against role **tars**; trigger lines (if authored)
are role-neutral so transfer to finch/jeff is assumed, not measured — state this in
the report.

### Arm-pairs and what each settles
| Pair | Comparison | Settles |
|------|-----------|---------|
| **1. Do they matter?** | A1 vs A0 | Does the block change behavior vs. its absence? The real-estate question. |
| **2. Does flat-load cost us?** | A1 vs A2 | Do the five irrelevant mindsets dilute the relevant one? The §2.2 distraction hypothesis, measured directly — the only thing that justifies the router. |
| **3. Is content steering? (opt)** | A2 vs A3 | Is it the *specific* guidance, or would any plausible prose do? **Run only when Pair 2 shows A2 > A1** — the same branch that triggers Step 2 — as the pre-authoring check. Reuses A2 responses; only A3 is new. |

**Metric (stated so the branch reached is reproducible, not a judgment call).**
- **Per-case winner:** the two order-swapped judgments must agree; if they disagree,
  the case is a **tie** (position-sensitive = no real preference).
- **Arm "reliably preferred" over its pair:** wins a clear majority of the *decisive*
  (non-tie) cases — and decisive cases must themselves be a majority (if most cases
  tie, the verdict is "≈", no preference). Report per-case winners + the decisive/tie
  counts, not just an aggregate.
- This rule applies uniformly to Pair 1 (A1 vs A0), Pair 2 (A1 vs A2), Pair 3 (A2 vs
  A3), and the Step 2 rerun (A1′ vs A2).

**Model settings** — daily-driver path: `noreason_model_settings()` (stance is a
chat-behavior property, not a reasoning task), config `llm.host`. Bump `_timeouts`
budgets only if warm-call timing demands it (watch durations per
`feedback_llm_call_timing`). Note: arms × cases × order-swap is the call budget —
keep the case set tight and let warm-call timing guide N.

### Decision tree (every branch is a measured pairwise result)
- **Pair 1 — A1 not reliably preferred over A0** → the block is inert; it doesn't
  earn its real estate. **Surface to TL as a doctrine decision** (cut or rebuild the
  block). Stop — selection/authoring/router are all moot. Best-value finding even
  though it ships no code.
- **Pair 1 positive, Pair 2 — A1 ≈ A2** → mindsets work *and* flat-load isn't
  hurting. **Ship nothing, touch zero files; the router is dead** (a measured null,
  the strongest possible kill). Record and update §2.2. **Plan complete.**
- **Pair 1 positive, Pair 2 — A2 reliably > A1** → a real selection/distraction gap:
  the relevant mindset alone beats all-six. Now run **Pair 3** as the pre-authoring
  gate:
  - **A2 ≈ A3** → the lift is prose-presence, not the specific content; sharpening
    anchors would polish text that isn't what's working. Skip authoring; surface to
    TL (the gap is structural — focus/router territory, not wording).
  - **A2 reliably > A3** → the matching content genuinely steers → **Step 2** (close
    the gap by authoring before reaching for the router).

**Verify 1:** `uv run python evals/eval_mindset_selection.py`; report at
`docs/REPORT-eval-mindset-selection.md`. The **A1-vs-A0 result is the headline** —
the direct answer to "do the mindset tokens pay their rent" — stated up top, not
buried in the arm-pair table. Report also carries per-pair decisive/tie counts,
per-case winners, judge model, and the decision-tree branch reached.
`scripts/quality-gate.sh lint` for the `_judge.py` addition.

## Step 2 — Author anchors + nudge (only if Pair 2 shows A2 > A1 *and* Pair 3 shows A2 > A3)

Rationale: a Pair-2 gap means selection is imperfect — the relevant mindset works
but the all-six load isn't surfacing it. Pair 3 confirms it's the *specific content*
doing the work (A2 > A3), not mere prose-presence — so sharpening anchors is the
right lever. With both, authoring is the cheap attempt to close the gap inside the
existing static prefix before paying for a runtime router. (If Pair 3 shows A2 ≈ A3,
the gap is structural — skip authoring, surface to TL.)

### 2a. Trigger lines (role-neutral, one per task type)
Add under each heading, as an italic line, e.g. for `debugging.md`:
```
## Debugging
_Use when the task is finding why something fails — an error, wrong output, a crash, "why isn't this working"._
- Identify the failure mode before proposing fixes — don't patch symptoms
  ...
```
Proposed trigger lines (refine wording during dev; keep one sentence each):
- **technical** — _Use when the task is doing the work: implementing, running, changing, or operating on code/systems._
- **exploration** — _Use when the task is investigating or mapping unknown territory before committing to an approach._
- **debugging** — _Use when the task is finding why something fails — an error, wrong output, a crash._
- **teaching** — _Use when the task is explaining or helping the user understand — "how does", "what is", "explain"._
- **emotional** — _Use when the user is frustrated, anxious, stuck, or the moment needs acknowledgment before action._
- **memory** — _Use when the task is recalling, recording, or reconciling what the user has told you over time._

### 2b. Nudge in `load_soul_mindsets`
```python
return (
    "## Mindsets\n\n"
    "Identify which task shape(s) this turn is and lead with the matching "
    "mindset; treat the others as background.\n\n"
    + "\n\n".join(parts)
)
```

### 2c. Fix the assertion
Confirmed no test currently pins the `## Mindsets` prefix (grep empty). If one is
added meanwhile, update it to include the new preamble; otherwise no-op.

### 2d. Rerun the ablation (did authoring close the Pair-2 gap?)
Rerun the eval with a fourth arm **A1′ — all-six-with-anchors-and-nudge** and judge
**A1′ vs A2** pairwise:
- **A1′ ≈ A2** → authoring closed the gap. Ship Step 2; the router stays dead. Record
  the before (A1<A2) / after (A1′≈A2) delta and update §2.2.
- **A2 still reliably > A1′** → authoring can't close it within the static prefix.
  That residual gap is the evidence that justifies and scopes the router — do **not**
  auto-build it; surface to TL.

**Verify 2:** `scripts/quality-gate.sh lint`; eyeball one assembled prompt per role
(`load_soul_mindsets("finch"|"jeff"|"tars")`) to confirm the preamble + 6 trigger
lines render once, in order; rerun report shows A1 / A2 / A1′ pairwise results.

## Deferred — router contingency (build only if Step 2 leaves A2 > A1′ — a measured distraction gap authoring can't close)

Documented so the analysis isn't lost; **not** in scope unless the ablation forces it:
- **LLM router**, single constrained call, temp=0, structured output limited to the
  6 labels (multi-label). Not embeddings/FTS — that's a cloud-scale, cost-driven
  optimization co doesn't need; the LLM is always available and better at
  intent/composite. Return a **triage object** (`{task_labels, …}`) so it can later
  carry `risk_level` without a breaking change.
- **Static/dynamic split:** static prefix keeps seed + always-on `base` + a 6-line
  mindset *manifest* + rules + critique (cached); dynamic suffix injects only the
  selected mindset body/bodies as `## Active mindset(s)`, placed late (recency).
  Manifest is the safety floor — a routing miss degrades to today's behavior.
- **One fallback:** router error → `base` + all six. Minimize the per-turn tax
  (fold into an existing pre-turn step; don't add a mandatory second inference if
  avoidable).

## Decision record
Lean, evidence-first, **ablation-first**, core-flow scoped. The eval measures whether
the always-on mindsets block earns its system-prompt real estate (Pair 1) and whether
flat all-six load costs us against a focused subset (Pair 2) — pairwise, because
preference beats absolute calibration on a soft rubric and a 35B judge. Authoring and
the router are both downstream of a *measured* gap, not suspicion: authoring is the
cheap close-the-gap attempt, the router only if a distraction gap survives it. The
eval is a retained real-estate/regression guard regardless of verdict — even a
"ship nothing" result delivers a settled §2.2 and a standing guard. Full dialog
rationale in `docs/reference/RESEARCH-personality-self-working-style.md` §2.2 / §4.2.

## Delivery Summary — 2026-05-27

Scope: **build harness, defer run** (per the orchestrate-dev scoping decision). Step 1's
diagnostic run, the decision tree, and Step 2 (conditional authoring) are deliberately
out of this session.

| Task | done_when | Status |
|------|-----------|--------|
| TASK-0 — `build_rules_block()` extraction (`assembly.py`) | public fn exists, `build_static_instructions` calls it, prompt byte-identical, assembly test green | ✓ pass |
| TASK-1a — `judge_pairwise` + `PairwiseVerdict` (`evals/_judge.py`) | returns winner∈{A,B,tie}+rationale, order-swap documented, lint clean, importable | ✓ pass |
| TASK-1b — ablation harness (`evals/eval_mindset_selection.py`) | imports clean (`import` smoke), arms/cases/order-swap/decision-tree defined, lint clean — **not run** | ✓ pass (build only) |
| Step 1 run + decision tree | — | — deferred (separate real-35B run + TL gate) |
| Step 2 — anchors + nudge | — | — deferred (conditional on Step 1 result) |

**Files changed:** `co_cli/context/assembly.py`, `evals/_judge.py`, `evals/eval_mindset_selection.py` (new). No extra files.
**Tests:** scoped — `tests/test_flow_prompt_assembly.py` 5 passed, 0 failed.
**Doc Sync:** no-op — assembled prompt byte-identical; `build_rules_block` is an internal helper, not a runtime-behavior contract change.

**Overall: DELIVERED (scoped subset).**
Harness is built, lint-clean, and import-verified. Next is not `/review-impl` of more code but the **diagnostic run**: `uv run python evals/eval_mindset_selection.py` (real 35B, tens of min) → read the DECISION branch → if `AUTHOR`, that unblocks Step 2; otherwise surface the verdict (INERT / FLAT_OK / STRUCTURAL) to TL. Run `/review-impl <slug>` first if you want a full-suite + evidence pass on the three built files before that.
