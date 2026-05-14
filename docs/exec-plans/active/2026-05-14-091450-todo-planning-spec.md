# todo-planning-spec

## Problem

There is no dedicated spec for co-cli's todo-based planning capability. The behavior is implemented (`co_cli/tools/todo/rw.py`, `co_cli/context/_compaction_markers.py`, `co_cli/commands/resume.py`, `co_cli/deps.py:115-150`) and was fully consolidated by the recent `todo-continuity` plan (id field, merge mode, all-or-nothing validation, `/resume` rehydration, compaction snapshot format). But the spec coverage is scattered across `tools.md` (one row in the tool-groups table), `compaction.md` (snapshot wiring), `core-loop.md` (side-channel context), `prompt-assembly.md` (one bullet), `tui.md` (one bullet), and `system.md` (one bullet). No single spec owns the planning contract, the validation rules, the rehydration semantics, the snapshot format, the delegation behavior (`fork_deps` resets `session_todos` to empty — implicit, undocumented), or the relationship between todo and the higher-level exec-plan artifacts.

Create a dedicated spec that consolidates the todo capability and explicitly frames it as the agent's runtime planning surface.

## Status

Open decisions resolved — ready for /orchestrate-plan todo-planning-spec.

## Open Decisions Resolved — 2026-05-14

### D1. Spec scope — doc-only vs extension
- Question: Should the spec be pure documentation consolidation, doc + minor enforcement, or a design surface for extensions?
- Recommended: Doc-only consolidation — todo-continuity just shipped a thoroughly-grilled contract; capture it before drift.
- Chosen: **Doc-only consolidation.**
- Why: Spec work and impl work have different gates and deliverables; gaps must surface as separate plans, not in-spec design.
- Constraint: Any functional gap surfaced during this grill is recorded under `### Recommended follow-up plans`, not designed in-spec.

### D2. Planning framing — what does the capability *do*?
- Question: Is todo the agent's planning surface, a tracking checklist, or both scoped by horizon?
- Recommended: Within-session planning surface — matches what shipped (id-stable items, merge updates, snapshot-survives-compaction, /resume rehydration).
- Chosen: **Within-session planning surface.**
- Why: The shipped behavior was specifically engineered so the list functions as a durable plan, not a scratch checklist.
- Constraint: Spec must explicitly call this out as the capability's purpose, not just describe the tool mechanics.

### D3. Spec filename
- Question: `todo.md`, `planning.md`, or capability-oriented name?
- Recommended (revised after user clarification "todo.md is already causing confusion"): `self-planning.md` — leads with the capability; "self-" disambiguates from human-authored planning.
- Chosen: **`docs/specs/self-planning.md`.**
- Why: Capability-first naming; auto-tasking lives inside the doc as a sibling facet, not in the filename.
- Constraint: Filename never says "todo"; artifact noun "todo item" is reconciled inside the doc (see D10).

### D4. Audience — engineering spec or agent canon?
- Question: Single `docs/specs/` doc, or also a `agent_docs/` companion?
- Recommended: `docs/specs/self-planning.md` only — consistent with the rest of the engineering specs.
- Chosen: **Engineering spec only.**
- Why: Agent gets behavior from the existing `todo_write` docstring; a second copy in `agent_docs/` invites divergence.
- Constraint: If the docstring's planning framing needs tightening, that's a follow-up plan, not part of this spec.

### D5. Relationship to exec-plans
- Question: Should the spec describe how self-planning relates to `docs/exec-plans/` artifacts?
- Resolved-from-clarification: User clarified "exec-plans are plans of developing co, the agentic system, not related to specs." Build-time vs runtime layers — different universes.
- Chosen: **Not addressed in the spec.** Exec-plans are out of scope at the runtime spec layer.
- Why: `docs/exec-plans/` are developer/TL workspace artifacts for building co-cli; `docs/specs/` are runtime behavior of the shipped co-cli agent. The agent at runtime does not interact with exec-plans.
- Constraint: Spec must not cross-reference exec-plans, build-time skills (`/orchestrate-plan`, `/orchestrate-dev`, `/ship`), or developer workflow.

### D6. Source-of-truth migration
- Question: Pull todo content out of the 6 specs that currently touch it (`tools.md`, `compaction.md`, `core-loop.md`, `prompt-assembly.md`, `tui.md`, `system.md`), or leave them alone?
- Recommended: Selective pull — self-planning.md owns capability + behavioral contract; integration specs keep their integration concern and cross-link.
- Chosen: **Selective pull.**
- Why: self-planning.md should own the *what* (schema, validation, snapshot format, rehydration). The integration specs own *how* (compaction.md keeps snapshot wiring into head/marker/tail; tui.md keeps user-visible surface).
- Constraint: Light edits to ~4 of 6 specs (cross-ref + trim duplicated *what*); deeper edits to `compaction.md` and `tui.md` only if duplication is loud.

