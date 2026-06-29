# Phase 5.5 — Named-agent selector for `delegate` (eval-gated persona-mode contract)

**Parent milestone:** `2026-06-24-234633-loop-decoupling-milestone.md` (post-3.6 delegation enhancement). **Built on:** parent-milestone **Phase 5** (shipped v0.8.506) — the owned loop is now the sole agent turn, so the unified `run_standalone_owned` driver this plan extends already exists. **Split from:** the phase 3.7 plan (`2026-06-27-172529-loop-decoupling-phase3-7.md`), which shipped **R1** (the prose delegation guidance — D6 mode-setting in the `delegate` description). **Design input:** `docs/reference/RESEARCH-delegation-interface-peer-survey.md` §3, §6 R2, §7 Q1–Q4.

## Context

The delegation-interface peer survey found the **named-agent / `subagent_type` selector** is the single most convergent schema element co lacks: 4/5 peers have one (codex `agent_type`, opencode/claude-code `subagent_type`, openclaw `agentId`); co and hermes are the only two delegating to one anonymous generalist (`RESEARCH-delegation-interface-peer-survey.md:100,119`).

**Three facts (source-grounded this planning pass) shape the design:**

1. **The selector is the structural twin of prose mode-setting, and co already shipped the prose half.** The survey's load-bearing finding (`:121`): "peers that let the model pick a role lean less on prose telling the agent what mode to be in, because **the role IS the mode**" (codex `explorer` vs `worker`). co's **R1** (phase 3.7) already carries mode in the `delegate` description — *"State whether the sub-agent should just research or also make changes, and how to verify"* (`co_cli/tools/system/delegate.py:36-37`). So this plan is **not** adding a new capability; it asks whether **promoting mode from free-form prose to a structured persona-mode field** helps co's small model. The baseline to beat is **R1 prose, not nothing**.

2. **The peers' types carry tool surfaces + locked models; co's will not.** codex roles lock model/effort per role (`role.rs:310-368`); claude-code enumerates each agent *with its tool list* (`prompt.ts:43`). co adopts only the **schema keyword**, with a deliberately **thinner semantics**: a role narrows **persona/mode only**, never the tool surface (the 3.6 anonymous-full-agent principle holds — `:188`). co matches converged practice on shape, diverges on substance by design.

