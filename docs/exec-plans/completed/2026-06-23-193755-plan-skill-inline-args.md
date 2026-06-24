# Make `/plan` honor an inline task, and eval the real plan→todo→done flow

## Context

The `regeneralize-plan-skill` delivery (shipped v0.8.468) added a plan→todo emission bridge to `co_cli/skills/plan/SKILL.md` but deliberately left its runtime validation as a non-blocking note: no eval drives the **real** `/plan` skill through dispatch and confirms the bridge fires and the todos get executed to done. The downstream todo→done lifecycle is covered (`evals/eval_agentic_loop.py::W12.D` completeness gate); plan-creation/checkpoint is covered (`evals/eval_multistep_plan.py` W11.A/B/C, which explicitly does not assert todo state). Neither drives the `/plan` skill dispatch nor asserts `session_todos` populated from a `/plan` turn.

While scoping that eval, a precondition surfaced. Slash dispatch only interpolates a skill's args when the body contains `$ARGUMENTS` (`co_cli/commands/core.py:141` — `if args and "$ARGUMENTS" in body:`). The bundled `plan` skill body has **no `$ARGUMENTS`** (none of the 5 bundled skills do), so `/plan write a literature review on X` **drops the task text** — the agent receives only the methodology body. Yet `plan/SKILL.md` frontmatter advertises `argument-hint: "[what to plan]"`, so the advertised contract and the actual behavior disagree. An eval of "the real plan skill" that passed a task inline would therefore be testing a path the task never reaches.

User decision (this session): fix the skill so `/plan <task>` carries the task, then eval that fixed flow.

Source facts established by reading:
- Dispatch interpolation: `co_cli/commands/core.py:140-146`. Trigger is `if args and "$ARGUMENTS" in body`. With `$ARGUMENTS` present but no args, the guard is false and the literal `$ARGUMENTS` survives into `delegated_input` — an unhandled leak.
- Existing dispatch tests: `tests/test_flow_slash_dispatch.py` covers positional `$1..$N` (incl. the `$10` no-smash case, `:97`), the `$ARGUMENTS` raw-blob substitution (`:125`), and a no-`$ARGUMENTS`-body passthrough (`test_no_args_body_unchanged`, `:145`). The leak case (body **has** `$ARGUMENTS`, invoked with **no** args) is **untested**.
- Eval harness: `evals/eval_multistep_plan.py` already drives multi-turn `run_turn` via `_drive_turns` (`:195`) and registers cases in `main()`'s runner tuple (`:557`). `evals/eval_skills.py::case_w4_a_dispatch_user_skill` (`:102`) is the reference for driving a skill through `dispatch()` → `DelegateToAgent` → `run_turn`. `evals/_deps.py` builds full production deps via `create_deps()`, so `deps.skill_catalog` includes the real `plan` skill.
- The completeness pattern to mirror: `evals/eval_agentic_loop.py::_case_w12_d_completeness_gate` (`:570`) — `_final_todo_states` reads the last `todo_read`/`todo_write` ToolReturnPart; PASS folds a structural floor (todo fired, no unresolved item unless flagged) with the judge.
- self-planning spec: `docs/specs/self-planning.md` — todo list IS the agent's session plan; `CoSessionState.session_todos` is the runtime source of truth.

## Problem & Outcome

**Problem:** `/plan <task>` silently drops the task (no `$ARGUMENTS` in the body), contradicting its own `argument-hint`; and the plan→todo→done flow through the real `/plan` skill has no eval coverage.

**Outcome:** `/plan <task>` carries the task into the delegated turn so the agent plans the actual request; a bare `/plan` (no args) still delegates a clean body and falls back to conversation context with no literal placeholder leaking to any skill; and a new eval case drives the **real** `/plan` skill through dispatch on a multi-step task, confirming it produces a scoped, ordered plan, materializes it as a session todo ledger, and drives those todos to completion.

**Failure cost:** Without the dispatch/skill fix, `/plan <task>` keeps discarding the task — the skill's primary invocation is broken and the `argument-hint` lies. Without the eval, the plan→todo bridge — the *sole* differentiator that saved `plan` from the coding-cluster cut — rests entirely on an untested path, so the skill's whole justification is unverified and a regression that stops `/plan` from emitting or completing todos would ship silently.

## Scope

