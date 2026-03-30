# TODO — Product-Core Hard Parts And Simplification Plan

**Task type: architecture cleanup + product-primitive hardening**

## Goal

Keep the product goal unchanged:

- make `co`'s hardest product-specific surfaces explicit
- keep the foreground turn trustworthy and inspectable
- improve memory, delegation, async continuity, and trust UX

But align the plan with the current code after the `pydantic-ai==1.73.0` upgrade.

This is a forward plan for simplifying the existing system. It is not a proposal to replace the runtime.

## Current Position

The codebase already has the right high-level runtime split:

- `_chat_loop()` is the control plane
- `run_turn()` is the foreground turn executor
- `_finalize_turn()` owns post-turn lifecycle
- main and task agents already use public `pydantic_ai` APIs

The remaining hard parts are mostly product-shape problems, not runtime-shape problems:

- memory and history semantics are still flatter than the product needs
- delegation is bounded and useful, but too ephemeral as a product object
- background continuity exists, but only as subprocess continuity
- risky actions still look too much like raw arguments instead of structured actions

## Design Direction

`co` does not need a new runtime. It needs a clearer product structure around the runtime it already has.

The foreground turn should stay small and authoritative. `_chat_loop()` remains the control plane. `run_turn()` remains the only foreground executor. Approval resume stays in-turn. Post-turn work stays post-turn. This keeps the trust boundary simple and inspectable.

The next simplification layer is agent assembly. The code already uses the right SDK surfaces, but it still pays too much local boilerplate around role fallback and specialist-agent construction. That should be reduced, not abstracted into a new framework.

The larger issue is product state. Memory, checkpoints, delegation, background tasks, and approvals all exist today, but too much of their meaning still lives in conventions, strings, or storage details. The system needs explicit product objects for those behaviors.

That is also the correct reading of `pydantic_ai` 1.73. The SDK should be used where it deletes code or improves clarity:

- keep public toolset and history-processor APIs
- use wrapper toolsets or richer deferred-tool payloads only when they simplify local code
- do not move product ownership into generic capability hooks just because they exist
- do not migrate the product runtime to `Agent.from_spec()` or YAML agents

The intended end state is:

- one short diagram still explains the foreground turn
- agent and specialist construction follow a small number of explicit patterns
- memory classes are typed and inspectable
- delegation and background work share a visible lineage model
- long-lived work has explicit waiting states
- risky actions have structured previews and a better rollback story

## Workstreams

### Phase 1 — Foreground Turn Contract

Keep the current foreground split and tighten its boundaries in code.

- replace loose turn and finalization typing with explicit local contracts
- keep `_chat_loop()` control-plane-only
- keep `run_turn()` as the single foreground executor
- keep renderer behavior attached to the stream path

Primary files:

- `co_cli/main.py`
- `co_cli/context/_orchestrate.py`
- `co_cli/display/_stream_renderer.py`

Delivery:

- `docs/TODO-2-foreground-turn-contract-tightening.md`

### Phase 2 — Agent Runtime Boilerplate

Reduce repeated agent and model assembly code without adding a new framework layer.

- centralize role fallback
- shrink repeated specialist-agent constructor glue
- normalize to public SDK imports where it reduces churn
- adopt wrapper toolsets only if they remove code

Primary files:

- `co_cli/agent.py`
- `co_cli/_model_factory.py`
- `co_cli/context/_history.py`
- `co_cli/memory/_signal_detector.py`
- `co_cli/memory/_consolidator.py`
- `co_cli/tools/_subagent_agents.py`
- `co_cli/commands/_commands.py`

Delivery:

- `docs/TODO-1-agent-runtime-boilerplate-reduction.md`

### Phase 3 — Typed History And Memory

Promote memory and history semantics from conventions into typed product objects.

- distinguish learned memory, standing context, session checkpoints, and task-local context
- make history assembly respect those distinctions
- remove dead memory paths during the migration
- add one typed inspect or edit surface

Primary files:

- `co_cli/knowledge/_frontmatter.py`
- `co_cli/tools/memory.py`
- `co_cli/memory/_lifecycle.py`
- `co_cli/context/_history.py`
- `co_cli/commands/_commands.py`

Delivery:

- `docs/TODO-3-history-memory-typing.md`

### Phase 4 — Work Record Foundation

Add one first-class record for work done on the user's behalf.

- unify inline delegation and background tasks under one lineage model
- preserve scope, model, budget, and approval linkage
- expose one user-visible read path

Primary files:

- `co_cli/tools/subagent.py`
- `co_cli/tools/_subagent_agents.py`
- `co_cli/tools/task_control.py`
- `co_cli/tools/_background.py`
- `co_cli/deps.py`

Delivery:

- `docs/TODO-4-work-record-foundation.md`

### Phase 5 — Narrow Workflow Layer

Build a small product workflow layer above the task runner, not a generic engine.

- add explicit waiting states
- ship one or two concrete workflow templates
- keep workflow state inspectable and tied to work records

Primary files:

- `co_cli/tools/task_control.py`
- `co_cli/tools/_background.py`
- new workflow modules

Delivery:

- `docs/TODO-5-narrow-workflow-layer.md`

### Phase 6 — Trust UX And Action Previews

Improve risky-action previews, scope clarity, and rollback.

- render at least one risky action class as a structured preview
- improve remembered-approval wording and scope clarity
- adopt richer deferred-tool payloads only if they simplify the path
- add grouped-edit checkpoint and revert

Primary files:

- `co_cli/context/_orchestrate.py`
- `co_cli/tools/_tool_approvals.py`
- `co_cli/tools/files.py`
- `co_cli/tools/shell.py`

Delivery:

- `docs/TODO-6-trust-ux-action-previews.md`

## Strategic Principles

- Keep the foreground turn explicit and small.
- Use SDK primitives only when they delete code.
- Prefer explicit product records over runtime-only conventions.
- Type memory before making memory smarter.
- Keep delegation bounded by default.
- Improve trust through previews, provenance, and reversibility.

## Recommended Order

1. `docs/TODO-1-agent-runtime-boilerplate-reduction.md`
2. `docs/TODO-2-foreground-turn-contract-tightening.md`
3. `docs/TODO-3-history-memory-typing.md`
4. `docs/TODO-4-work-record-foundation.md`
5. `docs/TODO-5-narrow-workflow-layer.md`
6. `docs/TODO-6-trust-ux-action-previews.md`

## Non-Goals

- rewrite `co` around a different runtime framework
- move the core loop into generic SDK capability hooks for its own sake
- adopt `Agent.from_spec()` or YAML agents for the main runtime
- build recursive multi-agent autonomy
- add broad new behavior before state and trust contracts are cleaner

## Acceptance Criteria

This roadmap is succeeding when:

- the foreground turn still fits in one ownership diagram
- agent and specialist setup is materially smaller and more uniform
- history, checkpoints, standing context, and recalled memory are distinct in code
- memory recall remains read-only by default
- synchronous delegation and background tasks share one visible lineage model
- long-lived work has explicit state
- risky actions are previewable and more reversible than today