3. **The crux ("what is a co role?") is settled to a persona-mode contract — NOT free-text, skills, souls, or a generic registry.** The option space (survey §7 Q1; this plan's prior parked draft) was *none / free-text / skills / souls / new registry*. Resolved with the user 2026-06-29 (see Decisions):
   - **Free-text hint — rejected.** No peer offers unconstrained free-text (even openclaw's free-string rides a discovery tool over a *defined* set, `:127`), so it is not convergent; and it is **largely redundant with R1** — `task` is already free-form, so a second free-text mode field duplicates what R1 prose carries. Dominated on both convergence and additivity.
   - **Skills as roles — rejected.** A co skill is partly a *tool-routing* instruction; spawning "as a skill" leaks tool-shaping into what must be persona-only (violates surface-unchanged).
   - **Souls as roles — rejected.** Souls (`finch/jeff/tars`) are built-in doctrine (never queryable by design) and are *identity*, not *function* — "hand this research to tars" is semantically empty.
   - **Generic `researcher/editor/verifier` registry — rejected as the *content*.** That bland coding-shop set is exactly co's least-distinctive surface (`feedback_skill_curation_knowledge_work_positioning`). The trap is the *content* of the set, not the *existence* of a set.
   - **Adopted: a small, closed, co-native persona-mode contract** — instructions-only, surface-unchanged, eval-gated against R1 prose. The actual mode set (distinctively knowledge-work, not a borrowed coding menu) is authored as the first sub-step of TASK-1 and validated by the eval.

## Problem & Outcome

**Problem:** co delegates every subtask to one anonymous full-surface generalist, carrying delegation *mode* in ad-hoc R1 prose the small orchestrator authors itself. 4/5 peers instead let the model **pick a named mode** whose brief is pre-authored. Whether promoting mode to a structured persona-mode field helps co's small model — net of the cost of a second field it must keep consistent with `task` — is unproven for co's tier.

**Outcome:** an **eval-backed, user-settled decision** on whether co adopts a persona-mode selector. If **go**: `delegate(task, subagent_type=None)` where `subagent_type` names a small co-native persona-mode whose tuned brief is injected into the delegated agent's instructions (surface unchanged by role; zero-regression default = today's anonymous generalist). If **no-go**: only R1 stands, and co stays in the anonymous/hermes camp **deliberately**.

> **→ RESOLVED: GO** (TASK-1 eval, 2026-06-29). The tuned persona-mode brief **ties-or-beats** the R1-prose baseline on `qwen3.6:35b-a3b` (B never loses across 2 scenarios × both judge orders), the pick is **3/3 correct and stable**, no `task`/mode semantic cost surfaced, and the lean menu costs ≈76 prefill tokens. Both silent failure modes (small model ignores/mis-picks the field; wrong content) were checked and did not materialize. See **TASK-1 Delivery** below and the survey R2 DECISION block. TASK-2 (the gated impl) is unblocked.

**Failure cost:** building the contract on frontier-convergence alone, without the eval, ships a `subagent_type` field the small model ignores, mis-picks, or contradicts against `task` — adding always-on prefill cost and selection instability for no outcome gain. Choosing the wrong *content* (a generic coding-role set) ships co's least-distinctive surface. Both are silent: the feature looks adopted while degrading or no-op'ing real delegation.

## Scope

**In:**
- **TASK-1 (design + eval, NO production code):** author candidate small co-native persona-mode set(s) + tuned briefs as **eval-local fixtures**; run a real-Ollama A/B (R1-prose baseline vs structured-mode-brief) on seeded knowledge-work scenarios for `qwen3.6:35b-a3b`; measure value (does B beat A?) and the two disqualifiers (semantic cost / `task`-vs-mode consistency, pick stability); settle the surfacing model (lean enumerate-in-description vs deferred discovery) against a prefill-budget measurement; record go/no-go in the survey doc.
- **TASK-2 (impl, GATED on a TASK-1 "go"):** add optional `subagent_type: str | None = None` to `delegate`; a small closed co-native mode→brief table; the lean always-on selection menu in the `delegate` description; on-use injection of the picked mode's full brief into the delegated agent's instructions; surface unchanged by role; zero-regression default.

**Out:**
- **Per-role tool surfaces or locked models** (the peers' richer semantics) — co's 3.6 principle: role narrows persona only, surface stays the full approval-gated visibility model.
- **An extensible / user-defined role registry** — the contract is a small closed built-in set; user-authored roles are a separate post-validation plan if ever wanted.
- **`docs/specs/` edits** — spec sync is `sync-doc` post-delivery, never in `files:`.
- **OQ-3 (D7 home), OQ-4 (`clarify` mid-delegation)** — deferred (see Open Questions).

## Behavioral Constraints

- **Eval before surface change.** No production `subagent_type` lands until TASK-1's A/B shows a structured persona-mode **beats R1 prose** for qwen3.6 *and* clears the disqualifiers. Quality-neutral-but-costly or unstable-pick ⇒ **no-go**, R1 stands. **(Met 2026-06-29: B ties-or-beats A and never loses; picks 3/3 correct + stable; no semantic cost — go.)**
- **Baseline is R1 prose, not nothing (the de-confounding constraint).** Arm A is today's shipped R1 mode-in-`task` behavior, driven through the real owned path — not a strawman bare task. The eval measures the *delta of structured-over-prose*, the only delta that justifies the field.
- **Surface unchanged by role (settled feasibility).** A mode changes only `spec.instructions`; the tool surface stays `SurfaceModeEnum.VISIBILITY_MODEL` (`co_cli/agent/spec.py:21-36,74` decouples surface from instructions — the delegated agent still self-loads any tool via `tool_view`). No feasibility conflict; do not relitigate.
- **Two-tier surface (prefill discipline — `delegate` is ALWAYS-visibility).** The selection menu in the `delegate` description is **lean** — one when-to-use line per mode (claude-code `"- {mode}: {when-to-use}"`, `prompt.ts:43`) — paid on every turn. The **rich** per-mode persona brief is injected into the delegated agent's `instructions` only on the turn that mode is used. Never inline the full briefs into the always-on description (the codex `role.rs` "Available roles" shape would blow co's prefill budget — `feedback_defer_tradeoff_context_over_latency`).
- **Small + closed + co-native.** The set is a handful of distinctively-knowledge-work modes, not a borrowed `researcher/editor/verifier` coding menu and not an open/extensible registry. Smallness is what makes the lean inline menu affordable; if the set ever grew, surfacing would be forced to a deferred `agents_list`-style discovery tool.
- **Zero-regression default.** `subagent_type` is optional; omitting it = today's anonymous generalist byte-for-byte. `task` stays required and free-form (the universal converged core, survey `:119`).
- **Scaffolding tenet holds (Phase 5 baseline).** The selector reuses `TaskAgentSpec` + `run_standalone_owned`; it threads a *workflow* value (the picked mode's brief) into the existing per-step instruction builder — never a delegate-specific construction path or a parameterized mega-loop.

## High-Level Design

### Why eval-first, and exactly what it measures

R1 already lets the small orchestrator express mode in prose. The unproven claim is that **a cheap classification pick + a human-tuned brief** beats **the weak orchestrator authoring mode-prose ad-hoc** — i.e., the value is *offloading brief-authorship off the small model onto a tuned table*. TASK-1 tests precisely that delta:

- **Arm A (baseline):** `delegate(task)` with R1 prose mode-setting — today's shipped behavior, real owned-path drive. **No R1-prose tuning for the baseline (PO-m-2):** Arm A is the small model authoring mode-prose *unaided*, exactly the cost the structured field claims to remove — so a flat result reads as "structured mode is no better," not "R1 was under-prompted."
- **Arm B (treatment):** an eval-local `TaskAgentSpec` whose `instructions` builder injects a picked mode's tuned brief, driven by `run_standalone_owned` directly — **threading `propagate_approvals=True` + the parent frontend (CD-m-3)** to match the production delegate path (`delegation.py:123-130`), or the A/B is not apples-to-apples. (This is the first eval-layer direct `run_standalone_owned` caller — daemons call `run_standalone`.)
- **Headline:** judge-scored delegated-summary quality / task success, B vs A, on seeded knowledge-work scenarios.
- **Disqualifiers (either ⇒ no-go even at flat quality):** (a) **semantic cost** — does a `task`/`subagent_type` mismatch arise or confuse; (b) **pick stability** — does the orchestrator choose a sensible mode reliably, without destabilizing delegation (over-delegating, ignoring the field).
- **Surfacing sub-decision:** measure the prefill cost of the lean inline menu; confirm enumerate-in-description is affordable for the small set, else recommend the deferred discovery tool. (Survey `:130,193`, "pick per budget measurement.")

**Eval discipline (carried from the phase-3.7 critique, CD-m-5):** Arm B is an **eval-local** spec + role-injected instructions builder. It must **not** mutate `DELEGATE_AGENT_SPEC` / `_delegate_agent_instructions` to add an eval seam — convenience lives in the eval layer, never the production signature (`feedback_no_eval_test_driven_api`). Use the centralized `evals/_settings.py` / `_deps.py` / `_timeouts.py` (shared `noreason`/`reasoning` settings, seeded workspace, fail-fast, tail the spans log; RCA any slow LLM call, never widen a timeout without approval).

### TASK-2 impl shape (one concrete approach, on a TASK-1 "go")

Current source (post-Phase-5, re-verified this pass):
- `delegate(ctx, task)` — `co_cli/tools/system/delegate.py:22`.
- `delegate_to_agent(parent_deps, task)` → `run_standalone_owned(DELEGATE_AGENT_SPEC, …)` — `co_cli/agent/delegation.py:97,123`.
- `_delegate_agent_instructions(deps) -> str` — `delegation.py:60`; `DELEGATE_AGENT_SPEC` singleton — `delegation.py:87`.
- `TaskAgentSpec` (frozen dataclass) — `co_cli/agent/spec.py:52-74`; `run_standalone_owned` — `co_cli/agent/loop.py:618`.

Concrete thread-through (no new abstraction):
1. `delegate(ctx, task, subagent_type: str | None = None)` — add the param + a lean when-to-use menu of the modes in the docstring (selection surface). Pass `subagent_type` to `delegate_to_agent`.
2. `delegate_to_agent(parent_deps, task, subagent_type=None)` — resolve `subagent_type` against the closed mode→brief table. **Unknown ⇒ fail loud (CD-M-1, settled C1):** return a delegation-refused string naming the valid modes (mirroring the depth-cap refusal `delegation.py:115-119` and the FLAT_EXACT unknown-name precedent `spec.py:56-59`), never a silent fall-back to anonymous — a silent fallback would mask the very pick-instability disqualifier TASK-1 measures, and the model-authored field has no user channel to recover from a silent miss. On a valid pick, build a **per-call spec** via `dataclasses.replace(DELEGATE_AGENT_SPEC, instructions=lambda deps: _delegate_agent_instructions(deps, mode_brief))`. `None` ⇒ unchanged `DELEGATE_AGENT_SPEC` (zero-regression).
3. `_delegate_agent_instructions(deps, mode_brief: str | None = None)` — when a brief is given, compose it into the base brief (persona surface, on-use); the deferred-tool stub logic is untouched. The closure over `mode_brief` is stable across steps (mode doesn't change mid-delegation), so per-step recomputation stays correct.
4. The mode→brief table + the lean menu strings live in one small module owned by `delegation.py` (the delegation domain), sourced once.

### Scaffolding-tenet check

`run_standalone_owned` and the per-step `_delegate_agent_instructions` are unchanged in structure; the mode is a value threaded into the existing builder via a per-call closure. No new construction path, no parameterized loop — the Phase-5 tenet baseline holds.

## Tasks

### TASK-1 — Author candidate co-native mode set + run the validating A/B eval (NO production code) — ✓ DONE (GO)
- **files:** `docs/reference/RESEARCH-delegation-interface-peer-survey.md` (record the decision), a new eval under `evals/` (e.g. `evals/eval_delegate_persona_mode.py`)
- Author one (or a small number of) candidate **small, closed, co-native** persona-mode set(s) + tuned briefs as **eval-local fixtures** — distinctively knowledge-work, explicitly NOT a generic `researcher/editor/verifier` coding menu. **Illustrative (non-binding) candidate modes as a positive anchor (PO-m-1):** e.g. a *synthesis/distill* mode (gather scattered sources → condensed brief — co's core knowledge work) and a *critique/adversarial-review* mode (stress-test a claim or artifact — the co critique/dream lineage). These are a Gate-1 falsifiability anchor for "is the authored set distinctively co-native?", NOT the final set — the eval still authors and validates it. Build the A/B: **Arm A** = R1 prose baseline (real owned-path `delegate` behavior); **Arm B** = eval-local `TaskAgentSpec` with a mode-brief-injected `instructions` builder, calling `run_standalone_owned` directly (must NOT mutate `DELEGATE_AGENT_SPEC`/`_delegate_agent_instructions`). Run on real Ollama (`qwen3.6:35b-a3b`) over seeded knowledge-work scenarios; record headline value (B vs A) + the two disqualifiers (semantic cost, pick stability) + the surfacing prefill-budget reading.
- **done_when:** the A/B eval runs end-to-end on real Ollama (real `run_standalone_owned` drive, piped to a timestamped log with spans tailed), produces the A-vs-B comparison + disqualifier readings + prefill measurement on the seeded scenarios, and the survey doc records the settled mode set + surfacing choice + the **go/no-go** recommendation for TASK-2.
- **success_signal:** a documented, eval-backed decision on whether and how co adopts the persona-mode selector, with the candidate set and the R1-baseline delta cited.
- **prerequisites:** none (Phase 5 already shipped; the crux is settled).

### TASK-2 — Implement the persona-mode selector (GATED on a TASK-1 "go") — ✓ DONE
- **files:** `co_cli/tools/system/delegate.py` (the `delegate` signature + lean menu), `co_cli/agent/delegation.py` (`_delegate_agent_instructions`, `delegate_to_agent`, the mode→brief table + menu source)
- Implement the concrete thread-through in High-Level Design: optional `subagent_type` on `delegate`; per-call spec via `dataclasses.replace` closing over the picked mode's brief; on-use brief injection into `_delegate_agent_instructions`; the lean when-to-use menu in the `delegate` description; the small closed co-native mode→brief table. `None` reproduces today's anonymous generalist exactly. **Unknown `subagent_type` ⇒ fail loud** (CD-M-1, settled C1): a delegation-refused string naming the valid modes, never a silent anonymous fallback.
- **done_when:** a delegation with a named `subagent_type` runs end-to-end on the owned path (real `run_standalone_owned`) with the mode's brief composed into the delegated agent's instructions **without displacing the deferred-tool stub block** (CD-m-2 — both share the one instructions string) and the **tool surface verified unchanged** (still `VISIBILITY_MODEL`, real approval flags); an **unknown `subagent_type` returns the fail-loud refusal naming the valid modes** (not a silent anonymous run); `subagent_type=None` reproduces today's behavior; the lean menu is present in the `delegate` description and its prefill cost matches the TASK-1 budget reading; repo-wide stale-reference grep across `co_cli/` + `tests/` + `evals/` is clean; the full pytest suite is green.
- **success_signal:** the model can optionally select a named delegated-agent persona-mode; the tool surface is unchanged by mode; omitting it is byte-for-byte today.
- **prerequisites:** TASK-1 returns "go". **(Satisfied 2026-06-29 — see TASK-1 Delivery.)**

## Testing

TASK-1 is the primary gate — a real-Ollama A/B (R1-prose baseline vs structured persona-mode) on seeded knowledge-work scenarios, exercising the real `run_standalone_owned` drive, measuring value + the two disqualifiers + the surfacing prefill cost. No new production code lands unless it returns "go". TASK-2 (gated) is verified by a runtime delegation exercise asserting (a) the mode brief reaches the delegated agent's instructions, (b) the surface is unchanged (`VISIBILITY_MODEL`, real approval flags), (c) `subagent_type=None` reproduces today — plus a repo-wide stale-reference grep and the full suite (the rename/drop discipline, `review.md:19`). Functional-only: assert observable delegation behavior, never the contract's internal shape. All runs pipe to timestamped `.pytest-logs/` logs with the spans log tailed (RCA slow LLM calls; never widen a timeout without approval).

## Open Questions

- **OQ-3 (D7 home) — DEFERRED.** D7 ("verify side-effects") sits in the `delegate` *description* (R1, phase 3.7 — where the orchestrator acts on the summary). Re-raise only if the delegated agent's *own* instructions also need it.
- **OQ-4 (`clarify` mid-delegation) — DEFERRED.** Out of scope unless a delegated subtask must ask the user mid-task; re-raise then.

(OQ-1 "adopt at all?" and OQ-2 "what is a co role?" are **resolved** — see Context fact 3 and Decisions — and no longer open.)

## Next step

Gate 1 cleared and TASK-1 ran: the eval returned **GO** (see TASK-1 Delivery). TASK-2's gate (`prerequisites: TASK-1 returns "go"`) is now **satisfied** — `/orchestrate-dev loop-decoupling-phase5-5` proceeds to TASK-2 (the gated impl: optional `subagent_type`, lean enumerate-in-description menu, on-use brief injection, unknown ⇒ fail-loud, `None` ⇒ byte-for-byte today).

## Decisions

The crux resolution (rows R-*) is the overdesign-avoidance record — the rejected role shapes must survive as history. CD/PO rows are the C1→C2 critique outcome (converged C2: Core Dev + PO both `Blocking: none`).

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| R-free-text | reject | No peer offers unconstrained free-text (even openclaw's free-string rides a discovery tool over a defined set); and a second free-text mode field is largely redundant with R1, since `task` is already free-form. Dominated on convergence + additivity. | Crux resolved to a closed persona-mode contract (Context fact 3). |
| R-skills | reject | A co skill is partly a tool-routing instruction; "spawn as a skill" leaks tool-shaping into a persona-only field (violates surface-unchanged). | — |
| R-souls | reject | Souls are built-in doctrine (never queryable) and are identity, not function — "hand this research to tars" is semantically empty. | — |
| R-registry | reject | A generic `researcher/editor/verifier` set is co's least-distinctive surface (`feedback_skill_curation_knowledge_work_positioning`). The trap is the set's *content*, not its existence. | — |
| R-contract | adopt | A small, closed, co-native persona-mode contract is the convergent schema (matching the 4/5 peers on shape) with deliberately thinner persona-only semantics; eval-gated against R1 prose. | Whole plan direction (Context fact 3, Scope, Behavioral Constraints). |
| CD-M-1 | adopt | C1 *is* the critique pass, so the deferred unknown-`subagent_type` branch must collapse now (no hedged branches). Fail-loud matches the FLAT_EXACT unknown-name precedent (`spec.py:56-59`) + depth-cap refusal (`delegation.py:115-119`); a silent anonymous fallback would mask the pick-instability disqualifier. | HLD step 2 + TASK-2 body + `done_when`: unknown ⇒ fail-loud refusal naming valid modes; fallback option removed. |
| CD-m-1 | adopt | R1 mode-prose is at `delegate.py:36-37`, not `:38-40`. | Context fact 1 citation corrected. |
| CD-m-2 | adopt | Mode brief + deferred-tool stub share one instructions string; composition must not displace the stub. | TASK-2 `done_when` asserts non-displacement. |
| CD-m-3 | adopt | Arm B must match the production delegate path (`propagate_approvals=True` + parent frontend, `delegation.py:123-130`) or the A/B is not apples-to-apples. | HLD Arm B + TASK-1 Arm B updated; flagged as first eval-layer direct `run_standalone_owned` caller. |
| PO-m-1 | adopt | The negative guardrail gave Gate 1 no positive anchor to falsify the co-native claim before the eval is built. | TASK-1 names 2-3 illustrative, non-binding candidate modes (synthesis/distill, critique/adversarial-review). |
| PO-m-2 | adopt | If Arm A used hand-tuned R1 prose, a flat result would be ambiguous; the field's claim is that the small model authors mode-prose worse than a tuned brief. | HLD Arm A: explicit the baseline is the model authoring mode-prose unaided, no R1 tuning. |

## TASK-1 Delivery — eval-backed GO (2026-06-29)

**Deliverable:** `evals/eval_delegate_persona_mode.py` (new, no production code) + decision recorded in `docs/reference/RESEARCH-delegation-interface-peer-survey.md` (R2 DECISION block + Q1/Q2 resolved).

**Design as built (resolving the plan's one under-specified point — how the headline isolates brief-quality from pick-quality while honoring "R1-not-nothing"):** headline B-vs-A isolates *brief quality given a correct pick* — Arm A = the small model authoring an R1-laden delegated task **unaided** (a real `llm_call` author-and-pick step, PO-m-2) → production `DELEGATE_AGENT_SPEC`; Arm B = plain task + the scenario-correct mode brief → eval-local `dataclasses.replace` spec (never mutating `DELEGATE_AGENT_SPEC`/`_delegate_agent_instructions`, CD-m-5). Both drive the real `run_standalone_owned` forked + `propagate_approvals=True` + parent frontend (CD-m-3). Pick stability + semantic cost are read from the same combined author-and-pick call, repeated ×3.

**Readings (real Ollama `qwen3.6:35b-a3b-agentic`, judge `gemini-3.5-flash`, 2 seeded knowledge-work scenarios, single UAT smoke run):**
- Headline (both judge orders, disagreement = tie): **B wins 1 (critique), ties 1 (synthesis), A never wins.**
- Pick stability + correctness: **3/3 correct and stable on both scenarios.**
- Semantic cost: no `task`/`subagent_type` mismatch.
- Surfacing prefill: lean 2-mode menu ≈ **76 tokens** → enumerate-in-description adopted.

**Verdict: GO.** Settled mode set `synthesis` + `critique` (small, closed, co-native). TASK-2 (gated) is now unblocked. Caveat carried into TASK-2: 2-scenario single-run smoke, not powered — revisit briefs if a wider set shows synthesis flat.

## Delivery Summary — 2026-06-29 (TASK-2)

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | A/B eval runs end-to-end on real Ollama + survey records the go/no-go | ✓ pass (GO) |
| TASK-2 | named `subagent_type` runs the owned path with the mode brief composed (stubs not displaced); surface unchanged (`VISIBILITY_MODEL`); unknown ⇒ fail-loud refusal naming valid modes; `None` reproduces today; lean menu in the description; stale-ref grep clean | ✓ pass |

**What was built (TASK-2):**
- `co_cli/agent/delegation.py` — `PERSONA_MODES` closed table (`synthesis`, `critique`; briefs ported from the eval-validated fixtures); `_delegate_agent_instructions(deps, mode_brief=None)` composes the brief **before** the deferred-tool stubs (CD-m-2 non-displacement); `delegate_to_agent(parent_deps, task, subagent_type=None)` resolves the mode into a per-call `dataclasses.replace` spec (surface unchanged), **fails loud** on an unknown name (CD-M-1, no silent anonymous fallback), `None` ⇒ the `DELEGATE_AGENT_SPEC` singleton byte-for-byte.
- `co_cli/tools/system/delegate.py` — optional `subagent_type` param + the lean when-to-use menu in the docstring (the model-facing selection surface; ≈83-token prefill, within the ≤100 budget set in TASK-1). Menu intro aligned to the eval-validated text.

**Design notes / scope calls:**
- **Menu home = the `delegate` docstring** (the pydantic-ai tool description), per the plan's `files:` scope. R1 guidance is *also* mirrored in `co_cli/context/guidance.py` `DELEGATE_GUIDANCE` (instruction-floor), which was left untouched — the param's allowed-values menu belongs in the param documentation, and `guidance.py` is out of TASK-2 `files:`. If review/behavioral evidence later shows the model under-reads the docstring menu, surfacing into `DELEGATE_GUIDANCE` is a scoped follow-up.
- ⚠ **Extra file:** `tests/test_flow_delegation.py` — TASK-2 `files:` lists only the two production files, but the plan's Testing section requires a runtime delegation exercise for the new path; the existing delegation tests live here. Added three functional tests (brief-reaches-instructions + stubs-not-displaced; persona-mode-surface-unchanged; unknown-subagent_type-fails-loud).

**Tests:** scoped — 41 passed, 0 failed (`tests/test_flow_delegation*.py`, `test_flow_owned_subagent.py`, `test_flow_slash_dispatch.py`, `test_toolset_guidance.py`; real-Ollama delegation paths green).
**Doc Sync:** fixed — `docs/specs/agents.md` (`delegate_to_agent` pseudocode + signature, persona-mode contract in §2, `PERSONA_MODES` in registry/exports, new test-gate row).

**Overall: DELIVERED**
Both tasks pass; lint clean; scoped tests green; doc sync clean. The persona-mode selector is live behind the eval-proven go, surface-invariant, with a lean always-on menu and fail-loud unknown handling.

## Selector cardinality — `subagent_type` is OPTIONAL, justified on effectiveness (no-fit eval, 2026-06-29)

The `optional` choice was challenged ("don't justify it by zero-regression — this surface is brand new"). Correct: there is no caller to break, and co enforces zero-backward-compat, so "regression safety" is not a reason. The decision was re-settled on **delegation effectiveness across the full task distribution**, by adding a **no-fit decider** scenario (`P5.N`) to `evals/eval_delegate_persona_mode.py` — a mechanical read→write→verify task that matches neither mode.

**Measured (real Ollama `qwen3.6:35b-a3b-agentic`, judge `gemini-3.5-flash`):**
- **Fit tasks:** a mode brief **helps** (critique beats baseline) or is **neutral** (synthesis ties) — never hurts; picks 3/3 correct + stable.
- **No-fit task:** the model **omits the field 3/3** (correct routing — no mis-fire), and **forcing** a mode is **neutral** — synthesis-vs-default and critique-vs-default both tie in both judge orders ("forcing adds nothing").

**Decision: OPTIONAL, because it dominates across the distribution —**
- **Mandatory (synthesis|critique required)** is dominated: on no-fit work it forces a mode that at best *ties* the default while removing the model's ability to express "no mode," and charges a pick on *every* delegation. Measurable cost, zero upside.
- **Mandatory + a "general" third mode** is also dominated: a `general` brief ≡ today's default, so it is behaviorally identical to optional-omit but pays an extra always-on menu line and forces emitting the field every turn — and reintroduces the generic-registry smell the plan rejected (R-registry).
- **Optional** captures the fit-case gain, relies on routing the model demonstrably does correctly (omit on no-fit, pick on fit), and pays nothing on no-fit. It is the only cardinality that secures the upside without taxing the cases where modes add nothing.

**Honest bound:** across runs, forcing a mode on the no-fit task was *neutral-to-harmful* (one run tied, one run the default beat the forced mode in both judge orders) — so the rejection of mandatory rests on "forcing never helps and sometimes hurts + costs a forced pick + correct routing already happens." `fail-loud` is correctly scoped to *unknown* names (a real error), not to a *missing* field (a valid, common case).

**Overhead — the field is NOT free (with/without-menu A/B, scenario P5.O, 2 runs).** The same delegation decision was driven through a real model request twice — production `delegate` def (menu + `subagent_type`) vs a control def with both stripped (only delta). Measured:
- **Static prefill: ~77 tokens** (the clean isolate — deterministic, small, within the ≤100 budget).
- **Generation + latency overhead when the field is present: real and non-trivial** — fit decision **+369→+416 output tokens / +4.5→+5.3 s**; no-fit decision **+90→+146 tokens / +1.3→+2.1 s**. Noisy (3 repeats; conflates field-emission, richer task-authoring, run variance) but directionally robust across both runs.
- **Trigger fidelity: mostly but not perfectly preserved** — one run delegated 2/3 with-menu vs 3/3 control on the fit probe (a single perturbation).
- **Correct use:** no-fit omits 3/3 (both runs); fit sets the right mode 2/3 (the live tool-call pick is noisier than the dedicated classifier's 3/3).

So the claim "optional-with-default adds no semantic/reasoning overhead" is **refuted**: there is a ~77-token static prefill *and* a measurable per-decision generation/latency cost when the field is engaged. The optional selector is justified **not** by being free but by the trade being favourable: the overhead is bounded, paid mostly when the model actually engages the field, and offset by the fit-case quality gain (headline: B beats-or-ties A, never loses) while no-fit work correctly omits the field and avoids most of it. This refines — not reverses — the GO. Run record: `evals/_outputs/delegate_persona_mode-*-run.jsonl`; logs `.pytest-logs/*-eval-persona-*.log`.

## Final — Team Lead

Plan approved — PO `Blocking: none` (C1), Core Dev `Blocking: none` (C2). Convergence at C2.

The load-bearing result: the named-agent selector is adopted as a **small, closed, co-native persona-mode contract**, eval-gated against the **R1 prose baseline** (not nothing) — because co already carries delegation mode in prose, the only delta that justifies the field is that a tuned brief keyed by a cheap pick beats the small model authoring mode-prose unaided. Surface unchanged by role (persona-only, the 3.6 principle holds), two-tier surfacing (lean always-on menu / rich on-use brief), unknown `subagent_type` fails loud. A no-go on TASK-1 keeps co anonymous deliberately.

> Gate 1 — cleared. TASK-1 ran and returned **GO** (2026-06-29): the load-bearing risk — **building a surface the small model ignores or mis-uses** — was tested by the real-Ollama A/B against the R1 baseline and did not materialize (B never loses; picks 3/3 correct + stable; prefill ≈76 tokens). Scope held to eval-then-gated-impl; per-role tool surfaces, an extensible registry, and spec edits remain out.
> Next: `/orchestrate-dev loop-decoupling-phase5-5` for TASK-2 (gate satisfied — TASK-1 go).

## Implementation Review — 2026-06-29

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | A/B eval runs end-to-end on real Ollama + survey records go/no-go (+ no-fit decider, overhead A/B) | ✓ pass | `eval_delegate_persona_mode.py` drives real `run_standalone_owned` (Arm A/B), `dataclasses.replace` Arm B never mutates `DELEGATE_AGENT_SPEC`/`_delegate_agent_instructions` (CD-m-5); 4 dimensions produced (run `…160432Z-run.jsonl`); survey R2 records mode set, surfacing, GO, cardinality, overhead (survey:196-214) |
| TASK-2 | named `subagent_type` runs owned path with brief composed (stubs not displaced); surface unchanged (`VISIBILITY_MODEL`); unknown ⇒ fail-loud; `None` ⇒ byte-for-byte; lean menu present; grep clean | ✓ pass | `delegation.py:168-184` — `None`→singleton (guard at :169 skipped), unknown→fail-loud refusal at :174 *before* the fork/run at :184, valid→per-call `dataclasses.replace` at :177; `_delegate_agent_instructions` composes brief before stubs (CD-m-2); `delegate.py:22-53` param + lean menu; call path `delegate`→`delegate_to_agent`→`run_standalone_owned` confirmed |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `_PersonaMode.name` write-only dead field; single-payload wrapper after removal | `co_cli/agent/delegation.py` | blocking (clarity-by-subtraction) | Collapsed `_PersonaMode` → `PERSONA_MODES: dict[str, str]` (name→brief); resolve reads brief directly; tests updated |
| Dead `run: Any` param threaded into 3 eval runners, never read | `evals/eval_delegate_persona_mode.py` | blocking (one-sided param) | Dropped from `_run_scenario`/`_run_nofit_decision`/`_run_overhead_ab` + call sites |
| Stale comment "TASK-2 will inject into the real builder" (TASK-2 shipped) | `evals/eval_delegate_persona_mode.py:310` | minor | Reworded to the frozen-eval-fixture rationale |
| ALWAYS schema-budget guard tripped — bucket 21989 > ceiling 21500 (the lean menu on the ALWAYS `delegate` docstring) | `tests/test_orchestrator_schema_budget.py` | blocking | Trimmed redundant `subagent_type` Args line (−52 chars), then consciously re-pinned ceiling 21500 → 22000 with a dated rationale (the menu IS the ALWAYS selection surface, eval-validated affordable; rich brief stays on-use) |

### Tests
- Command: `uv run pytest` (full suite, fail-fast)
- Result: **887 passed, 0 failed** in 491s (after fixes). First run caught the budget-guard failure (816 passed, 1 failed); re-run after the conscious re-pin is fully green.
- Log: `.pytest-logs/<ts>-review-impl-2.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads, exit 0)
- Persona-mode behavior is LLM-mediated (delegation decision + mode pick) — verified via the eval (4 dimensions, real Ollama: picks correct on fit, omits on no-fit) + the no-LLM functional tests (`test_persona_mode_*`, `test_delegate_unknown_subagent_type_refuses_loudly`) + the real-LLM delegation suite (green in the full run). Chat interaction non-gating.
- `success_signal` TASK-2 verified: the model optionally selects a named mode (eval: 3/3 fit picks, 3/3 no-fit omits), the surface is unchanged by mode (`test_persona_mode_leaves_tool_surface_unchanged`), omitting reproduces today (full delegation suite green). TASK-1 `success_signal`: documented eval-backed decision recorded in survey R2 + plan.

### "Eval baked in md" (explicit request)
The full eval — all four dimensions (headline A/B, pick stability, no-fit cardinality decider P5.N, with/without-menu overhead A/B P5.O) and its honest conclusions (GO; optional-not-mandatory on effectiveness; the field is NOT overhead-free) — is recorded in the plan (TASK-1 Delivery, Selector cardinality, Overhead sections) and the survey R2 DECISION block. Deliberately NOT added to `docs/specs/uat_evals.md`: that is the standing W1–W12 mission UAT matrix; this is a one-shot build-time decision artifact (build-time/runtime layer split), so its home is the plan + survey, not the runtime spec.

### Overall: PASS
All blocking findings fixed (a dead production field collapsed, a dead eval param dropped, the conscious schema-budget re-pin); full suite green at 887; boot smoke clean; the eval and its overhead findings are fully recorded in markdown. Ready for Gate 2 → `/ship`.