**In scope:**
- Dispatch: make `$ARGUMENTS` (and `$0`) interpolation safe when **no** args are passed — substitute empty, never leak the literal. Affects all skills' invocation, not just `plan`.
- `plan/SKILL.md`: add a `$ARGUMENTS` placeholder so `/plan <task>` carries the task, phrased to read cleanly when empty (bare `/plan` falls back to conversation).
- A new eval case (`W11.D`) in `evals/eval_multistep_plan.py` driving the real `/plan` skill on a multi-step task → plan + populated todo ledger + execution to done.

**Out of scope:**
- Adding `$ARGUMENTS` to the other 4 bundled skills (`doctor`/`documents`/`office`/`skill-creator`) — they operate on conversation/paths by design; only `plan` advertises a task arg. (The dispatch fix benefits them passively but forces no change.)
- Unfilled positional `$N` leak (body references `$5` but fewer args given) — a separate pre-existing edge; the `plan` body uses only `$ARGUMENTS`, so it is not exercised here. Mentioned, not fixed.
- A new rubric version — reuse `multistep_plan.v2` for plan-quality judgment; the todo-specific assertions are structural (`session_todos`), not rubric criteria.
- `docs/specs/` edits (sync-doc post-delivery owns specs; `self-planning.md`/`skills.md` may warrant a one-line note on the arg contract — left to sync-doc).

## Behavioral Constraints

1. The dispatch change MUST NOT regress: a body with no `$ARGUMENTS` and no args stays byte-identical (`test_no_args_body_unchanged`); args-present substitution (`$ARGUMENTS` blob, positional `$1..$N` incl. `$10`) is unchanged.
2. A bare `/plan` (no args) MUST deliver a body with no literal `$ARGUMENTS`/`$0` token and read cleanly (no dangling "Request:" label with empty value that confuses the agent). **Soft-mandatory task:** the body falls back to a task already present in the conversation; if there is no task in args AND none in the conversation, the body MUST instruct the agent to ask the user what to plan rather than fabricate a plan from nothing. This is a skill-body instruction only — NO dispatch-level arg validation, no error path, no `args-required` flag (keeps `plan` consistent with the other 4 bundled skills and preserves the "plan what we just discussed" flow).
3. `/plan <task>` MUST carry the task text into `delegated_input` so the agent plans the actual request.
4. The eval is a **UAT smoke with a PASS/SOFT_PASS/FAIL ladder** — WEAK_LOCAL todo emission is probabilistic, so a single run demonstrates the flow *can* complete, mirroring `W11.A`'s ladder. It is NOT a deterministic gate; emission shortfall degrades to SOFT_PASS under the judge, it does not hard-FAIL on a probabilistic miss alone.
5. Eval assertions are **functional/observable only** and read **one** named source: `deps.session.session_todos` (the live post-merge runtime source of truth, `co_cli/deps.py`; written at `co_cli/tools/todo/rw.py`). Assert: populated (count ≥2) after the plan turn, items reaching `completed`, no unresolved `pending`/`in_progress` left unflagged in the closing summary. This mirrors `W12.D`'s completeness *logic* but computed over `session_todos` directly — it does NOT import `_final_todo_states` (which reads a ToolReturnPart snapshot, a different source that can diverge from live state). No structural/prose assertions on plan text.

## High-Level Design

### Dispatch (`co_cli/commands/core.py:140-146`)
Change the trigger so interpolation runs whenever the body contains `$ARGUMENTS`, substituting empty when no args:

```
body = skill.body
if "$ARGUMENTS" in body:
    args_list = args.split() if args else []
    body = body.replace("$ARGUMENTS", args or "")
    body = body.replace("$0", name)
    for i, arg in reversed(list(enumerate(args_list, 1))):
        body = body.replace(f"${i}", arg)
```

- args present → identical to today (blob + positional substitution).
- no args, `$ARGUMENTS` present → `$ARGUMENTS`/`$0` replaced with empty/name; no literal leak.
- no args, no `$ARGUMENTS` → guard false, body untouched (existing passthrough holds).

Minimal, monomorphic, single home (dispatch already owns interpolation). Unfilled positional `$N` is left as-is (out of scope).

### `plan/SKILL.md`
Add a `$ARGUMENTS` placeholder that reads cleanly empty. Placement is a dev decision within Constraint 2; the recommended shape is a trailing context line that degrades to whitespace when empty rather than a labeled field that dangles — e.g. a short trailing block carrying the request so the methodology comes first and the concrete task lands at the end. The `argument-hint` already matches once the body honors args. **Soft-mandatory:** the body also carries a low-inference reflex — when no task is supplied and none is evident in the conversation, ask the user what to plan before drafting (one imperative on the observable cue "no task to plan"), aligning with the skill's existing Phase-1 "restate the request" step.

