# rules-block-trim-finish

> **Supersedes** `2026-05-28-214128-prefill-trim-4-rules-block-trim.md` (combined into this plan;
> the original is deleted). **Child 4 of** `2026-05-28-141854-prefill-trim.md`. Carries forward
> prefill-trim-4's delivered-but-unshipped state and its two unfinished gates, now that both blockers
> are cleared:
> 1. The **eval-infra drift** that blocked the TASK-3 adherence gate is fixed (v0.8.286,
>    `eval-infra-output-sync` — shared `response_text(turn_result)` reads canonical
>    `AgentRunResult.output`; eval_skills W4.A no longer spuriously FAILs on empty `preview`).
> 2. The **deferred-discovery-diagnostic** that gated the rule-`06` trim has been **dropped** (never
>    run, cold-only/cached-away ROI, unowned). With no verdict to wait on, the `06` hold is **released**
>    and the `06` manifest-scan dedup — prefill-trim-4's largest stranded candidate — is back in scope.

## Inherited state (from prefill-trim-4, already in the working tree — uncommitted)

prefill-trim-4 delivered TASK-1/2/4 and paused at TASK-3. Those deliverables sit **uncommitted** in the
working tree and are owned by this plan now:

- `05_workflow.md` — trimmed **−93 ch**: the blocker-loop cue deduped to one canonical home
  (Execution), the duplicate in the Completeness validation list removed. All must-survive cues intact.
- `07_memory_protocol.md` — trimmed **−402 ch**: the `Triggers:` recall line and the 4-way
  `SaveResult.action` enum collapsed to the behavioral cue. All must-survive cues intact.
