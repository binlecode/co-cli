# Reinforce deliberation discipline (plan-before-mutate + ask-when-unsure) and redesign the W11.A/B eval cases to evaluate it from first principles

## Context

W11.A (`breakdown_before_execute`) and W11.B (`intermediate_checkpoint`) in `evals/eval_multistep_plan.py` flip pass/fail run-to-run independent of code. Across 4 runs during the `plan-skill-inline-args` delivery:

| Run | W11.A | W11.B |
|-----|-------|-------|
| 1 | PASS (judge 10) | PASS (judge 10) |
| 2 | FAIL (judge 0 — mutated before plan) **+[slow]** | FAIL (**judge 10**) **+[slow]** |
| 3 | FAIL (judge 10) **+[slow]** | FAIL (judge 5) |
| 4 | FAIL (judge 4) **+[slow]** | FAIL (judge 2) **+[slow]** |

`[slow]` trips are environment (out of scope; never bump timeouts without approval). The behavioral run-variance is in scope.

**Expected behaviors (defined by the user this session — these are the spec the eval must test):**
- **Be precise about the next action; never assume.** Ground decisions in facts/rules/context/history. If the direction is clear from those, proceed; if genuinely unsure, **ask** — do not manufacture an "obvious default." (`feedback_precise_ground_never_assume`.)
- This yields a deliberate bracket the eval must evaluate cleanly:
  - **Plan-before-mutate (clear request):** for a clear state-mutating multi-step request, lay the todo ledger **before** firing any mutating tool. (W11.A.)
  - **Ask-when-unsure (ambiguous request):** on a genuinely ambiguous continuation nudge, **checkpoint/confirm** rather than assume scope. (W11.B — the user validated this is correct behavior; the pole is KEPT, not retired.)
  - **Drive-to-done (explicit request):** on explicit total authorization, execute to completion. (W11.D / `multistep_plan.v3` — shipped.)

**Source facts (verified, file:line):**
- co already states the ask-when-unsure principle for all profiles, but with assumption-licensing phrasing: `co_cli/context/rules/03_reasoning.md:40-55` — *"Before asking… determine if the answer is discoverable… When a question has an obvious default interpretation, **act on it** rather than asking… A silent assumption is an undisclosed risk."* The "act on the obvious default" line is the part that licenses assuming.
- co already has reflexive todo discipline at peer parity — do NOT rebuild: `co_cli/tools/todo/rw.py:214-218` (proactive 3+ steps, create-before-starting, update incrementally), `:116-124` (`_check_one_in_progress` code-enforces ≤1 in_progress), `co_cli/context/rules/04_tool_protocol.md:32-35` (closing reflex).
- The weak overlay under-reinforces deliberation: `co_cli/context/overlays/weak_local.md:17-26,51-53` — drive-to-done ("execute immediately… only stop at a plan when the user explicitly asked"; "When NOT to over-plan") overrides both the ledger-first cue (→ W11.A mutate-before-plan) and the ask-when-unsure rule (→ W11.B assumes "do everything").
- Eval mechanics that constrain the redesign: the judge is **holistic** (`evals/_judge.py:177-180` — one pass/score over the WHOLE rubric, no per-criterion call), and `multistep_plan.v2` is loaded by **W11.A + W11.B + W11.C** (`eval_multistep_plan.py:285,388,483`); W11.D uses `v3`. So rewriting one v2 criterion in place perturbs all three cases' holistic score. `_mutated_before_plan:182` already treats `todo_write` as a satisfied plan signal (structural). `multistep_plan.v3.md:13-16` documents v2-criterion-2 (pause) as a deliberate inverse pole vs v3 — confirming W11.B is an intentional behavior class, now validated by the user.

## Failure Modes (observed, real runs)

1. **Mutate-before-plan (W11.A, run 2):** `file_write` before any `todo_write` — weak overlay drive-to-done overrides the ledger-first cue.
2. **Assume-don't-ask (W11.B, runs 3-4):** ran all remaining steps on the ambiguous "go ahead with the rest" instead of confirming — weak overlay drive-to-done overrides the ask-when-unsure rule.
3. **Eval doesn't cleanly evaluate the expected behavior:** W11.A leans on a holistic judge for a behavior that is structural (ledger-before-mutate); W11.B's ambiguity is mild ("go ahead with the rest" reads as near-authorization), so the case doesn't cleanly force the ask-vs-assume decision, and its judge score is shared (holistic) with A and C via v2.