### Eval `W11.D` (`evals/eval_multistep_plan.py`)
A new `_case_w11_d_plan_skill_todo_execution` runner, registered in `main()`'s tuple:
1. Build a `CommandContext` (mirror `eval_skills.py::_make_ctx`); `dispatch("/plan <multi-step self-contained knowledge task>", ctx)` → expect `DelegateToAgent`; apply `skill_env`/`active_skill_name` as `main.py` does.
2. Turn 0: `run_turn(user_input=outcome.delegated_input)` via `record_turn`. **Floor:** `deps.session.session_todos` populated with ≥2 items after the turn → the bridge fired.
3. Turn 1: a user "go ahead and complete it" turn. **Floor:** ≥1 item in `deps.session.session_todos` is `completed` AND no `pending`/`in_progress` remains unless the closing-summary text flags it (the `W12.D` completeness logic, computed over `session_todos` — no `_final_todo_states` import).
4. Judge the transcript against `multistep_plan.v2`. Combine: PASS = floor met (emission + execution) + judge pass; SOFT_PASS = judge passes but emission/execution partial; FAIL otherwise. Apply the same `TURN_BUDGET_S * len(inputs)` slow-guard as the other cases.

Task is **self-contained and completable** (no dependence on an unreachable fixture goal — unlike `W12.D`'s deploy-log), so todos *can* reach done. Fixture load is optional; reuse `multistep_research_baseline` for a realistic workspace if the harness expects it, but the task must not require its seeded artifacts.

## Tasks

### ✓ DONE TASK-1 — Dispatch: safe `$ARGUMENTS` interpolation on bare invocation
- **files:** `co_cli/commands/core.py`, `tests/test_flow_slash_dispatch.py`
- **done_when:** dispatching a synthetic skill (`_write_skill` fixture) whose body contains `$ARGUMENTS` with **no** args yields a `delegated_input` containing no literal `$ARGUMENTS`/`$0` token (new test — exercises the dispatch mechanism generically; the real-bundled-`plan`-body empty render is asserted in TASK-2 once that body gains `$ARGUMENTS`); the args-present substitution tests (`$ARGUMENTS` blob, positional `$1..$10`) and `test_no_args_body_unchanged` still pass; `uv run pytest tests/test_flow_slash_dispatch.py` green.
- **success_signal:** a bare `/plan` delegates a clean body (no `$ARGUMENTS` literal); `/plan write X` carries `write X`.
- **prerequisites:** none

### ✓ DONE TASK-2 — `plan/SKILL.md`: carry the inline task
- **files:** `co_cli/skills/plan/SKILL.md`, `tests/test_flow_slash_dispatch.py`
- **done_when:** the body contains a `$ARGUMENTS` placeholder AND a soft-mandatory reflex instructing the agent to ask what to plan when no task is supplied and none is in the conversation (Constraint 2); a dispatch-level test over the **real bundled `plan` skill** asserts both (a) `dispatch("/plan <task>")` → `delegated_input` contains the task text, and (b) bare `dispatch("/plan")` → `"$ARGUMENTS" not in delegated_input` and no dangling empty label (the real-body empty render — CD-m-3, sited here because the body only gains `$ARGUMENTS` in this task); the bundled-library load gate (`tests/test_flow_skill_bundled_library.py`) is green; lint clean.
- **success_signal:** `/plan plan a literature review on topic X` delegates a body that contains "literature review on topic X".
- **prerequisites:** TASK-1
- **note:** the instruction-floor guards (`test_instruction_budget.py`, `test_instruction_floor_coupling.py`) do NOT apply — skill bodies are injected via `delegated_input` at dispatch time, not assembled into the static floor (CD-m-4). Do not run them for this edit.

### ✓ DONE TASK-3 — Eval `W11.D`: real `/plan` → todo ledger → done
- **files:** `evals/eval_multistep_plan.py`
- **reader:** reads live `deps.session.session_todos` directly (the eval holds `deps`) — no import of `_final_todo_states` from `eval_agentic_loop.py`, no inline copy (CD-m-2).
- **done_when:** a new `W11.D` runner is registered in `main()`'s tuple and drives the real `/plan` skill via `dispatch()` → `run_turn`; running `uv run python evals/eval_multistep_plan.py` executes W11.D end-to-end and emits a `W11.D: PASS|SOFT_PASS|FAIL` line whose reason reports the observed `deps.session.session_todos` count and completed-count; the case asserts (functionally) `session_todos` populated after the plan turn and todos reaching `completed`/flagged after the execute turn, under the PASS/SOFT_PASS ladder.
- **success_signal:** W11.D run shows `/plan` produces a scoped ordered plan, a populated session todo ledger (≥2 items), and drives the todos to completion (or flags any it could not finish).
- **prerequisites:** TASK-2

## Testing

- Functional only. TASK-1: new pytest in `tests/test_flow_slash_dispatch.py` for the no-args-with-`$ARGUMENTS` leak case; existing substitution + passthrough cases must stay green (shared dispatch path → run the whole file).
- TASK-2: bundled-library load/parse gate (`test_flow_skill_bundled_library.py`) — no plan-content assertions (functional-only policy). A dispatch-level assertion that `/plan <task>` carries the task belongs in `test_flow_slash_dispatch.py` if added, not the library test.
- TASK-3: the eval itself is the runtime exercise — one real end-to-end run, reported under the PASS/SOFT_PASS/FAIL ladder (evals are UAT smoke; probabilistic emission is expected and does not invalidate the run).
- Full suite + lint at review-impl (the dispatch change touches the shared skill-invocation path used by every skill).

## Open Questions

None — ready to implement. The dispatch-fix shape, the bare-invocation contract, the eval gating ladder, and fixture choice are settled inline above. Re-raise trigger: if review finds the unfilled-positional `$N` leak is actually reachable by a bundled skill (it is not today), pull that into TASK-1's scope.

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-m-1 | adopt | Two distinct todo sources (live `session_todos` vs ToolReturnPart snapshot) can diverge; the plan must name one. | Constraint 5 + HLD eval steps + TASK-3 now read live `deps.session.session_todos` only; dropped the `_final_todo_states` reuse phrasing (kept the W12.D completeness *logic*). |
| CD-m-2 | adopt | The reader dependency was implicit. | TASK-3 gains a `reader:` line stating it reads live `session_todos`, no eval→eval import, no inline copy. |
| CD-m-3 | modify | Right instinct (test the real bundled body's empty render) but mis-sited: at TASK-1 the real `plan` body has no `$ARGUMENTS` yet, so the assertion is vacuous. | TASK-1 keeps the generic synthetic leak test; the real-bundled-`/plan` empty-render + task-carry assertion moved to TASK-2 done_when (and `tests/test_flow_slash_dispatch.py` added to TASK-2 `files:`), where the body has `$ARGUMENTS`. |
| CD-m-4 | adopt | Confirms skill-body edits are not on the static instruction floor; saves a needless guard run. | TASK-2 gains a `note:` that the floor guards do not apply. |
| PO-m-1 | adopt | The eval-gap cost is more concretely the loss of `plan`'s sole survival justification. | Failure cost reworded to lead with the bridge-as-sole-differentiator framing. |
| PO-m-2 | acknowledge | Honoring `$ARGUMENTS` aligns runtime with the skill's stated contract; the bare-`/plan`→conversation fallback must be preserved. | No change — already Constraint 2; flagged so dev does not drop the fallback. |
| PO-m-3 | acknowledge | PASS/SOFT_PASS ladder is the correct stance for probabilistic WEAK_LOCAL emission; the deterministic gate already exists (W12.D). | No change — Constraint 4 stands. |
| Soft-mandatory arg (user, Gate 1) | adopt | A bare `/plan` with no task should not flail or fabricate, but hard-mandatory (dispatch validation + error UX + `args-required` flag) is scope creep, breaks the "plan what we discussed" flow, and makes `plan` inconsistent with the other 4 bundled skills. A skill-body reflex achieves it with zero dispatch change. | Constraint 2 + HLD `plan/SKILL.md` + TASK-2 done_when: the body asks the user what to plan when no task is supplied and none is in the conversation; no dispatch-level arg validation. |

## Final — Team Lead

Plan approved — converged on C1 (both Core Dev and PO returned `Blocking: none`). Three change-sets, one coherent plan: the eval (TASK-3) cannot honestly exercise "the real `/plan` skill with a task" until the dispatch arg path (TASK-1) and the skill body (TASK-2) carry the task — fix-then-eval is the only ordering that tests the shipped contract. All five adopted minors are wording/precision sharpenings; no scope or design change.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev plan-skill-inline-args`

## Delivery Summary — 2026-06-23

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | bare invocation yields no literal `$ARGUMENTS`/`$0`; args-present substitution + `test_no_args_body_unchanged` still pass | ✓ pass |
| TASK-2 | `plan` body carries `$ARGUMENTS` + ask-when-no-task reflex; real-bundled `/plan <task>` carries task, bare `/plan` renders clean | ✓ pass |
| TASK-3 | `W11.D` registered + drives real `/plan` via dispatch→run_turn; reports `session_todos` counts under PASS/SOFT_PASS/FAIL ladder | ✓ pass |

**Tests:** scoped — 16 passed (`test_flow_slash_dispatch.py` + `test_flow_skill_bundled_library.py`), 0 failed; lint clean. W11.D eval **PASS** (`rubric=v3`, `todos_after_plan=3`, `completed=3`, `unresolved=0`, judge 9/10).
**Doc Sync:** fixed — `docs/specs/skills.md` dispatch arg-interpolation contract (guard, substitution-table conditions, "used verbatim" sentence) realigned to `core.py`.

### Plan amendments (approved by user during delivery)
1. **New rubric `multistep_plan.v3`** — the plan's Out-of-scope assumed `multistep_plan.v2` was a reusable generic plan-quality rubric. Empirically false: v2 is hard-coupled to the Helios scenario AND its criterion 2 *rewards pausing*, which directly penalizes W11.D's authorized drive-to-completion (a real run scored W11.D 5/10 on "didn't pause" despite a flawless 3-todos→3-completed ledger). Authored `evals/_rubrics/multistep_plan.v3.md` (scenario-agnostic plan→todo→done) and pointed W11.D at it. **This reverses the "no new rubric version" Out-of-scope item.**
2. **W11.D task = real knowledge-work scenario** — the first task drafts (LRU/LFU note) were synthetic, reverse-engineered to force todo emission. Per user direction ("eval must validate behavior on a real use-case scenario fitting co's core mission"), W11.D now drives a **stakeholder briefing grounded in the seeded Helios artifacts** (synthesis of `project_helios_context` + `decision_use_sqlite`) — knowledge work, not a code refactor; completable in-conversation so todos reach `completed`.
3. **Ledger-isolation fix (eval bug found during delivery)** — W11.A/B/C share the `deps` instance and accumulate `session_todos`; W11.D's emission floor was reading that stale cross-case state. Added `deps.session.session_todos = []` at W11.D start so the floor measures only this case's emission. Without it, the floor passes for the wrong reason.

### Files changed
- `co_cli/commands/core.py` — dispatch guard `args and "$ARGUMENTS"` → `"$ARGUMENTS" in body` (TASK-1)
- `co_cli/skills/plan/SKILL.md` — trailing `$ARGUMENTS` block + Phase-1 ask-when-no-task reflex (TASK-2)
- `tests/test_flow_slash_dispatch.py` — bare-invocation leak test + real-bundled-`/plan` task-carry/clean-empty tests
- `evals/eval_multistep_plan.py` — `W11.D` runner + registration; `main()` prints true 4-state verdict
- `evals/_rubrics/multistep_plan.v3.md` — new plan→todo→done rubric (amendment 1)

### Known issues (out of plan scope — flagged, not fixed)
- **W11.A / W11.B flaky** across all 4 delivery runs: behavioral run-variance on v2's *pause* criterion + intermittent `[slow]` trips. Untouched by this plan (W11.D is appended after them). **Environmental degradation observed**: ollama throughput collapsed across back-to-back heavy eval runs (turns hitting the 50s `CALL_TIMEOUT_S` cap with empty output in run 3, recovered partially by run 4). Per policy, timeouts NOT changed — RCA points to environment saturation, not logic. A clean-environment re-run is advisable to confirm W11.A/B baseline.

**Overall: DELIVERED**
All three tasks pass their `done_when`; W11.D validated green end-to-end on the real scenario. Two plan amendments (new rubric + real-scenario task) were approved by the user mid-delivery; the "no new rubric version" Out-of-scope item is formally reversed. Pre-existing W11.A/B flakiness + ollama degradation flagged for review-impl.

**Next step:** `/review-impl plan-skill-inline-args`

## Implementation Review — 2026-06-23

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | bare invocation leaks no literal `$ARGUMENTS`/`$0`; args-present substitution + `test_no_args_body_unchanged` pass | ✓ pass | `core.py:140-147` guard is `if "$ARGUMENTS" in body:`; `args` is always a str (`""` on bare invoke, `core.py:117`) so `args.split()`/`replace` never hit `None` — clean simplification of the HLD's defensive `args or ""`. `test_arguments_token_not_leaked_on_bare_invocation:182` asserts exact `"Plan:  (via argskill)"` |
| TASK-2 | body carries `$ARGUMENTS` + ask-when-no-task reflex; real-bundled `/plan <task>` carries task, bare `/plan` renders clean | ✓ pass | `SKILL.md:92` trailing `$ARGUMENTS` block (renders to whitespace empty); `SKILL.md:19` ask-when-no-task reflex on the observable cue "no request … and none in the conversation". `test_bundled_plan_skill_carries_inline_task:205` + `test_bundled_plan_skill_bare_renders_clean:223-225` confirm both |
| TASK-3 | `W11.D` registered + drives real `/plan` via dispatch→run_turn; emits verdict line; reads live `session_todos` | ✓ pass | Registered `eval_multistep_plan.py:779`; dispatches real `/plan` (`:656`), asserts task carried (`:662`), reads live `deps.session.session_todos` (`:706,711`) — no `_final_todo_states` import (grep-confirmed); ledger isolated at `:647`. **Re-ran: W11.D PASS** (`rubric=v3 todos_after_plan=3 completed=3 unresolved=0 judge.score=10`) |

Call path traced: `dispatch()` → `DelegateToAgent(delegated_input=body,…)` → eval applies `skill_env`/`active_skill_name` (`:666-668`) → `run_turn` (`:675`). Sync-doc (`docs/specs/skills.md:116-143`) realigned to the new guard + substitution-table conditions — verified against `core.py`.

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Module docstring blanket-states all cases grade against `multistep_plan.v2.md`, but W11.D uses v3 | `evals/eval_multistep_plan.py:13-14` | minor | Fixed — docstring now states W11.A-C use v2, W11.D uses v3 |

### ⚠ Staged-file hygiene — ACTION REQUIRED before `/ship`
The working tree mixes **this delivery** with **unrelated in-flight work**. `/ship` must stage only this plan's files.

- **This delivery (stage these):** `co_cli/skills/plan/SKILL.md`, `evals/eval_multistep_plan.py`, `evals/_rubrics/multistep_plan.v3.md` (untracked), `docs/specs/skills.md`.
- **Already committed (no action):** TASK-1's dispatch fix + all dispatch tests landed in commit `8fd2e989`; `co_cli/commands/core.py` and `tests/test_flow_slash_dispatch.py` carry the verified TASK-1/TASK-2 code at HEAD.
- **Do NOT stage (unrelated — separate effort):** the working-tree `core.py` diff (removes the `/history` command + edits `/memory` desc), `co_cli/commands/history.py` (deletion), `docs/reference/RESEARCH-skills-prompt-gaps.md`, `docs/specs/tui.md`, `uv.lock`, and the other untracked exec-plans under `docs/exec-plans/active/`.

### Tests
- Command: `uv run pytest -v` (full) + scoped `tests/test_flow_slash_dispatch.py tests/test_flow_skill_bundled_library.py`
- Result: full **842 passed, 0 failed** (220s); scoped **16 passed, 0 failed**
- Logs: `.pytest-logs/*-review-impl.log`, `.pytest-logs/*-review-scoped.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- `evals/eval_multistep_plan.py` (W11.D): ✓ **PASS** — real `/plan` skill drives a Helios stakeholder briefing into a 3-item session todo ledger, all 3 driven to `completed`, honest closing summary (judge 10/10). `success_signal` verified: `/plan <task>` produces a scoped ordered plan, a populated ledger (≥2), and drives it to done. LLM-mediated; the deterministic dispatch (task-carry, clean-empty render) is additionally covered by the scoped pytest. Chat interaction non-gating.
- Pre-existing, out of scope: W11.B FAILed (judge 5 synthesis miss + `[slow] 123.3s vs 105s`) — the v2 checkpoint case the delivery flagged as flaky/env-dependent; this plan only appends W11.D and never touches it. W11.A/W11.C PASS this run.

### Overall: PASS
All three tasks meet their `done_when`: TASK-1/TASK-2 verified by green scoped tests + source reading, TASK-3 re-executed green end-to-end (W11.D PASS). Full suite green, lint clean, boot smoke OK. One minor docstring mismatch fixed. **Gate-2 caveat:** at `/ship`, stage only the four delivery files listed above — the working tree carries unrelated `/history`-removal work that must not ride along.
