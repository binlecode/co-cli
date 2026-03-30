# TODO — Product-Core Hard Parts And Forward Plan

## Goal

Turn `co`'s hardest product-specific surfaces into explicit, maintainable product primitives instead of leaving them spread across chat-loop glue, tool conventions, and historical one-off fixes.

This TODO is not a generic "agent rewrite" plan.

It is a practical product-architecture plan for the parts of `co` that remain hard even with a stronger SDK:

- foreground turn control and approval-resume
- history validity and compaction
- memory semantics and user-model quality
- session and async task continuity
- bounded delegation and specialist observability
- trust UX for actions, previews, and reversibility

It also incorporates current peer-system/product signals from practical product docs, not research papers.

## Executive Verdict

`co` already has the right instincts:

- local-first control
- explicit approval boundaries
- inspectable state
- grouped deps and specialist isolation
- memory as a product differentiator

The remaining challenge is not missing "agent capabilities."

The real challenge is that several product-critical behaviors are still encoded as mixed concerns:

- one part policy
- one part orchestration
- one part display
- one part storage convention

That makes the system harder to reason about than it needs to be.

The best-practice direction is:

- keep foreground chat turns simple and authoritative
- move long-lived work into explicit workflow/task primitives
- make memory typed and legible
- make approvals first-class checkpoints, not just tool-call interruptions
- keep delegation bounded, visible, and budgeted
- optimize for reversibility and inspectability over clever autonomy

## Peer-System Signals

### Anthropic Claude Code

What matters:

- hierarchical memory scopes and recursive project memory lookup
- first-class subagents with isolated context and permissions
- explicit settings/permission surfaces

Product signal:

- memory scope boundaries and specialist isolation are now table stakes for serious assistants, not optional extras

Relevant docs:

- `https://docs.anthropic.com/en/docs/claude-code/memory`
- `https://docs.anthropic.com/en/docs/claude-code/sub-agents`
- `https://docs.anthropic.com/en/docs/claude-code/settings`

### OpenAI Agents SDK / Agent product direction

What matters:

- foreground runner loop remains simple
- streaming, results, context management, and human-in-the-loop are explicit concepts
- durable execution is treated as a separate concern for long-lived workflows

Product signal:

- do not overload the foreground chat loop with background-work semantics
- durable workflows and human approval belong in explicit workflow/runtime layers

Relevant docs:

- `https://openai.github.io/openai-agents-python/agents/`
- `https://openai.github.io/openai-agents-python/running_agents/`
- `https://openai.github.io/openai-agents-python/ref/guardrail/`

### Letta

What matters:

- structured memory blocks
- always-visible core memory instead of retrieval-only memory
- explicit memory block operations

Product signal:

- stable standing context should be explicit and typed, not merely retrieved by luck

Relevant docs:

- `https://docs.letta.com/letta-code/memory`
- `https://docs.letta.com/guides/agents/custom-memory/`

### Mem0

What matters:

- explicit add/update/delete memory operations
- emphasis on evolving user state instead of append-only notes

Product signal:

- memory mutation semantics should be explicit, typed, and conflict-aware

Relevant docs:

- `https://docs.mem0.ai/overview`
- `https://docs.mem0.ai/v0x/core-concepts/memory-operations/update`

### Pydantic AI

What matters:

- stronger built-ins around capabilities, hooks, thinking, MCP/tool composition

Product signal:

- SDK built-ins can reduce local boilerplate
- they do not solve `co`'s hardest product-specific behaviors on their own

Relevant docs:

- `https://github.com/pydantic/pydantic-ai/releases`
- `https://ai.pydantic.dev/builtin-tools/`

## Current Hard Parts In `co`

### 1. Foreground Turn Ownership Is Correct But Too Distributed

Current ownership is split across:

- `co_cli/main.py`
- `co_cli/context/_orchestrate.py`
- `co_cli/display/_stream_renderer.py`
- `co_cli/tools/_tool_approvals.py`

Current strengths:

- the foreground turn has a real owner
- approval-resume stays inside one turn
- text, thinking, tool output, and retry handling are not completely tangled
- `_run_foreground_turn()` and `_finalize_turn()` already removed a meaningful amount of chat-loop glue from `_chat_loop()`
- approval resume can run through a lightweight task agent instead of the full main-agent prompt stack

Current problems:

- turn lifecycle is still conceptually spread across main loop, turn executor, renderer, and approval helpers, even though the boundaries are cleaner than before
- approval semantics are still modeled as deferred tool-call interruptions rather than higher-level action checkpoints
- post-turn signal detection and session persistence still sit outside `run_turn()`
- slash-command flow and delegated-agent flow still create transcript/state branching without durable work records

Why this is hard:

- correctness depends on message-history integrity, not just on user-visible output
- an interrupt or approval bug can silently corrupt the next turn
- frontend and orchestration state must remain coherent across partial segments

Best-practice direction:

- the foreground chat loop should remain a small, authoritative executor
- all other long-lived or deferred behavior should be pushed out into explicit workflow/task primitives
- approval checkpoints should be modeled at the product-action level where possible, not only at raw tool-call level

Confirmed solution direction:

- keep `run_turn()` as the single foreground owner
- keep `main.py` focused on REPL/startup/session orchestration; do not move turn logic back into it
- create explicit internal contracts for:
  - approval collection
  - post-turn lifecycle
  - delegated-work records
  - background workflow/task orchestration above the current subprocess runner

### 2. History Validity And Compaction Are Still Product-Critical And Easy To Break

Current code:

- `co_cli/context/_history.py`
- `co_cli/main.py`
- `co_cli/commands/_commands.py`

Current strengths:

- history processors are explicit and layered
- tool-output trimming, safety detection, opening-context injection, and compaction are all real features, not aspirations
- background precomputation already exists
- `HistoryCompactionState` now owns the background compaction task lifecycle and the precomputed-compaction cache boundary

Current problems:

- automatic compaction policy, manual `/compact`, and checkpointing still use different paths
- valid message-boundary preservation is subtle and fragile
- standing context and retrieved context are now split, but memory typing is still too weak to make the separation legible everywhere
- compaction artifacts are still message-shaped placeholders rather than first-class internal product objects

Why this is hard:

- invalid history is worse than missing history
- if the system compacts at the wrong boundary, resumed turns can become semantically wrong
- compaction is not just an optimization; it is a trust and continuity feature

Best-practice direction:

- history governance should be one owned subsystem
- compaction artifacts and policies should be explicit
- standing context should not depend on the same path as topic recall

Confirmed solution direction:

- keep `HistoryCompactionState` as the compaction owner
- keep history processors, but reduce them to narrow, clearly-owned transformations
- split:
  - standing context
  - topic recall
  - summary/checkpoint artifacts
  - trim/compaction placeholders
- unify `/compact` and automatic compaction governance where shared logic actually helps

### 3. Memory Is Useful, But The Product Model Is Still Flatter Than It Needs To Be

Current code:

- `co_cli/tools/memory.py`
- `co_cli/memory/_lifecycle.py`
- `co_cli/memory/_consolidator.py`
- `co_cli/memory/_signal_detector.py`

Current strengths:

- memory already has dedup, consolidation, retention, decay, proactive injection, and article separation
- `always_on` exists
- session-summary artifacts are separated with `artifact_type="session_summary"` and are excluded from default recall/search
- targeted mutation tools already exist via `update_memory` and `append_memory`

Current problems:

- memory is still too close to "smart note store" instead of a typed user/project model
- `_touch_memory()` still exists in `co_cli/tools/memory.py`, but it is dead code now; the contract should be made explicit by deleting it
- `always_on` solves standing context injection, but not typed user/project/profile semantics
- durable user facts, project norms, checkpoint summaries, and task-local context still live too close together
- low-confidence extracted memory still risks polluting durable state

Why this is hard:

- user modeling fails gradually, not all at once
- a memory system can appear to work while quietly storing stale, contradictory, or over-generalized state
- personalization only helps if the system knows what kind of thing it is remembering

Best-practice direction:

- memory should be typed, source-aware, time-aware, and user-editable
- explicit memory operations should exist for add/update/supersede/archive, not only "save"
- standing memory should be distinct from retrieved memory
- checkpoint/session-summary artifacts should not be recalled as ordinary learned facts

Confirmed solution direction:

- introduce first-class memory classes inside the existing markdown substrate, building on the existing `artifact_type`/`always_on` fields:
  - `user_profile`
  - `project_norm`
  - `working_preference`
  - `standing_instruction`
  - `session_checkpoint`
  - `ephemeral_task_context`
