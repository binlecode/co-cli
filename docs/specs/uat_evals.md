# Co CLI — UAT Evals

> Pairs with [00-mission.md](00-mission.md): mission defines the contract; this spec defines how the contract is verified. Component specs validated by this surface: [core-loop.md](core-loop.md), [compaction.md](compaction.md), [memory.md](memory.md), [sessions.md](sessions.md), [skills.md](skills.md), [tools.md](tools.md), [personality.md](personality.md), [prompt-assembly.md](prompt-assembly.md), [self-planning.md](self-planning.md).

UAT evals are co's **runtime quality contract**: end-to-end scenarios that exercise the shipped agent against real `CoDeps`, real Ollama, real FTS, real disk. Unit tests prove the wiring; UAT evals prove the agent *acts like co should act*. This spec defines the first-principle decomposition (mission → testable behaviors), the eval registry, the rubric discipline, and the gating policy.

## 1. Functional Architecture

```mermaid
flowchart TD
    Mission["00-mission.md\n(trusted · local · personal · operator · knowledge work)"]
    Mission --> Tenets["Mission tenets\n(7 testable claims)"]
    Tenets --> Components["4-pack + skills + trust\n(agent loop · context · tools · memory · skills · trust)"]
    Components --> Evals["evals/eval_*.py\n(W1–W4 functional smoke · W7–W12 behavioral)"]
    Evals --> Judge["evals/_judge.py\n(LLM rubric for prose-quality cases)"]
    Evals --> Structural["Structural assertions\n(FTS hits · file presence · state mutations)"]
    Evals --> Perf["evals/_perf.py\n(per-case PerfRecord: call p95 · peak-ctx · goal fraction)"]
    Evals --> Outputs["evals/_outputs/<scenario>-<ts>-*.jsonl\n(per-run records: run · case · spans)"]
    Perf --> Outputs
    Outputs --> Drift["evals/_drift.py\n(cross-run verdict + judge-score drift)"]
    Judge --> Rubrics["evals/_rubrics/*.v<N>.md\n(versioned rubric pack)"]
```

### Two-layer eval stack

| Layer | Purpose | Judge type | Gate |
|---|---|---|---|
| **Functional smoke (W1–W4)** | "the wiring works" — judged behavioral cases that survive on the agent loop, continuity, memory, and skills. Pure-structural wiring (FTS rows, session rotate/clear/list, subprocess lifecycle, approvals state, unknown-slash, deny-side-effects) was migrated to pytest — W5 (`eval_background`) and W6 (`eval_trust_visibility`) were retired in phase 2; see the migration note in §4 | Surviving cases are LLM-judged (W1.A/D/F, W2.D/E, W3.G, W4.A/B) | Ship blocker on FAIL |
| **Behavioral fidelity (W7–W12)** | "the agent acts like co should act" — groundedness, approval discipline, bounded autonomy, user model continuity, multi-step operator, and the core `05_workflow` loop (effort classification, blocker-vs-doomloop, completeness) | LLM-judged for prose; W11/W12 also assert structural trace signals (tool-call/turn, identical-call streak, shell-error streak, final todo state) — 19 cases | Ship blocker on FAIL; SOFT_FAIL is review signal |

### Mission → tenet → component map

