# REVIEW: flow/bootstrap — Startup Flow Ownership
_Date: 2026-03-07_

## Executive Summary

This review assesses the startup documentation boundary across:

- `docs/DESIGN-flow-preflight.md`
- `docs/DESIGN-flow-bootstrap.md`
- `docs/DESIGN-doctor.md`

against the current implementation in:

- `co_cli/main.py`
- `co_cli/_preflight.py`
- `co_cli/_bootstrap.py`
- `co_cli/_doctor.py`
- `co_cli/_status.py`
- `co_cli/tools/capabilities.py`

Headline:

- `preflight` still exists in code, but it is too narrow to remain the primary startup flow doc.
- `preflight` should be simplified to a model dependency check, not treated as a full startup flow.
- `bootstrap` is the correct doc to own the startup sequence from post-`create_deps()` through readiness for the first user turn.
- `doctor` should remain a reusable diagnostic tool/subsystem callable by both system-owned flows and agent-owned troubleshooting flows.
- The current doc set over-separates startup: readers must mentally stitch together `preflight`, `bootstrap`, and `doctor` to understand one startup path.

Recommended direction:

- Use this review as the opening document for a refactoring flow that a tech lead can take forward into planning and delivery.
- Make `docs/DESIGN-flow-bootstrap.md` the canonical startup flow doc.
- Fold the current preflight flow into bootstrap as a dedicated fail-fast stage.
- Simplify `preflight` to a narrow model dependency check: provider credentials, Ollama reachability, and reasoning/model-chain viability before agent creation.
- Keep `doctor` as a reusable diagnostic capability and document it accordingly.
- Retire `docs/DESIGN-flow-preflight.md` as a top-level flow doc after its content is merged into bootstrap.

---

## Scope and Method

Date of review: 2026-03-07 (local)

Method:

- Read the startup call path in `chat_loop()`.
- Compare actual sequencing with `DESIGN-flow-preflight.md` and `DESIGN-flow-bootstrap.md`.
- Trace all `run_doctor()` callsites to determine whether doctor is a flow, a component, or both.
- Check whether the design index and core docs currently point readers at the right startup document.

Primary code evidence:

- `co_cli/main.py`
- `co_cli/_preflight.py`
- `co_cli/_bootstrap.py`
- `co_cli/_doctor.py`
- `co_cli/_status.py`
- `co_cli/tools/capabilities.py`

Primary doc evidence:

- `docs/DESIGN-flow-preflight.md`
- `docs/DESIGN-flow-bootstrap.md`
- `docs/DESIGN-doctor.md`
- `docs/DESIGN-core.md`
- `docs/DESIGN-index.md`

---

## Refactoring Objective

This review is intended to initiate a refactoring flow for tech lead review, planning, and delegation.

It is not just assessing doc drift. It is defining the design intention the team should align on before planning implementation work.

Primary objective:

- simplify startup architecture around `bootstrap` as the single canonical startup flow

Secondary objectives:

- simplify `preflight` to a model dependency check
- preserve `doctor` as a reusable diagnostic tool available to both system-owned and agent-owned execution paths
- reduce startup documentation fragmentation so future changes land in the correct owning doc

Success condition for this review:

- a tech lead should be able to use it to confirm target architecture, decide scope, and break the work into planning/delivery tasks for the team

---

## Target Architecture

The target model should be:

1. `bootstrap` is the canonical startup flow
2. `model dependency check` is an internal bootstrap/startup stage
3. `doctor` is a reusable diagnostic subsystem/tool

In practical terms:

- startup questions route to `DESIGN-flow-bootstrap.md`
- model/provider viability is described inside bootstrap as a narrow gating stage
- diagnostic health checks remain reusable from multiple callsites and are not framed as startup ownership

Desired startup narrative:

1. frontend and deps are created
2. model dependency check runs and may fail fast
3. startup continues with task runner, skills, agent creation, MCP startup/fallback
4. bootstrap initialization runs
5. doctor reports broader system/integration health
6. prompt loop begins

This target architecture preserves the good runtime boundary while removing unnecessary conceptual layering.

---

## Current Startup Reality

## 1) Actual startup sequence in code

The real startup path is spread across `main.py`, `_preflight.py`, and `_bootstrap.py`.

Observed order in `chat_loop()`:

1. Construct `TerminalFrontend`
2. Call `create_deps()`
3. Set `deps.config.skills_dir`
4. Run `run_preflight(deps, frontend)`
5. Create and inject `TaskRunner`
6. Load skills and populate `deps.session.skill_registry`
7. Create agent via `get_agent(...)`
8. Enter agent context and perform MCP fallback handling
9. Run `run_bootstrap(...)`
10. Enter the prompt loop

This means the startup experience is not “preflight” and not “bootstrap” in isolation. It is a larger wakeup/startup sequence whose two explicitly named phases are:

- a narrow pre-agent gate
- a broader bootstrap initialization sweep

### Assessment

If a reader asks, “what happens at startup before the first prompt?”, `DESIGN-flow-preflight.md` is not the correct primary answer. It covers only Step 4 above.

`DESIGN-flow-bootstrap.md` is much closer to the right ownership boundary because it already describes later startup work that a user actually experiences:

- knowledge sync
- session restore/new session
- skills loaded report
- integration health sweep

The missing piece is that bootstrap does not yet fully absorb the earlier startup stages around preflight, skill load ordering, and `get_agent()`/MCP connect fallback.

---

## 2) What preflight does today

`run_preflight()` is narrow and mechanical:

- validate provider configuration
- probe Ollama reachability
- verify local model availability for configured roles
- prune configured model chains when installed models are missing
- raise `RuntimeError` on blocking failure
- emit warnings through `frontend.on_status()` on degraded but non-fatal states

What it does not do:

- no knowledge sync
- no session restore
- no skills load
- no MCP initialization
- no integration sweep beyond LLM/provider/model concerns
- no final “startup readiness” ownership

### Assessment

This is a valid subflow, but not a full startup flow. It is a fail-fast gate, not the startup narrative.

The strongest conceptual distinction is:

- `preflight`: “Can the agent be created safely with a viable reasoning model?”
- `bootstrap`: “Now that startup can proceed, initialize runtime state and report integration health.”

That distinction is real in code and worth preserving, but it does not require two peer top-level startup flow docs.

---

## 3) What bootstrap does today

`run_bootstrap()` is the only named phase that performs user-visible startup initialization after the agent can be created.

It currently owns:

- knowledge sync
- session restore/new session
- skills loaded status line
- doctor integration sweep

In practice, startup understanding already spills into this doc because `DESIGN-flow-bootstrap.md` also documents:

- skills load before bootstrap
- knowledge backend resolution before bootstrap

### Assessment

This is already acting like the startup flow doc in everything but name and declared ownership. The right move is to complete that transition rather than preserve `preflight` as a co-equal startup flow doc.

---

## 4) What doctor should be in the architecture

`doctor` should not be treated as “the thing bootstrap happens to call once.”

It is more useful and more accurate to treat it as a reusable diagnostic tool/subsystem that can be called from different layers when there is a real troubleshooting need.

Current code already shows that shape:

- bootstrap uses it for startup integration health
- `co status` uses it outside the agent runtime
- `check_capabilities` uses it inside the agent runtime

That boundary is valuable and should be preserved intentionally.

### Preferred design role

`doctor` should remain callable from both:

- system-owned flows
  - startup/bootstrap
  - status/health CLI surfaces

- agent-owned flows
  - explicit `/doctor` usage
  - troubleshooting during runtime when an agent sees degraded behavior, missing integration health, or timeouts worth investigating

### Assessment

This is a strong reason to keep doctor as a real component/subsystem in the design. It is not just documentation residue from a startup refactor. It serves a cross-cutting operational role.

What should change is not its existence, but its framing:

- not a startup flow owner
- not a peer to bootstrap
- a reusable diagnostic capability with multiple callers

---

## Doc Findings

## 1) `DESIGN-flow-preflight.md` is accurate about code existence but wrong as the top-level startup abstraction

The document correctly describes that `run_preflight()` still exists and still runs before `get_agent()`.

However, it overstates its place in the startup model by implicitly standing in for “startup validation” as a standalone peer flow. That was a reasonable shape when preflight was closer to “all startup checks.” It is no longer accurate after doctor/bootstrap split responsibilities.

Why this matters:

- a new contributor looking for “startup flow” is sent to both preflight and bootstrap from `DESIGN-index.md`
- the preflight doc reads like the authoritative startup check surface
- the actual code now has a second health stage in bootstrap via `run_doctor(deps)`

Result:

- readers must merge two docs to understand startup
- the docs do not present a single owner for startup sequencing
- the separation is by implementation artifact, not by reader task

Severity: medium

---

## 2) `DESIGN-flow-bootstrap.md` is the better owner for startup, but it still underspecifies earlier stages

The bootstrap doc already contains the broadest and most helpful startup narrative. It includes both actual bootstrap behavior and some pre-bootstrap setup.

That is strong evidence that the repository already wants this doc to serve as the startup guide.

What is still missing for it to become the canonical startup doc:

- an explicit “full startup sequence” section beginning at `create_deps()`
- preflight as a nested stage inside startup, not an external peer flow
- `get_agent()` creation and MCP fallback as part of startup sequencing
- a clear blocking vs non-blocking boundary:
  - preflight blocks startup on failure
  - bootstrap/doctor never block startup

Severity: low

---

## 3) `DESIGN-doctor.md` documents a real component, not a startup flow

`run_doctor()` is used in three distinct contexts:

- bootstrap Step 4
- `check_capabilities` tool
- `co status` via `get_status()`

That means doctor is not just a startup step. It is a reusable integration health module shared across runtime and non-runtime callsites.

This is the strongest argument for keeping a separate doctor doc:

- shared schema: `CheckItem`, `DoctorResult`
- shared entry point: `run_doctor(deps=None)`
- shared check family: `check_google`, `check_obsidian`, `check_brave`, `check_mcp_server`, `check_knowledge`, `check_skills`
- multiple consumers with different runtime context availability

### Assessment

There is no need for a separate “doctor flow” doc.

There is value in a doctor component doc because doctor is intended to remain a reusable diagnostic tool callable by both system and agent surfaces. The current `DESIGN-doctor.md` fits that pattern better than a flow doc would.

Severity: low

---

## 4) The design index currently points readers at an outdated startup boundary

`DESIGN-index.md` currently answers:

- “What runs at startup before the first user message?”
  with:
  - `DESIGN-flow-preflight.md`
  - `DESIGN-flow-bootstrap.md`

This is technically workable but poor information architecture. For the reader’s question, the better answer is one startup doc with internal stages.

### Assessment

The index should point startup readers primarily to `DESIGN-flow-bootstrap.md` after that doc is expanded to own the full wakeup sequence.

Severity: medium

---

## 5) There is some factual drift that reinforces the need to consolidate

Observed drift:

- docs still reference `co_cli/status.py` while the file is `co_cli/_status.py`
- preflight doc’s ownership language implies startup checks belong in `_preflight.py` only
- preflight doc lags implementation details such as optional `summarization` role pruning

None of these are severe on their own. Together, they show that the narrower a doc boundary gets, the easier it is for the repository to accumulate stale claims around adjacent responsibilities.

Severity: low

---

## Architecture Judgment

## Should there be a separate preflight flow?

As code: yes.

As a top-level design flow doc: no.

Reasoning:

- the code phase is real and worth documenting
- the reader task is startup comprehension, not internal helper taxonomy
- preflight is only one stage in startup and no longer owns the broader startup experience

Recommended treatment:

- keep “preflight” as a subsection inside the bootstrap/startup doc
- preserve the fail-fast semantics and model-chain mutation details there
- simplify the conceptual role from “preflight” to “model dependency check”
- remove the standalone `DESIGN-flow-preflight.md` once merged

---

## Is there a need for a doctor flow doc?

No.

`doctor` is not a flow in the same sense as:

- one user turn
- approval decision chain
- startup/wakeup

It is a reusable health-check component with three callsites.

Recommended treatment:

- do not create or preserve a separate doctor flow doc
- keep `DESIGN-doctor.md` only if a module/component doc for `_doctor.py` is useful

If the goal is maximum doc compression, doctor content can be split across:

- startup integration sweep in `DESIGN-flow-bootstrap.md`
- `check_capabilities` behavior in `DESIGN-tools-execution.md`
- `co status` mapping in status/config docs

That said, keeping `DESIGN-doctor.md` is defensible because `_doctor.py` has:

- a compact internal schema
- multiple consumers
- non-trivial boundary rules around `deps=None` vs `deps` mode

---

## Recommended Doc Restructure

## Refactoring Intention: Simplify `preflight` to model dependency check

This review does **not** recommend deleting the current `_preflight.py` behavior from code as cleanup.

It recommends a narrower and clearer refactoring:

- keep the current behavior
- reduce the conceptual weight of the phase
- stop presenting it as a peer startup flow
- simplify it to one startup substage whose sole purpose is validating model dependencies before agent creation

What the current code actually checks in `run_preflight()`:

- provider credential readiness
- Ollama server reachability
- local model availability for configured roles
- reasoning-chain viability
- chain pruning when fallback models are available

That is a useful boundary, but the current `preflight` framing implies a broader startup authority than the code now has. It should be simplified to `model dependency check` in the design model.

### Intended end state

After refactoring, the startup model should read like this:

1. Startup/wakeup begins
2. Model dependency check runs
3. Agent/runtime startup continues
4. Bootstrap initialization runs
5. Doctor integration sweep reports broader health state
6. Prompt loop begins

This keeps the fail-fast semantics exactly where they belong while removing the misleading idea that `preflight` is itself the startup flow. The intention is simplification, not renaming for its own sake.

### Refactoring scope

Phase 1: documentation refactor

- merge `DESIGN-flow-preflight.md` content into `DESIGN-flow-bootstrap.md`
- rename the bootstrap subsection from “preflight” to “model dependency check”
- update `DESIGN-index.md` and `DESIGN-core.md` so startup points to bootstrap only
- remove claims that `_preflight.py` owns startup validation broadly

Phase 2: optional code naming cleanup

- leave behavior unchanged
- optionally rename `_preflight.py` and `run_preflight()` later so code names match the simplified model
- examples:
  - `_preflight.py` → `_model_dependency_check.py`
  - `run_preflight()` → `run_model_dependency_check()`

### Explicit non-goals

This refactoring is **not** trying to:

- merge doctor into model dependency checking
- remove doctor as a reusable diagnostic tool
- move non-LLM integration checks earlier into the fail-fast gate
- eliminate the pre-agent fail-fast boundary
- change startup semantics

The goal is clarity, not behavioral redesign.

## Option A — Recommended

Keep bootstrap as the single startup flow owner and keep doctor as a compact component doc.

Changes:

1. Expand `docs/DESIGN-flow-bootstrap.md` to own the full startup/wakeup sequence:
   - frontend creation
   - `create_deps()`
   - preflight fail-fast gate
   - task-runner injection
   - skill load and registry population
   - `get_agent()` creation
   - MCP connect fallback
   - bootstrap steps 1-4
   - handoff to prompt loop

2. Merge the essential content of `docs/DESIGN-flow-preflight.md` into a “Stage: Preflight” section inside bootstrap.

3. Rename that subsection to “Model Dependency Check”.

4. Remove `docs/DESIGN-flow-preflight.md`.

5. Update `docs/DESIGN-core.md` and `docs/DESIGN-index.md`:
   - startup/wakeup points only to `DESIGN-flow-bootstrap.md`
   - preflight no longer appears as a peer startup flow

6. Keep `docs/DESIGN-doctor.md`, but narrow and strengthen its charter:
   - `_doctor.py` schema
   - check families
   - `deps=None` vs runtime mode
   - caller matrix
   - explicit system-owned and agent-owned caller model
   - no implication that it is the startup owner

Why this is best:

- one canonical startup doc
- preserves useful module reference for doctor as a cross-cutting diagnostic tool
- aligns docs with how readers think about startup
- keeps blocking vs non-blocking health logic explicit

---

## Option B — Maximum Consolidation

Use one startup flow doc and no separate doctor component doc.

Changes:

- do everything in Option A
- fold doctor details into:
  - `DESIGN-flow-bootstrap.md`
  - `DESIGN-tools-execution.md`
  - status/config docs
- remove `docs/DESIGN-doctor.md`

Tradeoff:

- fewer docs
- worse discoverability for `_doctor.py` as a shared subsystem
- more duplication risk across bootstrap/tool/status docs

Assessment:

This is not preferred for the intended refactoring flow. Because doctor is expected to remain callable by both system and agent surfaces, removing its component doc weakens clarity around a reusable operational boundary.

