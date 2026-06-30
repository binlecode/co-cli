# Phase 5.6 — Design-aware hardening of the loop-decoupling milestone surface (the delta `/audit-conformance` can't produce)

**Parent milestone:** `2026-06-24-234633-loop-decoupling-milestone.md`. Inserted **after Phase 5.5** (persona-mode selector, shipped v0.8.508) and **before Phase 6** (spec sync + `0.9.0`). Sequencing: `5.6 (design-aware delta)` → ship → **`/audit-conformance` (the mechanical R1–R12 sweep, now non-overlapping)** → its emitted `rules-conformance-cleanup` plan → `Phase 6 (spec sync + 0.9.0)`. You harden, then sweep, then canonize — `0.9.0` stamps a settled structure.

## Context

The loop-decoupling milestone rewrote co's most safety-critical code — the agent turn — across **nine phases** (1 → 5.5), with heavy relocation (symbols re-homed, the entire graph path deleted, error/recovery/length-retry moved into the owned loop, a write-capable in-turn delegation surface added). `/review-impl` validated each phase **diff-scoped** and is blind to cross-phase accretion (`review.md:30`; `project_architecture_erosion_tension`). No one has reviewed the resulting structure **as a whole**.

**Why this is a distinct phase and not just `/audit-conformance` (the load-bearing scoping decision, PO-M-1):** `/audit-conformance` already *is* the mechanical rule engine — it scans `co_cli/` against `review.md` + `code-conventions.md` (one-sided members, wrong home, underscore leaks, dead code, DRY, import-side-effects, wrapper bags), **takes a scope argument**, and emits a `file:line`+rule-cited cleanup plan through the same Gate-1 flow (`audit-conformance/SKILL.md:4,9,49`). Re-deriving that engine in this plan for production code would be pure duplication, and then scheduling a whole-tree audit afterward would re-scan the same surface. **So 5.6 owns only the delta audit-conformance structurally cannot produce:**
1. **Scaffolding-tenet conformance + cutover graph-holes** — a *milestone-specific design judgment* (the tenet lives at `milestone:204`, not in any generic rule): does the owned loop honor "orchestrator and subagent drivers share scaffolding, differ only by workflow; any new divergence is a smell," and did the cutover leave graph-shaped holes (orphaned branches/fields/threading)? A cold R1–R12 scan cannot make this call.
2. **Eval/test surface conformance** — audit-conformance scans `co_cli/` only; it never reads `evals/` or `tests/`. The milestone's eval changes (the new direct-`run_standalone_owned` callers) need `testing.md` review (functional-only, no eval-driven API, centralized settings).
3. **Cross-boundary visibility adjudication** — the underscore contract's *both-directions* call requires seeing which owned-loop `_private` symbols are imported *from* `tests/`/`evals/` — a view audit-conformance lacks because it doesn't scan those trees.

**The mechanical dims (structure/modularity/boundary/shape/dead-code/DRY over production code) are explicitly NOT 5.6's** — they are the post-ship `/audit-conformance` run (the user's "followed-by" step), now genuinely non-redundant because 5.6 deliberately does not pre-do them.

**Seed findings already source-verified (the audit is not planning against imagined space):**
- **Cutover graph-hole / dead state (dim 1 — strongest evidence):** the old graph-path tool-cap blackboard fields `tool_calls_in_model_request`, `consecutive_tool_cap_violations`, `tool_cap_hard_stop` (`co_cli/deps.py:188,192,196`) are zeroed in `reset_for_turn` (`deps.py:265-267`) but **never read or written** anywhere else in `co_cli/` — the live cap moved to `turn_state.py:ToolCapState`. Textbook one-sided-field + cutover residue (`feedback_clarity_by_subtraction`).
- **Cross-boundary underscore reaches (dim 3):** `tests/test_flow_delegation.py:44,48` import `_delegate_agent_instructions`, `_build_subagent_toolset`; `tests/test_flow_owned_turn.py:21` imports `_is_reasoning_overflow`; `evals/eval_delegate_persona_mode.py:96` imports `_drive_model_request`. Each needs the both-directions adjudication: legitimate harness reach, or drop the underscore.

