# RESEARCH: co-tools-skills-analysis

Status: shipped-foundations

Aspect: tool harness, skills, MCP, delegation
Pydantic-AI patterns: tool registration, deferred approvals, structured outputs, toolsets, isolated sub-agents

This note covers the harness side of `co`: native tools, MCP, slash-command skills, delegated subagents, and how those surfaces should evolve without over-engineering the system.

The companion context paper is [RESEARCH-co-agent-context-gap.md](/Users/binle/workspace_genai/co-cli/docs/reference/RESEARCH-co-agent-context-gap.md). That document owns the context model. This document owns harness implications and pragmatic adoption.

This revision is grounded in a deeper scan of current `co` code plus current local heads of the main reference repos:

- `codex` `a4a9536fd`
- `claude-code` `f6dbf44`
- `gemini-cli` `88638c14f`
- `aider` `861a1e4d1`
- `openclaw` `62d5df28d`
- `letta` `4cb2f21`
- `mem0` `21df43c6`
- `opencode` `7291e2827`
- `nanobot` `b957dbc`

## Executive Summary

`co` is already aligned with several strong current patterns:

- explicit native tool registration with approval metadata in `co_cli/agent.py`
- a real approval loop in `co_cli/context/_orchestrate.py`
- grouped runtime deps via `CoDeps`
- markdown-backed skill overlays dispatched as slash commands
- isolated read-only subagents via `make_subagent_deps()`
- MCP toolset attachment as an extension surface rather than a second agent architecture

The main opportunity is not "more tools" or "more agent orchestration." The opportunity is to sharpen contracts so the system stays legible as it grows:

1. keep native tools first-class and boring
2. keep skills lightweight and turn-scoped
3. keep subagents isolated and purpose-built
4. add scope and observability before adding autonomy

## Current Code Reality

The harness today is spread across a small number of decisive seams:

- `co_cli/agent.py`
  - registers native tools with explicit `requires_approval`
  - attaches MCP toolsets
  - exposes available skills to the model as a prompt-layer list
- `co_cli/context/_orchestrate.py`
  - handles deferred approvals and resume flow
  - respects session approvals and explicit user prompts
- `co_cli/commands/_commands.py`
  - dispatches built-in slash commands first
  - resolves skill invocations after built-ins
  - applies turn-scoped env vars and skill identity only
- `co_cli/deps.py`
  - provides grouped shared state and subagent isolation
- `co_cli/tools/delegation.py` and `co_cli/tools/_delegation_agents.py`
  - define focused read-only subagents for coding, research, and analysis
- `co_cli/tools/task_control.py` and `co_cli/tools/_background.py`
  - provide filesystem-backed background task execution, status, cancellation, and retention cleanup

That is already a coherent harness. It is simpler and more maintainable than many peer systems because it does not mix every capability into a generic agent-runtime abstraction.

## Converged Best Practice

Across strong peer systems, the main harness patterns that actually converge are:

- explicit execution boundaries matter more than tool count
- approvals should be inspectable and low-friction
- specialist agents should have scoped prompts, scoped tools, and isolated state
- extension surfaces should be additive, not destabilizing
- workflow overlays should stay editable and legible

`co` already follows most of these. The practical question is where to tighten.

The current repo heads sharpen that picture:

- `codex` is still iterating on sandbox, MCP approvals, and command-safety surfaces
- `claude-code` keeps fixing permission rule matching, always-allow correctness, subagent permission edges, and memory isolation bugs
- `gemini-cli` continues pushing policy-engine, sandbox, MCP, and A2A seams together
- `openclaw` keeps hardening ACP, spawn depth/lineage, sandbox inheritance, and session/compaction recovery
- `opencode` remains a strong signal for polished session/workspace routing, while explicitly declaring that permissions are not a sandbox
- `nanobot` keeps proving that heartbeat/cron/channel growth can stay lightweight if the runtime contracts are simple

## Tool Model Assessment

