# Loop decoupling — PHASE 6: spec sync + `0.9.0` (milestone capstone)

> Phase plan for the milestone `2026-06-24-234633-loop-decoupling-milestone.md` (PHASE 6). Sequencing gate satisfied: 5.6 (v0.8.510) → `/audit-conformance` (v0.8.512) → **this**. This is the final phase; on ship the milestone + design artifacts archive and the version bumps `0.8.512 → 0.9.0`.

## Context

Phase 5 (v0.8.506) cut over to the owned loop and **deleted the pydantic-ai graph path** — `Agent.iter()`, `SurrogateRecoveryModel`, `_CallSeamToolset`, `_RepairingStreamedResponse`, `DeferredToolRequests` suspend/resume, the `use_owned_loop` selection flag, and all orphaned wiring. That cutover was deliberately **source-only**: CHANGELOG 0.8.506 states "spec sync + `0.9.0` are Phase 6." So **every** runtime spec that describes the turn loop, the model boundary, tool dispatch, approval, or the span seams is now stale, describing machinery that no longer exists. Phase 6 is the milestone's designated spec-reconciliation capstone.

**Current owned-loop reality (source-mapped, file:line — the reconciliation target):**

| Concern | Old (deleted) | Current |
|---|---|---|
| Orchestrator turn | `run_turn` / `_execute_run` / `agent.iter()` | `run_turn_owned` (`agent/loop.py:198`), `_orchestrator_step_loop` (`loop.py:280`), per-step `drive_model_request` (`loop.py:135`) |
| Turn state | `_TurnState` | `TurnState` (`agent/turn_state.py:99`); typed `TurnExit` enum (`turn_state.py:36`); `ToolCapState` (`turn_state.py:60`) |
| Model boundary | `SurrogateRecoveryModel(WrapperModel)` | `model_turn()` (`llm/model_turn.py:66`) driving `pydantic_ai.direct.model_request_stream`; surrogate-retry + chat span inlined; JSON repair in `RepairingStreamedResponse` (`llm/_json_repair.py:132`) |
| Tool dispatch + cap + spill | `_CallSeamToolset.call_tool` | `dispatch_tools()` (`agent/dispatch.py:234`) with step-boundary `ToolCapState`; `make_run_context()` (`dispatch.py:51`) |
| Approval | `DeferredToolRequests` / `_run_approval_loop` / `_collect_deferred_tool_approvals` | inline `collect_inline_approvals()` (`agent/approval.py`), pre-fan-out, headless auto-deny |
| Task agents | `agent.run()` + `UsageLimits` graph; `use_owned_loop` branch | `run_standalone_owned()` (`loop.py:618`); `run_standalone` (`run.py`) is daemon-only, no graph branch, no flag |
| Preflight | graph-registered `history_processors=` + `agent.instructions()` | `run_history_processors()` (`preflight.py:60`), `assemble_instructions()` (`preflight.py:199`) emitting `InstructionPart`s per step |

**Spec-drift inventory (repo-wide grep of deleted symbols across `docs/specs/`):** the drift is **wider than the milestone's Phase 6 prose enumerated** (it named core-loop, pydantic-ai-integration, prompt-assembly/personality, agents-R3). Actual load-bearing staleness:

| Spec | Deleted-symbol hits | Nature |
|---|---|---|
| `pydantic-ai-integration.md` | 38 | **Heaviest** — whole §§2.1–2.7 describe deleted wrappers/graph/approval. Reframe to "pydantic-ai as provider + message library." |
| `core-loop.md` | 29 | **Heavy** — `turn ⊇ run ⊇ model request` vocabulary, `agent.iter`, `_execute_run`, `_TurnState`, the deferred approval flow. Rewrite to `turn ⊇ step`. Destination of the §2.1 layering-rationale merge. |
| `tools.md` | 15 | `_CallSeamToolset`, `DeferredToolRequests` approval loop, `SurrogateRecoveryModel`, the approval-loop diagram. |
| `observability.md` | 12 | span seams point at deleted homes: `chat`→`SurrogateRecoveryModel`, `tool`→`_CallSeamToolset`, `invoke_agent`→`_execute_run`. Now `model_turn.py` / `dispatch.py` / `loop.py`. |
| `agents.md` | 7 | `output_type=[str, DeferredToolRequests]`, `_CallSeamToolset`/`SurrogateRecoveryModel` in build, `use_owned_loop` flag + graph branch in `run_standalone`, `_approval_resume_filter`. Also the R3 divergence record to **add**. |
| `compaction.md` | ~8 | `_execute_run`, `_CallSeamToolset.call_tool` as the cap seam, JSON-repair location, the L0 `tool_calls_in_model_request` field (deleted in 5.6 → now `ToolCapState`). |
| `tui.md` | 1 | `_execute_run()` reasoning-display read-site. |
| `prompt-assembly.md` | 0 (symbol grep) | But the **mechanism** is stale: describes `agent.instructions()` SDK registration; now `assemble_instructions()` assembles `InstructionPart`s per step in the owned loop. |
| `personality.md` | 0 (symbol grep) | Same mechanism staleness (static-builder iteration is still accurate; the per-turn registration framing is not). |

