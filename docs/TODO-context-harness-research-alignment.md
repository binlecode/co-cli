# TODO: Align Context/Harness Research with Current `co` Implementation

**Task type: docs + targeted code cleanup**

## Goal

Replace architecture-level guidance with implementation-ready work tied to the live code paths that currently drift from the intended model.

This TODO is grounded in a code scan of:

- `co_cli/tools/memory.py`
- `co_cli/memory/_lifecycle.py`
- `co_cli/context/_history.py`
- `co_cli/context/_orchestrate.py`
- `co_cli/deps.py`
- `co_cli/tools/task_control.py`
- `co_cli/tools/_background.py`
- `co_cli/commands/_commands.py`
- `co_cli/tools/_tool_approvals.py`
- `co_cli/tools/shell.py`
- `tests/test_memory.py`
- `tests/test_delegate_coder.py`

## Current Code Facts

These are the implementation facts this delivery must align docs and behavior around.

### FACT-1 — `recall_memory()` mutates files on read

Current flow in `co_cli/tools/memory.py`:

1. search via FTS or grep
2. `_dedup_pulled(matches, ...)`
3. follow one-hop `related`
4. call `_touch_memory()` on each direct match

`_touch_memory()` rewrites frontmatter and sets `updated=<now>`. Default recall is therefore not read-only.

### FACT-2 — session summaries are stored as ordinary memories

Current flow:

1. `/new` in `co_cli/commands/_commands.py` calls `_index_session_summary()`
2. it persists the summary through `persist_memory(...)`
3. `persist_memory()` always writes `kind: memory`
4. the only session-specific marker is `tags=["session"]` and `provenance="session"`

Result: resumability artifacts and durable user memory share the same primary type and recall path.

### FACT-3 — opening context currently uses topic recall only

`inject_opening_context()` in `co_cli/context/_history.py` currently:

1. finds the latest user message
2. calls `recall_memory(ctx, user_msg, max_results=3)`
3. injects the returned display as a system message

There is no explicit "always include this standing context" path. Any standing behavior is currently accidental.

### FACT-4 — subagent isolation is already implemented correctly

`make_subagent_deps()` in `co_cli/deps.py`:

- shares `services`
- shares `config`
- resets `session`
- resets `runtime`

This is already covered by `tests/test_delegate_coder.py::test_make_subagent_deps_resets_session_state`.

### FACT-5 — background task support already exists beyond "future async"

Current background-task behavior:

- `start_background_task()` starts a subprocess through `TaskRunner`
- `TaskRunner` persists metadata and output under `.co-cli/tasks/<task_id>/`
- `TaskRunner.__init__()` performs retention cleanup and crash-orphan recovery
- `check_task_status()` returns exit code, duration, and tailed output
- `cancel_background_task()` kills the running task
- `list_background_tasks()` lists session-visible tasks
- `precompute_compaction()` already performs bounded background work during idle time

### FACT-6 — skill grants can bypass approval for deferred tools

Current approval chain in `_collect_deferred_tool_approvals()`:

1. skill grant auto-approval
2. session auto-approval
3. user prompt

`run_shell_command()` treats `ctx.tool_call_approved` as sufficient to execute non-safe commands. That means a skill that grants `run_shell_command` can bypass the normal user prompt for shell commands that are not DENY-classified.

This is a real contract mismatch. Skill grants are currently functioning as a second approval channel.

## Non-Goals

Do not introduce any of the following in this delivery:

- a new durable memory backend
- a multi-tier memory architecture
- a skills plugin runtime
- MCP-centric runtime rewrites
- generic multi-agent orchestration infrastructure

## Proposal-Derived Adoptions Kept

The prior `co-agent-context` proposal contained several larger refactors that do not fit the current delivery. The concrete adoptions from that analysis that do fit `co`, and are therefore retained in this TODO, are:

- retrieval should be side-effect free by default
- session summaries should be explicit resumability artifacts rather than ordinary durable memory
- standing context should be explicit in the current memory substrate via bounded metadata, not implicit recall behavior
- context boundaries should be documented against the current `CoDeps` model instead of speculative multi-agent architecture

## Delivery Plan

## TASK-1 — Remove write-on-read behavior from default recall

### Why

`recall_memory()` is described and expected as a read path, but it rewrites memory files through `_touch_memory()`.

### Processing change

Change `co_cli/tools/memory.py` so the default recall path is:

1. search
2. optional `_dedup_pulled(...)`
3. optional one-hop `related` expansion
4. format results
5. return without rewriting any matched files

Delete the `_touch_memory()` call from `recall_memory()`.

Do not replace it with another implicit write path in this delivery.

### Files

- `co_cli/tools/memory.py`
- `tests/test_memory.py`
- `docs/DESIGN-system.md`
- `docs/DESIGN-tools.md`

### Test changes

Replace read-mutation expectations with read-only expectations:

- remove or rewrite `test_recall_touches_pulled_memories`
- remove or rewrite `test_gravity_affects_recency_order`
- add a test that snapshots file contents or `updated` before/after `recall_memory()` and asserts no write occurs

### Done when

- `recall_memory()` does not call `_touch_memory()`
- recalling a memory does not change its frontmatter timestamps
- docs no longer describe "gravity" or touched-on-read recency as default behavior

---

## TASK-2 — Separate session-summary artifacts from durable memory semantics

### Why

`/new` currently stores resumability summaries as plain `kind: memory` entries with a `session` tag. That makes recall and docs blur session summaries with durable user memory.

### Processing change

Keep the same markdown substrate and file location, but add explicit artifact typing.

Implement:

1. add optional frontmatter field `artifact_type`
2. support `artifact_type: session_summary`
3. plumb `artifact_type` through `MemoryEntry`, `_load_memories()`, and `persist_memory()`
4. update `/new` to persist:
   - `kind: memory`
   - `provenance: session`
   - `artifact_type: session_summary`
   - `tags: ["session"]`
5. make `recall_memory()` and `search_memories()` exclude `artifact_type=session_summary` by default
6. make `list_memories()` display artifact type when present

This keeps storage simple while making the semantics explicit.

### Files

- `co_cli/knowledge/_frontmatter.py`
- `co_cli/tools/memory.py`
- `co_cli/memory/_lifecycle.py`
- `co_cli/commands/_commands.py`
- `docs/DESIGN-system.md`
- `docs/DESIGN-core-loop.md`
- `docs/DESIGN-tools.md`

### Test changes

Add functional coverage for:

- `/new` writing a memory record marked `artifact_type: session_summary`
- default `recall_memory()` excluding session-summary artifacts
- `list_memories()` exposing the artifact marker

### Done when

- session checkpoints are explicitly marked as `artifact_type: session_summary`
- default memory recall does not return those artifacts
- docs state that session summary is a resumability artifact, not long-term memory

---

## TASK-3 — Add explicit standing-context metadata inside the existing memory substrate

### Why

Research calls for standing context, but the current code has no explicit field for it. `inject_opening_context()` only does topic-based recall from the last user turn.

### Processing change

Add a first-class `always_on: bool` memory field.

Implement:

1. extend frontmatter validation to accept `always_on: bool`
2. extend `MemoryEntry` and `_load_memories()` to carry `always_on`
3. extend `persist_memory()` to write `always_on`, default `False`
4. extend `save_memory()` to accept `always_on: bool = False`
5. add a helper in `co_cli/tools/memory.py` to load a small bounded set of `always_on` memories
6. update `inject_opening_context()` to inject:
   - explicit `always_on` memories first
   - then demand-driven recall for the current user message
7. cap default standing-context injection to a small fixed bound so it does not become a second transcript

Use `always_on` as the name. It already matches the current proposal vocabulary and avoids reopening naming work.

### Files

