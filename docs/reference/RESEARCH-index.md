# RESEARCH: Index And Gap Map

This index reorganizes the research notes by two views:

1. system aspect
2. Pydantic-AI design pattern

It exists to reduce overlap between the research notes and to make current gaps easier to audit against the shipped runtime.

## Status Groups

- `shipped-foundations`: the note mostly describes runtime that already exists
- `shipped-with-gaps`: the note describes real shipped foundations but still contains unresolved semantic gaps or forward work
- `forward-looking`: the note is primarily future work

## By System Aspect

### Bootstrap And Runtime Contracts

- [`RESEARCH-gaps-bootstrap-converged.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-gaps-bootstrap-converged.md)
  Status: `shipped-with-gaps`
  Owns: startup gating, degradation shape, readiness verdicts, filesystem preflight

### Context, Memory, And Session Semantics

- [`RESEARCH-co-agent-context-gap.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md)
  Status: `shipped-with-gaps`
  Owns: grouped deps, memory semantics, opening context, session-summary typing, standing context

### Tools, Skills, MCP, And Delegation Harness

- [`RESEARCH-co-tools-skills-analysis.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-tools-skills-analysis.md)
  Status: `shipped-foundations`
  Owns: tool registration, approval surface, skills shape, subagent boundaries, MCP extension model

### Turn Loop, Interaction, And User-Facing Flow

- [`RESEARCH-progressive-output-best-practice.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-progressive-output-best-practice.md)
  Status: `shipped-with-gaps`
  Owns: thinking/progress display policy, progress ownership, display modes

- [`RESEARCH-input-queue.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-input-queue.md)
  Status: `forward-looking`
  Owns: queued user turns, steering/follow-up queue direction, turn-loop ownership

### Background And Asynchronous Execution

- [`RESEARCH-cron-scheduler.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-cron-scheduler.md)
  Status: `forward-looking`
  Owns: recurring tasks, scheduled runs, persistent scheduler model

### Observability And Operator Legibility

- [`RESEARCH-oberservability.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-oberservability.md)
  Status: `shipped-foundations`
  Owns: OTel spine, task/delegation event model, execution visibility direction

### Frontier Behavior And Product Direction

- [`RESEARCH-agent-behavior-gaps.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-agent-behavior-gaps.md)
  Status: `forward-looking`
  Owns: critic loops, retrieval-unit quality, intentionally deferred multi-agent/routing work

- [`ROADMAP-co-evolution.md`](/Users/binle/workspace_genai/co-cli/docs/reference/ROADMAP-co-evolution.md)
  Status: `shipped-foundations`
  Owns: strategic product direction from the shipped system

- [`RESEARCH-peer-systems.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-peer-systems.md)
  Status: `reference synthesis`
  Owns: frontier convergence scan and long-range product signals

- [`RESEARCH-voice.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-voice.md)
  Status: `forward-looking`
  Owns: voice surface, transcript-first architecture, interruption semantics for audio

## By Pydantic-AI Design Pattern

### Dependency Injection And Runtime State

Primary notes:
- [`RESEARCH-co-agent-context-gap.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md)
- [`RESEARCH-pydantic-ai-vs-pi-mono.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-pydantic-ai-vs-pi-mono.md)

Current gaps:
- memory semantics layered on top of `CoDeps` are still blurrier than the dependency model itself
- standing context remains implicit in the memory substrate

### Tool Registration, Structured Output, And Approval Deferral

Primary notes:
- [`RESEARCH-co-tools-skills-analysis.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-tools-skills-analysis.md)
- [`RESEARCH-co-agent-context-gap.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md)

Current gaps:
- clearer approval-scope language and visibility
- more operator-visible MCP trust/degraded-mode semantics

### Streaming Event Loop And Frontend Rendering

Primary notes:
- [`RESEARCH-progressive-output-best-practice.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-progressive-output-best-practice.md)
- [`RESEARCH-input-queue.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-input-queue.md)
- [`RESEARCH-pydantic-ai-vs-pi-mono.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-pydantic-ai-vs-pi-mono.md)

Current gaps:
- no queue around the turn loop
- no persistent `off|summary|full` reasoning/progress mode
- weak separation between waiting state and richer progress ownership

### History Processors, Compaction, And Opening Context

Primary notes:
- [`RESEARCH-co-agent-context-gap.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md)

Current gaps:
- read-on-recall mutation
- session-summary artifact typing
- explicit standing-context injection path

### Specialist Agents, Usage Limits, And Isolation