**Secondary stale set (caught by widening the grep to `run_turn(` + the deleted file `orchestrate.py` — CD-M-1).** The deleted-symbol grep above missed the renamed entrypoint and the deleted module; a wider grep finds four more stale specs:

| Spec | Stale reference | Nature |
|---|---|---|
| `01-system.md` | `run_turn()` flow (`:106,109`) + **cites the deleted file** `co_cli/agent/orchestrate.py` in the public-interface (`:264`) and files (`:286`) tables | **Load-bearing** — the top-level system spec points at a deleted module. Must reconcile. |
| `skills.md` | `run_turn()` in the REPL→turn flow (`:46,152`) | Bare symbol rename → `run_turn_owned`. |
| `sessions.md` | `run_turn` `finally`-block usage flush (`:78`) | Bare symbol rename; the mechanism (once-per-turn flush) is still accurate. |
| `uat_evals.md` | `run_turn` in `evals/_trace.py` ref (`:208`) | Bare symbol rename. |

`/audit-conformance` is **code-only** — it does not touch `docs/specs/`. So if Phase 6 reconciles only the milestone-named subset, the remaining specs ship at `0.9.0` documenting deleted symbols (and, for `01-system.md`, a deleted file), with **no other mechanism** to ever fix them. The grounded scope is therefore **all stale specs** (nine primary + the four secondary above), not the under-enumerated prose list (see High-Level Design).

**Two additional Phase-6-owned deliverables (from the milestone contract):**
- **Refresh the on-hold `sdk-coupling-cleanup` plan** (`docs/exec-plans/active/2026-06-24-220958-sdk-coupling-cleanup.md`). It is a pre-cutover S1–S6 census; S2/S6 landed at cutover and S1/S2/S4/S6 cite deleted files — **fully obsolete**. Per the milestone (Phase 5.6 finding CD-m-1): do **not** "archive as subsumed" — **refresh** it to own the *new* post-cutover SDK reaches 5.6 routed to it: `_output.OutputToolset` (`preflight.py:258`, used by `build_output_toolset` `:244`) and `make_run_context` (`dispatch.py:51`). "Resolve before `0.9.0`" = the SDK boundary is **reviewed** (each reach gets a verdict), not stamped blind.
- **R3 — record delegation conscious-divergences** (confirmed with user 2026-06-27): in `agents.md`, document that co **deliberately** omits async/parallel delegation (synchronous owned loop, holds a tool slot) and per-call model/scope overrides ("full agent inherits parent" principle), with peer counts (async/parallel D9 5/5; per-call model 3/5; per-call toolsets 1/5) as context — so reviewers see decisions, not oversights.

## Problem & Outcome

**Problem:** the runtime spec layer documents a deleted architecture. A reader (human or agent) consulting `core-loop.md` / `pydantic-ai-integration.md` / `tools.md` / `observability.md` today is told co runs `agent.iter()` with `SurrogateRecoveryModel` and `_CallSeamToolset` and suspends on `DeferredToolRequests` — none of which exists. The specs are co's runtime source-of-truth; at the `0.9.0` capstone they must describe the owned loop.

**Outcome:** every runtime spec describes the owned loop accurately (`turn ⊇ step`, `model_turn` over `direct.model_request_stream`, `dispatch_tools` + `ToolCapState`, inline approval, `run_turn_owned` / `run_standalone_owned`). The §2.1 layering rationale lives in `core-loop.md` as the canonical *why*. The delegation divergences are recorded in `agents.md`. The `sdk-coupling-cleanup` plan owns the two surviving SDK reaches with a verdict on each. Version is `0.9.0`; the milestone is shipped and archived.

**Failure cost:** low-acute, high-chronic. No runtime behavior changes (docs + a version string). But shipping `0.9.0` with specs describing deleted symbols permanently anchors the spec layer to a fiction — every future planner grounding a decision in these specs (the CLAUDE.md mandate to "cite design docs rather than reconstruct from memory") inherits the lie, and there is no downstream mechanism (`/audit-conformance` is code-only) that will ever catch it. The drift only compounds.

## Scope