## Problem & Outcome

**Problem:** For the weak local model, two deliberation behaviors — plan-before-mutate and ask-when-unsure — are under-reinforced (the base rule licenses "act on the obvious default" and the weak overlay's drive-to-done overrides both). Separately, the W11.A/B eval cases do not cleanly evaluate these expected behaviors (judge-heavy for a structural behavior; mild ambiguity; holistic rubric shared across cases).

**Outcome:** (a) The base rule and weak overlay reinforce the bracket — proceed when grounded, ask when genuinely unsure (never assume), and lay the ledger first for state-mutating multi-step work — without regressing drive-to-done on explicit authorization. (b) W11.A and W11.B are redesigned from first principles to evaluate the expected behavior on observable signals: W11.A gates on ledger-before-mutation; W11.B uses a continuation with one genuinely scope-ambiguous step (not a destructive/auto-approved action) so ask-about-that-step is unambiguously correct, graded on an observable signal (the step's tool call absent + a clarifying question present) plus a focused rubric that does not contaminate W11.A/C.

**Failure cost:** Without (a), the weak model keeps mutating before planning and assuming scope on ambiguous asks — real users get unrequested state changes and over-runs. Without (b), the eval can never stably certify these behaviors: it grades a structural behavior by holistic judge, tests a too-mild ambiguity, and couples three cases' scores — so it gives false negatives on correct behavior and false confidence otherwise.

## Scope

**In scope:**
- `co_cli/context/rules/03_reasoning.md` — sharpen the "act on the obvious default" line (`:46-48`) to "proceed when grounded in facts/context; if genuinely unsure, ask; never assume." All-profile change.
- `co_cli/context/overlays/weak_local.md` — reinforce, for the weak model, BOTH reflexes against the drive-to-done override: (i) for a state-mutating 3+ step directive, `todo_write` is the first execution step; (ii) on a genuinely ambiguous request, ask one precise question before acting (don't assume). Terse — floor-budget constrained.
- Eval redesign in `evals/eval_multistep_plan.py` (+ rubric files in `evals/_rubrics/`): W11.A gates on the structural ledger-before-mutation signal; W11.B uses a continuation with one genuinely scope-ambiguous step and asserts an observable signal (that step's tool call absent + a clarifying question present) plus a focused rubric decoupled from W11.A/C's holistic score.

**Out of scope:**
- `[slow]` budgets / `CALL_TIMEOUT_S` / `TURN_BUDGET_S` — environment; RCA-first, never bump without approval.
- `souls/` + personality DOCTRINE — built-in platform core; this is the rules/overlay layer only.
- The todo tool cues (`rw.py:214-218`) and closing reflex (`04_tool_protocol.md:32-35`) — already at parity.
- W11.D / `multistep_plan.v3` and W11.C synthesis behavior — shipped/correct; do not change their target behaviors (W11.C's rubric home may shift if v2 is decomposed, but its synthesis criterion must be preserved verbatim).
- `docs/specs/` edits — `prompt-assembly.md` is the design home, updated by sync-doc post-delivery.

## Behavioral Constraints

1. Prompt changes MUST be reflexive cues on **observable** signals (state-mutating + 3+ steps; a genuinely ambiguous request), never high-inference judgment prose (`feedback_instructions_counter_model_limits`).
2. MUST NOT regress drive-to-done on **explicit** authorization (W11.D) or co's anti-stall act-in-same-response stance — ledger-first and ask-when-unsure are added *first steps / branch on ambiguity*, not a return to "always stop and ask."
3. The `03_reasoning.md` change is **all-profile** — frontier behavior must not regress (frontier passed W11.A/B in run 1). Keep it a sharpening of existing intent, not a new constraint.
4. Floor budget: editing `weak_local.md`/`03_reasoning.md` is gated by `tests/test_instruction_budget.py` (`INSTRUCTION_BLOCK_CEILING = 25_000`) and `tests/test_instruction_floor_coupling.py` (F5, no deferred-tool `name(` in floor prose). **Verified live (default config): floor = 18,323 chars → 6,677 headroom** — ample for a base sharpen + two terse overlay clauses; no ceiling re-pin anticipated. (The stale 24,694/~306 figure in the test docstring is `personality=tars`, not the default the guard measures.) Run both guards in dev; re-pin only if a guard actually fails (`feedback_instruction_floor_guards_on_rule_edits`).
5. Eval redesign MUST follow `feedback_eval_real_use_case_scenario` (real Helios fixture) and `feedback_functional_tests_only` (assert observable behavior: tool-call ordering, `session_todos`, and for W11.B the absence of the ambiguous step's tool call + presence of a clarifying question — not prose structure). W11.B's ambiguity MUST be genuine **scope**-ambiguity (NOT a destructive/auto-approved action — that would test the approval gate, not the ask reflex) so the expected behavior (ask about that step) is unambiguously correct. UAT-smoke ladder; not a deterministic gate.
6. The eval redesign MUST account for the holistic judge + v2-shared-by-A/B/C: do not silently shift A/C grading. Either decompose v2 into per-behavior rubrics (A: plan-before-mutate; B: ask-when-unsure; C: synthesis preserved) or lead with structural gates and give B its own focused rubric — the choice is an Open Question for the critique loop.

## High-Level Design

### Thrust 1 — Base rule (`03_reasoning.md`)
Sharpen `:46-48`: "When a question has an obvious default interpretation, act on it" → "When the next action is clear from facts/context/history, proceed; when you are genuinely unsure, ask one precise question — never assume an unstated default." Preserve the surrounding discover-vs-ask and explicit-assumption lines. All-profile; minimal sharpening.

### Thrust 2 — Weak overlay (`weak_local.md`)
Add two terse reflexive clauses so drive-to-done doesn't override deliberation:
- Execution: *for a state-mutating directive needing 3+ steps, `todo_write` first (that IS execution), then carry out the steps.*
- Ambiguity branch: *if a request is genuinely ambiguous about what to do, ask one precise question before acting — don't assume.*
Budget likely forces a ceiling re-pin (Constraint 4).

### Thrust 3 — Eval redesign (first principles)
- **W11.A** — input: a clear state-mutating multi-step request. Gate primarily on the **structural** signal (`_mutated_before_plan` / `session_todos` written before any mutating tool), reducing judge reliance for a structural behavior.
- **W11.B** — input: a continuation that is mostly clear but contains ONE genuinely **scope-ambiguous** step (unclear *what* is wanted — NOT merely risky; avoid a destructive action, which the eval frontend auto-approves (`_deps.py:62-65`) and would test the approval gate, not the ask reflex). Expected: the agent asks one precise question about THAT step and does not fire it that turn, while it may proceed on the clear steps (proceed-when-clear preserved — not "halt the whole run"). Grade an **observable** signal — the ambiguous step's tool call is absent that turn AND a clarifying question is present — plus a **focused rubric** decoupled from W11.A/C (precedent: `eval_agentic_loop.py:504-535`).
- **Rubric mechanics** (Constraint 6 / Open Question): decompose v2 into per-behavior rubrics vs. structural-gate-plus-focused-B-rubric. Preserve W11.C's synthesis criterion verbatim; do not touch W11.D/v3.

### Thrust 4 — Validate
Re-run `evals/eval_multistep_plan.py` on the real Helios fixture; confirm W11.A fires `todo_write` before mutation and W11.B asks/confirms on the ambiguous input. Report `[slow]` but exclude from judgment.

## Tasks

### ✓ DONE TASK-1 — Sharpen the base ask-when-unsure rule (all profiles)
- **files:** `co_cli/context/rules/03_reasoning.md`
- **done_when:** the "act on the obvious default" line (`:46-48`) is replaced with proceed-when-grounded / ask-when-genuinely-unsure / never-assume, preserving the discover-vs-ask and explicit-assumption lines; `uv run pytest tests/test_instruction_budget.py tests/test_instruction_floor_coupling.py` green.
- **success_signal:** the base rule no longer licenses acting on an unstated default; frontier behavior (W11.A/B run-1-style) is preserved.
- **prerequisites:** none

### ✓ DONE TASK-2 — Reinforce ledger-first + ask-when-unsure in the weak overlay
- **files:** `co_cli/context/overlays/weak_local.md`
- **done_when:** two terse reflexive clauses present (ledger-first for state-mutating 3+ step work; ask-one-question on genuine ambiguity) without contradicting drive-to-done on explicit authorization; `uv run pytest tests/test_instruction_budget.py tests/test_instruction_floor_coupling.py` green (6,677-char headroom verified — no re-pin expected; F5 passes).
- **success_signal:** in a re-run, W11.A fires `todo_write` before mutating and W11.B asks/confirms on the ambiguous input.
- **prerequisites:** TASK-1

### ✓ DONE TASK-3 — Redesign W11.A and W11.B to evaluate the expected behavior (first principles)
- **files:** `evals/eval_multistep_plan.py`, `evals/_rubrics/` (new/edited rubric files per the chosen mechanics)
- **done_when:** W11.A gates on the structural ledger-before-mutation signal (`_mutated_before_plan`/`session_todos`), not solely the holistic judge; W11.B's input contains one genuinely scope-ambiguous step (NOT a destructive/auto-approved action) and the case asserts an observable signal — the ambiguous step's tool call is absent on that turn AND a clarifying question is present (net-new structural wiring, precedent `eval_agentic_loop.py:504-535`) — plus a focused rubric that does NOT change W11.A's or W11.C's grading (verified: re-run shows W11.A and W11.C verdicts not regressed); W11.C's synthesis criterion preserved; W11.D/v3 untouched; `uv run python evals/eval_multistep_plan.py` runs all W11 cases end-to-end emitting verdict lines under the ladder.
- **success_signal:** W11.A and W11.B each evaluate exactly one expected behavior on an observable signal; a wrong behavior (mutate-first / assume-don't-ask) reliably FAILs and the right behavior reliably PASSes.
- **prerequisites:** none (eval-only; independent of TASK-1/2, but validated together in TASK-4)
- **note:** functional/observable assertions only (`feedback_functional_tests_only`); reuse the real Helios fixture (`feedback_eval_real_use_case_scenario`).

### ✓ DONE TASK-4 — Validate the bracket on the real scenario
- **files:** `evals/eval_multistep_plan.py`
- **done_when:** with TASK-1/2/3 in place, `uv run python evals/eval_multistep_plan.py` runs the full W11 suite on the real Helios fixture (warm environment); the record shows W11.A ledger-before-mutation and W11.B ask/confirm-on-ambiguity under the PASS/SOFT_PASS ladder; `[slow]` reported but excluded as environment.
- **success_signal:** W11.A and W11.B pass on a warm run with the reinforced prompts + redesigned cases; the prompt-vs-eval mismatch is gone.
- **prerequisites:** TASK-1, TASK-2, TASK-3

## Testing

- TASK-1/2: instruction-floor guards (`test_instruction_budget.py`, `test_instruction_floor_coupling.py`) — the gate for injected rule/overlay edits. Functional-only.
- TASK-3: redesigned eval cases are themselves the runtime exercise; assertions are structural/observable (tool ordering, `session_todos`, confirm-before-acting). No prose-structure assertions.
- TASK-4: one warm end-to-end run under the ladder; `[slow]` is environment, not a behavioral fail.
- Full suite + lint at review-impl (base-rule + overlay edits touch the assembled prompt for every turn and every profile).

## Open Questions

- [ ] **Rubric mechanics for TASK-3 (critique-loop decision):** decompose `multistep_plan.v2` into per-behavior rubrics (A: plan-before-mutate, B: ask-when-unsure, C: synthesis) vs. lead with structural gates and give only B a focused rubric. **Recommended default:** per-behavior rubrics — cleanest separation, removes the holistic-shared-score coupling Core Dev flagged, and each case grades exactly one behavior. Re-raise if the decomposition would disturb W11.C's synthesis grading or W11.A's existing structural gate.

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| PO-M-1 (C1) | adopt-then-superseded | C1: realigning v2-criterion-2 was goalpost-moving on a deliberate pole. NOW the user has *defined* the expected behavior (ask-when-unsure on ambiguity is correct), so redesigning the eval to test it is principled, not goalpost-moving. | Thrust B was pulled in C1; the user's directive re-includes the eval redesign with a defined target behavior (Thrust 3). W11.B KEPT and validated, not retired. |
| CD-M-1 (C1) | adopt | Judge is holistic; v2 shared by A+B+C — an in-place criterion edit perturbs three cases. | Constraint 6 + Thrust 3 + Open Question require decoupling B's grading from A/C (per-behavior rubrics or structural gates), with a re-run verifying A/C aren't regressed. |
| CD-M-2 (C1) | adopt | Earlier done_when implied structural grading the case didn't perform. | TASK-3 now makes W11.A's structural gate explicit and requires W11.B to grade an observable confirm signal (not implied prose grading). |
| PO-m-1 (C1) | adopt | ~5/7 FAILs carry `[slow]`; "variance dropped" is environment-confounded. | Success signals are qualitative/observable (ledger-before-mutate; ask-on-ambiguity), not "variance dropped." |
| PO-m-2 (C1) | adopt | ≤1 in_progress already code-enforced. | No "don't batch-complete" prose added. |
| CD-m-1 (C1) | adopt-then-corrected | C1 figure (~306 headroom) was stale (`personality=tars`). CD-M-3 (C2) measured the default-config floor live: 18,323 / 6,677 headroom. | Superseded by CD-M-3 — ceiling re-pin dropped; guards pass as-is. |
| User directive 1 (fold-in) | adopt | Same overlay/root-conflict/eval; cohesive. | Plan broadened from plan-before-mutate only to BOTH deliberation behaviors. |
| User directive 2 (target) | adopt | Directive is general; the base "act on obvious default" phrasing is the assumption-licensing weak spot. | Thrust 1 sharpens `03_reasoning.md` (all profiles) + Thrust 2 reinforces the weak overlay. |
| User directive 3 (eval) | adopt | Eval must evaluate the expected behavior from first principles. | Thrust 3 / TASK-3: redesign W11.A (structural ledger gate) + W11.B (genuine ambiguity → observable ask/confirm), decoupled rubric. |
| CD-M-3 (C2) | adopt | Verified live: floor=18,323, headroom=6,677 (not ~306; the 24,694 figure was `personality=tars`, not the default the guard measures). Base edit + overlay share the one ceiling (`assembly.py:33,84-88`). | Constraint 4 corrected to 6,677 headroom; "ceiling re-pin in scope" dropped from TASK-1/TASK-2 (guards green as-is). |
| PO-m-1 (C2) | adopt | W11.B must isolate a single ambiguous step (ask about THAT), not "halt the whole run" — else it degenerates into "always pause" and contradicts W11.D. | Thrust 3 / TASK-3 / Constraint 5: W11.B isolates one scope-ambiguous step; proceed-when-clear preserved. |
| PO-m-2 (C2) | adopt | W11.B was otherwise judged by a model on a rubric co-authored with the prompt it steers (circularity); W11.A has the structural `_mutated_before_plan` anchor, W11.B had none. | W11.B's primary gate is now the observable signal (ambiguous step's tool call absent that turn + clarifying question present), not solely the LLM rubric. |
| CD-m-2 (C2) | adopt | A "risky/irreversible" W11.B step is gated by the approval system, which the eval frontend auto-approves (`_deps.py:62-65`) — it would test the approval path, not the ask reflex. | Dropped "risky/irreversible" framing; W11.B is pure scope-ambiguity (Scope, Thrust 3, TASK-3, Constraint 5). |
| CD-m-3 (C2) | adopt | W11.B is currently pure-judge (no structural gate); the confirm signal is net-new wiring. | TASK-3 done_when names the new T2 structural assertion (no mutating call + question/confirm marker), precedent `eval_agentic_loop.py:504-535`. |
| CD-m-4 (C2) | acknowledge | Per-behavior rubric decoupling via `load_rubric(name,version)` is mechanical; C lifts v2-criterion-3 verbatim. | None — Open-Question default (per-behavior rubrics) confirmed; re-run A/C-not-regressed check stands. |

## Final — Team Lead

Plan approved — converged on C2. PO approved with no blocking issues (the plan faithfully encodes the proceed-when-clear / ask-when-unsure / never-assume spec and keeps both poles of the bracket testable). Core Dev's one blocker (CD-M-3) was a stale floor-budget figure — corrected against a live measurement (18,323 floor / 6,677 headroom; no ceiling re-pin needed); the eval-redesign mechanics were verified achievable. All C2 minors adopted: W11.B reframed to pure scope-ambiguity (avoids the approval-gate confound), gated on an observable ask-signal (avoids prompt↔rubric circularity), isolating a single ambiguous step (preserves proceed-when-clear). Three thrusts — base-rule sharpen, weak-overlay reinforcement, eval redesign — are one coherent deliberation-discipline bracket.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev agentic-discipline-prompts`

## Delivery Summary — 2026-06-24

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | base "act on the obvious default" line replaced with proceed-when-grounded / ask-when-genuinely-unsure / never-assume; floor guards green | ✓ pass |
| TASK-2 | weak overlay carries ledger-first + ask-when-unsure reflexes; floor guards green | ✓ pass |
| TASK-3 | W11.A/C on focused per-behavior rubrics; W11.B redesigned with observable ask-signal gate; W11.C synthesis verbatim; W11.D untouched; eval runs end-to-end | ✓ pass |
| TASK-4 | warm end-to-end run shows W11.A ledger-before-mutation + W11.B ask-on-ambiguity; `[slow]` excluded as environment | ✓ pass |

**Open Question resolved → per-behavior rubric decomposition.** `multistep_plan.v2` split into `plan_before_mutate.v1` (A), `ask_when_unsure.v1` (B), `synthesis_from_sources.v1` (C); v2 kept on disk for historical run records; W11.D stays on v3. W11.C's synthesis criterion lifted verbatim.

**Tests:** floor guards (budget ceiling + F5 coupling) green ×2; lint clean. The eval is the runtime exercise (not a pytest case).

**Eval validation (warm, real Helios fixture).** All four cases behaviorally correct; every FAIL is `[slow]` environment-only — confirmed by untouched **W11.D** passing clean in run 1 but tripping `[slow]` in run 2 (same code → pure local-Ollama latency variance). No timeout bump (RCA-first; out of scope per plan + `feedback_long_llm_call_rca_first`).
- W11.A — `mutated_before_plan=False`, 5-step plan, judge 10 → plan/ledger-before-mutate ✓
- W11.B — `t2_mutated=False t2_asked=True`, judge 10 → grounded (searched memory+sessions), found no agreed value, asked instead of inventing one ✓
- W11.C — both sources referenced, judge 9 → synthesis not regressed ✓
- W11.D — todos 3/3 completed, judge 9 → drive-to-done on authorization unchanged ✓

**Two-run delta (headline).** Run 1 W11.B = *assume-don't-ask* (agent grounded, found no convention, then picked a default destination). Run 2 W11.B = *proceed-on-clear + ask-on-ambiguous*. Fixed by two dev-loop corrections:
1. **Scenario** — the run-1 ambiguous step ("save it wherever the team keeps this") was defensibly-defaultable (Gate 1 note #2 materialized). Replaced with a **non-defaultable** policy value ("set the data-retention window to what we agreed on" — never decided, no sensible default, references a nonexistent agreement).
2. **Prompt** — the run-1 weak-overlay clause was a high-inference conditional ("if genuinely ambiguous, ask"), which **violated this plan's Constraint 1** (reflex-on-observable-cue, never high-inference judgment). Reframed to an **excuse→reality reflex** on the observable cue ("if you catch yourself thinking 'they didn't specify, so I'll pick a default' / 'we must have agreed on X' → STOP and ask"). Device pattern borrowed from the `skill-prompt-device-adoption` plan's anti-rationalization block (surfaced mid-delivery by the user's cross-review pointer).

**Doc Sync:** clean — no `##` section added/renamed/removed; `prompt-assembly.md` lists section headings only (explicitly orientation-only, not a contract), all still accurate. *Optional follow-up (not done — enhancement, not an inaccuracy):* `prompt-assembly.md` §base-vs-overlay-partition could gain the deliberation bracket (base judgment statement + weak observable reflex) as a second worked example alongside conciseness — left for ship/review judgment.

**Files changed:**
- `co_cli/context/rules/03_reasoning.md` (TASK-1)
- `co_cli/context/overlays/weak_local.md` (TASK-2)
- `evals/eval_multistep_plan.py` (TASK-3)
- `evals/_rubrics/plan_before_mutate.v1.md`, `ask_when_unsure.v1.md`, `synthesis_from_sources.v1.md` (TASK-3 — new)

**Overall: DELIVERED**
All four tasks pass `done_when`; the deliberation bracket (plan-before-mutate · ask-when-unsure · synthesis · drive-to-done) is behaviorally validated on the real fixture. Remaining `[slow]` trips are environment — do not bump timeouts.

**Next step:** `/review-impl agentic-discipline-prompts` — full suite + evidence scan + behavioral verification → verdict appended.

## Implementation Review — 2026-06-24

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | base "act on the obvious default" replaced w/ proceed-when-grounded / ask / never-assume; discover-vs-ask + explicit-assumption lines preserved; floor guards green | ✓ pass | `03_reasoning.md:46-48` — old 3-line "obvious default → act on it" block replaced with "When the next action is clear … proceed. When you are genuinely unsure … ask one precise question rather than assume an unstated default." Discover-vs-ask (`:40-44`) and explicit-assumption (`:53-55`) lines untouched. All-profile sharpen, not a new constraint. |
| TASK-2 | two terse reflexive clauses (ledger-first; ask-on-ambiguity) without contradicting drive-to-done; floor guards green | ✓ pass | `weak_local.md:23-28` — ledger-first ("`todo_write` … first execution step … laying the ledger IS acting") + excuse→reality ask reflex ("if you catch yourself thinking 'they didn't specify, so I'll pick a sensible default' … STOP and ask"). Sits inside `## Execution` ahead of the act-in-same-response block; drive-to-done (`:17-21`) intact. Observable-cue reflex, not high-inference (Constraint 1). |
| TASK-3 | W11.A structural ledger gate; W11.B observable ask-signal gate (non-destructive ambiguous step); focused rubric not changing A/C; C synthesis verbatim; D/v3 untouched; runs end-to-end | ✓ pass | W11.A gate `eval_multistep_plan.py:343-345` (`t0_mutated_before_plan` → FAIL before judge). W11.B gate `:449-462` (`t2_mutated` FAIL / `not t2_asked` FAIL / then judge), signals computed `:437-438`, non-defaultable retention-policy input `:423-424`. Per-behavior rubrics loaded `:294,397,509`. `synthesis_from_sources.v1.md:16-22` lifted verbatim from `multistep_plan.v2.md:37-42` (same distinctive phrases, same source filenames). v3/W11.D untouched (`:807-810` runner list). |
| TASK-4 | warm end-to-end run shows W11.A ledger-before-mutation + W11.B ask-on-ambiguity; `[slow]` excluded | ✓ pass | Delivery warm run (Delivery Summary): W11.A `mutated_before_plan=False`; W11.B `t2_mutated=False t2_asked=True`. Structural gates that grade these signals independently re-verified by source read above. LLM-mediated — verified via eval, not chat (skill Phase 7). |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| W11.B clarifying-question signal is a coarse heuristic (`"?" in assistant_text` matches any question mark, incl. rhetorical) | `eval_multistep_plan.py:438` | minor | No change — the plan explicitly chose "a clarifying question is present" as the observable signal (Constraint 5, CD-m-3); the `ask_when_unsure.v1` judge backs it on quality. Acceptable as designed for a UAT-smoke ladder. |

_No blocking issues. Scope-creep scan: working tree also carries `co_cli/skills/*`, `docs/reference/RESEARCH-skills-*`, `tests/test_flow_skill_bundled_library.py`, `uv.lock` — these belong to other concurrent active plans (skills curation, pydantic-ai migration), were present in the conversation-start snapshot, and are NOT introduced by this delivery. Excluded from review._

### Tests
- Command: `uv run pytest -q` (includes floor guards `test_instruction_budget.py` + `test_instruction_floor_coupling.py`)
- Result: 843 passed, 0 failed
- Log: `.pytest-logs/20260624-000349-review-impl.log`
- Lint: `scripts/quality-gate.sh lint` — PASS (ruff check + format, 395 files)

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads — assembles the prompt from the edited rule + overlay at zero LLM cost)
- Eval import/wiring smoke: ✓ `eval_multistep_plan` imports, `_case_w11_b_ask_when_unsure` present, old `_case_w11_b_intermediate_checkpoint` gone, all three `v1` rubrics load
- W11.A/B deliberation reflexes (LLM-mediated): verified via the eval's warm delivery run (W11.A `mutated_before_plan=False`; W11.B `t2_mutated=False t2_asked=True`); structural gates that grade those signals re-verified by source. Not re-run here — the eval is real-Ollama with documented run-to-run `[slow]` environment variance the plan excludes from judgment (`feedback_long_llm_call_rca_first`); chat turn non-gating per skill Phase 7.

### Overall: PASS
All four `✓ DONE` tasks confirmed against `done_when` with file:line evidence; full suite green (843), lint clean, boot + eval-wiring smoke clean; synthesis criterion preserved verbatim, v2 retained for historical records, W11.D/v3 untouched; no scope creep from this delivery. Ready for Gate 2 → `/ship`.