Primary notes:
- [`RESEARCH-co-tools-skills-analysis.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-tools-skills-analysis.md)
- [`RESEARCH-co-agent-context-gap.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md)
- [`RESEARCH-oberservability.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-oberservability.md)

Current gaps:
- better delegated-work visibility
- stronger lineage/budget reporting
- bounded backgroundable workflows beyond subprocess tasks

### Durable And Asynchronous Workflows

Primary notes:
- [`RESEARCH-cron-scheduler.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-cron-scheduler.md)
- [`RESEARCH-oberservability.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-oberservability.md)
- [`ROADMAP-co-evolution.md`](/Users/binle/workspace_genai/co-cli/docs/reference/ROADMAP-co-evolution.md)

Current gaps:
- recurring schedules
- resumable multi-step workflows
- event model that unifies background tasks and delegated work

## Aggregate Gap Map

### P0 Semantic Gaps

- `memory.read_path_mutates_state`
  Aspect: context and memory
  Pattern: history/context semantics
  Primary note: [`RESEARCH-co-agent-context-gap.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md)

- `session_summary_artifacts_not_typed`
  Aspect: context and session
  Pattern: history/context semantics
  Primary note: [`RESEARCH-co-agent-context-gap.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md)

- `standing_context_not_explicit`
  Aspect: context and session
  Pattern: history/context semantics
  Primary note: [`RESEARCH-co-agent-context-gap.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md)

### P1 Runtime Contract Gaps

- `startup_has_no_structured_readiness_verdict`
  Aspect: bootstrap
  Pattern: runtime contract / dependency bootstrap
  Primary note: [`RESEARCH-gaps-bootstrap-converged.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-gaps-bootstrap-converged.md)

- `workspace_state_paths_lack_preflight`
  Aspect: bootstrap
  Pattern: runtime contract / dependency bootstrap
  Primary note: [`RESEARCH-gaps-bootstrap-converged.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-gaps-bootstrap-converged.md)

- `mcp_operational_legibility_is_thin`
  Aspect: tools and extension surface
  Pattern: toolsets and approval deferral
  Primary note: [`RESEARCH-co-tools-skills-analysis.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-tools-skills-analysis.md)

### P1 Interaction Gaps

- `no_turn_input_queue`
  Aspect: turn loop
  Pattern: streaming event loop
  Primary note: [`RESEARCH-input-queue.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-input-queue.md)

- `reasoning_display_control_plane_is_too_coarse`
  Aspect: user-facing interaction
  Pattern: streaming event loop
  Primary note: [`RESEARCH-progressive-output-best-practice.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-progressive-output-best-practice.md)

### P2 Workflow And Observability Gaps

- `delegated_work_has_weak_lineage_and_budget_visibility`
  Aspect: delegation and observability
  Pattern: specialist agents and usage limits
  Primary notes:
  [`RESEARCH-co-agent-context-gap.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md),
  [`RESEARCH-oberservability.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-oberservability.md)

- `background_tasks_are_subprocess_only`
  Aspect: asynchronous execution
  Pattern: durable workflows
  Primary notes:
  [`RESEARCH-cron-scheduler.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-cron-scheduler.md),
  [`ROADMAP-co-evolution.md`](/Users/binle/workspace_genai/co-cli/docs/reference/ROADMAP-co-evolution.md)

- `execution_events_model_missing`
  Aspect: observability
  Pattern: durable workflows / streaming events
  Primary note: [`RESEARCH-oberservability.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-oberservability.md)

### P3 Frontier Extensions

- `critic_loop_for_high_risk_actions`
  Aspect: behavior quality
  Pattern: output validation / reflection
  Primary note: [`RESEARCH-agent-behavior-gaps.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-agent-behavior-gaps.md)

- `retrieval_units_for_long_sources_are_weak`
  Aspect: knowledge quality
  Pattern: retrieval and evidence gathering
  Primary note: [`RESEARCH-agent-behavior-gaps.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-agent-behavior-gaps.md)

- `voice_surface_not_shipped`
  Aspect: multimodal surface
  Pattern: streaming interaction surface
  Primary note: [`RESEARCH-voice.md`](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-voice.md)

## Reorg Rules

- Use the aspect owner doc as the primary home for a gap. Other docs should link, not restate.
- Use `RESEARCH-peer-systems.md` for broad frontier synthesis only, not implementation plans.
- Use `RESEARCH-pydantic-ai-vs-pi-mono.md` for framework pattern framing only, not for current-state repo truth.
- Keep implementation-shape gaps in the aspect doc that matches the shipped subsystem.
