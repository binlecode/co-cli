# TODO: Context/Harness Adoption List from Research Review

**Task type: roadmap-shaped design + incremental implementation** — capture the concrete adoptions worth pursuing from the updated context and harness research, without pulling `co` into a larger architecture than the current product needs.

## Context

The updated research review concluded that `co` should not chase wholesale peer-system architectures. The right path is selective adoption:

- keep the current product identity: local-first, approval-first, inspectable, pragmatic
- strengthen the parts of the current implementation that already match converged best practice
- adopt only the next layer of capability that the live system can actually support cleanly

This TODO is the positive counterpart to `docs/TODO-context-harness-research-alignment.md`.

## Problem & Outcome

**Problem:** The research now identifies strong adoption candidates, but they are still spread across two reference docs. That makes it easy for future work to either ignore them or over-expand them.

**Outcome:** One concrete adoption list that translates research into scoped work items, sequenced by value and risk.

## Scope

**In:**

- context-model clarifications worth adopting
- harness/approval/MCP/skills/subagent improvements worth adopting
- bounded async/background workflow improvements worth adopting
- doc and code tasks that concretely advance those adoptions

**Out:**

- large storage migrations
- framework-heavy multi-agent orchestration
- generalized plugin systems
- cloud-first product pivots
- multimodal/cross-surface expansion without a current product pull

## Adoption Summary

The main adoptions worth pursuing are:

1. make standing context explicit inside the current memory substrate
2. make memory mutation semantics more explicit and user-legible
3. make memory/history/session-summary boundaries explicit in docs and product language
4. harden approval edge cases without adding approval complexity
5. improve MCP operational legibility rather than abstracting it away
6. keep skills lightweight, but make their metadata and safety model clearer
7. improve delegated-work observability and context-boundary clarity
8. evolve background execution from shell-task visibility first, not from speculative autonomy

## Implementation Plan

### TASK-1 — Add explicit standing-context metadata to the current memory model

Adopt a small metadata mechanism for memories that should behave like standing context.

Requirements:

- stays inside the current markdown/frontmatter memory model
- remains inspectable and editable by the user
- does not introduce a second durable memory store
- can be used by `inject_opening_context()` or adjacent recall logic

Why this adoption matters:

- matches converged practice from stronger memory systems
- improves recall predictability without requiring a major redesign

**files likely affected:**

- `co_cli/tools/memory.py`
- frontmatter validation
- context/history docs

**done_when:** `co` has one explicit standing-context mechanism in the current substrate.

---

### TASK-2 — Make memory mutation semantics user-legible

Adopt clearer semantics around memory record operations:

- add
- update
- append
- delete
- list/search

This is partly already present in the tools, but the behavior and product language should become more explicit and consistent.

Why:

- converged systems increasingly treat memory as a structured evolving record, not an opaque note bag
- this improves correctness and user trust without requiring more infrastructure

**files likely affected:**

- `co_cli/tools/memory.py`
- DESIGN/research docs
- any memory-facing help text

**done_when:** Memory operations are documented and named as a stable contract, not just tool behavior.

---

### TASK-3 — Explicitly separate memory, history, session summary, and knowledge in canonical docs

Adopt a single clear contract in DESIGN docs:

- memory = durable learned state
- history = transcript
- session summary = resumability artifact
- knowledge/articles = consultable sources

Why:

- this is already how the current system mostly behaves
- making it explicit prevents future over-design and semantic drift

**files:**

- `docs/DESIGN-system.md`
- `docs/DESIGN-core-loop.md`
- any future memory/context design doc

**done_when:** One canonical DESIGN doc states this boundary directly.

---

### TASK-4 — Harden approval edge cases while preserving the current approval model

Adopt the next layer of approval quality work without increasing conceptual complexity.

Focus areas:

- remembered approval patterns
- multiline/heredoc command behavior
- skill-grant boundaries
- clearer user-visible rationale in prompts/status

Why:

- current strong systems keep paying tax here
- this is a product-trust improvement, not an architecture expansion

**files likely affected:**

- `co_cli/context/_orchestrate.py`
- `co_cli/tools/_tool_approvals.py`
- `co_cli/tools/_exec_approvals.py`
- shell policy docs

**done_when:** Approval behavior is narrower, clearer, and less surprising at the edges.

---

### TASK-5 — Improve MCP operational legibility

