# TODO — Product-Core Hard Parts Alignment

**Task type: doc-restructure**

## Context

This TODO was written as a forward roadmap, but parts of it no longer match the codebase after the `pydantic-ai==1.73.0` upgrade and the follow-on product work that already landed.

### Code accuracy verification

- The foreground runtime is already using the right public Pydantic-AI surfaces: one main `Agent`, one lightweight resume agent, `RunContext[CoDeps]`, `history_processors`, filtered native toolsets, additive MCP toolsets, and `DeferredToolRequests` / `DeferredToolResults`.
- Old Phase 1 overstates how much foreground-turn work is still open. `run_turn()`, `_TurnState`, `TurnResult`, `_run_approval_loop()`, and `_finalize_turn()` already make the foreground contract explicit.
- Old Phase 2 is still real, but narrower than described. The remaining issue is local boilerplate around main/task agent construction and specialist factories, not a missing runtime abstraction.
- Old Phase 3 is no longer greenfield. `session_summary` artifact typing, default recall exclusion for session checkpoints, and `always_on` standing context are already implemented.
- Old Phase 4 is partially shipped, not complete. Inline subagent lineage now carries `run_id`, session background tasks exist in `session.background_tasks`, and `/history` plus `/tasks` expose a useful live operator surface, but that surface is still session-local, transcript-derived, and lost on shutdown or history reset.
- Old Phase 5 is still real, but it needs to be reframed around explicit async work states and bounded orchestration, not a generic workflow engine.
- Old Phase 6 remains valid and should stay downstream of the async-work-state cleanup.

### Workflow artifact hygiene

- The current repo no longer contains a dedicated Phase 4 delivery TODO file, so Phase 4 status must be judged from code only. This roadmap should not depend on a missing artifact for validation.

### Workstream review

1. **Phase 1 — Foreground Turn Contract:** keep, but narrow. The control-plane split is already right; remaining work is mainly local type tightening and reducing residual `Any`/loose contracts where they still obscure ownership.
2. **Phase 2 — Agent Runtime Boilerplate:** keep, but move behind W1. This is still a worthwhile simplification slice, but it should follow the user-visible async-work-state gap rather than lead it.
3. **Phase 3 — Typed History And Memory:** mostly shipped; retire as a standalone roadmap phase. What remains belongs to incremental memory UX and compaction hardening, not a broad semantic rewrite.
4. **Phase 4 — Work Record Foundation:** partially shipped; fold into the next roadmap slice instead of retiring it. The missing piece is durable, inspectable work-state semantics above the current session-local operator surface.
5. **Phase 5 — Narrow Workflow Layer:** keep, but redefine. The target is explicit waiting, queue, timeout, and delivery states over current delegation/background primitives, starting from the still-incomplete Phase 4 foundation.
6. **Phase 6 — Trust UX And Action Previews:** keep, but make it dependent on the revised async-work-state workstream.

## Problem & Outcome

**Problem:** the current roadmap mixes shipped work, real remaining gaps, and speculative redesigns. It risks sending implementation into areas that are already done while understating the actual missing product layer.

**Failure cost:** Ankor or future dev work can duplicate shipped lineage/memory changes, introduce non-idiomatic Pydantic-AI abstractions, and miss the true hard part: inspectable, bounded async work states above the existing runtime.

**Outcome:** this TODO becomes an evidence-backed alignment artifact. It should tell Ankor, in one pass, which old phases are retired, which are still valid, which “shipped” claims are only partial, what Pydantic-AI guidance should be treated as the current default, and what agentic-flow patterns are worth borrowing from Codex, OpenCode, and OpenClaw.

## Scope

- Rewrite this roadmap against the current `co-cli` codebase only.
- Limit the alignment to the product-core hard parts roadmap; do not redefine the broader product roadmap.
- Fold in Pydantic-AI 1.73.0 best practice as it applies to `co`, not generic SDK feature inventory.
- Fold in convergent peer-system best practices from the local `codex`, `opencode`, and `openclaw` repos.
- Reorder the remaining product roadmap so it reflects current reality.
- Do not implement runtime changes in this TODO.