---

## Proposed Ownership Model

After restructure, doc ownership should read like this:

- `DESIGN-flow-bootstrap.md`
  - canonical startup/wakeup flow
  - includes preflight as an internal stage
  - includes doctor sweep as a later startup stage

- `DESIGN-doctor.md`
  - component doc for reusable diagnostic and integration health checks
  - explicit shared use by bootstrap, status, and agent-side capability/troubleshooting flows
  - no claim to own startup

- `DESIGN-tools-execution.md`
  - `check_capabilities` tool contract and return shape

- `DESIGN-core.md`
  - high-level lifecycle table only

- `DESIGN-index.md`
  - route startup questions to bootstrap only

---

## Concrete Edit Plan

## Minimum changes required

1. Rewrite `docs/DESIGN-flow-bootstrap.md` intro so it explicitly owns startup/wakeup.
2. Add a top-level sequence diagram beginning at `chat_loop()` startup.
3. Fold preflight details into bootstrap under a dedicated “model dependency check” subsection.
4. Remove the top-level startup routing to `DESIGN-flow-preflight.md` from `DESIGN-index.md`.
5. Update `DESIGN-core.md` lifecycle table so startup points to bootstrap as the main flow doc.
6. Fix stale `_status.py` references in docs.

---

## Optional cleanup

1. Rename `DESIGN-flow-bootstrap.md` to `DESIGN-flow-wakeup.md` if the repo wants a name that better matches reader intent.
2. Keep a short redirect note or stub in the old preflight doc path for one PR if churn minimization matters.
3. Tighten `DESIGN-doctor.md` so it is clearly a component doc, not adjacent startup flow documentation.

---

## TL Guidance

This section is intended for the tech lead who will take this review into planning.

## Decisions to confirm before planning

1. Confirm `bootstrap` as the single startup flow owner.
2. Confirm `preflight` is being simplified to `model dependency check` in the design model.
3. Confirm doctor remains a reusable diagnostic tool/component, not a bootstrap-only helper.
4. Confirm whether code naming cleanup is in scope for the first refactor or deferred until after doc/model cleanup lands.

## Recommended workstreams

1. Documentation ownership refactor
   - merge startup ownership into `DESIGN-flow-bootstrap.md`
   - remove `DESIGN-flow-preflight.md` as a top-level flow doc
   - update index/core routing

2. Concept and naming cleanup
   - replace “preflight” language in design docs with “model dependency check”
   - preserve code behavior unchanged
   - optionally plan later symbol/file renames if desired

3. Doctor role clarification
   - update `DESIGN-doctor.md` to make the system-owned and agent-owned caller model explicit
   - ensure bootstrap/tool/status docs reference doctor consistently as diagnostic capability

## Suggested sequencing

1. Land doc/model simplification first.
2. Re-review startup docs for ownership clarity.
3. Decide whether code naming should follow.
4. Only then consider broader runtime enhancements for doctor-triggered troubleshooting.

## Risks to watch

- accidental behavioral drift while doing “cleanup”
- moving non-LLM integration checks into the fail-fast gate and making startup more brittle
- collapsing doctor too far and losing its cross-cutting diagnostic role
- retaining old terminology in index/core docs after ownership changes

## Done criteria

- one canonical startup flow doc exists and is clearly `bootstrap`
- preflight is no longer represented as a peer top-level startup flow
- model dependency check semantics remain intact
- doctor is clearly documented as reusable by both system and agent call paths
- index/core docs route readers to the right owning documents

---

## Verdict

**NEEDS_ATTENTION**

The code architecture is coherent:

- preflight is a real fail-fast substage
- bootstrap is the real startup initialization phase
- doctor is a reusable health-check subsystem

The documentation architecture is not coherent enough:

- startup ownership is split across too many docs
- `preflight` has become too narrow to justify a peer top-level startup flow doc
- readers do not have one canonical startup/wakeup document

Recommended final state:

- `flow-bootstrap` becomes the canonical startup flow doc
- standalone `flow-preflight` is retired and merged into bootstrap
- `preflight` is simplified to `model dependency check`
- `DESIGN-doctor.md` remains as the component doc for a reusable diagnostic tool callable by both system and agent flows

That structure best matches the latest code and the reader’s mental model.