- keep recall read-only by default
- remove the dead `_touch_memory()` helper so the read-only contract is obvious in code
- add inspection/edit flows that expose the user model directly

### 4. Async Autonomy Exists, But Only In Stage-One Form

Current code:

- `co_cli/tools/task_control.py`
- task-runner/storage internals
- `co_cli/main.py`

Current strengths:

- background subprocess tasks are real
- tasks have persisted state and status surfaces
- there is already a basis for off-turn work
- task metadata already persists `approval_record` and `span_id`, plus retention cleanup and orphan recovery

Current problems:

- async work is subprocess-shaped, not workflow-shaped
- no first-class recurring plans
- no explicit approval checkpoints inside multi-step background work
- there is still no durable lineage between foreground turn, delegated work, task artifact, and eventual follow-up

Why this is hard:

- async autonomy compounds risk faster than synchronous chat
- if the user cannot inspect lineage, approvals, and intermediate state, trust collapses
- recurring work needs stronger state and policy contracts than one-shot subprocesses

Best-practice direction:

- keep foreground chat turns synchronous and simple
- introduce explicit workflow/task objects for multi-step or deferred work
- approvals, credentials, waiting, retries, and reminders should all be modeled as workflow states

Confirmed solution direction:

- evolve from `TaskRunner` as subprocess runner to a more general workflow/task runner
- start with narrow workflow classes:
  - `research_and_report`
  - `watch_and_notify`
  - `revisit_later`
  - `approval_blocked_plan`
- keep tasks inspectable as files plus metadata, not hidden runtime-only state

### 5. Delegation Boundaries Are Good, But User-Facing Legibility Is Still Weak

Current code:

- `co_cli/tools/subagent.py`
- `co_cli/tools/_subagent_agents.py`
- `co_cli/deps.py`

Current strengths:

- specialist toolsets are actually isolated
- `make_subagent_deps()` already resets session/runtime state correctly
- specialists are intentionally bounded
- synchronous subagent results already expose role, model, scope, and request-budget metadata
- subagent spans already record role/model/request-limit/requests-used

Current problems:

- allowed-tool scope and provenance are still not first-class user-visible objects
- specialist results are still shaped as synchronous tool outputs, not durable artifacts
- there is no strong notion of "delegated work record" even though delegation exists

Why this is hard:

- delegated work is only trustworthy if its boundary is visible
- child runs need traceability: who ran, with what tools, under what budget, for what purpose
- once subagents become longer-lived, ad hoc inline delegation stops scaling

Best-practice direction:

- keep specialists bounded and explicit
- expose delegation metadata as part of the product model, not just tracing
- use durable work records for delegated tasks once they leave the immediate turn

Confirmed solution direction:

- add explicit delegated-work metadata:
  - role
  - model
  - scope
  - tools allowed
  - request budget
  - source turn/session
- when delegation becomes async, persist a work record/artifact instead of only returning tool text

### 6. Trust UX Needs Stronger Reversibility And Preview Discipline

Current code:

- `co_cli/tools/_tool_approvals.py`
- `co_cli/tools/shell.py`
- file tools
- slash-command flows

Current strengths:

- approvals exist
- shell has policy classification
- session-scoped remembered approvals exist
- approval prompts already expose scoped session hints for shell utility, path, domain, and tool subjects

Current problems:

- approval units are still sometimes too raw
- file and multi-step changes still need better previews and rollback ergonomics
- "always" approvals are session-scoped but not time-bounded or reason-bounded
- trust depends too much on the user reading raw tool arguments

Why this is hard:

- users trust agents through clarity and reversibility, not only through permission prompts
- a system can be permissioned and still feel unsafe if actions are hard to inspect

Best-practice direction:

- approvals should be action-shaped
- previews should be default for broad edits
- destructive or high-scope actions should have stronger checkpointing
- approval scope language should match the user's mental model

Confirmed solution direction:

- add action-preview objects for risky write batches
- strengthen approval subject language with scope and expiry
- add reversible checkpoint support for grouped file edits and multi-step plans

## Strategic Principles Moving Forward

These principles should constrain all near-term design work:

### 1. Foreground turns stay simple

The chat loop should own immediate interaction only.

Do not turn the foreground turn executor into a full workflow engine.

### 2. Durable work must become explicit