- `co_cli/knowledge/_frontmatter.py`
- `co_cli/tools/memory.py`
- `co_cli/memory/_lifecycle.py`
- `co_cli/context/_history.py`
- `docs/DESIGN-system.md`
- `docs/DESIGN-core-loop.md`
- `docs/reference/RESEARCH-co-agent-context.md`

### Test changes

Add functional coverage for:

- saving a memory with `always_on=True`
- loading only `always_on` memories
- `inject_opening_context()` injecting standing context even when the latest user message has no lexical match
- `always_on=False` remaining the default for old and new records

### Done when

- there is one explicit standing-context field in the current memory model
- standing context is injected through a dedicated path rather than incidental recall behavior
- no new storage tier is introduced

---

## TASK-4 — Prevent skill grants from bypassing shell and other approval-gated tools

### Why

The current approval ordering lets `skill_tool_grants` auto-approve any deferred tool call before the normal prompt path runs. This can bypass shell approval for `run_shell_command`.

### Processing change

Constrain skill grants to convenience for already-safe tooling, not approval bypass.

Implement:

1. add a helper in `co_cli/context/_orchestrate.py` that checks whether a skill grant is eligible for auto-approval
2. deny skill-grant auto-approval when `deps.session.tool_approvals.get(tool_name, False)` is `True`
3. explicitly deny skill-grant auto-approval for `run_shell_command`, even though its approval is raised inside the tool
4. keep the order:
   - eligible skill grant
   - remembered/session approval
   - user prompt
5. document that skill grants do not override the main approval model

Minimum acceptable rule for this delivery:

- skill grants may not bypass any approval gate — neither `requires_approval=True` at the registry level (step 2), nor `ApprovalRequired` raised inside the tool (step 3 / `run_shell_command`). Note: `run_shell_command` is registered `requires_approval=False` but raises `ApprovalRequired` internally, which pydantic-ai promotes into the deferred path — so step 2 alone does not protect it; step 3's explicit carve-out is required.

### Files

- `co_cli/context/_orchestrate.py`
- `co_cli/tools/shell.py`
- `docs/DESIGN-system.md`
- `docs/DESIGN-core-loop.md`
- `docs/DESIGN-tools.md`

### Test changes

Add functional coverage for:

- a skill grant not bypassing approval for `run_shell_command`
- a skill grant not bypassing any tool with `requires_approval=True`
- a skill grant still working for intended low-risk convenience tools

### Done when

- skill grants cannot auto-approve shell execution
- skill grants no longer act as a second approval system
- docs state the exact approval precedence implemented in `_collect_deferred_tool_approvals()`

---

## TASK-5 — Sync DESIGN docs to the actual runtime contracts

### Why

The code already has stronger contracts than the current wording in several places. This task is documentation sync, but it must be tied to exact live behavior.

### Processing change

Update canonical docs so they state these exact contracts:

- memory recall is read-only by default
- durable memory, transcript history, and session-summary artifacts are different things
- `make_subagent_deps()` shares `services` and `config` but resets `session` and `runtime`
- background tasks already exist and are bounded async execution, not hypothetical future capability
- MCP is additive
- approvals are orchestrator-owned
- skill grants are turn-scoped convenience only and cannot bypass protected approvals

Implement:

1. read each canonical DESIGN/research doc against the live code paths changed or validated in TASK-1 through TASK-4
2. replace any wording that still describes read-path memory mutation, blurred session-summary semantics, or future-only background-task capability
3. make the `make_subagent_deps()` isolation contract explicit in canonical docs
4. align approval-language wording with the actual orchestrator-owned flow after TASK-4 lands
5. keep MCP wording additive and operational rather than architectural

### Files

- `docs/DESIGN-system.md`
- `docs/DESIGN-core-loop.md`
- `docs/DESIGN-tools.md`
- `docs/reference/RESEARCH-co-agent-context.md`
- `docs/reference/RESEARCH-co-tools-skills-analysis.md`