### D7. Behavioral invariants — rules vs conventions
- Question: Which implicit invariants in the codebase does the spec formalize as rules vs conventions?
- Recommended: Rules a, b, c, e, f, g; convention d (linear status transition order).
- Chosen: **Rules:** (a) only ONE item `in_progress` at a time, (b) proactive todo creation for any 3+ step directive, (c) model assigns and owns `id` (no auto-assignment), (e) snapshot truncation to 10 active items, (f) active = pending + in_progress (terminal items excluded from snapshot), (g) fresh write replaces, merge updates by id. **Convention:** (d) typical status order pending → in_progress → completed/cancelled (not enforced).
- Why: D2 (planning surface framing) requires "decompose into a plan; one step at a time" to be a real rule, not just docstring lore. Status-transition order is rarely violated but enforcement would constrain edge cases unnecessarily.
- Constraint: Rule (a) is currently docstring-only in `co_cli/tools/todo/rw.py:189` and not checked in `_run_fresh` / `_run_merge`. Spec states the rule; enforcement is a follow-up plan (see `### Recommended follow-up plans`).

### D8. Delegation semantics
- Question: Codify the current `fork_deps` reset of `session_todos`, or leave silent?
- Recommended: Codify reset as the contract.
- Chosen: **Codify reset as contract.**
- Why: `co_cli/deps.py:347-355` creates `inherited_session = CoSessionState(google=..., session_approval_rules=..., reasoning_display=...)` — `session_todos` is not in the inherited list and defaults to empty. Delegation agents (`web_research`, `knowledge_analyze`, `reason`) do focused sub-work; inheriting a 10-item parent plan would pollute their planning.
- Constraint: Spec must state "delegation agents start with an empty todo list and plan their own sub-work; the parent's plan is not visible to them." No follow-up plan needed unless someone wants the opposite behavior.

### D9. Cross-spec relationships within the runtime universe
- Question: Which adjacent runtime specs does self-planning.md anchor to via cross-refs?
- Recommended: Banner cross-refs to `compaction.md` + `tools.md`; in-body cross-refs to `system.md` + `core-loop.md`; D6 selective-pull determines `prompt-assembly.md` / `tui.md` treatment.
- Chosen: **Banner: compaction + tools. In-body: system + core-loop. Conditional: prompt-assembly + tui per D6 outcome.**
- Why: Compaction integration (snapshot survival) and tool-surface ownership are the tightest couplings. Memory / personality / skill specs are not directly coupled at the documentation level.
- Constraint: Never cross-ref to build-time docs (exec-plans, dev-workflow skills) per D5 layer rule.

### D10. Terminology lock
- Question: Canonical noun for the unit of the agent's plan?
- Recommended: "Todo item" for the artifact (matches code); "self-planning" / "the agent's plan" for the capability.
- Chosen: **"Todo item" (artifact noun) — "self-planning" (capability noun).**
- Why: Matches code identifiers (`TodoItem`, `session_todos`, `todo_write`, `todo_read`) so spec language greps against source. Avoids "task" entirely because of the background-job tool overload (`task_start`/`task_status`/`task_cancel`).
- Constraint: Spec must explicitly bridge the filename ↔ artifact-noun mismatch with one sentence: "todo items are the unit; the list of todo items is the agent's runtime plan."

### Resolved from codebase (no interview)
- D11. `fork_deps` behavior with `session_todos` → resets to empty (`co_cli/deps.py:347-355`; `inherited_session` does not include `session_todos`). Feeds D8.
- D12. One-in-progress rule enforcement status → docstring-only (`co_cli/tools/todo/rw.py:189` in `todo_write` docstring); NOT checked in `_run_fresh` / `_run_merge`. Feeds D7 and follow-up plan recommendation.
- D13. Existing spec coverage of todo → 6 specs touch it: `tools.md:32` (tool-groups row), `compaction.md:69,75,116,175,190,460,517,607,682` (snapshot wiring + char cap), `core-loop.md:276,282` (side-channel context), `prompt-assembly.md:93` (one bullet), `tui.md:116` (one bullet), `system.md:182` (one bullet). Feeds D6 scope.
- D14. Existing spec template → cross-ref banner, "1. Functional Architecture" (mermaid), "2. Core Logic" (numbered subsections), tables for schemas. Reference templates: `skill.md`, `compaction.md`, `tools.md`.

### Deferred
None.

### Recommended follow-up plans

Surfaced during grilling but **out of scope** for this spec per D1 (doc-only):

- **Enforce one-in-progress invariant.** Rule (D7-a) is docstring-only; add validation to `_run_fresh` and `_run_merge` in `co_cli/tools/todo/rw.py` so a write that produces >1 `in_progress` item is rejected all-or-nothing. Plan drafted: `docs/exec-plans/active/2026-05-14-093656-todo-one-in-progress-enforce.md`.

---
Summary: 10 open → 9 resolved · 0 deferred · 4 from codebase · 1 closed-from-clarification (D5) · 1 follow-up plan recommended
