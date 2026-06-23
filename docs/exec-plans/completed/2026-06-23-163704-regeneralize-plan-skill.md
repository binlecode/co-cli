# Regeneralize the `plan` skill into a general knowledge-work planner

> **Scope note (2026-06-23):** This plan was originally the full "coding-cluster realign" (cut `review`/`triage` + regeneralize `plan`). The deletion half was **split out and executed directly** as a mechanical change-set alongside the `refactor` removal (same low-risk pattern: `git rm` the dirs, drop both from `_BUNDLED_NAMES`, update the two RESEARCH docs; verified grep-clean + lint + scoped skill tests). What remains here is the substantive, judgment-heavy piece: regeneralizing the kept `plan` skill — and the plan has been renamed to slug `regeneralize-plan-skill` to match this refined scope (creation timestamp preserved).

## Context

co's bundled skill library is realigned to co's **knowledge-work / WEAK_LOCAL** positioning (sourced: `docs/specs/prompt-assembly.md:82`, §2.1 low-inference-reflex yardstick criterion 6 — "co-fit not coding-fit… co is knowledge-work"; and `co_cli/config/llm.py:44`). `refactor`, `review`, and `triage` have been removed (coding-agent skills off-mission for a knowledge-work agent). Current bundle (5): `doctor`, `documents`, `office`, `plan`, `skill-creator`.

`plan` was **kept** rather than cut — but the load-bearing reason is NOT merely "planning generalizes" (that alone would equally justify cutting it as redundant with the base agent, which plans implicitly). `plan`'s distinctive value is its **scoping-only / no-frozen-detail discipline** — a WEAK_LOCAL-specific guard (Behavioral Constraint 3) the base agent does not self-enforce. Its subject (a request → a scoped, ordered plan) is genuinely domain-agnostic, so the discipline is worth preserving across knowledge-work and coding alike.

But `plan/SKILL.md` is **currently coding-specific**: task-ordering "schema before logic / tests before implementation / infra before code"; a `Files:` field; open-question categories "public API/CLI/config schema, test gaps, new package/tool"; Rules "no implementation code during planning, verifiable without running the full test suite." It needs de-coding so it serves non-coding plans as first-class.

Peer plan skills were surveyed at source (hermes `skills/software-development/plan/SKILL.md`, opencode `tool/todowrite.txt` + `tool/plan-enter.txt`, codex `collaboration-mode-templates/templates/plan.md`). Three findings: (1) co's existing decisions are **convergent** — its core principle is hermes' verbatim, its no-frozen-detail stance matches codex's "don't over-specify v1," its `Files:`→`Touches:` generalization matches codex's "behavior-level over file-by-file inventories"; (2) two peer techniques are worth **borrowing** and are folded in below (codex's explore-before-ask, opencode's todo status discipline); (3) several peer practices are co's **rejected** anti-patterns (hermes' complete-code-at-plan-time, codex's "decision-complete / implementer makes zero decisions") and are recorded as non-borrows so they aren't re-proposed.

## Problem & Outcome

**Problem:** The kept `plan` skill is framed as a software-implementation planner, mismatched to co's knowledge-work positioning.

**Outcome:** `plan` reads as a general planner (research / writing / project / code), with coding as one example — preserving its scoping discipline, core principle, and Common Mistakes block — and, for a multi-task plan, materializes its task list as a session todo ledger via `todo_write` so a long-running or complex plan is tracked, refined, and adapted to completion instead of printed once and lost on the first compaction.

**Failure cost:** Without this, the one survivor of the coding-cluster cut still presents co as a coding agent on its most-used planning surface, and a future reader re-litigates "why keep a planner at all" without the scoping-discipline rationale recorded. Without the todo bridge specifically, a plan for a long-running task is inert prose that scrolls out of context on the first compaction — the agent loses its own plan mid-task, which is the exact case the skill exists to serve.

## Scope

**In scope:**
- Regeneralize `co_cli/skills/plan/SKILL.md` into a general knowledge-work planner (the de-coding table below).
- Add a **plan→todo bridge**: instruct the skill to emit a multi-task plan as a session todo list (`todo_write`) and keep it current as work proceeds (status transitions + `todo_write(merge=True)`). This is a capability change, not just generalization — validated as such (see Validation approach).