### Native tools

The native tool design is one of `co`'s strongest choices.

Why it works:

- tool registration is explicit
- mutation tools are marked for approval at registration time
- read-only tools stay approval-free
- tool visibility is model-visible and session-visible
- the approval loop lives outside tools, which keeps tool code simpler

This is exactly the kind of non-over-engineered design `co` should keep. The system does not need a heavier capability framework unless a concrete failure mode demands it.

Relative to current peers, this is still the right baseline:

- keep native tools as the default path, not plugins first
- keep approval metadata explicit at registration time
- keep tool return contracts boring and uniform

### MCP as extension surface

MCP support is also positioned correctly.

`co` treats MCP as an extension surface attached at agent construction, not as the center of the runtime. That is pragmatic. It allows tool expansion without forcing the entire local operator model to depend on MCP semantics.

The current main need is better operational clarity, not more abstraction:

- clearer visibility into which MCP tools are connected
- stable naming and approval behavior
- clear degradation when a server is unavailable

Current peer movement strengthens this recommendation. `codex`, `gemini-cli`, `claude-code`, `openclaw`, and `nanobot` are all still investing in MCP or adjacent protocol surfaces, but the recurring failure modes are operational:

- trust on first use
- approval shape
- compatibility drift
- tool naming and visibility
- degraded behavior when servers are unavailable or partially supported

For `co`, that means MCP should stay additive and legible. It should not become the hidden center of the runtime.

### Approval chain

The approval model is strong because it is layered but still understandable:

- shell policy can deny, allow, or defer
- tool-level `requires_approval` flags drive deferred approval
- session approvals can persist user trust decisions

This is close to the right tradeoff. The frontier lesson is not to add more approval states. It is to keep the trust UX predictable.

The repo-head scan reinforces that this is where systems keep paying tax:

- `codex` just added an "always allow" path for MCP tool calls
- `claude-code` changelog still shows steady work on wildcard matching, heredocs, multiline commands, settings hierarchy, and permission-prompt correctness
- `opencode` explicitly states that permissions are a UX boundary, not a sandbox

So the best adoption target for `co` is not more permission complexity. It is fewer surprising edge cases and clearer persistence behavior.

## Skills Model Assessment

### What the system is today

Skills in `co` are not a full agent framework. They are markdown-backed slash-command overlays with:

- a name and description
- a body injected as the synthetic user turn
- optional env vars
- optional turn-scoped skill identity

This is a strong design choice.

It preserves:

- editability
- inspectability
- low implementation complexity
- explicit user invocation

It also avoids a common failure mode in agent systems: hiding too much task logic inside opaque autonomous planners.

This now looks even more defensible against the current reference set. `claude-code` and `letta` both lean on editable instruction artifacts and scoped specialist overlays; `nanobot` proves file-backed operational prompts can scale surprisingly far when the contract stays simple.

### What should improve

The next improvements should stay within the current shape:

- make skill metadata and constraints more explicit
- keep built-in commands higher priority than skills
- keep skill side effects strictly turn-scoped
- improve visibility into which skills changed after reload

What `co` should not do yet:

- do not turn skills into a large plugin runtime
- do not let skills accumulate durable hidden state
- do not make skills a second approval system

One concrete warning from the peer scan: `claude-code` has had to fix cases where skill-allowed tools accidentally bypassed prompts. `co` should keep treating skill grants as narrow turn-scoped convenience, never as a parallel security model.

## Subagent Model Assessment

### What is already right

The delegation design is pragmatically strong:

- subagents are purpose-built, not generic
- each one has a narrow tool surface
- `make_subagent_deps()` gives them fresh session and runtime state
- they remain read-only specialists rather than semi-autonomous operators

This is a better current fit for `co` than broader multi-agent orchestration systems.

That judgment is stronger after the repo-head scan:

- `openclaw` keeps spending real complexity budget on spawn lineage, ACP bridge behavior, and sandbox inheritance
- `claude-code` keeps fixing subagent permission and memory-isolation edges
- `nanobot` keeps background subagents simple by limiting their tool surfaces and announcement pattern

Those are all reminders that subagent systems get expensive fast. `co`'s current read-only specialist shape is still the right level.

### What should improve next

The next step is sharper scope, not more autonomy:

- make specialist context visibility more explicit
- add better observability for delegated work
- keep request budgets tight and legible
- add spawn or chaining controls if delegation expands

`co` should resist copying framework-heavy multi-agent patterns before it needs them. The current subagent model is good because it is boring, bounded, and debuggable.

## Background Workflows

`co` already has a real background-task harness:

- `start_background_task`
- `check_task_status`
- `cancel_background_task`
- `list_background_tasks`
- filesystem-backed retention and orphan recovery in `TaskRunner`

That means `co` is no longer purely synchronous. It already has the beginnings of bounded asynchronous execution.

The gap is that these background runs are still shell-task oriented rather than agent-workflow oriented. Compared with the current heads:

- `nanobot` offers a lightweight file-backed heartbeat and cron model
- `openclaw` keeps adding follow-up, queued work, and session recovery semantics
- `gemini-cli` keeps exploring A2A and executor-style orchestration

The right adoption for `co` is still restrained:

- improve observability and artifact legibility for background work
- add bounded agentic follow-up only when a concrete use case demands it
- keep queueing and recovery simpler than the larger systems

## Updated Gap Summary

Relative to the current reference heads, the main harness gaps are now:

- MCP operational legibility: connected tools, naming, approval expectations, degraded mode
- approval edge-case hardening: multiline commands, remembered approvals, and skill-grant boundaries
- delegated-work observability: clearer status, lineage, and context visibility
- bounded asynchronous agent workflows: beyond shell background jobs, but still inspectable
- session/workspace routing polish: more in the direction of `opencode`, without adopting its broader product surface

## Pragmatic Adoption For `co`

### P0: keep the harness simple and explicit

- preserve native `agent.tool()` registration as the main capability path
- preserve orchestration-owned approvals rather than moving approval logic into tools
- preserve `make_subagent_deps()` isolation semantics
- preserve skills as slash-command overlays rather than autonomous background planners

### P1: improve trust and visibility

- expose clearer status for connected MCP tools and approval requirements
- make active skill grants more visible in status and traces
- tighten documentation around native tool classes: read-only, mutating, delegated, extension
- make background task lineage and approval records easier to inspect from the user surface

### P2: extend only where the product is already pulling

- if longer-running workflows grow, improve queueing and observability first
- if subagent usage grows, add stricter scope controls before adding more specialist types
- if skills become more important, improve validation and metadata before adding lifecycle machinery
- if MCP usage grows, improve compatibility reporting and trust-on-first-use before adding more wrappers

## Guardrails

The following directions are prohibited for `co` unless new code evidence explicitly justifies them:

- **No multi-tier durable memory without new code evidence**: do not add tiered or layered memory storage systems before a concrete failure mode in the current flat substrate is demonstrated in code.
- **No MCP-centric runtime**: MCP must remain an additive extension surface attached at agent construction. Do not restructure the agent runtime so that MCP toolsets become the primary capability path or replace native tool registration.
- **No generic multi-agent orchestration without a concrete product requirement**: do not introduce a general-purpose agent-spawning, task-routing, or orchestration framework. Subagents must remain purpose-built read-only specialists tied to a specific product use case.

## Strategic Conclusion

The harness does not need a reinvention. It needs disciplined evolution.

The converged best-practice shape for `co` is:

- native tools as the primary capability surface
- MCP as a bounded extension surface
- approvals owned by orchestration
- skills as explicit markdown overlays
- subagents as isolated, read-only specialists

That is consistent with `co`'s product identity: a pragmatic local operator with explicit trust boundaries, not a maximal agent framework.