- `06_skill_protocol.md` — **untouched** (was held on the now-dropped diagnostic; TASK-A below trims it).
- `tests/test_instruction_budget.py` — the single shared instruction-budget guard (created per
  context-stability TASK-7's spec; ceiling `24,200` = measured `23,769` + ~431 headroom). **Passes.**

**Realized so far:** −495 ch (~123 tok) of the −350–700 tok plan goal. The shortfall is entirely the
held `06` manifest-scan dedup, which this plan now completes.

### Baseline band (prefill-trim-4 TASK-1, 2 runs each — the regression reference)

- **skills:** W4.A–D PASS/PASS, W4.E SOFT_PASS 0/3 ×2.
- **memory:** W3.A–F PASS/PASS, W3.G **{PASS, SOFT_FAIL}** (inherent variance — a single SOFT_FAIL
  inside this band is NOT a regression).

> Note on W4.A: prefill-trim-4's trimmed runs showed W4.A 2 PASS / 2 FAIL with `preview=''`. Root cause
> was the eval-infra drift (now fixed in v0.8.286), **not** the trim. The re-run gate (TASK-B) runs on
> the fixed harness, so W4.A is expected to read clean.

## Problem & Outcome

**Problem.** The rules block is ~32% of every cold prefill and rides every post-compaction state. The
05/07 trims are applied but unverified and uncommitted; the `06` manifest-scan repetition (the largest
dedup candidate) is still on the floor.

**Outcome.** Complete the `06` trim, re-run the adherence gate on the fixed eval harness across all
three trimmed files, re-pin the instruction-budget guard downward to the post-`06` measurement, and
ship — banking the full conservative rules-block trim with zero adherence regression.

**Failure cost.** Left here, the 05/07 trims rot uncommitted and the second-biggest floor component
keeps paying duplicated-guidance tokens on every cold turn.

## Behavioral Constraints (carried forward verbatim from prefill-trim-4)

- **Trim = dedup + inert-prose only.** Collapse repeated injunctions to one canonical home; cut
  reference-enumerations and pure mechanism-pedagogy. **Never** remove a routing/when-to-use cue or a
  safety/correctness injunction. This model needs more explicit guidance, not less (FM-1).
- **Load-bearing `06` cues that MUST survive** (grep-checked): `skill_view` before edit/patch; "skill
  body is your procedure, not reference"; drift→`skill_patch`/`skill_edit` immediately; create bar
  (3+ steps / reusable); confirm before create-on-behalf; the `skill_manage`-family tool injunctions
  (`skill_view`/`skill_patch`/`skill_edit`/`skill_create`). The create-dedup "search first" cue in
  `## Create` is a **distinct** manifest mention (scoped to dedup-before-create) — keep it; it is not
  one of the 3 redundant discovery repetitions.
- **Contiguous-from-01 invariant** (`assembly.py:56-61`) — no renumbering, no file deletion.
- **Eval-gated** — adherence must not regress vs the inherited baseline band.
- **Single instruction-budget guard** — re-pin `tests/test_instruction_budget.py` downward; never add a
  second rules-only guard (context-stability reads this same one).
- **Coworker-maintained assets** (`souls/`, `_profiles/`, `evals/judges/`, `memories/`) untouched.

## Tasks

### TASK-A — `06` manifest-scan dedup (the released hold)

**Files:** `co_cli/context/rules/06_skill_protocol.md`.

**Action:** "scan the `<available_skills>` manifest before any multi-step task" is stated **3×** —
intro para (`:5-7`), manifest para (`:9-11`), and `## Discovery` (`:13-17`). Collapse to **one
canonical home** (keep `## Discovery`'s actionable form; thin the two upstream echoes to a single
lead-in). Compress `## Background review` (`:70-79`) — a long mechanism explanation whose only
behavioral cue is "don't double up" — to that cue. Preserve every must-survive cue above and the
distinct `## Create` "search first" dedup mention.

**done_when:**
- A scripted grep of the must-survive anchor phrases returns **all hits** (`skill_view`,
  `skill body is your procedure`, `skill_patch`, `skill_edit`, `skill_create`, the create bar, the
  create-on-behalf confirm).
- `uv run pytest tests/test_flow_prompt_assembly.py -x` passes (contiguity + assembly intact).
- The 3× discovery repetition is reduced to one canonical statement (grep: the
  "before any multi-step task" + manifest-scan phrasing appears once in the discovery sense).

**success_signal:** `06` reads once-not-thrice on manifest-scan with no behavioral cue lost.

**prerequisites:** none (the diagnostic gate is dropped).

### TASK-B — eval-gated adherence verification (re-run on the fixed harness)

**Files:** none (runs the gate; records result in the Delivery Summary).

**Action:** Re-run `evals/eval_skills.py` and `evals/eval_memory.py` **≥2× each** on the full trimmed
rules (05+06+07). `eval_mindset_selection` stays **descoped** (it was removed from the suite in
v0.8.286 and is orthogonal to a rules trim — its pairwise delta isolates the mindset block, identical
across arms). Record **per-run scores** as a band.

**done_when:** the trimmed band sits **within or above** the inherited baseline band per domain (skills
W4.A–E, memory W3.A–G with W3.G's known `{PASS, SOFT_FAIL}` variance). A single trimmed run dipping
inside baseline variance is NOT a regression; a band that drops *below* baseline is. If a domain
regresses, restore the cut cue in that file and re-run.

**success_signal:** Adherence band held across all three trimmed files.

**prerequisites:** TASK-A.

### TASK-C — re-pin the instruction-budget guard + ship the deliverables

**Files:** `tests/test_instruction_budget.py`.

**Action:** The `06` trim lowers the static instruction block below the current `24,200` ceiling.
Re-measure `build_static_instructions(deps.config)` post-`06`-trim and re-pin
`INSTRUCTION_BLOCK_CEILING` to that measurement + ~400-char headroom (tighten, never raise). Update the
docstring's measured-figure line. Then commit all inherited + new deliverables (05/06/07 trims + the
re-pinned guard) under `/ship`.

**done_when:**
- `uv run pytest tests/test_instruction_budget.py -x` passes against the re-pinned ceiling.
- The re-pinned ceiling is **below** the current `24,200` (the `06` trim must lower it); the forbidden
  pre-trim `24,256` is never re-introduced.

**success_signal:** A future verbose rule addition fails CI; exactly one instruction-budget guard exists.

**prerequisites:** TASK-B (ceiling set from the verified post-trim state).

## Testing

- `tests/test_flow_prompt_assembly.py` (contiguity + assembly) and `tests/test_instruction_budget.py`
  (re-pinned, single guard).
- `evals/eval_skills.py`, `evals/eval_memory.py` — the adherence gate (stochastic UAT diagnostics;
  compare to the inherited baseline band, not an absolute bar).
- `scripts/quality-gate.sh full` at ship.
- `/sync-doc` follow-up if any spec quotes the trimmed `06` prose.

## Carried-forward decisions (do NOT relitigate)

- **Gate-1 proceed-vs-guard-only = PROCEED** (resolved during prefill-trim-4 invocation). The rules
  trim is the chosen posture; the shared budget guard is already in place.
- **`06`-gate release.** prefill-trim-4 held `06` behind the deferred-discovery-diagnostic's TASK-3
  verdict. That diagnostic is dropped (never run, low ROI); the `skill_manage` injunctions were always
  on the must-survive list, so the namespace-confusion concern the diagnostic probed is protected by
  the grep gate in TASK-A regardless. The hold is released — no verdict to wait on.

---

## Status — Team Lead

Successor plan assembled from prefill-trim-4's carried-forward state. Both prior blockers cleared
(eval-infra fixed in v0.8.286; `06`-gate diagnostic dropped). 05/07 trims + budget guard already in the
working tree; `06` trim, eval re-run, and guard re-pin remain.

> Ready for `/orchestrate-dev rules-block-trim-finish` (TASK-A → TASK-B → TASK-C → `/ship`).
