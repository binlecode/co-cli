# RESEARCH: co-agent-context-gap
_Date: 2026-03-17_

Status: shipped-with-gaps

Aspect: context, memory, session semantics
Pydantic-AI patterns: dependency injection, history processors, opening-context injection, specialist isolation

## Executive Summary

This document re-evaluates `co`'s context-management gaps against the current codebase, not against an aspirational Pydantic-AI rewrite.

The current implementation already has several strong foundations:

- a real grouped dependency container in `CoDeps` with `services`, `config`, `session`, and `runtime`
- explicit subagent isolation through `make_subagent_deps()`
- real bounded background-task execution through `TaskRunner`
- runtime-owned approval orchestration in `_collect_deferred_tool_approvals()`

The main remaining gaps are narrower and more concrete than earlier drafts suggested:

1. memory recall still mutates files on read
2. session summaries still share the same primary type and recall path as durable memories
3. standing context is still accidental topic recall rather than explicit metadata
4. several docs still understate current background-task and boundary contracts
5. delegated-work visibility is still weaker than the isolation boundary itself

The earlier recommendation to replace current flows with `@agent.system_prompt`, typed specialist payloads, or explicit compaction agents is not supported as near-term implementation guidance by the latest code review. Those ideas remain possible future directions, but they are not the confirmed next steps for `co` today.

## Current Code Model

### 1. Dependency and Context Structure

`co_cli/deps.py` already gives the system a clear dependency model:

- `CoServices`: long-lived runtime handles such as shell, knowledge index, task runner, model registry
- `CoConfig`: scalar configuration copied from settings
- `CoSessionState`: mutable tool-visible session state such as approvals, skill grants, todos, page tokens, and skill registry
- `CoRuntimeState`: orchestration-owned transient state such as usage, safety, and opening-context state

This is already a strong, explicit contract. The main issue is not missing dependency structure; it is that some context semantics layered on top of it are still implicit or semantically blurry.

### 2. Prompt and Context Assembly

`co_cli/agent.py` currently assembles context through a mix of:

- one prebuilt `system_prompt` string from `assemble_prompt()`
- multiple `@agent.instructions` functions for date, shell guidance, project instructions, personality memories, critique, and skill listing
- history processors for opening-context injection, tool-output trimming, safety checks, and history compaction

This means `co` is not purely "pre-assembled prompt string" and not purely "dynamic runtime prompt decorators" either. It is a hybrid.

That matters because earlier research overstated the gap. The code already uses runtime-gated instruction layers effectively. The real problem is narrower:

- `inject_opening_context()` still injects recalled memory as hidden middleware
- some context categories remain implicit rather than explicitly typed in the memory substrate

### 3. Memory Behavior

`co_cli/tools/memory.py` still combines several different behaviors in one substrate:

- durable memory storage in markdown with frontmatter
- FTS-backed or grep-backed recall
- one-hop `related` traversal
- duplicate consolidation
- read-path gravity via `_touch_memory()`

The most important confirmed mismatch is that `recall_memory()` is still not read-only. After recall and optional related traversal, it calls `_touch_memory()` on direct matches, which rewrites frontmatter and updates timestamps.

This remains the clearest context-model bug because:

- the tool behaves like a read path but performs hidden writes
- tests still encode this behavior as intentional
- docs and future reasoning become harder because "what changed" is no longer explicit

### 4. Session Summaries

`_index_session_summary()` in `co_cli/context/_history.py` produces a summary for `/new`, but persistence still routes through the normal memory write path. That means session-summary artifacts are still written as ordinary `kind: memory` entries, with only tags and provenance distinguishing them.

This creates a semantic blur between:

- durable learned memory
- resumability/checkpoint artifacts

The storage substrate itself does not need to change. The gap is missing artifact typing and missing default recall separation.

### 5. Opening Context

`inject_opening_context()` still works by:

1. counting user turns
2. reading the latest user message
3. calling `recall_memory(ctx, user_msg, max_results=3)`
4. injecting the returned display as a system message

This means standing context is not a first-class concept today. Anything "always present" is only present if topic recall happens to surface it.

The gap is therefore not "co lacks context injection." It already has that. The gap is that standing context has no explicit representation in the current memory model.

### 6. Subagent Boundaries

`make_subagent_deps()` already shares `services` and `config` while resetting `session` and `runtime`. Delegation tools use this to spawn isolated read-only specialists with narrow tool surfaces:

- coder: file reads only
- research: web search/fetch only
- analysis: knowledge and Drive search only

This is a better current-state contract than earlier research gave it credit for. The live gap is not absence of isolation; it is weak user-facing visibility into:

- what boundary applied
- what budget the child ran under
- where its result came from

That makes this an observability/documentation gap, not a required rewrite to typed specialist payload models.

### 7. Background Work

Background execution is not hypothetical. The code already supports:

- task start
- status checks
- cancellation
- listing
- file-backed output and metadata
- retention cleanup and orphan recovery

The main background-task gap is therefore operational legibility:

- lineage
- approval context
- output artifact visibility
- clearer differentiation between current subprocess tasks and future agentic async ideas

### 8. Approval Semantics

The current approval flow is narrower than earlier drafts described:

1. session auto-approval
2. user prompt

Skill dispatch currently sets turn-scoped env and active skill identity, but it is no longer a second approval channel for deferred tools.

That closes the earlier shell-bypass concern. The remaining approval work is therefore more about scope legibility and documentation than about a live trust-model break.

## Confirmed Gaps

### Gap 1: Read Paths Still Mutate Memory State

#### Current behavior

`recall_memory()` still rewrites matching memory files through `_touch_memory()`.

#### Why it matters

- it makes read behavior non-obvious
- it couples retrieval ranking and durable mutation
- it creates hidden state changes that are harder to reason about and document