**Boundary with `sdk-coupling-cleanup` (settled; CD-m-1):** `docs/exec-plans/active/2026-06-24-220958-sdk-coupling-cleanup.md` is **fully obsolete** post-cutover — its two actionable axes already landed at cutover (S2 `ToolCapState` consolidation → `turn_state.py:60`, `loop.py:223,661`; S6 compaction `RunContext`→`CoDeps` → `compaction.py:322,376,464,548`, zero `RunContext` left), and S1/S2/S4/S6 cite files Phase 5 *deleted* (`llm/surrogate_recovery_model.py`, `agent/orchestrate.py`). So there is **no axis double-up** — the boundary is clean because that plan's actionable work no longer exists. The pydantic-ai axis's *residual* is the **new post-cutover SDK reaches**: `build_output_toolset`'s `pydantic_ai._output.OutputToolset` (`preflight.py:258`, a recorded "Phase-5 cleanup item") and the principal one, `make_run_context`'s synthetic `RunContext` fabrication (`dispatch.py:71`, the S6-class successor that didn't exist when sdk-coupling was written). 5.6 **routes** these to a refreshed sdk-coupling plan; it does not fix them.

## Problem & Outcome

**Problem:** the milestone's nine-phase rewrite left three structural concerns that the diff-scoped phase reviews — and a generic whole-tree audit — both structurally miss: cutover graph-holes that need milestone history to recognize, the eval surface (outside `co_cli/`), and the both-directions underscore call on owned-loop internals reached from tests/evals.

**Outcome:** those three are clean — cutover residue collapsed, the owned-loop's intended public surface settled against its cross-boundary reaches, the milestone's evals conformant to `testing.md` — recorded as a `file:line`+rule-cited ledger and fixed at source. The mechanical R1–R12 sweep of production is then handed to `/audit-conformance`, and Phase 6 spec-syncs a settled structure.

**Failure cost:** silent, and Phase-6-amplified. `0.9.0` (Phase 6) canonizes the owned loop into `core-loop.md`; if fake-private symbols, graph-hole dead state, and eval-driven seams are still present, the spec blesses them into a de-facto public/intended surface, and the next maintainer pays the erosion tax on the most safety-critical code in the system. Spec-sync freezes whatever shape it finds.

## Scope

**In (5.6 owns, fixes at source):**
- **Dim A — scaffolding-tenet + cutover graph-holes** across the milestone production surface: `co_cli/agent/{loop,delegation,preflight,recovery,turn_state,spec,core,dispatch,toolset,orchestrator}.py`, `co_cli/deps.py` (the tool-cap fields), `co_cli/llm/` (`model_turn`). Orphaned branches/fields/threading whose reason the cutover removed; unjustified orchestrator-vs-subagent driver divergence.
- **Dim B — eval/test surface vs `testing.md`:** `evals/_*.py` + the new direct-`run_standalone_owned` callers + the loop/delegation test files — functional-only assertions, no eval-driven production API, centralized settings, real data.
- **Dim C — cross-boundary visibility:** every owned-loop/delegation `_private` symbol imported from `tests/`/`evals/` — adjudicated drop-the-underscore (public) or stop-the-reach (private), both directions.

**Out:**
- **Mechanical dims 1–4 over production (structure/home/dead-code/DRY/import-side-effects/wrapper-bags)** — owned by the post-ship `/audit-conformance` run (the user's "followed-by"); 5.6 deliberately does not pre-do them, which is what makes that run non-redundant. (Dim-1 *cutover graph-holes* are 5.6's because recognizing them as cutover residue is the design judgment; generic accreted dead code is the audit's.)
- **pydantic-ai SDK-coupling axis** — `preflight.py:258` `_output.OutputToolset` and `dispatch.py:71` `make_run_context` — **routed** to a refreshed sdk-coupling plan, never fixed here (OQ-1).
- **`docs/specs/` edits** — Phase 6 (layer rule; specs sync post-delivery, never in `files:`).
- **Guard / fitness / structural tests** — forbidden (`testing.md`; `project_architecture_erosion_tension` rejected them). Fixes eliminate violations at source.
- **Behavior change** — no-behavior-change pass; the full suite green is the proof.

## Behavioral Constraints

- **No-behavior-change.** Every fix is structural; observable behavior is unchanged. Full real-LLM suite green is the proof, not new assertions.
- **Fix at source, never freeze** (`review.md:25-27`).
- **Architectural findings escalate, not auto-fix.** A finding needing a public-API restructure / module split / new dependency is escalated at the ledger gate and split to its own plan (mirrors `/review-impl` Phase 4).
- **Route, never fix, even when the target is stale (PO-m-2).** A routed SDK-coupling finding waits in the ledger as `route→sdk-coupling (pending re-ground)` and is **not** actioned in 5.6 — the "route, never fix" line holds even though the destination plan needs re-grounding first.
- **First-principles in both layers (`feedback_cutover_cleanup_first_principles`).** A graph-hole is collapsed in the eval layer too, not left half-removed.

## High-Level Design

### Method (inventory → adjudicate → fix at source)

Dims A/B/C are swept into a single `file:line`+rule-cited **Findings Ledger** appended to this plan (severity blocking/minor; disposition fix / escalate / `route→sdk-coupling (pending re-ground)`), seeded from the verified findings in Context and completed by the sweep. The ledger is the **maintainer decision gate** — read it, confirm the fix-here set, before any source change. Then a fix pass applies the minimal idiomatic source fix per confirmed-blocking finding, re-greps repo-wide for stale references, and runs the full suite green.

### Why the dims A/B/C split is the non-overlapping core

Each is something `/audit-conformance` cannot produce: Dim A needs the milestone's design intent (the tenet + cutover history) to tell a graph-hole from intentional structure; Dim B is a tree (`evals/`/`tests/`) the audit never scans; Dim C needs the cross-boundary import view (who reaches the `_private` symbol) the audit lacks. The mechanical remainder over `co_cli/` is the audit's job, run after.

### Sequencing and the `/audit-conformance` division of labour

`5.6 (dims A/B/C)` → ship → `/audit-conformance` (mechanical R1–R12 over `co_cli/`, scoped or whole-tree per its periodic cadence — `project_architecture_erosion_tension`) → its `rules-conformance-cleanup` plan → `Phase 6`. The audit is non-redundant precisely because 5.6 did not pre-do dims 1–4; it scans a tree whose highest-churn region's *design-aware* issues are already resolved.

## Tasks

### ✓ DONE TASK-1 — Inventory dims A/B/C → `file:line`+rule-cited Findings Ledger
- **files:** `docs/exec-plans/active/2026-06-29-134853-loop-decoupling-phase5-6.md` (append the ledger); audited read-only: `co_cli/agent/{loop,delegation,preflight,recovery,turn_state,spec,core,dispatch,toolset,orchestrator}.py`, `co_cli/deps.py`, `co_cli/llm/` (`model_turn`), `evals/_*.py` + `evals/eval_delegate_persona_mode.py`, the loop/delegation test files.
- Sweep dim A (scaffolding-tenet + cutover graph-holes), dim B (eval/test vs `testing.md`), dim C (cross-boundary underscore adjudication). Record each finding: `file:line`, dim, cited rule, severity, disposition (fix / escalate / `route→sdk-coupling (pending re-ground)`). Seed from Context (the dead tool-cap fields; the four underscore reaches; the two routed SDK reaches) and complete the sweep.
- **done_when:** a `## Findings Ledger` table is appended covering dims A/B/C across every named file, each row carrying `file:line` + rule + severity + disposition; the seed findings appear with their adjudication; the two SDK reaches appear as `route→sdk-coupling (pending re-ground)`.
- **success_signal:** N/A (inventory artifact for a refactor).
- **prerequisites:** none.

### ✓ DONE TASK-2 — Fix every confirmed-blocking dim-A/B/C finding at source
- **files:** the production + eval/test files named in TASK-1's ledger as confirmed-blocking fix-here (set bounded by the ledger; `escalate` / `route` rows excluded). Expected to include `co_cli/deps.py` (delete the dead tool-cap fields + their `reset_for_turn` lines) and the owned-loop/delegation modules + their test/eval importers for the dim-C adjudications.
- Apply the minimal idiomatic source fix per finding — collapse cutover graph-holes (incl. the dead tool-cap blackboard), correct underscore contracts both directions, bring evals into `testing.md` conformance, remove unjustified scaffolding divergence. No new abstractions; no behavior change. Architectural findings escalate.
- **done_when:** every confirmed-blocking ledger row resolved with a `file:line` of the fix; a **repo-wide stale-reference grep** across `co_cli/` + `tests/` + `evals/` is clean; `scripts/quality-gate.sh full` (lint + full pytest) is green.
- **success_signal:** the milestone surface honors the scaffolding tenet with no cutover residue, no fake-private symbols reached cross-boundary, and conformant evals — a *judgment* confirmed by a second cold read, **not** a green/red signal (PO-m-3): dim-A scaffolding/graph-hole conformance has no objective metric, so Gate 2 reads the ledger + the cold-read confirmation, not a test result.
- **prerequisites:** TASK-1 (ledger + maintainer gate).

## Testing

No new tests (structural pass; guard/fitness tests forbidden — `testing.md`, `project_architecture_erosion_tension`). Behavior-preservation proof = the **existing full suite green** after TASK-2 + the **repo-wide stale-reference grep** (the rename/re-home discipline, `review.md:19`). Run piped to a timestamped `.pytest-logs/` log, spans tailed; RCA any failure to root cause (a structural fix that reddens a test changed behavior — revert and re-scope; never edit the test to pass unless the symbol it names was legitimately re-homed). Dim A is a judgment call, not a measurement (PO-m-3) — recorded in the ledger, confirmed by cold read, not gated by a signal. Post-ship `/audit-conformance` is the independent mechanical verification of the production remainder.

## Open Questions

- **OQ-1 — refresh the `sdk-coupling-cleanup` plan post-cutover, then feed it the routed reaches.** That draft is obsolete (S2/S6 landed; cites deleted files). Its real residual is the **new post-cutover SDK reaches** 5.6 routes: `preflight.py:258` `_output.OutputToolset` and the principal one, `dispatch.py:71` `make_run_context` (synthetic `RunContext`). **Re-raise trigger:** when 5.6's ledger routes ≥1 SDK reach (it will — both seeds), and before Phase 6 so `0.9.0` does not stamp an unreviewed SDK boundary. Not 5.6's scope to resolve — the routed findings wait `pending re-ground`.

## Next step

Gate 1 (PO + TL): right problem? correct scope? Is the dims-A/B/C delta genuinely the non-overlapping core, with the mechanical remainder cleanly left to the post-ship `/audit-conformance`? Then `/orchestrate-dev loop-decoupling-phase5-6` (TASK-1 inventory → maintainer reads the ledger → TASK-2 fixes the confirmed-blocking set), then run `/audit-conformance` for the production remainder, then Phase 6.

## Findings Ledger (TASK-1 — 2026-06-29)

Swept dims A/B/C across every named file. Dim-A production scan (10 agent modules + `deps.py` + `llm/model_turn`) found the milestone's owned loop structurally clean **except** the dead tool-cap blackboard; dim-B found the milestone's new eval surface fully conformant with two minor borderline-structural test assertions; dim-C surfaced the 4 seeded underscore reaches for both-directions adjudication. The two SDK reaches are routed, not fixed.

| # | Dim | `file:line` | Finding | Rule | Sev | Disposition |
|---|-----|-------------|---------|------|-----|-------------|
| A1 | A | `deps.py:188` (+ comment 186-187, reset `:267`) | `tool_calls_in_model_request` write-only; only write is `reset_for_turn`, zero reads in `co_cli/`. Comment falsely claims "zeroed by the owned loop (agent/loop.py)" — that writer was the deleted graph path. Live counter is `turn_state.py:ToolCapState`. | `feedback_clarity_by_subtraction` (one-sided field); cutover graph-hole | **blocking** | **fix** — delete field + comment + `reset_for_turn` line |
| A2 | A | `deps.py:192` (+ comment 189-191, reset `:265`) | `consecutive_tool_cap_violations` write-only; only write is `reset_for_turn`, zero reads. Comment claims reset "when a non-violating CallToolsNode fires" — `CallToolsNode` was the deleted graph node. | same | **blocking** | **fix** — delete field + comment + reset line |
| A3 | A | `deps.py:196` (+ comment 193-195, reset `:266`) | `tool_cap_hard_stop` write-only; only write is `reset_for_turn`, zero reads. Comment claims "Read by the orchestrator" — the reader was the graph path. | same | **blocking** | **fix** — delete field + comment + reset line |
| A4 | A | `loop.py` / `delegation.py` (whole milestone surface) | **Scaffolding tenet HONORED** — no unintended orchestrator-vs-subagent driver divergence, no dead branches, no threaded-but-unused params. The only divergences (subagent `final_result` loop, `frontend=None`/`renderer=None`, no `request_limit`) are documented workflow differences (`loop.py:11-22`). | tenet `milestone:~204` | — | **clean** — no action (dim-A cold-read confirmation, PO-m-3) |
| C1 | C | def `loop.py:135`; reach `evals/eval_delegate_persona_mode.py:96,605` | `_drive_model_request` — the module docstring's named "shared per-step model-request primitive" used by both drivers; the eval reaches it for a faithful live single-step decision. Public-grade. | `feedback_underscore_visibility_contract` | **blocking** | **fix** — drop underscore → `drive_model_request` |
| C2 | C | def `loop.py:568`; reach `tests/test_flow_delegation.py:48,170,229,250,310,312` | `_build_subagent_toolset` — documented mirror (`loop.py:14`) of the **public** `build_native_toolset`; symmetry says its subagent twin is public too. | same | **blocking** | **fix** — drop underscore → `build_subagent_toolset` |
| C3 | C | def `loop.py:117`; reach `tests/test_flow_owned_turn.py:21,48,50,55,57` | `_is_reasoning_overflow` — pure `ModelResponse→bool` classifier; the test pins its truth table (observable input→output behavior). Public-grade predicate. | same | **blocking** | **fix** — drop underscore → `is_reasoning_overflow` |
| C4 | C | def `delegation.py:89`; reach `tests/test_flow_delegation.py:44,265,271,284,292,305`; **eval deliberately avoids it** (`eval_delegate_persona_mode.py:26,308`) | `_delegate_agent_instructions` — the genuine both-directions split: the eval goes through the public spec (its CD-m-5 / `feedback_no_eval_test_driven_api` discipline), but the tests reach the private builder to assert instruction content. **TL/PO lean: drop underscore** — it is a genuine production function (used `delegation.py:126,179`), and making it public legitimizes the test reach without forcing the eval off its public-spec path. Alternative: stop-the-reach (tests assert via the public delegate surface). | `feedback_underscore_visibility_contract` vs `feedback_no_eval_test_driven_api` | **blocking** | **fix (maintainer-confirm)** — drop underscore → `delegate_agent_instructions`, *or* stop-the-reach if maintainer prefers honoring the eval's avoidance |
| B1 | B | `tests/test_flow_delegation.py:143,146` | `tool_dispatch_sem is/is not parent` — object-identity asserts verifying fork semaphore isolation. Borderline structural, but identity is the mechanism for a load-bearing isolation/deadlock contract with no clean functional alternative. | `feedback_functional_tests_only` | minor | **keep (maintainer-discretion)** — lean keep; the behavioral alternative (provoke deadlock) is impractical/flaky |
| B2 | B | `tests/test_flow_delegation.py:195` | `tools[name].tool_def.sequential is True` — asserts a tool-def field value, not that sequential tools run sequentially. Structural. | `feedback_functional_tests_only` | minor | **maintainer-discretion** — rewrite to assert sequential-execution behavior, or cut as Low-criticality (`feedback_test_criticality_gate`) |
| B3 | B | `evals/eval_delegate_persona_mode.py` + `evals/_settings.py`/`_deps.py`/`_timeouts.py` | **Eval surface CLEAN** — centralized settings, no eval-driven production API, real data, no mocks/fakes/cleanup. The milestone's new `run_standalone_owned`/`_drive_model_request` eval callers are `testing.md`-conformant. | `testing.md` | — | **clean** — no action |
| R1 | (route) | `preflight.py:258` | `pydantic_ai._output.OutputToolset.build` reach (recorded Phase-5 cleanup item). | SDK-coupling axis (OQ-1) | — | **route→sdk-coupling (pending re-ground)** — not actioned in 5.6 |
| R2 | (route) | `dispatch.py:51` `make_run_context` (**plan cites `:71`; actual def `:51`**) | synthetic `RunContext` fabrication — the S6-class post-cutover successor; the principal routed SDK reach. | SDK-coupling axis (OQ-1) | — | **route→sdk-coupling (pending re-ground)** — not actioned in 5.6 |

**Confirmed fix-here set (blocking):** A1, A2, A3 (delete dead fields) + C1, C2, C3 (drop underscore) + C4 (drop underscore, pending maintainer confirm of disposition). **Maintainer-discretion (minor):** B1, B2. **Routed, not actioned:** R1, R2. **No escalations** — every fix is a delete or a rename; none needs a public-API restructure / module split / new dependency.

> **Maintainer gate (TASK-1 → TASK-2):** confirm the fix-here set and the C4 disposition before any source change.

## Delivery Summary — 2026-06-29

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `## Findings Ledger` appended, dims A/B/C × every file, each row `file:line`+rule+sev+disposition; seeds adjudicated; SDK reaches routed | ✓ pass |
| TASK-2 | every confirmed-blocking row resolved; repo-wide stale-ref grep clean; lint green; scoped flow tests green | ✓ pass |

**Maintainer gate:** confirmed the fix-here set; C4 disposition = **drop underscore** (publicize `delegate_agent_instructions`).

**Fixes applied (all confirmed-blocking):**
- **A1/A2/A3** — deleted the 3 dead tool-cap blackboard fields (`tool_calls_in_model_request`, `consecutive_tool_cap_violations`, `tool_cap_hard_stop`) + their stale comments + their 3 `reset_for_turn` lines (`deps.py`). They were write-only cutover residue with comments falsely claiming graph-path readers the cutover deleted; the live cap is `turn_state.py:ToolCapState`.
- **C1–C4** — dropped the underscore on 4 cross-boundary-reached owned-loop/delegation primitives: `_drive_model_request`→`drive_model_request`, `_build_subagent_toolset`→`build_subagent_toolset`, `_is_reasoning_overflow`→`is_reasoning_overflow` (`loop.py`), `_delegate_agent_instructions`→`delegate_agent_instructions` (`delegation.py`). All callers in `co_cli/`, `tests/`, `evals/` updated (word-boundary rename; the `test_delegate_agent_instructions_*` test name was correctly preserved).

**Not actioned (per scope):** B1/B2 (minor, maintainer-discretion — left as-is, non-blocking); R1 `preflight.py:258`, R2 `dispatch.py:51` (routed→sdk-coupling, pending re-ground). A4/B3 were clean (no action). No escalations — every fix was a delete or rename.

**Tests:** scoped — 27 passed, 0 failed (`tests/test_flow_{owned_turn,delegation,model_request_cap}.py`; real-LLM turns warm, no stalls). Import-resolution smoke green. Full suite deferred to `/review-impl`.
**Doc Sync:** skipped by design — `docs/specs/` edits belong to Phase 6 (plan Scope/Out layer rule).

**Overall: DELIVERED**
All confirmed-blocking dim-A/B/C findings fixed at source; the owned-loop surface honors the scaffolding tenet with no cutover residue and no fake-private symbols reached cross-boundary; eval surface conformant. No behavior change (renames + dead-field deletes only). The dim-A "graph-hole conformance" judgment (PO-m-3) is recorded in the ledger + confirmed by the cold structural scan, not a green/red signal.

**Next step:** `/review-impl loop-decoupling-phase5-6` — full suite + evidence scan + behavioral verification → verdict appended. Then `/audit-conformance` (mechanical R1–R12 over `co_cli/`), then Phase 6.

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| PO-M-1 | adopt | Dims 1–4 of the first draft were a 1:1 restatement of `/audit-conformance`'s R1–R12 engine (which takes a scope arg, `SKILL.md:4,9,49`), and a whole-tree audit was then scheduled to re-scan the same surface — pure duplication. Only the design-aware delta (scaffolding-tenet/graph-holes, eval surface, cross-boundary visibility) is something the audit structurally cannot produce. | Whole-plan re-scope: dims reframed to A/B/C (the delta); mechanical dims 1–4 over production moved to Scope/Out → the post-ship `/audit-conformance` (the "followed-by"); Context "why a distinct phase" rewritten; Tasks/Testing re-shaped to dims A/B/C; sequencing notes the audit is non-redundant because 5.6 doesn't pre-do dims 1–4. |
| PO-m-1 | adopt | Sequencing inversion (scoped pass first, redundant whole-tree audit second) resolves once PO-M-1 lands; 5.6-before-Phase-6 is correct (the spec-sync-freezes-drift failure cost is real and load-bearing). | Folded into the PO-M-1 re-scope; sequencing section keeps 5.6 before Phase 6 with the failure-cost rationale. |
| PO-m-2 | adopt | "Route, never fix" must hold even though the destination sdk-coupling plan is stale, or orchestrate-dev might silently fix a routed SDK finding. | Behavioral Constraints: routed findings carry disposition `route→sdk-coupling (pending re-ground)` and are not actioned; OQ-1 + TASK-1 done_when reflect it. |
| PO-m-3 | adopt | Dim A (scaffolding/graph-holes) has no objective signal beyond cold-read judgment; Gate 2 must not expect a green/red result for it. | TASK-2 `success_signal` + Testing name dim A as a judgment call confirmed by cold read, not a measurement. |
| CD-m-1 | adopt | sdk-coupling-cleanup is not merely stale but fully obsolete — both actionable axes (S2 ToolCapState, S6 compaction RunContext) already landed at cutover; remaining items cite deleted files. Stating it precisely strengthens the clean-boundary claim. | Context boundary paragraph rewritten: S2/S6 cited as already-landed (`turn_state.py:60`, `compaction.py:322,376,464,548`); the axis's residual is the new post-cutover reaches only. |
| CD-m-2 | adopt | The dead tool-cap blackboard fields are the single strongest existing-evidence finding and make the premise concrete. | Context seed findings: `deps.py:188,192,196` + `reset_for_turn:265-267` named as the lead dim-A cutover graph-hole; TASK-2 files/expectation name `co_cli/deps.py`. |
| CD-m-3 | adopt | The eval underscore reach (`_drive_model_request`, `evals/eval_delegate_persona_mode.py:96`) is a 4th cross-boundary reach confirming dim C carries eval-surface work. | Added to the dim-C seed reach list in Context + TASK-1. |
| CD-m-4 | adopt | `make_run_context` (`dispatch.py:71`) is the principal new post-cutover synthetic-`RunContext` reach (S6-class successor), more substantial than the `_output` reach; naming it sharpens the dedup line. | Context + Scope/Out + OQ-1 name `dispatch.py:71 make_run_context` as the principal routed SDK reach. |
| CD-m-5 | acknowledge | TASK-1's "ledger appended" done_when has a structurally-unavoidable soft edge for any inventory, adequately compensated by TASK-2's machine-checkable done_when (grep + `quality-gate.sh full`) and the cold-read success_signal. | No change; noted that the completeness backstop is on TASK-2 + post-ship audit. |

## Final — Team Lead

Plan approved — Core Dev `Blocking: none` (C1); PO `Blocking: PO-M-1` (C1) **adopted verbatim as recommended** (re-scope to the design-aware delta; hand the mechanical R1–R12 remainder to the post-ship `/audit-conformance`), so convergence is reached at C1 without a counter-proposal gap.

The load-bearing result: **5.6 is not a second audit-conformance.** It owns only the three things a generic whole-tree rule scan structurally cannot produce — cutover graph-holes (needs milestone history), eval-surface conformance (outside `co_cli/`), and the both-directions underscore call on owned-loop internals reached from tests/evals. The mechanical structure/home/dead-code/DRY sweep over production is the post-ship `/audit-conformance` run (the user's "followed-by"), now genuinely non-redundant. The pydantic-ai SDK axis (`_output.OutputToolset`, `make_run_context`) is routed to a refreshed sdk-coupling plan, never fixed here. 5.6 → audit-conformance → Phase 6.

> Gate 1 — PO + TL review required before proceeding.
> Review this plan: **right problem? correct scope?** The load-bearing risk was 5.6 duplicating `/audit-conformance`; the plan now carves 5.6 down to the non-overlapping design-aware delta (dims A/B/C) and hands the mechanical remainder to the post-ship audit.
> Once approved, run: `/orchestrate-dev loop-decoupling-phase5-6`.

### Gate 1 decision (2026-06-29) — PASS (TL + PO)

All load-bearing seed findings source-verified before sign-off:
- Dead tool-cap fields `deps.py:188,192,196` are written only in `reset_for_turn:265-267`, never read in `co_cli/`; the live cap is `turn_state.py:60 ToolCapState` (`loop.py:223,661`) — confirmed one-sided cutover residue.
- All four dim-C underscore reaches confirmed in `tests/`/`evals/` (`_is_reasoning_overflow`, `_build_subagent_toolset`, `_delegate_agent_instructions`, `_drive_model_request`).
- Both routed SDK reaches confirmed (`preflight.py:258 _output.OutputToolset`; `make_run_context` — note actual def is `dispatch.py:51`, not `:71` as cited; cosmetic drift, routed not fixed, harmless).
- Scaffolding tenet present in the milestone's "Post-3.6 sequencing … scaffolding tenet (2026-06-27)" section.

**Right problem:** yes — the three concerns are invisible to both diff-review and a generic whole-tree audit, and Phase-6 spec-sync would freeze the drift, making the fix-before-canonize ordering load-bearing.
**Correct scope:** yes — the PO-M-1 carve (dims A/B/C in; mechanical R1–R12 / SDK axis / specs / guard-tests out) holds under scrutiny. Two acknowledged soft edges (the dead-fields finding mechanically overlaps the later audit but is fixed first, no double-work; dim A has no objective gate beyond cold read per PO-m-3) are inherent and non-blocking.

Approved → `/orchestrate-dev loop-decoupling-phase5-6`.