**In:**
- Reconcile **all** stale runtime specs to the owned loop: the nine primary (`core-loop.md`, `pydantic-ai-integration.md`, `agents.md`, `tools.md`, `observability.md`, `compaction.md`, `tui.md`, `prompt-assembly.md`, `personality.md`) **plus** the four secondary (`01-system.md`, `skills.md`, `sessions.md`, `uat_evals.md` — stale `run_turn`/`orchestrate.py` references, TASK-8).
- **Merge** the design §2.1 "Why three layers, not two" rationale into `core-loop.md` (full), with the framing half carried into `pydantic-ai-integration.md`.
- **Add** the R3 delegation-divergence record to `agents.md`.
- **Refresh** the `sdk-coupling-cleanup` exec-plan to own the two post-cutover SDK reaches (verdict each); mark the pre-cutover census obsolete.
- **`0.9.0`** version bump + CHANGELOG; archive the milestone metaplan + design artifact + this phase plan.

**Out:**
- Any source change to `co_cli/` beyond the version string in `pyproject.toml` (the cutover already shipped; this phase is docs + version). **No behavior change.**
- *Fixing* the two SDK reaches. The milestone says "reviewed, not stamped blind" — the refresh gives each a verdict (forced-keep+pinned vs actionable); actually acting on an actionable one is the refreshed `sdk-coupling-cleanup` plan's own future Gate-1 cycle, not Phase 6.
- The pydantic-ai v2 migration (separate plan, runs last per the milestone sequencing).
- Specs with **zero** drift (`dream.md`, `memory.md`, `bootstrap.md`, `config.md`, `self-planning.md`, `00-mission.md`, the `skills-*.md`) — untouched unless a cross-reference into a reconciled spec breaks. (Verified clean: no `run_turn`/`orchestrate.py`/deleted-symbol hits. `01-system.md`, `skills.md`, `sessions.md`, `uat_evals.md` are **not** in this list — they are in-scope via TASK-8, per CD-M-1.)

## Behavioral Constraints

- **Zero runtime behavior change.** The only `co_cli/` edit is the `pyproject.toml` version string. Specs and exec-plans are not shipped code. The full suite must stay green across the version bump (it is green at 0.8.512 per the 5.6/audit ships).
- **Specs are reconciled via `/sync-doc`, not an orchestrate-dev code pass.** Per CLAUDE.md's workflow, `docs/specs/` is owned by `sync-doc` (the mechanism orchestrate-dev auto-invokes post-delivery). Phase 6 has no code to deliver, so the spec reconciliation runs as **direct `/sync-doc` invocations**. Consequently, per the orchestrate-plan rule, **no task lists `docs/specs/` in `files:`** — spec targets are named in task bodies; the executor is `/sync-doc`. The only `files:` entries are the `sdk-coupling-cleanup` plan (a build-time artifact), `pyproject.toml`, and `CHANGELOG.md`.
- **Every retained mechanism claim is source-verified against the EDITED owned-loop source at `file:line`** (per `feedback_plan_findings_verify_edited_files` and `feedback_ground_mechanism_claims_in_source`) — never reconstructed from the old spec prose or from memory. A reconciled spec is done only when its deleted-symbol grep returns zero *and* its surviving claims cite current source.
- **No spec adds a backward-compat / migration note** (`feedback_zero_backward_compat`). The graph path is gone; specs describe the present, not "formerly X, now Y" tables. The design §2.1 *does* explain why the run layer was removed — that is canonical rationale, not a compat table.
- **`build-time` vs `runtime` layer stays clean** (`reference_layer_buildtime_vs_runtime`): the §2.1 rationale that merges into `core-loop.md` is reframed as shipped-behavior *why*, not as build-time milestone history; the milestone/phase narrative stays in the exec-plans.

## High-Level Design

**Scope decision (settled; PO confirms at Gate 1): reconcile all nine stale specs, not the milestone's under-enumerated subset.** The milestone prose named four targets but the cutover deleted symbols documented across nine specs, and the milestone *itself* mandated "spec sync ... are Phase 6" for the whole cutover. `/audit-conformance` is code-only and will never reconcile a spec. Leaving `observability.md` / `tools.md` / `compaction.md` describing `_CallSeamToolset` and `SurrogateRecoveryModel` at the `0.9.0` capstone is strictly worse than the marginal cost of reconciling them now (the source map already supplies every current `file:line`). The defensible alternative — reconcile the four named, defer the rest to a follow-up `/sync-doc` — only adds a coordination boundary and risks the deferral never happening. Recommendation: **all nine, now.**