Anything that spans:

- approvals
- retries
- time
- user absence
- or multiple delegated steps

should be represented as an explicit task/workflow object.

### 3. Memory must be typed before it gets smarter

Do not chase more extraction or more recall before the substrate clearly distinguishes:

- standing instructions
- profile facts
- project norms
- checkpoints
- ephemeral task context

### 4. Inspection beats hidden optimization

Prefer:

- explicit artifacts
- visible lineage
- editable state

over hidden heuristics, even when the hidden approach is shorter.

### 5. Specialist isolation is a product feature

Subagent scoping is not just an implementation detail.

It should stay bounded, legible, and intentionally narrow.

### 6. Trust is built by previews, provenance, and reversibility

Approval prompts alone are not enough.

## Proposed Solution Plan

## Phase 1 — Clarify Ownership Boundaries

- [ ] Reduce `main.py` further toward REPL/startup/session ownership only.
- [ ] Keep post-turn signal detection/session persistence from spreading back into generic chat-loop code as more features land.

Files:

- `co_cli/main.py`
- `co_cli/context/_orchestrate.py`
- `co_cli/display/_stream_renderer.py`
- `co_cli/tools/_tool_approvals.py`

Delivery: `docs/TODO-phase1-ownership.md`

## Phase 2 — Make History Governance One Coherent Subsystem

- ✓ DONE: Manual `/compact` and automatic compaction both use `_run_summarization_with_policy()` in `_history.py` — paths are already unified.
- [ ] Decide whether compaction summaries/markers should become explicit internal artifact types instead of ad hoc message bodies.

Files:

- `co_cli/context/_history.py`
- `co_cli/commands/_commands.py`
- `co_cli/main.py`
- `co_cli/deps.py`

## Phase 3 — Upgrade Memory From Flat Recall To Typed User Model

- [ ] Add first-class typed memory classes within the existing markdown substrate.
- [ ] Add explicit `supersede` and `archive` semantics rather than only add/update/append/checkpoint.
- [ ] Remove dead `_touch_memory()` helper from `co_cli/tools/memory.py` — still present (code scan 2026-03-29); was marked DONE prematurely.
- [ ] Add a typed inspection/edit surface for "what `co` knows" beyond raw memory inventory.

Files:

- `co_cli/tools/memory.py`
- `co_cli/memory/_lifecycle.py`
- `co_cli/memory/_consolidator.py`
- `co_cli/memory/_signal_detector.py`
- future command/UI surface for memory inspection

## Phase 4 — Evolve Async Work Into Explicit Workflows

- [ ] Add a narrow workflow layer above them for agentic deferred work.
- [ ] Model explicit workflow states such as:
  - `queued`
  - `running`
  - `waiting_for_approval`
  - `waiting_for_input`
  - `waiting_until`
  - `completed`
  - `failed`
  - `cancelled`
- [ ] Record source-turn/session lineage and approval checkpoints as first-class task metadata.
- [ ] Add one or two workflow templates only; do not generalize too early.

Files:

- `co_cli/tools/task_control.py`
- task-runner/storage modules
- `co_cli/main.py`
- new workflow module(s)

## Phase 5 — Make Delegation Legible And Durable

- [ ] Add explicit delegated-work records for slash-command and child-agent branches (moved from Phase 1 — land alongside the first concrete reading surface).
- [ ] Add explicit delegated-work artifact contracts for anything longer-lived than one synchronous tool result.
- [ ] Expose allowed-tool scope and provenance (tools_allowed, requests_limit) in both traces and user-visible status.
- [ ] Persist delegated work artifacts when a child run becomes async or long-lived.

Files:

- `co_cli/tools/subagent.py`
- `co_cli/tools/_subagent_agents.py`
- `co_cli/deps.py`

## Phase 6 — Improve Trust UX

- [ ] Strengthen action previews for write-heavy or multi-file operations.
- [ ] Add checkpoint/revert surfaces for grouped edits.
- [ ] Improve approval subject language with clearer scope semantics.
- [ ] Add expiry or stronger scoping for remembered approvals where appropriate.
- [ ] Treat previews and provenance as first-class output objects, not ad hoc strings.

Files:

- `co_cli/tools/_tool_approvals.py`
- `co_cli/tools/files.py`
- `co_cli/tools/shell.py`
- display/frontend modules