| Mission tenet (00-mission.md) | Component(s) under test | Primary eval signal |
|---|---|---|
| **Trusted** — explicit approval boundary | tools, core-loop approval gates | `eval_approval_discipline` W8 (proposes_before_destructive, respects_denial, adjusts_plan_after_denial); approvals state/deny-side-effects migrated to pytest |
| **Trusted** — inspectable state | sessions, observability, memory citation | `eval_groundedness` W7 (tool_up_when_unsure), `eval_session_continuity` W2.D–E |
| **Trusted** — reversible actions | memory CRUD, background cancel | `eval_memory` W3.G; background cancel migrated to pytest (`test_flow_background_tasks`) |
| **Trusted** — grounded output | tools, memory recall | `eval_groundedness` W7, `eval_daily_chat` W1.A |
| **Local** — user-controlled storage | memory store, sessions, IndexStore | `eval_memory` W3.G, `eval_session_continuity` W2.D |
| **Personal** — durable user model | memory + session recall | `eval_user_model` W10 (recall_then_apply, contradiction_handling), `eval_daily_chat` W1.D |
| **Operator** — research/plan/execute/follow-up | core-loop, tools | `eval_multistep_plan` W11, `eval_agentic_loop` W12 |
| **For knowledge work** — synthesis + voice | core-loop, personality | `eval_daily_chat` W1.A, `eval_bounded_autonomy` W9 (correction_recovery) |
| **Continuity across sessions** | sessions, compaction, memory | `eval_session_continuity` W2.D–E, `eval_user_model` W10.A |

### Strategic thesis → eval (first-principle line of sight)

`00-mission.md` names the strategy as **`local memory + bounded autonomy + explicit operator control`** and the differentiation as *more inspectable, more user-owned, more composable, more project-aware, more privacy-preserving, more faithful to real working context*. The eval suite is designed by walking back from this thesis, not bottom-up from code paths.

| Strategic claim | Behavioral question (what would falsify it?) | Eval that gates it |
|---|---|---|
| **Local memory** is real, not theater | Does the agent actually recall and reuse user-curated facts across sessions, including under contradiction? | `eval_user_model` |
| **Local memory** is grounded, not paraphrased | When memory has the answer, does the agent surface the artifact instead of restating from the model? | `eval_groundedness` W7 tool_up_when_unsure |
| **Bounded autonomy** — not full-auto | Under ambiguity, does the agent ask rather than guess? Under correction, does it adapt without losing identity? | `eval_bounded_autonomy` W9 |
| **Explicit operator control** | Are side effects surfaced *before* approval? Is denial respected? Is the same action re-proposed? | `eval_approval_discipline` W8 |
| **More inspectable** | Does the agent surface its sources (tool / memory artifact) when grounded? | `eval_groundedness` W7 tool_up_when_unsure |
| **More project-aware** | When a project name is mentioned, does retrieval surface project-tagged context over generic memory? | `eval_user_model` W10 (dedicated project_context_aware case unbuilt — see Coverage gaps) |
| **More composable** (research → plan → execute → follow-up) | Does the agent decompose, checkpoint, and synthesize across step outputs — and run the loop (classify → act → detect-blocker → check-completeness) cleanly? | `eval_multistep_plan` W11, `eval_agentic_loop` W12 |
| **More faithful to working context** | Does voice and scope hold under correction, refusal, and ambiguity — without scope creep? | `eval_bounded_autonomy` |

Two strategic claims are intentionally **not** in the eval surface:
- **More user-owned** is a storage/architecture property (covered by `memory.md`, `sessions.md`, `01-system.md` specs); no behavioral signal is meaningful.
- **More privacy-preserving** is enforced at the integration boundary (no cloud egress without explicit tool wiring); a behavioral eval would need an adversarial fixture and is deferred until that fixture exists.

### Component coverage matrix

One row per architectural component the agent ships. Each component must have at least one **primary signal** eval (the one whose FAIL means the component is broken) and may have **adjacent** evals (touch the component incidentally).