#### Confirmed adoption

Adopt strict read-only default recall. Keep consolidation and explicit writes as mutation paths; remove silent timestamp refresh from recall.

This is already the top-priority confirmed adoption.

### Gap 2: Session Summary Artifacts Are Still Semantically Blurry

#### Current behavior

Session summaries produced for `/new` still land in the same primary memory type as durable learned state.

#### Why it matters

- resumability artifacts and durable memory serve different product purposes
- default recall should not surface checkpoint summaries as if they were durable learned facts
- docs cannot cleanly explain the model while the artifact typing is implicit

#### Confirmed adoption

Keep the current markdown substrate, but add explicit artifact typing and default recall exclusion for session-summary artifacts.

### Gap 3: Standing Context Is Still Accidental

#### Current behavior

Opening context is driven entirely by topic recall from the latest user message.

#### Why it matters

- important stable preferences or working agreements have no explicit standing status
- recall quality depends on lexical overlap rather than intent
- the product cannot cleanly explain what "always carried forward" means

#### Confirmed adoption

Add bounded `always_on` metadata inside the existing memory substrate and inject it through a dedicated path before normal topic recall.

This keeps the current architecture while making standing context explicit.

### Gap 4: Docs Lag the Real Runtime Contracts

#### Current behavior

The code already has:

- grouped deps
- subagent isolation
- bounded background tasks
- additive MCP toolsets

But some docs still describe these areas too loosely or with stale assumptions.

#### Why it matters

- readers infer larger architectural gaps than the code actually has
- future design work drifts toward unnecessary rewrites
- product semantics remain harder to teach internally

#### Confirmed adoption

Sync DESIGN and research docs to the actual runtime contracts after the core semantic fixes land.

## Gaps That Are Real but Not Adopted as Current Work

The following ideas came up in prior research and are not dismissed forever, but they are not confirmed next steps for `co` based on the latest code review.

### 1. Full `@agent.system_prompt` Migration

Earlier research framed this as the natural replacement for opening-context middleware. The current codebase does not justify that as an immediate move.

Why it is not currently adopted:

- the agent already uses runtime-gated `@agent.instructions` successfully
- the concrete bug is not "lack of runtime prompt assembly"; it is missing explicit standing-context metadata
- replacing history-processor behavior with prompt decorators would be a larger refactor than the currently confirmed gap requires

Status: future option, not current recommendation.

### 2. Typed Specialist Payload Models Replacing `make_subagent_deps()`

Earlier research treated typed handoff models as the clean answer to subagent boundaries. The current code review shows that the live isolation contract is already sound.

Why it is not currently adopted:

- `make_subagent_deps()` already enforces the core security boundary
- the immediate problem is weak visibility and documentation, not missing isolation
- introducing multiple specialist dep models would expand architecture without a concrete failure requiring it

Status: future option, not current recommendation.

### 3. Explicit Compaction Agents and `UsageLimits`-Driven FSM Refactors

Earlier research proposed explicit compaction-agent invocation and stronger use of framework-native recovery primitives.

Why it is not currently adopted:

- compaction already exists and background precomputation already exists
- the immediate gaps are semantic clarity and inspectability, not absence of compaction machinery
- a larger workflow rewrite would exceed the current problem scope

Status: future option, not current recommendation.

### 4. Explicit Memory-Priority Mutation Tools

An explicit "promote to always_on" or memory-priority tool may become useful later, especially if standing context grows more sophisticated.

Why it is not currently adopted:

- there is no `always_on` field in the code yet
- the first step is making standing context explicit in the substrate
- adding a policy-heavy mutation tool before the substrate exists would be premature

Status: possible later follow-up after standing-context metadata ships.

## Recommended Adoption Path

The recommended path is the current canonical TODO, summarized here from highest-value confirmed fixes to follow-on legibility work.

### P0: Semantic and Trust Corrections

1. remove write-on-read behavior from `recall_memory()`
2. type session-summary artifacts explicitly and exclude them from default recall
3. add bounded `always_on` standing-context metadata

### P1: Contract Sync

4. update canonical DESIGN and research docs to match the shipped runtime contracts
5. add forward-looking guardrails that block overbuilt rewrites disconnected from current product needs

### P2: Operational Legibility

6. improve MCP operational visibility
7. improve delegated-work observability
8. improve background-task visibility
9. add explicit context-boundary docs for delegated specialists and future background turns

## Relationship to the Canonical TODO

The authoritative implementation plan is:

- `docs/TODO-context-harness-research-alignment.md`

This research document is diagnostic support for that TODO. It should not be treated as an independent roadmap.

## Guardrails

The following directions are prohibited for `co` unless new code evidence explicitly justifies them:

- **No multi-tier durable memory without new code evidence**: do not add tiered or layered memory storage systems (e.g., working memory + episodic + semantic) before a concrete failure mode in the current flat substrate is demonstrated in code.
- **No MCP-centric runtime**: MCP must remain an additive extension surface. Do not restructure the agent runtime so that MCP toolsets become the primary capability path or replace native tool registration.
- **No generic multi-agent orchestration without a concrete product requirement**: do not introduce a general-purpose agent-spawning, task-routing, or orchestration framework. Subagents must remain purpose-built read-only specialists tied to a specific product use case.

## Bottom Line

The latest code review does not support a sweeping context-architecture rewrite as the next step for `co`.

It supports a narrower, more defensible conclusion:

- keep the current grouped dependency model
- keep the current subagent isolation model
- keep the current background-task substrate
- fix the semantic mismatches that make the model harder to reason about
- improve visibility and docs before introducing larger architectural moves

That path is more consistent with `co`'s current product identity: local-first, approval-first, inspectable, and pragmatic.