## Concrete Best-Practice Adoptions

These are the specific patterns to adopt from peers without copying any one system wholesale.

### Adopt From Claude Code

- hierarchical memory scope
- explicit project/user memory separation
- specialist isolation with clear permissions

### Adopt From OpenAI Agents SDK

- simple foreground runner
- separate durable workflow layer for long-lived work
- explicit guardrail/human-in-the-loop concepts

### Adopt From Letta

- structured always-visible standing memory
- explicit memory blocks for stable context

### Adopt From Mem0

- explicit mutation semantics for evolving memories
- conflict-aware updates instead of note accumulation

### Adopt From Pydantic AI

- new built-ins where they delete SDK boilerplate
- do not expect them to solve product-specific ownership problems

## Things To Reject

- [ ] Reject a full foreground-loop rewrite into a general graph/workflow engine.
- [ ] Reject untyped "more memory" work before memory-class separation lands.
- [ ] Reject making subagents broad or recursive by default.
- [ ] Reject hidden background autonomy that does not produce inspectable state.
- [ ] Reject using new SDK features only for novelty if they do not reduce local complexity.

## Acceptance Criteria

This roadmap is succeeding when:

- foreground turn ownership can be explained in one short module boundary diagram
- history compaction and standing-context logic have one clear owner
- memory recall is read-only by default
- the user model is inspectable by type, not just by raw memory files
- async tasks/workflows expose lineage, state, and approval checkpoints
- delegated work reports role, scope, tools, model, and budget clearly
- risky writes have better preview and rollback ergonomics than today

## Recommended Implementation Order

1. ownership cleanup for foreground turn vs background work
2. history/compaction ownership cleanup
3. memory typing and read-only recall
4. delegation accounting and delegated-work metadata
5. workflow/task state model above subprocess tasks
6. trust UX improvements for previews/checkpoints

## Delivery Bundle Plan

| # | Slug | Phases | Rationale | Size |
|---|------|--------|-----------|------|
| ✅ | `phase1-ownership` | Ph1 + Ph3 micro | Done — see `docs/TODO-phase1-ownership.md` | Small |
| 2 | `phase3-memory-typing` | Ph3 + Ph2 remaining | Ph2's open item (compaction artifact types) is answered by Ph3's memory class decisions — `session_checkpoint` resolves it naturally | Medium |
| 3 | `phase5-delegation-sync` | Ph5 synchronous | SkillRunResult, skill_run_history, tools_allowed/requests_limit — must land with its first reading surface; too small to absorb Ph4, too coupled to ship headless | Small |
| 4 | `phase4-async-workflows` | Ph4 | Workflow states, lineage, approval checkpoints, 1–2 templates; depends on Ph5 work record concepts existing | Large |
| 5 | `phase6-trust-ux` | Ph6 | Action previews, checkpoint/revert, approval language + expiry; independent of Ph4 — can be pulled forward if trust UX becomes higher priority | Medium |

## Non-Goals

- rewrite `co` around a different framework
- add more connectors before context fusion improves
- add broad multimodal surfaces before state/approval contracts are stronger
- build recursive autonomous multi-agent systems
- optimize for benchmarked "agent power" at the cost of inspectability

## Sources

Peer and product docs consulted for this TODO:

- `https://docs.anthropic.com/en/docs/claude-code/memory`
- `https://docs.anthropic.com/en/docs/claude-code/sub-agents`
- `https://docs.anthropic.com/en/docs/claude-code/settings`
- `https://openai.github.io/openai-agents-python/agents/`
- `https://openai.github.io/openai-agents-python/running_agents/`
- `https://openai.github.io/openai-agents-python/ref/guardrail/`
- `https://docs.letta.com/letta-code/memory`
- `https://docs.letta.com/guides/agents/custom-memory/`
- `https://docs.mem0.ai/overview`
- `https://docs.mem0.ai/v0x/core-concepts/memory-operations/update`
- `https://github.com/pydantic/pydantic-ai/releases`
- `https://ai.pydantic.dev/builtin-tools/`

Internal repo references consulted:

- `docs/reference/RESEARCH-peer-systems.md`
- `docs/reference/RESEARCH-co-agent-context-gap.md`
- `docs/reference/ROADMAP-co-evolution.md`
- `docs/DESIGN-core-loop.md`