**Execution shape:** grouped by reconciliation tier so each `/sync-doc` invocation is bounded:
- **Tier A (heavy rewrite):** `core-loop.md` (+ §2.1 merge), `pydantic-ai-integration.md` (reframe + framing-half of §2.1).
- **Tier B (seam/symbol relocation — same deleted symbols, mechanical):** `agents.md` (+ R3), `observability.md`, `tools.md`, `compaction.md`.
- **Tier C (mechanism reframe):** `prompt-assembly.md`, `personality.md`, `tui.md` — instruction registration → per-step `assemble_instructions`; the `_execute_run` read-site.

**The §2.1 merge** (design lines 35–59): the "turn and step are intrinsic; run is a graph artifact" rationale becomes `core-loop.md`'s canonical explanation of the two-level loop, replacing the deleted `turn ⊇ run ⊇ model request` framing. The design subsection carries a `→ MERGE TO SPEC, Phase 6` marker; this is its destination. After merge, the design artifact may archive (it does not "die with the plan").

**The sdk-coupling-cleanup refresh:** rewrite the plan's status + census. Mark S1–S6 obsolete (cite which deleted files each referenced). New census = the two reaches: `_output.OutputToolset` (`preflight.py:258`) and `make_run_context` (`dispatch.py:51`). For each, a source-grounded verdict (forced + pinned → no-action, or actionable → task), mirroring the old plan's census discipline. Result: a small, current, post-cutover SDK-coupling plan that the eventual v2 migration consumes as its baseline.

## Tasks