## Behavioral Constraints

- `_chat_loop()` remains the REPL/control plane and `run_turn()` remains the single foreground-turn executor.
- The main runtime stays on public Pydantic-AI primitives: `Agent`, `RunContext[CoDeps]`, `history_processors`, filtered toolsets, and deferred approval payloads.
- Do not propose `Agent.from_spec()`, YAML agents, or a generic capability-hook rewrite for the main runtime.
- Do not reopen shipped memory typing or work-record lineage as if they were still net-new phases.
- Any future async workflow work must stay bounded, inspectable, and auditable from transcript state, session state, or an explicit durable work log if one is later introduced.
- Any delegated-work design must make spawn depth, visibility, waiting state, and delivery/timeout state explicit before adding higher-order orchestration machinery.

## High-Level Design

The revised roadmap should collapse the old six-phase story into three active workstreams plus one maintenance note:

### W1 — Async Work States And Bounded Orchestration

Purpose: solve the real remaining hard part: long-lived work that is more inspectable than raw subprocesses and more bounded than an open-ended multi-agent workflow engine.

- Treat current subagent lineage and current background task state as two incomplete foundations that still need one first-class product model.
- Add visible waiting, timeout, queued, and delivery states as net-new work, not as residual polish.
- Make lineage inspectable without requiring the user to reconstruct it from raw tool arguments or transient transcript state.
- Keep orchestration product-specific and bounded; no generic workflow engine.
- Make this the first roadmap emphasis because it is the highest user-value gap.

### W2 — Agent Surface Cleanup

Purpose: reduce local assembly boilerplate while preserving the current runtime shape.

- Keep one main agent and one lightweight resume agent.
- Keep specialist agents as narrow, typed helpers.
- Remove duplicated assembly code only where it clearly deletes code or reduces churn.
- Treat this as an opportunistic maintenance lane or a dependency-free cleanup slice, not the first product-facing milestone.

### W3 — Trust UX And Structured Action Previews

Purpose: make risky actions easier to understand, approve, and roll back.

- Improve structured previews for at least one high-risk action class.
- Tighten approval scope wording and remembered-approval visibility.
- Add grouped-edit checkpoints and explicit revert affordances only where the UX becomes materially clearer.

### Maintenance Note — Memory And Compaction

Typed memory semantics are no longer the main open phase. Follow-on work here should be limited to:

- memory inspectability polish
- compaction-summary quality/identifier preservation if real failures show up
- avoiding any regression that collapses durable memory, checkpoints, and standing context back together

## Non-Goals

- Replacing the current runtime with a second orchestration framework.
- Replanning shipped memory typing or shipped work-record lineage as if those phases were still net new.
- Building a generic workflow engine, planner runtime, or agent DAG layer before bounded async work states exist.
- Migrating the main runtime to `Agent.from_spec()`, YAML agents, or SDK-native config DSLs.
- Expanding delegation depth or autonomy before visibility, status, and approval scope are explicit.

## Pydantic-AI Guidance

### Adopt

- Keep `CoDeps` as the single injected dependency object and keep tool/runtime state ownership outside tool bodies.
- Keep `history_processors` as the right place for context governance, safety checks, and compaction/truncation policy.
- Keep dynamic prompt layers as `@agent.instructions` rather than rebuilding one mutable mega-prompt every turn.
- Keep the approval loop on `DeferredToolRequests` / `DeferredToolResults`; that is the idiomatic same-turn approval-resume path and already matches the runtime.
- Keep filtered native toolsets plus additive MCP toolsets. MCP stays an extension surface, not a second runtime architecture.
- Keep specialist agents typed and isolated through `make_subagent_deps()` rather than sharing mutable session/runtime state.