### Done when

- none of the above docs contradict the shipped behavior from TASK-1 through TASK-4
- the memory/history/session-summary boundary appears in at least one canonical DESIGN doc and one research doc

---

## TASK-6 — Add forward-looking guardrails that match the current product stage

### Why

The repo already has research and design material that mentions future directions. Those docs need a local guardrail so future edits do not reintroduce overbuilt designs disconnected from the current code.

### Processing change

Add a short guardrail section to the relevant proposal/research docs stating:

- do not introduce multi-tier durable memory without new code evidence
- do not turn skills into a plugin runtime
- do not center the runtime on MCP
- do not add generic multi-agent orchestration layers before a concrete product requirement exists

This is a docs-only task, but it must be written against the live contracts established by TASK-1 through TASK-5.

### Files

- `docs/reference/RESEARCH-co-agent-context.md`
- `docs/reference/RESEARCH-co-tools-skills-analysis.md`

### Done when

- future proposal edits have an explicit local warning against over-engineering
- the guardrail language references the current code model rather than abstract architecture preference

---

## TASK-7 — Improve MCP operational legibility without adding runtime abstraction

### Why

The current alignment work correctly treats MCP as additive, but users still need a clearer operational picture of what MCP contributes in a live session:

- which MCP tools are connected
- which are approval-gated
- which are degraded or unavailable
- how MCP tool names appear in status and traces

This fits `co` because it strengthens inspectability and trust without making MCP the center of the runtime.

### Processing change

Improve user-visible MCP reporting rather than introducing an abstraction layer.

Implement:

1. audit how MCP-provided tools are surfaced in agent bootstrap, capability/status output, and traces
2. make connected MCP tool identity stable and user-legible in the relevant status surfaces
3. surface approval posture for MCP tools using the same vocabulary already used for native tools
4. surface degraded/unavailable MCP state explicitly rather than collapsing it into missing tools
5. sync DESIGN docs so MCP is described as additive capability with explicit operational visibility

Do not add a new MCP plugin/runtime layer in this delivery.

### Files

- `co_cli/agent.py`
- bootstrap/status rendering paths
- `docs/DESIGN-system.md`
- `docs/DESIGN-tools.md`

### Done when

- a user can tell which MCP tools are active, approval-gated, or degraded from normal product surfaces
- MCP tool naming is stable across status-oriented output
- docs describe MCP visibility as an operational contract, not a hidden implementation detail

---

## TASK-8 — Improve delegated-work observability without widening subagent power

### Why

`co` already has specialist delegation and already isolates child-agent deps correctly. What is missing is operational clarity:

- what the child was asked to do
- what budget or scope it ran under
- what context boundary applied
- where the returned result came from

This is a good fit for `co` because it improves inspectability around an existing feature rather than adding more delegation capability.

### Processing change

Add observability to the current delegation path before adding any new subagent behaviors.

Implement:

1. audit current delegation result/status surfaces in `delegation.py` and `_delegation_agents.py`
2. make delegated task scope and specialist role explicit in returned metadata/display
3. surface child-agent result provenance and any relevant request-budget information where the current implementation already tracks it
4. sync docs to describe exactly what delegated specialists inherit and what they do not inherit
5. keep the current isolation model; do not add broader child-agent permissions in this delivery

### Files

- `co_cli/tools/delegation.py`
- `co_cli/tools/_delegation_agents.py`
- `docs/DESIGN-system.md`
- `docs/DESIGN-tools.md`

### Done when

- delegated work is easier to inspect from status/result surfaces
- users can see the scope and provenance of delegated outputs
- docs describe the current delegation boundary without implying stronger subagent capabilities

---

## TASK-9 — Improve background-task visibility before any agentic async expansion

### Why

The code already has real bounded background execution. The next problem is not missing async machinery; it is weak visibility into:

- task lineage
- approval history
- persisted artifacts and outcomes
- the difference between current shell-task execution and any future agentic follow-up