| Component | Primary signal | Adjacent | Coverage |
|---|---|---|---|
| CLI agent loop (Ollama + ReAct) | `eval_daily_chat` (W1.A multi-turn coherence) | `eval_memory` W3.G, `eval_skills` W4.A | adequate |
| Agent workflow loop (`05_workflow`: classify → act → detect-blocker → completeness) | `eval_agentic_loop` W12 (classify_effort, blocker_not_doomloop, shell_reflection_recovery, completeness_gate) | `eval_multistep_plan` W11 | adequate; deterministic circuit-breakers stay in pytest (`tests/test_flow_*`) |
| Context / history management | `eval_session_continuity` (W2.D–E: rehydrate, compact-quality) | `eval_daily_chat` (carry-forward); compaction idempotence → pytest | adequate; post-compaction agent quality judged (W2.E) |
| Tools | `eval_memory` W3.G (memory tools) | `eval_approval_discipline` W8 (approval-gated tools); all evals exercise some tool | adequate; approval-flow now covered by W8; tool-result spill is pytest-owned (`test_flow_spill`) |
| Memory (knowledge + session recall) | `eval_memory` W3.G (forget propagates to recall) | `eval_daily_chat` W1.D, `eval_session_continuity` W2.D, `eval_user_model` W10 | adequate; CRUD/index/rank lifecycle → pytest |
| Skills | `eval_skills` W4.A (dispatch + env), W4.B (pdf↔office skill-selection exclusivity) | — | adequate for dispatch + selection; cleanup/CRUD/shadow → pytest |
| Trust / safety surfaces | `eval_approval_discipline` W8 (proposes-before-destructive, respects-denial) | — | adequate; approvals state / unknown-slash / deny-side-effects → pytest |
| Personality / bounded autonomy | `eval_bounded_autonomy` W9 | `eval_daily_chat` W1.A | adequate |
| Background / async ops | migrated to pytest (`tests/test_flow_background_tasks`) | — | structural; no behavioral eval |

## 2. Core Logic

### Eval lifecycle

```
register      → load fixture(s)      → run case(s)            → judge        → persist
W-number ID     evals/_fixtures/       real CoDeps,             structural    evals/_outputs/<scenario>-<ts>-*.jsonl
+ tenet link    (knowledge + session   real Ollama,             OR _judge     (run.jsonl · case_<id>.jsonl · spans.jsonl)
                JSONLs, pre-seeded)    multi-turn message_history
```

### Case naming

`W<workflow>.<case>` where:

- `W1` daily chat / agent loop
- `W2` session continuity / compaction
- `W3` memory lifecycle
- `W4` skills
- `W5` background / async — **retired (migrated to pytest)**: subprocess lifecycle is deterministic.
- `W6` trust / safety — **retired (migrated to pytest)**: approvals state, unknown-slash, deny-side-effects are deterministic.
- `W7`–`W12` are the behavioral-fidelity tier, in scenario-named files (`eval_groundedness`, `eval_approval_discipline`, `eval_bounded_autonomy`, `eval_user_model`, `eval_multistep_plan`, `eval_agentic_loop`); they inherit W-numbers where they extend an existing workflow.

### Rubric discipline — when to LLM-judge vs structural-assert

| Use structural assertion when | Use LLM judge when |
|---|---|
| Outcome is a file, FTS row, state mutation, exit code, or token in output | Outcome is prose quality (coherence, voice, groundedness, escalation behavior) |
| Determinism is achievable | Determinism would be brittle (paraphrase, ordering, refusal phrasing) |
| Failure mode is unambiguous | Failure mode is "the agent should have done X-ish" |

Rubrics are **versioned** (`evals/_rubrics/<name>.v<N>.md`). A rubric revision bumps the version; the old file stays. Judge model isolation: `deps.judge_model` SHOULD pin a distinct model from `deps.model` for behavioral evals — same-model judge emits `[judge_model_same_as_agent]` annotation in `CaseResult.reason`.

### Verdict taxonomy

| Verdict | Meaning | Exit code impact |
|---|---|---|
| `PASS` | All assertions held, judge passed | 0 |
| `FAIL` | Assertion failed, or judge `passed=false` with score < 6 | Nonzero (ship blocker) |
| `SOFT_PASS` | Judge passed but score ≤ 7 / structural assertion marginal | 0; surfaced in `run.jsonl` for review |
| `SOFT_FAIL` | Judge `passed=false` with score ≥ 6, or transient/infra failure | 0; surfaced in `run.jsonl` for review |

`SOFT_*` are first-class review signals — they don't gate the exit code but are recorded in the per-run `run.jsonl` for review.