**Out of scope:**
- The `review`/`triage` deletion + RESEARCH-doc cleanup — **done** (split out, executed directly).
- `doctor` / `documents` / `office` / `skill-creator` (audited clean).
- `docs/specs/skills.md` edits (sync-doc post-delivery owns specs; the spec's "review" tokens are the dream-daemon reviewer, unrelated — no change expected).
- Reintroducing complete-code-at-plan-time style (rejected, A/B-checked).
- A mandatory run_turn A/B regression (see Validation approach).

## Behavioral Constraints

The regeneralized `plan` skill MUST:
1. Treat non-coding plans (research, writing, a project, an event) as **first-class**; coding is one illustrative example, never the framing.
2. Preserve the genuinely-general, already-validated content: the core principle ("a good plan makes implementation obvious — if a task leaves the doer guessing, sharpen the Done-when or split it"), Phase-1 scope / in-scope / out-of-scope / acceptance-criteria, and the Common Mistakes block (vague Done-when, scope leak, oversized task, skipped open-questions).
3. Keep the **scoping-only / no-frozen-detail** philosophy — a deliberate WEAK_LOCAL adaptation. Do NOT reintroduce complete-code-at-plan-time (freezes a weak model's weakest output into a trusted artifact and removes the feedback loop).
4. Keep the existing house style: frontmatter `description` + `**Invocation:**` + `## Phase N` + terminal `## Rules`.
5. For a plan with **more than one task**, emit the task list as a session todo list at the end of Phase 2 — one item per task, content = the task's verb phrase + its Done-when. This is the bridge the skill body owns: it materializes the plan as the durable ledger. **Ongoing status discipline is NOT the skill body's job** — the body reaches the model only as the one-turn `/plan` dispatch input (`commands/core.py:135-150`) and scrolls out before execution turns. Tracking and closure across a long-running task are owned by the always-injected base rule `04_tool_protocol.md:32` (the `todo_read` completion gate) plus the compaction snapshot (`_compaction_markers.py`); the skill body must not re-encode them. A single-task or purely informational/conversational request skips emission (no ledger needed).

## High-Level Design

De-code each coding-bound element while preserving structure:

| Element | Today (coding) | Regeneralized (general) |
|---|---|---|
| `description` | "Implementation plan drafting — feature request or bug into execution plan" | "Planning — turn a request (a feature, a document, a research question, a project) into a scoped, ordered plan with acceptance criteria and surfaced open questions" |
| Phase-1 acceptance examples | features/bugs/refactors (all SE) | keep one code example; add non-code (research: "the question is answered with cited sources"; writing: "the draft covers sections A–C"); generalize "read the relevant source files and tests" → "read the relevant material / current state" |
| Phase-1 ambiguity rule (`:21`) | "If the request is ambiguous, ask one clarifying question before proceeding — do not guess" | scope to preference/constraint ambiguity only, inheriting `03_reasoning.md:40-51` (discover-before-ask; act on the obvious default): "If a *discoverable* fact is missing, find it; ask only when the ambiguity is a preference or constraint that genuinely changes the plan." Removes the same base-rule duplication PO-M-1 (C3) struck from the borrow — applied here to the pre-existing line in the file being edited. |
| Phase-2 task field | `**Files:** <paths>` | `**Touches:** <what the task changes or produces>` (files for code; sections/sources/deliverables otherwise) |
| Phase-2 ordering rules | schema→logic, tests→impl, infra→code | general dependency order: prerequisites before dependents; foundational pieces before what builds on them; verification/review steps after the work they check (intentional shift: drops coding's TDD test-*first* convention for the general verify-*after* order — a doctrine change, not a rename) |
| Phase-2 atomic guard | "more than ~3 files → split" | "if a task spans many moving parts → split" |
| Phase-3 open-question categories | public API/CLI/config schema; test gaps; new package/tool | external dependencies or contracts others rely on; **backward compatibility / reversibility** (does this break something others depend on, and can it be undone — kept explicit, not folded into generic "risk", since coding plans on this zero-backward-compat repo lean on it and it reads as a general planning concern too); gaps in what's known; resources or access needed |
| Rules | "no implementation code during planning"; "verifiable without running the full test suite" | "plan names the steps and their done-conditions, not the finished work product"; "every Done-when is verifiable by inspection or a single check" |
| Body intro / Invocation line (`:11`) | "Translate a feature request, bug fix, or refactor goal into…" | "Turn a request — a feature, a document, a research question, a project — into a scoped, ordered plan with acceptance criteria and surfaced open questions." |
| **Estimated scope** line (`:51`) | "e.g. '4 tasks, ~3 files, low risk'" | "e.g. '4 tasks, low risk'" — drop the file-count proxy (a code-only signal); keep task-count + risk |

Common Mistakes block: keep all four. Make the vague-Done-when example dual (one general, one code) so it reads cross-domain; and de-code the **Oversized task** example the same way — its "editing six files / ~3 files or fewer" file-count framing becomes "a task bundling several distinct deliverables", with the split signal stated generally (the Phase-2 atomic guard already generalized to "many moving parts").

### Plan→todo bridge (new capability)
The skill currently prints a plan and stops; the task list lives only as conversation prose, which evaporates on compaction. Add an end-of-Phase-2 instruction, written as a low-inference reflex (`prompt-assembly.md` §2.1: observable cue, one imperative, the todo-write tool named in **plain words** — no `tool_name(` call syntax, per floor guard F5): *when the plan has more than one task, write each task to the session todo list as one item (content = verb phrase + Done-when).* This is **plan-time emission only** — the durable ledger gets created. Everything after reuses existing infra with **no new code**: items survive compaction (the "Active tasks" enrichment + `TODO_SNAPSHOT_PREFIX` marker, `_compaction_markers.py`) and `/resume` rehydration (`commands/resume.py`), and ongoing tracking/closure is owned by the always-injected `todo_read` completion gate (`04_tool_protocol.md:32`) — **not** re-encoded in the skill body, which isn't present during execution turns (`commands/core.py:135-150`). The scoping-only / no-frozen-detail philosophy (Constraint 3) keeps the ledger adaptable rather than a frozen checklist.

> **Deferred follow-up (out of scope):** opencode's fuller status discipline (`completed` only after verification not intent; blocked → keep `in_progress` + follow-up) is richer than co's current base gate. If wanted, it belongs in `04_tool_protocol.md` (base, all-turns), NOT this skill — a separate small plan, since base-rule edits trip the floor guards and affect every turn.

### Borrowed peer best practices (parity)
Surveyed at source; borrow what fits co's knowledge-work / scoping-only positioning, reject what fights it.

**Borrow:**
- **Todo emission from the plan** — opencode `tool/todowrite.txt` ("use proactively when the task is 3+ distinct steps; skip for a single trivial step or a purely informational request"). co adopts the *emit-when-multi-task* threshold as the Phase-2 bridge (Constraint 5 / TASK-2). The fuller real-time status discipline opencode also specifies is **not** borrowed into the skill body — see the bridge subsection's deferred follow-up.

**Already owned by BASE (do NOT re-encode in the skill body):**
- **Explore-before-asking + two-kinds-of-unknowns + recommended-default options** — codex `templates/plan.md` PHASE 1. co already ships this as an always-injected base rule, `03_reasoning.md:40-51` (`## Two kinds of unknowns`: discover-before-asking; obvious-default → act; preferences → present 2–4 options with a recommended default). It fires on every turn, including inside `/plan`. Re-authoring it in the skill body would duplicate a base reflex and violate the prompt-assembly partition (§2.1). The only plan-output-specific residue: Phase 3 may note that a *deferred* tradeoff open-question is recorded with its recommended default — inheriting, not restating, the base reflex.

**Reject (recorded so they aren't re-proposed):**
- **Complete, copy-pasteable code at plan time** — hermes `plan/SKILL.md` ("Add Complete Details"). co's explicitly-rejected anti-pattern (freezes a weak model's weakest output, kills the feedback loop; Constraint 3).
- **TDD-cycle-as-steps / bite-sized 2–5-min tasks / per-task commits / DRY-YAGNI** — hermes. Coding-specific; off-mission for a knowledge-work planner.
- **"Decision-complete, implementer makes zero decisions"** — codex Finalization rule. In tension with scoping-only; co leaves implementation judgment to the doer on purpose, to preserve the feedback loop.
- **Plan-as-agent-mode + hard mode-switch machinery** — opencode `plan-enter`/`plan-exit`, codex `Plan Mode`. co has no mode system; a skill-body instruction (hermes precedent) is the right shape.

**Convergence (validates existing decisions — cite as support, no change):** core principle = hermes verbatim; no-frozen-detail = codex "don't over-specify v1"; `Files:`→`Touches:` = codex "behavior-level over file inventories."

### Validation approach (settled, not an open question)
No mandatory run_turn A/B for the **generalization (de-coding) half** — the prior A/B on the plan body (`RESEARCH-skills-prompt-gaps.md`) hit a ceiling effect (proved no-harm, not upside), so a fresh A/B on wording would be similarly inconclusive and expensive. That half is validated by the bundled-library load/parse gate (plan still loads with non-empty body/description) + a manual read-through against 2–3 non-coding planning prompts.

The **todo-bridge half IS a capability change**, but its runtime emission is a probabilistic WEAK_LOCAL behavior, so it is validated by a **non-blocking** functional smoke (a multi-task prompt populates `CoSessionState.session_todos`; a single-task prompt does not) — observable behavior, functional-only. One run shows the bridge *can* fire, not that it reliably does, so it gates nothing (CD-M-1). TASK-2's blocking gate is the verifiable artifact: the reflex-form emission instruction present in the body, lint, and the bundled-library load.

## Tasks

### ✓ DONE TASK-1 — Regeneralize `plan/SKILL.md`
- **files:** `co_cli/skills/plan/SKILL.md`
- **done_when:** every element in the High-Level Design table is generalized per the right column (including the Phase-1 ambiguity rule `:21`, scoped to preference/constraint ambiguity so it stops duplicating `03_reasoning.md:40-51` — same partition fix PO-M-1 applied to the borrow); core principle, Phase-1 scope structure, Common Mistakes block, and scoping-only philosophy preserved; Phase 3 may note that a *deferred* tradeoff question is recorded with its recommended default (inheriting `03_reasoning.md:40-48`, NOT re-encoding the general elicitation reflex — PO-M-1); no complete-code-at-plan-time content introduced; house style intact; the skill loads (bundled-library gate green) with non-empty body/description; non-blocking smoke step — drive the `success_signal` prompt through its dispatch path once (`/plan plan a literature review on topic X` or its body via `run_turn`) and confirm a scoped, ordered, non-coding plan results; manual read-through confirms no coding-only framing remains.
- **success_signal:** `/plan plan a literature review on topic X` produces a scoped, ordered, non-coding plan with verifiable Done-when conditions.
- **prerequisites:** none

### ✓ DONE TASK-2 — Add the plan→todo bridge to `plan/SKILL.md`
- **files:** `co_cli/skills/plan/SKILL.md`
- **done_when:** Phase 2 ends with a reflex-form instruction to write each task to the session todo list (one item per task, content = verb phrase + Done-when) **when the plan has more than one task** — plan-time emission only, **no "keep it current" text in the body** (ongoing tracking is the base completion gate's job, CD-M-1); the todo-write tool is named in **plain words**, no `tool_name(` call syntax (floor guard F5); house style intact. Gating artifacts: the reflex instruction present in the body + lint + bundled-library load. A **non-blocking** smoke confirms a multi-task plan prompt populates `session_todos` and a single-task prompt does not — one WEAK_LOCAL run shows the bridge *can* fire, not that it reliably does, so it gates nothing (mirrors TASK-1's non-blocking smoke).
- **success_signal:** `/plan plan a literature review on topic X` (multi-step) produces the plan AND a populated session todo list; `todo_read` then reflects the tasks.
- **prerequisites:** TASK-1 (same file — de-code first so the bridge instruction lands in already-generalized prose)

## Testing

- Functional only. `tests/test_flow_skill_bundled_library.py` covers that `plan` still loads with non-empty body + description (load/parse, not structure).
- No plan-content assertions (would violate functional-only policy).
- Non-blocking smoke run of the `success_signal` prompt through dispatch (TASK-1 done_when).
- TASK-2 todo-bridge: **non-blocking** functional smoke that a multi-task plan prompt populates `session_todos` (single-task leaves it empty) — co-locate with `tests/test_flow_todo.py`; keep `test_flow_skill_bundled_library.py` load/parse-only (CD-m-1). Non-blocking because it exercises a probabilistic WEAK_LOCAL emission, not deterministic plumbing (CD-M-1).
- `scripts/quality-gate.sh lint` after the edit.
- Manual read-through against 2–3 non-coding planning prompts (per Validation approach); no mandatory A/B.

## Open Questions

None — ready to implement. (The A/B-regression candidate was resolved inline under Validation approach: not gating for the de-coding half, given the prior A/B's ceiling effect. The todo-bridge half IS a capability change but is validated by a single functional runtime check, not A/B. Re-raise trigger: a user reports the regeneralized `plan` producing degraded plans for *coding* tasks specifically, or the todo bridge firing on trivial single-task prompts.)

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| Split TASK-3 out | adopt | Two logical change-sets, different risk/kind, no dependency. Deletion is mechanical (refactor precedent → direct execution, no plan); plan regeneralization is judgment-heavy and deserves a focused cycle. | Deletion executed directly; this plan slimmed to plan-regeneralization only. |
| PO-m-1 | adopt | Keep-`plan` rationale must be the WEAK_LOCAL scoping discipline, not "planning generalizes" (which equally argues for cutting it as redundant with the base agent). | Context leads with the scoping-discipline rationale; Constraint 3 cross-referenced. |
| PO-m-2 | adopt | Back-compat/reversibility is load-bearing on a zero-backward-compat repo and reads as a general planning concern too. | HLD Phase-3 row keeps "backward compatibility / reversibility" explicit, not dissolved into "risk". |
| CD-m-2 | adopt | User-facing task needs a runtime-path exercise, not read-only verification. | TASK-1 done_when includes a non-blocking smoke step driving the `success_signal` prompt through dispatch. |
| PO-m-3 | adopt | Citation precision — thesis anchored to its real source. | Context cites `prompt-assembly.md:82` + `config/llm.py:44`, not a "base rules" paraphrase. |
| Fold todo bridge | adopt | The intended use of `plan` is to drive a todo ledger (think / track / refine / adapt) for long-running tasks; the skill never encoded it, so a multi-task plan evaporated on the first compaction. Folding it in here (vs. a separate plan) keeps the one survivor of the cluster cut coherent with its reason-to-exist, and reuses the todo subsystem with zero new code. | New Constraint 5 + HLD "Plan→todo bridge" subsection + TASK-2 + capability-grade functional validation. (User decision, Gate 1.) |
| Gate-1 table completeness | adopt | `TASK-1` done_when binds to "every element in the HLD table"; three coding-bound elements (body intro line, Estimated-scope line, Common Mistakes Oversized example) lived outside it and would survive on the read-through catchall alone. | Added two table rows + de-code instruction for the Oversized-task example; done_when is now self-contained. (PO finding, Gate 1.) |
| Borrow peer best practices | adopt | Surveyed hermes/opencode/codex plan skills at source. Borrow what fits; record rejected anti-patterns as non-borrows. | New "Borrowed peer best practices" HLD subsection (borrow/reject/convergence, file:line cited). (User decision, Gate 1.) *Superseded in C3: explore-before-ask removed (already in BASE, PO-M-1); status discipline scoped out of the skill body (CD-M-1).* |
| CD-M-1 (C3) | adopt | The skill body is a one-turn dispatch input (`commands/core.py:135-150`) that scrolls out before execution turns, so "keep it current" can't be enforced from it; and a single WEAK_LOCAL run is not a reliable gate. | Constraint 5 + bridge subsection + TASK-2 rescoped to **plan-time emission only**; ongoing tracking owned by base gate `04_tool_protocol.md:32`; TASK-2 runtime smoke downgraded to **non-blocking**, gating on the artifact (instruction present + lint + load); opencode status discipline moved to a deferred-follow-up (base-rule edit, out of scope). |
| CD-m-1 (C3) | adopt | Bridge check is dispatch-level behavior, not load/parse. | Testing names a single target — co-locate with `tests/test_flow_todo.py`; `test_flow_skill_bundled_library.py` stays load/parse-only. |
| CD-m-2 (C3) | adopt | Floor guard F5 forbids `tool_name(` call syntax in injected prose. | TASK-2 + bridge subsection require the SKILL.md body to name the todo-write tool in **plain words**; call syntax stays in the plan doc only. |
| PO-M-1 (C3) | adopt | Explore-before-ask / two-kinds-of-unknowns / recommended-default is already a shipped always-injected base rule (`03_reasoning.md:40-48`); re-encoding in the skill body duplicates a base reflex and violates the prompt-assembly partition (§2.1). | Removed the elicitation borrow from the skill body; "Borrowed peer best practices" now lists it under "Already owned by BASE"; TASK-1 keeps only a Phase-3 deferred-tradeoff recommended-default note that *inherits* the base rule. |
| PO-m-1 (C3) | resolved by PO-M-1 | The untraceable third delta (elicitation) was removed, not documented — Outcome (de-coding + bridge) is now complete. | No Outcome clause added; delta dissolved into BASE. |
| PO-m-2 (C3) | acknowledge | Confirms still one coherent plan; splitting would create two plans editing identical Phase-1/3 lines. | No split. |
| PO-G1-1 | adopt | Phase-1 line `:21` ("if ambiguous, ask one clarifying question") is broader than — and in tension with — the base discover-first rule `03_reasoning.md:40-51`; it's the *same* duplication PO-M-1 struck from the new borrow, but it pre-exists in the file being edited and the de-code pass is the moment to reconcile it (fix-pre-existing-on-touch). | New HLD row scoping `:21` to preference/constraint ambiguity (inherit `03_reasoning.md`); TASK-1 done_when binds it explicitly. (PO finding, Gate 1 re-review.) |
| PO-G1-2 | acknowledge | Phase-2 ordering generalization inverts coding's test-*first* (TDD) to verify-*after* — a doctrine shift worth flagging so it doesn't read as a botched rename. Citation drift `40-48`→`40-51` corrected. | HLD ordering row annotated; citation fixed. (PO note, Gate 1 re-review.) |

> Deletion-cycle decisions CD-M-1 (staging discipline), CD-m-1 (peers-tiers note-only scope), CD-m-3 (grep false-positives) applied to the directly-executed deletion change-set, not re-litigated here.

## Final — Team Lead

Plan approved — converged across C1/C2 (de-coding pass) + C3 (Gate-1-amended scope). C3 re-critiqued the amendments (todo bridge + peer borrows); both critics returned `revise` with one blocker each, and **both blockers were adopted verbatim** — no residual judgment, so convergence holds without a further cycle:
- **CD-M-1** → the bridge is scoped to **plan-time emission**; ongoing tracking/closure is owned by the always-injected base completion gate (`04_tool_protocol.md:32`), not the skill body (which scrolls out after the `/plan` turn, `commands/core.py:135-150`). TASK-2's runtime smoke is non-blocking.
- **PO-M-1** → the explore-before-ask / two-kinds-of-unknowns borrow was **removed** as redundant with the shipped base rule `03_reasoning.md:40-48`.

Net result is a tighter plan than the amendment first proposed: **de-code the plan skill + add a plan-time todo-emission bridge.** The speculative elicitation delta dissolved into existing BASE, and the heavier status-discipline borrow is recorded as a deferred, out-of-scope base-rule follow-up.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev regeneralize-plan-skill`

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev regeneralize-plan-skill`

## Cycle C3 — PO

**Assessment:** revise
**Blocking:** PO-M-1
**Summary:** The plan is still substantially one coherent plan — de-coding and the (settled) todo bridge both trace to Outcome, and the elicitation borrows ride on the exact Phase-1/3 lines already being de-coded, so they are not a splittable second concern. But one borrow ("two kinds of unknowns" + explore-before-ask) duplicates an already-shipped always-on BASE rule (`03_reasoning.md:40-44`), and the plan presents it as a fresh codex borrow blind to that — a real redundancy/first-principles concern, plus a traceability gap in the Outcome.

**Major issues:**
- **PO-M-1** [HLD "Borrowed peer best practices" / Behavioral Constraints / TASK-1 done_when]: The "explore before asking" + "two kinds of unknowns" borrow is **already a shipped, always-injected BASE rule** — `co_cli/context/rules/03_reasoning.md:40-44` ("Before asking the user a question, determine if the answer is discoverable through your tools… Only ask the user for decisions that depend on their preferences, priorities, or constraints") and the obvious-default clause at :46-48. It fires on every turn for every profile, including inside `/plan`. The plan cites it as a net-new codex borrow and is blind to co already owning it in BASE, so as written TASK-1 risks re-encoding a base reflex into a skill body — exactly the kind of duplication the prompt-assembly partition (§2.1: a reflex both profiles need lives once in BASE, not duplicated downstream) exists to prevent. Recommendation: do NOT re-author the general elicitation reflex in the skill body. Reframe this delta narrowly: Phase 1's de-coding already changes "read the relevant source files and tests" → "read the relevant material / current state" (that line stays, it is de-coding). For Phase 3, scope the borrow to the *plan-specific* shape only — i.e. an open question that is a preference/tradeoff carries a recommended default — and add an explicit note that the general explore-before-ask reflex is inherited from `03_reasoning.md` and is not restated. Update the "Borrow" bullet, Constraint 5's sibling text, and TASK-1 done_when accordingly so dev doesn't duplicate the base rule.

**Minor issues:**
- **PO-m-1** [Problem & Outcome]: The Outcome sentence enumerates de-coding + the todo bridge but is silent on the two elicitation borrows, so one of the three folded-in deltas has no traceability to the stated Outcome (a planning skill leaking its own scope-statement — the irony the TL flagged). Recommendation: after PO-M-1 narrows the borrow, add one clause to Outcome noting the planner resolves discoverable unknowns by inspection and surfaces only preference/tradeoff questions (with a recommended default) — so every in-scope delta is named in the Outcome.
- **PO-m-2** [Scope / coherence]: Confirming the TL's coherence question for the record — this is still ONE plan, not a grab-bag. The de-coding and the elicitation borrows edit the *same* Phase-1/3 prose; splitting "sharpen-plan-elicitation" out would create a second plan touching identical lines, which is worse, not cleaner. The todo bridge is a distinct capability but the Decisions-table rationale (keep the cluster survivor coherent with its reason-to-exist; zero new code) holds. No split recommended; the only real defect is PO-M-1's redundancy, not incoherence.

## Delivery Summary — 2026-06-23

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | Every HLD-table element generalized; core principle, Phase-1 structure, Common Mistakes, scoping-only philosophy preserved; no complete-code-at-plan-time; house style intact; bundled-library load green; read-through confirms no coding-only framing | ✓ pass |
| TASK-2 | Phase 2 ends with a reflex-form, plain-words todo-emission instruction gated on >1 task (plan-time only, no "keep-current" text, no `tool_name(` call syntax); artifact + lint + load green | ✓ pass |

**Tests:** scoped — `tests/test_flow_skill_bundled_library.py` 1 passed, 0 failed (plan loads with non-empty body + description). Lint clean.
**Doc Sync:** clean — `docs/specs/skills.md` has no plan-skill content to drift; sync-doc no-op (per plan scope).

**Non-blocking validation (gates nothing, per CD-M-1 / TASK-1/2 done_when):**
- Artifact gates (the actual blocking criteria) all green: reflex emission instruction present in body; F5 grep clean (no `tool_name(` call syntax); no ongoing-tracking/"keep-current" prose; bundled-library load + lint pass.
- Manual read-through: no coding-only framing remains — `Code:` is one of three peer acceptance examples (Research/Writing), `pytest` appears only in the dual vague-`Done when` example.
- Live WEAK_LOCAL LLM smoke (drive `/plan` through dispatch; multi-task populates `session_todos`, single-task does not): **not run as a committed test** — the plan explicitly framed it non-blocking and a single probabilistic run gates nothing; committing a flaky LLM test to the deterministic `test_flow_todo.py` would be a suite-quality regression. Bridge correctness rests on the artifact gates above. Re-raise trigger (per Open Questions) still applies if the bridge fires on trivial single-task prompts in real use.

**Overall: DELIVERED**
`plan/SKILL.md` regeneralized to a general knowledge-work planner (coding as one example) with the scoping-only discipline, core principle, and Common Mistakes block preserved, plus a plan-time todo-emission bridge for multi-task plans. All blocking gates green; non-blocking LLM smoke deferred by design.

## Implementation Review — 2026-06-23

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | Every HLD-table element generalized; core principle / Phase-1 structure / Common Mistakes / scoping-only preserved; no complete-code-at-plan-time; house style intact; loads with non-empty body/description; no coding-only framing | ✓ pass | SKILL.md:2 (description generalized), :9/:11 (Invocation + intro: feature/document/research/project), :13 (core principle preserved, de-coded "implementer"→"doer"/"implementation"→"the work"), :21 (ambiguity rule scoped to preference/constraint, inherits 03_reasoning.md), :25-28 (Code/Research/Writing acceptance examples + "relevant material / current state"), :38 (`**Touches:**`), :44-46 (general dependency order, verify-after), :48 ("many moving parts"), :50 ("4 tasks, low risk" — file-count dropped), :64-67 (4 general open-question categories incl. backward-compat/reversibility), :69 (deferred-tradeoff recommended-default, inherits not restates base reflex — PO-M-1), :77 (dual vague-Done-when), :79 (oversized = "distinct deliverables"), :84-85 (Rules de-coded) |
| TASK-2 | Phase 2 ends with reflex-form emission instruction, >1-task gated, plan-time only, no "keep-current" text, todo tool in plain words (no `tool_name(` syntax), house style intact | ✓ pass | SKILL.md:52 — "When the plan has more than one task, write each task to the session todo list as one item — its content the task's verb phrase plus its `Done when`. (Skip this for a single-task or purely informational request.)" Positioned at end of Phase 2 (before Phase 3 :54). Grep-confirmed: no `tool_name(` call syntax, no ongoing-tracking prose. test_instruction_floor_coupling F5 guard green. |

### Issues Found & Fixed
No issues found. (Core-principle wording "implementation"→"the work" examined adversarially: consistent with Constraint 2's own de-coding of "implementer"→"doer" in the same quoted principle; substance preserved, serves Constraint 1 — deliberate, not drift.)

### Tests
- Command: `uv run pytest -v`
- Result: 835 passed, 0 failed (the 8 `status=ERROR` log lines are deliberate error-path spans inside passing tests, not failures)
- Targeted floor guards (description-in-manifest budget + F5 + bundled-library load): green
- Log: `.pytest-logs/<ts>-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- `success_signal` (`/plan` → scoped non-coding plan + populated todos): LLM-mediated, verified via artifact (regenerated body loads in `<available_skills>` manifest, bundled-library gate green) + boot smoke; chat run non-gating per plan's deliberate non-blocking stance (CD-M-1). No code path changed — skill prose + manifest description only.

### Coverage note (eval gap, informational — not blocking)
The new TASK-2 plan→todo *emission* bridge is **not** covered by an eval. The downstream todo→done lifecycle IS (`eval_agentic_loop.py::W12.D` completeness_gate — the base completion gate that owns ongoing tracking per CD-M-1); plan-creation/checkpoint is (`eval_multistep_plan.py` W11.A/B/C, which explicitly does not assert todo state). Neither drives the `/plan` skill dispatch nor asserts `session_todos` populated from a `/plan` turn. Closing this is a separate small plan (a W11.D case driving `/plan` through dispatch) — deferred by the original plan's non-blocking decision; flagged for the user.

### Overall: PASS
Both DONE tasks verified against done_when with file:line evidence; full suite green (835 passed); no blocking findings; behavioral surface loads clean. One informational eval-coverage gap on the new emission bridge, deferred by design.