### TASK-1 — Reconcile `core-loop.md` to the owned loop + merge §2.1
- **files:** none in `docs/specs/` (executor: `/sync-doc core-loop`); reads `co_cli/agent/loop.py`, `turn_state.py`, `preflight.py`, the design §2.1.
- Replace the `turn ⊇ run ⊇ model request` vocabulary with `turn ⊇ step`; rewrite the foreground-turn flow, owners table, turn-contract/state (`TurnState`/`TurnExit`/`ToolCapState`), the run-contract section (now the step loop in `_orchestrator_step_loop`), approval (inline `collect_inline_approvals`, no deferred resume), the error matrix (typed `TurnExit` branches, `classify_provider_error`), and the public-interface/files tables. Merge the design §2.1 layering rationale as the canonical *why* — **present-tense architecture, no "formerly/now" or phase-history phrasing** (PO-m-4; the run layer's removal is explained as the intrinsic two-level shape, not as "we deleted X in Phase 5").
- **done_when:** `grep -nE 'DeferredToolRequests|agent\.iter|_execute_run|_TurnState|_run_approval_loop|SessionAgent|AgentRunResult|WrapperModel|run_turn\b' docs/specs/core-loop.md` returns zero (any surviving `run_turn` is `run_turn_owned`; manual-inspect that no hit is a bare graph-era term), AND every retained mechanism/symbol claim cites a current `co_cli/` `file:line` verified against the edited source, AND the §2.1 rationale is present.
- **success_signal:** N/A (doc reconciliation).
- **prerequisites:** none.

### TASK-2 — Reframe `pydantic-ai-integration.md` to "provider + message library"
- **files:** none in `docs/specs/` (executor: `/sync-doc pydantic-ai-integration`); reads `co_cli/llm/model_turn.py`, `_json_repair.py`, `agent/dispatch.py`, `preflight.py`, `agent/loop.py`.
- Rewrite §§2.1–2.7: the SDK surface is now providers + `ModelMessage`/`*Part` + `ToolDefinition` + `ModelSettings` + `direct.model_request_stream` + exceptions — **not** `Agent`/graph/`WrapperModel`/`DeferredToolRequests`. Document the surviving reaches honestly: the `_output.OutputToolset` private reach (`preflight.py:258`) and `make_run_context` (`dispatch.py:51`). The one wrapper that **survives** is `_SequentialMCPToolset(WrapperToolset)` (`co_cli/agent/mcp.py:19`) — a legitimate MCP-context mention, keep it. Carry the framing half of §2.1 (the run layer was the graph's unit, now gone) as **present-tense architecture, no phase-history phrasing** (PO-m-4). Update the layers table, the wrapper diagram, the public-interface and test-gate tables.
- **done_when:** `grep -nE 'SurrogateRecoveryModel|_CallSeamToolset|_RepairingStreamedResponse|agent\.iter|DeferredToolRequests|AgentRunResult|output_type=\[str' docs/specs/pydantic-ai-integration.md` returns zero, AND `grep -n 'WrapperModel' docs/specs/pydantic-ai-integration.md` shows no surviving `WrapperModel` claim (manual-inspect: any `WrapperToolset` hit is the kept `_SequentialMCPToolset` MCP base, not a graph-era model wrapper), AND the two surviving SDK reaches are documented with their current `file:line`, AND each retained claim is source-verified.
- **success_signal:** N/A.
- **prerequisites:** none.

### TASK-3 — Reconcile `agents.md` (build/run mechanics) + record R3 divergences
- **files:** none in `docs/specs/` (executor: `/sync-doc agents`); reads `co_cli/agent/build.py`, `run.py`, `loop.py`, `delegation.py`, `docs/reference/RESEARCH-delegation-interface-peer-survey.md` §6 R3.
- Fix: `output_type=[str, DeferredToolRequests]` → owned-loop output handling; remove `_CallSeamToolset`/`SurrogateRecoveryModel` from `build_orchestrator`/`build_task_agent` descriptions; remove the `use_owned_loop` flag + graph branch from `run_standalone` (it is daemon-only now, no flag); remove `_approval_resume_filter`/`DeferredToolRequests` references. **Add** an R3 subsection: co deliberately omits async/parallel delegation (synchronous owned loop, holds a tool slot) and per-call model/scope overrides ("full agent inherits parent"), with peer counts (D9 5/5; model 3/5; toolsets 1/5) as context.
- **done_when:** `grep -nE 'DeferredToolRequests|_CallSeamToolset|SurrogateRecoveryModel|use_owned_loop|_approval_resume_filter|output_type=\[str' docs/specs/agents.md` returns zero, AND the R3 divergence subsection is present with the peer counts, AND every build/run claim cites current source.
- **success_signal:** N/A.
- **prerequisites:** none.

### TASK-4 — Reconcile `observability.md`, `tools.md`, `compaction.md` (seam relocation)
- **files:** none in `docs/specs/` (executor: `/sync-doc` per file); reads `co_cli/agent/dispatch.py`, `llm/model_turn.py`, `agent/loop.py`, `agent/turn_state.py`, `co_cli/context/`.
- Relocate the deleted seams: `chat` span `SurrogateRecoveryModel`→`model_turn.py`; `tool` span + cap + MCP-spill `_CallSeamToolset.call_tool`→`dispatch_tools` (`dispatch.py`); `invoke_agent` span `_execute_run`→`loop.py`/`run.py`. In `compaction.md`: the L0 cap is `ToolCapState` at the step boundary (not `tool_calls_in_model_request`, deleted in 5.6); JSON repair is in `_json_repair.py`/`model_turn.py`; replace `_execute_run` references. In `tools.md`: replace the `DeferredToolRequests` approval-loop diagram/section with inline approval; `_CallSeamToolset` → `dispatch_tools`; `SurrogateRecoveryModel` → `model_turn`.
- **done_when:** `grep -nE 'SurrogateRecoveryModel|_CallSeamToolset|_RepairingStreamedResponse|_execute_run|DeferredToolRequests|AgentRunResult|tool_calls_in_model_request' docs/specs/observability.md docs/specs/tools.md docs/specs/compaction.md` returns zero, AND `grep -n 'WrapperModel\|WrapperToolset' docs/specs/{observability,tools,compaction}.md` shows no graph-era model-wrapper claim (manual-inspect: only a `_SequentialMCPToolset(WrapperToolset)` MCP mention is allowed), AND each relocated seam/symbol cites the current `file:line`.
- **success_signal:** N/A.
- **prerequisites:** none.

### TASK-5 — Reconcile `prompt-assembly.md`, `personality.md`, `tui.md` (mechanism reframe)
- **files:** none in `docs/specs/` (executor: `/sync-doc` per file); reads `co_cli/agent/preflight.py` (`assemble_instructions`, `run_history_processors`), `agent/_instructions.py`, `agent/loop.py`.
- Reframe the instruction-injection mechanism: it is no longer `agent.instructions()` SDK registration in `build_orchestrator`; it is `assemble_instructions()` (`preflight.py:199`) emitting `InstructionPart`s (static `dynamic=False` once per turn + the dynamic callbacks) per step in the owned loop, bridged onto `ModelRequestParameters.instruction_parts`. Preserve the cache-stability semantics (dynamic parts after the cached static block). In `tui.md`: the `reasoning_display` read-site is the owned step loop (`loop.py`), not `_execute_run`. Keep the still-accurate static-builder iteration in `personality.md`.
- **done_when (positive assertion — a bare deleted-symbol grep is blind to this conceptual drift, CD-M-2):** (a) `grep -n '_execute_run' docs/specs/tui.md docs/specs/prompt-assembly.md docs/specs/personality.md` returns zero; (b) `grep -c 'assemble_instructions' docs/specs/prompt-assembly.md` returns ≥1 (the new mechanism is named); (c) **manual-inspection clause** — every `@agent.instructions` / `agent.instructions()` SDK-registration reference in `prompt-assembly.md` (~12 today) and `personality.md` (~3) is reframed to the owned-loop per-step `assemble_instructions()` assembly (the `InstructionPart(dynamic=…)` semantics may remain, but no longer described as SDK `Agent` registration in `build_orchestrator`); (d) the cache-stability invariant (dynamic parts after the cached static block) is retained and source-verified against `preflight.py:199`.
- **success_signal:** N/A.
- **prerequisites:** none.

### TASK-6 — Refresh the `sdk-coupling-cleanup` plan (own the post-cutover reaches)
- **files:** `docs/exec-plans/active/2026-06-24-220958-sdk-coupling-cleanup.md`.
- Rewrite the status block + census: mark S1–S6 obsolete (cite which deleted files each referenced; note S2/S6 landed at cutover). **Also retire the plan's own actionable TASK-1 + TASK-2 as landed-at-cutover (CD-m-1)** — they are no longer pending: `ToolCapState` already exists (`turn_state.py:60`, not `tool_call_limit.py` as the old done_when claimed) and `compact_messages` already takes `deps: CoDeps` (`compaction.py:321`); both task bodies cite the deleted `agent/orchestrate.py`. New census = the two surviving reaches: `_output.OutputToolset` (`preflight.py:258`, via `build_output_toolset` `:244`) and `make_run_context` (`dispatch.py:51`). Each gets a source-grounded verdict (forced+pinned→no-action vs actionable→task), re-verified against installed pydantic-ai 1.92.0. The result is a current, minimal post-cutover plan; do **not** archive as subsumed.
- **done_when:** the plan no longer presents S1–S6 as actionable (each marked obsolete with the deleted-file citation), the two post-cutover reaches are each inventoried with a verdict + current `file:line`, AND `grep -nE 'use_owned_loop|_CallSeamToolset|orchestrate\.py:[0-9]' docs/exec-plans/active/2026-06-24-220958-sdk-coupling-cleanup.md` finds no surviving claim that cites a deleted file as live.
- **success_signal:** N/A (build-time artifact).
- **prerequisites:** none.

### TASK-8 — Reconcile the secondary stale specs (`run_turn`/`orchestrate.py`)
- **files:** none in `docs/specs/` (executor: `/sync-doc` per file); reads `co_cli/agent/loop.py`, `co_cli/main.py`.
- `01-system.md` (load-bearing): rewrite the turn-flow prose (`:106,109`) and the public-interface + files tables (`:264,:286`) — the entrypoint is `run_turn_owned` (`agent/loop.py:198`, invoked from `main.py:196`), and the deleted file `co_cli/agent/orchestrate.py` is replaced by `co_cli/agent/loop.py` everywhere it is cited. `skills.md` (`:46,152`), `sessions.md` (`:78`), `uat_evals.md` (`:208`): rename the bare `run_turn` symbol to `run_turn_owned`; the surrounding mechanism prose stays (it is still accurate).
- **done_when:** `grep -rnE 'run_turn\b|agent/orchestrate\.py' docs/specs/01-system.md docs/specs/skills.md docs/specs/sessions.md docs/specs/uat_evals.md` returns zero — the `\b` word-boundary (CD-m-4) catches the paren-less `run_turn` in `sessions.md:78`/`uat_evals.md:208`, not just `run_turn(` — and no reference to the deleted file remains; AND `01-system.md`'s public-interface/files tables cite `co_cli/agent/loop.py` verified at `file:line`.
- **success_signal:** N/A.
- **prerequisites:** none.

### TASK-7 — `0.9.0` bump + CHANGELOG + archive (via `/ship`)
- **files:** `pyproject.toml`, `CHANGELOG.md`.
- Bump `0.8.512 → 0.9.0` (the milestone-declared capstone bump, not the parity-increment rule — this is the fundamental-update major-minor bump the milestone reserved). Add the `0.9.0` CHANGELOG entry summarizing the milestone close (owned loop is the sole turn; specs reconciled; sdk-coupling plan refreshed). `git mv` to `completed/`: the milestone metaplan (`2026-06-24-234633-loop-decoupling-milestone.md`), the design artifact (`2026-06-24-234633-loop-decoupling-design.md`), and this phase plan (`2026-06-29-233608-loop-decoupling-phase6.md`).
- **done_when:** `grep -m1 version pyproject.toml` shows `0.9.0`; `CHANGELOG.md` has a `## [0.9.0]` entry; the three plan/design files are in `docs/exec-plans/completed/`; the cross-spec vocabulary cross-check `grep -c 'turn ⊇ run' docs/specs/core-loop.md docs/specs/pydantic-ai-integration.md` returns zero in both (the deleted three-level framing is gone consistently — CD-m-3); AND `scripts/quality-gate.sh full` is green (the suite is unaffected by docs + version string, confirming no accidental source breakage).
- **success_signal:** `uv run co chat` starts and serves a turn at version `0.9.0`.
- **prerequisites:** TASK-1..TASK-6 + TASK-8 (all specs reconciled + sdk plan refreshed *before* the version stamps the milestone closed — the milestone's "resolve before `0.9.0`" gate).

## Testing

No new tests — Phase 6 changes docs + a version string, **zero runtime behavior change** (functional-only testing policy; nothing new to assert). The single safety net is `scripts/quality-gate.sh full` green after the version bump (TASK-7 `done_when`), confirming the bump touched nothing executable. Per `feedback_ship_skip_suite_after_review`, there is no review-impl-of-code here (no code), so `/ship` runs its own safety-net suite. Spec correctness is gated structurally by each task's `done_when` (deleted-symbol grep returns zero + surviving claims source-verified at `file:line`), not by a test.

## Open Questions

None unresolved. The one genuine scope question — reconcile all nine stale specs vs the milestone's under-enumerated four — is **settled in High-Level Design** (reconcile all; `/audit-conformance` is code-only so no other mechanism fixes spec drift) and flagged for PO confirmation at Gate 1, not deferred.

## Decisions

C1: PO `approve / Blocking: none`; Core Dev `revise / Blocking: CD-M-1, CD-M-2, CD-M-3`. C2: Core Dev `approve / Blocking: none` — all three blockers source-verified resolved. Convergence at C2 (PO not re-run; the C1→C2 changes expand the stale-spec inventory and harden the done_when greps, strengthening rather than contradicting PO's "reconcile all stale specs" approval).

| Issue | Decision | Rationale | Change |
|-------|----------|-----------|--------|
| CD-M-1 | adopt | The milestone-named grep missed `run_turn(`/`orchestrate.py`: `01-system.md:264,286` cites the **deleted file** `agent/orchestrate.py`; `skills.md`/`sessions.md`/`uat_evals.md` carry bare stale `run_turn`. | Context += "Secondary stale set" table; Scope `In` += the four; Scope `Out` clean-list corrected; new **TASK-8** reconciles them (grep widened to `run_turn\b`/`orchestrate.py` per CD-m-4). |
| CD-M-2 | adopt | TASK-5's grep matched no prose; the `@agent.instructions`→`assemble_instructions` reframe is conceptual drift a deleted-symbol grep is structurally blind to. | TASK-5 `done_when` → positive assertion (`assemble_instructions` present ≥1) + manual-inspection clause over the ~12+2 live SDK-registration refs. |
| CD-M-3 | adopt | `AgentRunResult`/`WrapperModel` are deleted from source yet appear across the heavy specs, uncaught; `WrapperToolset` survives legitimately as `_SequentialMCPToolset`'s base (`co_cli/agent/mcp.py:19`). | `AgentRunResult\|WrapperModel` added to TASK-1/2/4 greps + a manual-inspection carve-out keeping the MCP `WrapperToolset` mention. |
| CD-m-1 | adopt | The sdk-coupling plan carries live actionable TASK-1/TASK-2 (beyond the S1–S6 census) whose work shipped at cutover (`ToolCapState` `turn_state.py:60`; `compact_messages(deps)` `compaction.py:321`). | TASK-6 body widened to retire the plan's own TASK-1/TASK-2 as landed-at-cutover. |
| CD-m-2 | adopt | Folds into CD-M-3 (pydantic-ai-integration needs the wrapper-concept additions). | TASK-2 grep gained `AgentRunResult` + the `WrapperModel`/`WrapperToolset` split. |
| CD-m-3 | adopt | Two unsynced `/sync-doc` invocations risk a `turn ⊇ step` vocabulary skew; the `dispatch_tools` Context cite was `:238`. | TASK-7 `done_when` += cross-spec `grep -c 'turn ⊇ run' …` zero in both; Context `dispatch_tools` → `:234`. |
| CD-m-4 | adopt | TASK-8's `run_turn\(` clause was paren-anchored, missing the paren-less `run_turn` in `sessions.md:78`/`uat_evals.md:208`. | TASK-8 `done_when` grep broadened to `run_turn\b`. |
| PO-m-4 | adopt | The §2.1 merge must read as present-tense architecture, not phase history — the one live layer-violation risk. | TASK-1 + TASK-2 bodies += "present-tense, no formerly/now phrasing" instruction. |
| PO-m-1, PO-m-2, PO-m-3, PO-m-5 | noted | Keep-as-is confirmations: the 4→9 expansion is correct on merit (`/audit-conformance` is code-only); the SDK-reach "review-not-fix" cut is right; a Gate-1 cycle is warranted (per-phase re-entry + scope call); the `0.9.0` capstone bump correctly overrides even/odd patch parity. | — |

## Final — Team Lead

Plan approved — PO `Blocking: none` (C1), Core Dev `Blocking: none` (C2).

This is the **final phase** of the loop-decoupling milestone. Phase 6 is **docs + a version string only — zero runtime behavior change**. Execution is not an `/orchestrate-dev` code pass: the spec reconciliation (TASK-1..TASK-5, TASK-8) runs as direct **`/sync-doc`** invocations (specs are `sync-doc`'s domain, so no task lists `docs/specs/` in `files:`); TASK-6 refreshes the build-time `sdk-coupling-cleanup` plan; TASK-7 is **`/ship`** (`0.9.0` bump + CHANGELOG + archive the milestone, design, and this phase plan).

> Gate 1 — PO + TL review required before proceeding.
> Review this plan: **right problem? correct scope?** The load-bearing call is the **scope expansion** (the milestone named four specs; the cutover left deleted symbols documented across thirteen — nine primary + four secondary — and `/audit-conformance` is code-only, so nothing else will ever reconcile them). The recommendation is **reconcile all now**; shipping `0.9.0` with `01-system.md` citing a deleted file is the worst outcome.
> Once approved, execute: `/sync-doc` per reconciled spec (TASK-1..5, 8) → refresh the `sdk-coupling-cleanup` plan (TASK-6) → `/ship loop-decoupling-phase6` (TASK-7).

## Delivery — Phase 6 complete (2026-06-30, `0.9.0`)

Gate 1 PASS (independent source-verification of every anchor; two minor fixes applied pre-execution — the `_SequentialMCPToolset` path corrected to `co_cli/agent/mcp.py:19`, and two dead `_execute_run` source comments fixed). Executed via direct `/sync-doc` reconciliations (specs are not staged in `files:`).

- ✓ **TASK-1** — `core-loop.md` reconciled to `turn ⊇ step`; design §2.1 merged as present-tense canonical *why*. Deleted-symbol grep zero.
- ✓ **TASK-2** — `pydantic-ai-integration.md` reframed to provider + message library; §2.1 framing half carried; `_output.OutputToolset` + `make_run_context` reaches documented; surviving `_SequentialMCPToolset(WrapperToolset)` kept. Grep zero.
- ✓ **TASK-3** — `agents.md` build/run mechanics reconciled; R3 delegation-divergence subsection added with peer counts. Grep zero.
- ✓ **TASK-4** — `observability.md` / `tools.md` / `compaction.md` span seams + tool-dispatch + L0 cap relocated to `model_turn` / `dispatch_tools` / `ToolCapState`. Grep zero.
- ✓ **TASK-5** — `prompt-assembly.md` / `personality.md` / `tui.md` instruction mechanism reframed to per-step `assemble_instructions()`; cache-stability invariant retained + source-verified. `_execute_run` zero; `assemble_instructions` present (12).
- ✓ **TASK-6** — `sdk-coupling-cleanup` plan refreshed: S1–S6 retired (S2/S6 landed at cutover), two surviving reaches inventoried with verdicts (R-A `OutputToolset` actionable; R-B `make_run_context` forced-no-action). Stays active.
- ✓ **TASK-8** — `01-system.md` (deleted-file `orchestrate.py` citation removed) + `skills.md` / `sessions.md` / `uat_evals.md` `run_turn` → `run_turn_owned`. Grep zero.
- ✓ **TASK-7** — `0.9.0` bump + CHANGELOG; milestone metaplan + design artifact + this phase plan archived to `completed/`.

**Follow-up flagged (out of scope — code-comment / audit lane):** graph-era prose remains in a handful of `co_cli/` source docstrings (notably `llm/_json_repair.py:9,136` claiming the deleted graph currently validates/dispatches; borderline `context/history_processors.py:290`, `context/prompt_text.py:95`). Most of the 27 `the graph` hits are accurate parity rationale and must stay; the genuinely-stale present-tense subset needs mechanism verification and belongs to a future `/audit-conformance` pass, not this docs+version capstone.

**Verification:** master deleted-symbol grep zero across all 13 specs; all `run_turn` → `run_turn_owned`; cross-spec `turn ⊇ run` zero in both heavy specs; `scripts/quality-gate.sh full` green (887 passed) confirming the version bump touched nothing executable.