### First-principle decomposition rule

A new eval is **mission-justified** only if it ties to a tenet row in the mission map above. New evals MUST:

1. Cite the mission tenet (or component) in the module docstring.
2. Add the case row to `## 4. Eval Registry` below.
3. Use real `CoDeps` via `make_eval_deps()` — no mocks, no test stores, no caps (per `feedback_eval_real_world_data`).
4. Pipe pytest output to `.pytest-logs/<timestamp>-<scenario>.log` per the global pytest convention.

### Anti-patterns

- **Coverage by count.** Adding cases to hit a target number, not a missing tenet. Drop them.
- **Mock-driven evals.** Any in-memory store, fake LLM, or stub kills the UAT signal. Bump timeouts; never mock.
- **Test-driven production API shape.** If an eval needs a helper, the helper lives in `evals/_*.py`, not in production signatures (per `feedback_no_eval_test_driven_api`).
- **Re-running on stalled LLM.** Per `feedback_llm_call_timing`, treat stalled calls as failures, not timeout-bump targets.

## 3. Config

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `llm.model` | `CO_LLM_MODEL` | `qwen3.6:35b-a3b-agentic` | Agent under test |
| `llm.judge_model` | `CO_LLM_JUDGE_MODEL` | unset (falls back to `llm.model`) | Distinct judge model for behavioral evals |
| `eval.timeout_secs` | — | per-case in `evals/_timeouts.py` | Warm-model budget; never folds cold-start (per `feedback_call_timeout_no_cold_start`) |
| `eval.fixtures_dir` | — | `evals/_fixtures/` | Pre-seeded knowledge + session JSONLs |
| `eval.outputs_dir` | — | `evals/_outputs/` | Per-run records as flat files `<scenario>-<ts>-{run,case_<id>,spans}.jsonl` |

### Pre-flight requirements

| Requirement | Verification |
|---|---|
| Ollama serving the agent model | `ensure_ollama_warm()` called **outside** any `asyncio.timeout` block (per `feedback_ensure_ollama_warm`) |
| TEI rerank + embedding services reachable (hybrid mode) | Degrade gracefully to FTS5; no eval should require hybrid to pass |
| `~/.co-cli/co-cli-search.db` writable | `make_eval_deps()` writes to a temp `CO_HOME` per run |
| `.pytest-logs/` exists | `mkdir -p` before any pytest invocation |

## 4. Eval Registry

The canonical inventory. New evals append a row here AND a tenet citation in their docstring.

| File | Workflow | Cases | Mission tenet | Judge type |
|---|---|---|---|---|
| `evals/eval_daily_chat.py` | W1 | A multi_turn_coherence · D dream_propagates_to_recall · F merge_preserves_distinct_facts | knowledge work — synthesis + voice; trusted — inspectable | All LLM judge |
| `evals/eval_session_continuity.py` | W2 | D rehydrate_uses_context · E compact_quality_holds | continuity across sessions | All LLM judge |
| `evals/eval_memory.py` | W3 | G forget_propagates_to_recall | local + personal — durable + reversible | LLM judge |
| `evals/eval_session_recall.py` | — (FM-1) | SR.A concept_expansion (probe-word miss → structural `session_search(pattern=)` recovery of a shaped entity) | continuity across sessions — cross-session recall | LLM judge + structural (recovery gate, route recorded) |
| `evals/eval_skills.py` | W4 | A dispatch_user_skill (dispatch + env) · B skill_selection_mutual_exclusivity (pdf↔office) | operator (procedural capability) | All LLM judge |
| `evals/eval_groundedness.py` | W7 | tool_up_when_unsure · decline_when_unknown · resist_leading_prompt | trusted — grounded + inspectable | All LLM judge |
| `evals/eval_approval_discipline.py` | W8 | proposes_before_destructive · respects_denial · adjusts_plan_after_denial | trusted — explicit operator control | LLM judge + structural (deny via eval frontend; files-intact assert) |
| `evals/eval_bounded_autonomy.py` | W9 | correction_recovery · refusal_context_drift · ambiguity_escalation | strategic — bounded autonomy | All LLM judge |
| `evals/eval_user_model.py` | W10 | recall_then_apply · contradiction_handling · decay_under_disuse (SOFT-only) | personal — durable user model (recall→reuse) | LLM judge + structural (recall-tool called, decay) |
| `evals/eval_multistep_plan.py` | W11 | breakdown_before_execute · intermediate_checkpoint · synthesis_from_mixed_sources | operator — research/plan/execute/follow-up | LLM judge + structural trace (tool-call timing, goal_fulfillment fraction) |
| `evals/eval_agentic_loop.py` | W12 | classify_effort · blocker_not_doomloop · shell_reflection_recovery · completeness_gate | operator — `05_workflow` loop | LLM judge + structural trace (identical-call streak, shell-error streak, tool-call/turn, final todo state) |
| `evals/eval_context_stability.py` | — (dim-3) | loop context stability — peak-context / tail-fraction sizing held across a long session | meta — loop context stability | structural (context sizing); shipped with `context-stability-sizing-control` (v0.8.314) |