### Defer Or Reject

- Reject `Agent.from_spec()` / YAML-agent migration for the main runtime.
- Reject moving product state ownership into generic SDK abstractions just because the SDK can express them.
- Defer `pydantic_graph` or other durable-workflow machinery unless `co` actually commits to resumable, multi-step agent workflows that exceed the current CLI turn model.
- Reject introducing a second orchestration framework to hide `run_turn()`; explicit turn ownership is a feature here, not a defect.

## Peer-System Guidance

### Codex

- Preserve explicit turn ownership and history ownership. Codex keeps prompt assembly and history mutation as first-class runtime responsibilities, not hidden side effects.
- Treat compaction as a real system action with one canonical replacement summary, not as silent accumulation of multiple summaries.
- Preserve the mental model that recent live context stays explicit while older context is summarized deliberately.

### OpenCode

- Keep a clear permission-scoped split between planning/analysis surfaces and full-build surfaces.
- Treat subagents as distinct session-like work units with explicit user navigation and bounded tool access.
- Prefer simple agent-mode boundaries and per-agent permission overrides over inventing new orchestration layers.

### OpenClaw

- Keep the session-tool surface small and hard to misuse.
- Make delegated work a first-class inspectable object with explicit status, lineage, and delivery semantics.
- Bound delegation with spawn-depth or equivalent visibility/control rules before expanding async workflows.
- Keep compaction and delegated-session behavior product-visible rather than burying them under generic “automation” language.

### Common best practice to adopt in `co`

- One explicit foreground executor.
- Delegation as bounded child work, not ambient autonomy.
- Inspectable lineage and status before richer orchestration.
- Permission and sandbox/approval scope expressed per agent or per tool family.
- Compaction as deliberate summary + recent context preservation.

## Implementation Plan

1. Rebaseline the roadmap around current-state evidence.
   Mark old Phase 3 as mostly shipped and old Phase 4 as partially shipped so future work starts from actual code reality.
2. Make async work states the first product-facing slice.
   Focus the next implementation TODO on explicit waiting, queue, timeout, delivery, and lineage semantics above the current split between subagent results and background tasks.
3. Keep agent-surface cleanup behind the product gap.
   Simplify main/task agent assembly and specialist boilerplate only after the user-visible async-work-state slice is clearly defined.
4. Follow with trust UX.
   Improve structured risky-action previews, approval scope wording, and grouped revert/checkpoint affordances after the async work-state model is in place.
5. Treat memory and compaction as maintenance follow-up.
   Limit future work there to inspectability and regression prevention rather than reopening the broader memory semantics phase.

## Testing

- `rg "^## Context|^## Workstream review|^## High-Level Design|^## Pydantic-AI Guidance|^## Peer-System Guidance|^## Implementation Plan|^# Audit Log" docs/TODO-product-core-hard-parts.md`
- Spot-check the rewritten claims against:
  - `co_cli/agent.py`
  - `co_cli/context/_orchestrate.py`
  - `co_cli/context/_history.py`
  - `co_cli/tools/memory.py`
  - `co_cli/tools/subagent.py`
  - `co_cli/tools/task_control.py`
  - `co_cli/commands/_commands.py`
- Peer-system verification sources:
  - `codex/REVIEW-system-architecture-by-codex.md`
  - `opencode/packages/web/src/content/docs/agents.mdx`
  - `opencode/packages/web/src/content/docs/permissions.mdx`
  - `openclaw/docs/concepts/session-tool.md`
  - `openclaw/docs/concepts/compaction.md`

## Open Questions

- Should the first W1 slice stop at session-scoped explicit work states, or should it also add durable cross-session work history immediately?
- Recommendation: make the first slice user-visible and bounded. Add explicit work-state vocabulary and inspectable lineage first; add durability only when restart/resume or `/new` semantics would otherwise erase meaningfully incomplete work.