Adopt better MCP visibility rather than more MCP abstraction.

Focus:

- which MCP tools are connected
- which require approval
- which are degraded/unavailable
- stable naming in status and traces

Why:

- current reference systems keep surfacing MCP trust/compatibility issues
- `co` should solve those operationally, not by hiding MCP behind more runtime machinery

**files likely affected:**

- `co_cli/agent.py`
- bootstrap/status rendering
- observability/status docs

**done_when:** Users can understand the active MCP surface and its trust posture without reading config internals.

---

### TASK-6 — Keep skills lightweight, but make their contract clearer

Adopt better skill clarity without turning skills into a plugin platform.

Focus:

- explicit metadata and constraints
- clearer user-visible listing/inspection
- clearer rule that grants are turn-scoped convenience only
- stronger validation for risky metadata

Why:

- this preserves a strong current pattern
- it improves reliability without growing the runtime shape

**files likely affected:**

- `co_cli/commands/_commands.py`
- skill loading/validation logic
- slash-command and skill docs

**done_when:** Skills remain simple, but their contract is explicit and hard to misuse.

---

### TASK-7 — Improve delegated-work observability before adding more subagent capability

Adopt a stronger observability layer for specialist delegation.

Focus:

- clearer status in traces/status output
- tighter visibility into request budget, scope, and result provenance
- clearer description of what child agents can and cannot see

Why:

- current peer systems show that subagent complexity grows quickly
- observability is the safe next investment

**files likely affected:**

- `co_cli/tools/delegation.py`
- `co_cli/tools/_delegation_agents.py`
- observability/status docs

**done_when:** Delegated work is more legible without widening subagent power.

---

### TASK-8 — Improve background-task visibility before introducing agentic async workflows

Adopt the next small step in async execution by strengthening the current background-task harness.

Focus:

- clearer lineage/approval record display
- stronger artifact visibility for task status and outcomes
- better explanation of how this differs from future agentic follow-up

Why:

- `co` already has bounded async execution
- the right next move is to make it operationally stronger before making it more autonomous

**files likely affected:**

- `co_cli/tools/task_control.py`
- `co_cli/tools/_background.py`
- DESIGN docs

**done_when:** Background tasks are easier to inspect and reason about from the user surface.

---

### TASK-9 — Introduce context-boundary docs for specialists and future background agent turns

Adopt an explicit boundary contract describing:

- what specialist subagents inherit
- what they do not inherit
- what future background agent turns would be allowed to see

Why:

- this keeps future autonomy additions grounded in the current `CoDeps` model
- it prevents invisible scope creep

**files:**

- `docs/DESIGN-system.md`
- `docs/DESIGN-core-loop.md`
- future delegation/context docs

**done_when:** There is an explicit boundary contract for delegated and future background agent work.

## Prioritization

### P0 — high-value, low-architecture work

- TASK-1 standing-context metadata
- TASK-3 explicit memory/history/session-summary/knowledge boundary
- TASK-4 approval edge-case hardening
- TASK-5 MCP operational legibility
- TASK-8 background-task visibility

### P1 — strong follow-up once P0 is settled

- TASK-2 memory mutation semantics
- TASK-6 clearer skill contract
- TASK-7 delegated-work observability
- TASK-9 context-boundary docs

### P2 — only after concrete product pull

- any move from background shell tasks toward bounded agentic async follow-up
- any extension of standing context beyond the current substrate
- any stronger subagent orchestration beyond today’s read-only specialists

## Validation Strategy

For doc-only tasks:

- verify DESIGN/research docs line up with:
  - `co_cli/agent.py`
  - `co_cli/context/_history.py`
  - `co_cli/commands/_commands.py`
  - `co_cli/deps.py`
  - `co_cli/tools/delegation.py`
  - `co_cli/tools/task_control.py`
  - `co_cli/tools/_background.py`
  - `co_cli/tools/memory.py`

For implementation tasks:

- add/update targeted functional tests where behavior changes
- run repo-policy-compliant pytest commands with `.pytest-logs/` logging

## Open Questions

1. Which adoption should become the first proposal: standing-context metadata, approval hardening, or MCP legibility?
2. Should “standing context” be introduced first as a pure docs/semantics change or with tool/UI affordances in the same delivery?
3. When background tasks eventually gain more agentic behavior, should the control artifact be file-backed, session-backed, or both?