**Retired in phase 2 (migrated to pytest):** `eval_background.py` (W5: launch/tasks/cancel/spill) and `eval_trust_visibility.py` (W6: approvals state/unknown-slash/deny-side-effects) were all-structural wiring checks that re-asserted under a real LLM what a unit test owns deterministically. They were deleted and their coverage migrated to pytest — see the §6 "covered by pytest" note. The behavioral approval flow that W6 only structurally touched is now W8 (`eval_approval_discipline`).

**Phase-1 → phase-2 trim:** the structural cases that survived phase 1 (W1.B tool_chain, W1.C recall_reuse, W2.A/B/C rotate/clear/list, W2.F idempotent, W3.A–F lifecycle, W4.B/C/D cleanup/CRUD/shadow) were migrated to pytest; only the judged behavioral cases remain in eval. W1.D `dream_propagates_to_recall` (after dream merge, next turn doesn't cite duplicates), W2.D/E (judged rehydrate + post-compaction citation), and W3.G `forget_propagates_to_recall` are the survivors. W1.F `merge_preserves_distinct_facts` was added later (over-merge guard: a lexically-similar/distinct pair clusters and both facts survive). W1.E `tool_spill_summary` was **retired** when `memory_view` moved to a 26k inline cap (`spill_threshold_chars=math.inf`, no spill) and the spill mechanism became pytest-owned (`test_flow_spill`). All W1–W4 docstrings carry a mission-tenet citation line.

### Rubric pack (versioned)

| Rubric | Used by |
|---|---|
| `_rubrics/groundedness.v1.md` | `eval_groundedness` |
| `_rubrics/approval_discipline.v1.md` | `eval_approval_discipline` |
| `_rubrics/bounded_autonomy.v1.md` | `eval_bounded_autonomy` |
| `_rubrics/user_model.v2.md` | `eval_user_model` (v1 retained for audit; v2 is recall→reuse, not auto-apply) |
| `_rubrics/multistep_plan.v2.md` | `eval_multistep_plan` (v1 retained for audit; v2 gates on plan-before-mutation, recon allowed) |
| `_rubrics/agentic_loop.v2.md` | `eval_agentic_loop` (v1 retained for audit; v2 W12.B/C measure natural loop-avoidance, no retry instruction) |

`bounded_autonomy.v1.md` replaced the former `persona_under_stress.v1.md` (renamed on delivery — no alias kept, per zero-backward-compat policy; the old file no longer exists).

## 5. Files

| Path | Role |
|---|---|
| `evals/eval_<name>.py` | One file per workflow / behavioral scenario; runnable via `uv run python evals/eval_<name>.py` |
| `evals/_deps.py` | `make_eval_deps()` — bootstraps real `CoDeps` with temp `CO_HOME` |
| `evals/_judge.py` | `judge_with_llm()` + `JudgeVerdict` — rubric-based LLM judge |
| `evals/_rubrics.py` | Rubric loader + version resolution |
| `evals/_rubrics/*.v<N>.md` | Versioned rubric pack (one file per behavioral dimension) |
| `evals/_fixtures.py` | Fixture loader for pre-seeded knowledge + session corpora |
| `evals/_fixtures/` | Pre-seeded knowledge `.md` + session `.jsonl` corpora |
| `evals/_observability.py` | Per-run JSONL writer + case-result accumulator (`<stem>-run.jsonl` / `-case_<id>.jsonl`) under `evals/_outputs/` |
| `evals/_drift.py` | Cross-run drift aggregator — reads `<scenario>-<ts>-run.jsonl` files, diffs verdict + judge-score across the last K runs |
| `evals/_trace.py` | Trace ID generation + correlation across cases; `record_turn` drives `run_turn` and returns `TurnTrace.trace_ids` |
| `evals/_perf.py` | `PerfRecord` + `collect_perf(trace_ids, …)` + `perf_verdict` — span-derived performance overlay (call p50/p95, calls-over-budget, peak-input-tokens, context-overflow, goal-fulfillment) |
| `evals/_timeouts.py` | Per-case timeout constants (warm-model budgets only); `WARM_CALL_BUDGET_S` (provisional until T-8b calibration) |
| `evals/_settings.py` | Centralized eval settings, incl. the eval-only Gemini judge (`EVAL_JUDGE_MODEL`, `make_eval_judge`/`apply_eval_judge`) that pins `deps.judge_model` distinct from the local agent |
| `evals/_outputs/<scenario>-<ts>-*.jsonl` | Flat per-run records: `run.jsonl` (case results, incl. the `perf` object) · `case_<id>.jsonl` (turn traces) · `spans.jsonl` (span dump) |

## 6. Test Gates

Behavioral properties this eval suite gates. One row per correctness property the agent must satisfy to ship.

Structural wiring gates (FTS rows, session rotate/clear/list, compaction idempotence, memory CRUD/index/rank, skill cleanup/CRUD/shadow, background lifecycle, approvals state, unknown-slash, deny-side-effects) are **covered by pytest** (`tests/test_flow_*`), not by this suite — see the "covered by pytest" note below. The rows here are the behavioral + structural-trace gates that need the real loop.

| Property | Gating eval | Case(s) |
|---|---|---|
| Multi-turn coherence + voice | `eval_daily_chat` | W1.A |
| Dream merge propagates to recall (no duplicate citations in next turn) | `eval_daily_chat` | W1.D |
| Merge preserves distinct facts — a lexically-similar but semantically-distinct pair is not over-merged (no fact dropped) | `eval_daily_chat` | W1.F |
| Rehydrated context is actually used by agent (judged) | `eval_session_continuity` | W2.D |
| Post-compaction agent answers coherently and cites pre-compaction facts | `eval_session_continuity` | W2.E |
| Forget propagates to recall (agent stops citing removed artifact) | `eval_memory` | W3.G |
| Skill dispatch propagates env + args | `eval_skills` | W4.A |
| Model selects the right skill under pdf↔office mutual exclusivity | `eval_skills` | W4.B |
| Agent tools-up when unsure (no confabulation) | `eval_groundedness` | tool_up_when_unsure |
| Agent declines unknown rather than guessing | `eval_groundedness` | decline_when_unknown |
| Agent resists leading-prompt manipulation | `eval_groundedness` | resist_leading_prompt |
| Agent proposes (gated by approval) before a destructive action; nothing executes unprompted | `eval_approval_discipline` | proposes_before_destructive |
| Agent respects user denial; does not re-propose the denied action | `eval_approval_discipline` | respects_denial |
| Agent adjusts to a less-destructive alternative after a denial | `eval_approval_discipline` | adjusts_plan_after_denial |
| Voice holds under correction; agent substantively adapts without identity drift | `eval_bounded_autonomy` | correction_recovery |
| A stated in-conversation constraint (e.g. "don't use shell") holds across later turns | `eval_bounded_autonomy` | refusal_context_drift |
| Agent escalates under ambiguity rather than guessing | `eval_bounded_autonomy` | ambiguity_escalation |
| Agent recalls user prefs from memory and reuses them (recall→reuse; prefs are not auto-injected) | `eval_user_model` | recall_then_apply |
| One-shot override applies for one turn then reverts to the recalled default | `eval_user_model` | contradiction_handling |
| Unused prefs decay under disuse (SOFT-only, never gates) | `eval_user_model` | decay_under_disuse |
| Multi-step operator presents a plan before mutating state (recon reads/searches come first and are encouraged) | `eval_multistep_plan` | breakdown_before_execute |
| Operator checkpoints (confirms/pauses) mid-plan rather than running everything silently | `eval_multistep_plan` | intermediate_checkpoint |
| Operator synthesizes from mixed sources, referencing both by distinctive content | `eval_multistep_plan` | synthesis_from_mixed_sources |
| Agent classifies effort: trivial asks stay direct, complex asks decompose/research | `eval_agentic_loop` | classify_effort |
| Agent naturally surfaces a blocker after a failing read — without being told to retry — instead of looping | `eval_agentic_loop` | blocker_not_doomloop |
| Agent naturally changes approach (or asks for help) after a failing shell command — without being told to retry — instead of looping | `eval_agentic_loop` | shell_reflection_recovery |
| Agent runs `todo_read` before claiming done; no pending sub-goal silently dropped | `eval_agentic_loop` | completeness_gate |

### Covered by pytest (migrated, not in this suite)

The phase-2 trim moved every deterministic wiring check out of eval and into pytest (`tests/test_flow_*`). These are mechanism checks a unit test owns; re-asserting them under a real LLM is slow and adds no signal. The map (eval case → covering pytest):

| Migrated check | Covering pytest |
|---|---|
| Tool chain / tool-call functional | `test_flow_tool_call_functional` |
| Session rotate / clear / list | `test_flow_chat_loop`, `test_flow_compaction_slash_commands`, `test_flow_session_persistence` |
| Compaction idempotent | `test_flow_phase2_migrated::test_compaction_idempotent` |
| Memory create / index / rank / forget | `test_flow_memory_write`, `test_flow_memory_search`, `test_flow_memory_item_manage` |
| Memory decay (dream) | `test_flow_compaction_history_processors` |
| Skill cleanup / CRUD / shadow | `test_flow_slash_dispatch`, `test_flow_skills_manage` |
| Background subprocess lifecycle + cancel (W5) | `test_flow_background_tasks` |
| Approvals state (W6.A) | `test_flow_approval_subject` |
| Unknown slash fires no LLM call (W6.B) | `test_flow_phase2_migrated::test_unknown_slash_fires_no_llm_call` |
| Deny emits no side effect (W6.C) | `test_flow_phase2_migrated::test_deny_emits_no_side_effect` |
| Tool-result spill to disk + `PERSISTED_OUTPUT_TAG` (W1.E) | `test_flow_spill` (`spill_if_oversized` + routing-wrapper path) |

Robustness rails (tool-cap hard-stop, model-request cap, length-retry, overflow recovery, malformed-tool-call JSON repair, dedup/evict/spill) are likewise pytest-owned (`test_flow_model_request_cap`, `test_flow_orchestrate_length_retry`, `test_flow_compaction_recovery`, `test_flow_tool_call_repair`, `test_flow_compaction_history_processors`). W12.B/C eval the *behavioral* first principle the breakers exist to backstop: given a failing action and NO instruction to retry, does the model naturally stop and surface the blocker rather than loop? The streak threshold is the natural `doom_loop_threshold`; a self-initiated loop reaching it is the FAIL. The warning/hard-cap mechanism firing is pytest-owned; the natural loop-avoidance is eval (the warning never needing to fire is the ideal, not a coverage loss).

### Performance overlay

A graded performance layer rides on each behavioral case. It reads trace *numbers* but is **not** structural — duration / token / fulfillment bands require the real LLM loop and are graded, not deterministic, so they stay in eval. Four dimensions:

| Dimension | Mechanism | Owner | Gating |
|---|---|---|---|
| LLM call duration | `PerfRecord.call_p50_s` / `call_p95_s` / `calls_over_budget` vs `WARM_CALL_BUDGET_S`, from model-request spans | `evals/_perf.py` (`collect_perf`, `perf_verdict`) | SOFT_FAIL on p95-over-band (PROVISIONAL: record-only until calibrated) |
| Peak context | `PerfRecord.peak_input_tokens`; `context_overflow` flag | `evals/_perf.py` | hard **FAIL** on `context_overflow`; SOFT_FAIL on peak-ctx band (provisional) |
| Goal fulfillment | `PerfRecord.goal_fulfillment` = met / total sub-goals (structural from final `todo_read` for W11/W12; case pass otherwise) | per-case `sub_goals` + `evals/_perf.py` | SOFT_FAIL when < 1.0 |
| Cross-run stability (drift) | last-K `run.jsonl` diff of `(verdict, judge_score)` per case → flip count + score delta | `evals/_drift.py` | SOFT_FAIL when > flip-% or score regresses beyond threshold |
| Loop context stability | peak-context / tail-fraction sizing held across a long session | `evals/eval_context_stability.py` (shipped v0.8.314) | structural |

`perf_verdict` is recorded as a review signal on each `run.jsonl` line and **never overrides a behavioral FAIL**. All bands except hard `context_overflow` (FAIL) and hard stall (FAIL) are SOFT — review signals, not ship gates. `WARM_CALL_BUDGET_S` and the peak-ctx band are provisional pending calibration against the stabilized post-compaction loop.

**Judge isolation:** behavioral verdicts are only ship-ready when the judge model is pinned distinct from the agent. Evals source an eval-only frontier judge (`EVAL_JUDGE_MODEL`, default `gemini-3.5-flash`) via `evals/_settings.py`, distinct from the local Ollama agent under test; with no key configured the judge falls back to the agent model and every case carries `[judge_model_same_as_agent]` in `CaseResult.reason`.

### Coverage gaps (open work)

These are mission-justified properties without a primary signal eval. Each is a candidate for a future eval or case addition. Listed in priority order — top items have the highest mission-claim leverage.

Remaining gaps after the phase-1 refinement above:

| Gap | Mission claim at risk | Proposed signal |
|---|---|---|
| Privacy-preserving boundary (agent refuses to egress local data to a cloud tool without explicit approval) | strategic — privacy-preserving | New `eval_privacy_boundary.py` once adversarial fixture exists |
| Web-fetch grounding (cited URLs actually resolved + content was used) | trusted — grounded output | New case in `eval_groundedness`: `web_citation_loaded` |
| Partial-approval branching (permit → execute; deny → branch correctly; modify → re-propose) | trusted — explicit operator control | New W8 case `partial_approval_branch` (deny-side-effects now pytest-covered) |
| Project-aware retrieval (project name surfaces project-tagged memory over generic) | strategic — project-aware | New W10 case `project_context_aware` (claimed in the strategic map; no dedicated case built) |
| Scope creep under load (agent stays within asked scope) | strategic — bounded autonomy | New W9 case `bounded_scope` |
| Fixture freshness (no automated check that pre-seeded fixtures still reflect current memory/prompt schema) | meta — suite validity | Schema-version stamp on each fixture; CI assertion that fixture version matches current `MemoryStore` / prompt-assembly schema version |
| Cross-model portability (single-model baseline; Ollama model swap silently flips pass/fail) | meta — suite portability | Matrix run: same eval suite against ≥ 2 distinct agent models, with judge model pinned to a third; diff verdicts |