This fits `co` because it strengthens a live feature users can already hit today.

### Processing change

Strengthen the current background-task harness rather than introducing autonomous workflows.

Implement:

1. audit what `task_control.py` and `_background.py` currently persist and display
2. improve task status output so lineage and outcome artifacts are easier to inspect
3. surface approval-relevant context where it already exists, without inventing a new approval system for tasks
4. sync docs so background tasks are described as bounded async subprocess work, not as hypothetical future capability
5. explicitly document the boundary between current background tasks and any future agentic async work

### Files

- `co_cli/tools/task_control.py`
- `co_cli/tools/_background.py`
- `docs/DESIGN-system.md`
- `docs/DESIGN-tools.md`

### Done when

- background-task status and outcomes are easier to inspect from the user surface
- docs clearly distinguish current bounded async execution from future agentic follow-up
- no new autonomous background-agent layer is introduced

---

## TASK-10 — Add explicit context-boundary docs for specialists and future background turns

### Why

The current code already has a concrete boundary model through `CoDeps` and `make_subagent_deps()`, but the contract is not yet stated in one place for readers and future implementers:

- what a delegated specialist inherits
- what it does not inherit
- what a future background agent turn would be allowed to see if such a feature is ever added

This is low-cost, high-value guardrail work that reduces future semantic drift.

### Processing change

Write the context-boundary contract against the current code model.

Implement:

1. document the current inheritance boundary for delegated specialists using the exact `services/config shared, session/runtime reset` contract
2. document the current opening-context and memory boundaries so specialist context is not described as implicit or unlimited
3. add an explicit note for future background turns stating that no broader inheritance model exists today
4. make proposal/research docs refer back to this concrete boundary rather than speculative multi-agent architecture

### Files

- `docs/DESIGN-system.md`
- `docs/DESIGN-core-loop.md`
- relevant research docs touched by TASK-5 and TASK-6

### Done when

- one canonical doc explains the active context boundary for delegated specialists
- future docs have a concrete local reference point for background-turn scope
- proposal language no longer implies hidden inheritance semantics that the code does not implement

## Validation

### Code validation

Run targeted functional tests for the changed paths:

- `tests/test_memory.py`
- `tests/test_commands.py`
- `tests/test_delegate_coder.py`
- any new tests added for skill-grant approval behavior
- any targeted tests added for delegation or background-task visibility changes

Use repo-required pytest logging under `.pytest-logs/`.

### Doc validation

Read updated docs side-by-side with:

- `co_cli/tools/memory.py`
- `co_cli/memory/_lifecycle.py`
- `co_cli/context/_history.py`
- `co_cli/context/_orchestrate.py`
- `co_cli/deps.py`
- `co_cli/tools/task_control.py`
- `co_cli/tools/_background.py`
- `co_cli/commands/_commands.py`
- `co_cli/tools/_tool_approvals.py`
- `co_cli/tools/shell.py`
- `co_cli/tools/delegation.py`
- `co_cli/tools/_delegation_agents.py`

## Shipping Order

Implement in this order:

1. TASK-4 skill-grant approval clamp
2. TASK-1 recall read-only fix
3. TASK-2 session-summary artifact typing
4. TASK-3 standing-context metadata and injection
5. TASK-5 DESIGN + research sync
6. TASK-6 proposal guardrails
7. TASK-7 MCP operational legibility
8. TASK-8 delegated-work observability
9. TASK-9 background-task visibility
10. TASK-10 context-boundary docs

Reason for order:

- TASK-4 closes the highest-risk trust gap
- TASK-1 and TASK-2 remove the clearest semantic mismatches
- TASK-3 builds on the now-cleaner memory model
- TASK-5 and TASK-6 lock in the cleaned-up contracts before follow-on visibility work
- TASK-7 through TASK-10 are fit-for-`co` follow-ons that improve inspectability without expanding architecture
