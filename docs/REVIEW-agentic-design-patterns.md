# REVIEW: Agentic Design Patterns — What Actually Applies to co-cli

This document reviews the agentic patterns that matter for the current `co-cli`
implementation.

The right frame for `co-cli` is:

- single-user, interactive, approval-first operator CLI
- one primary agent with read-only delegated sub-agents
- strong tool and context governance
- practical memory and retrieval, not autonomous long-running agency

That means some patterns from the book are central, some are partial, and some
are intentionally deferred.

## High-Value Patterns That Are Already Real

### 1. Tool Use Is the Core Pattern

This is the strongest area of the current design.

What exists now:

- The main agent is built with `pydantic-ai` and registers tools explicitly in
  `co_cli/agent.py`.
- Tool outputs follow the repo convention: user-facing tools return structured
  `dict` payloads with a `display` field.
- Approval is not embedded inside most tools; it is orchestrated through
  `DeferredToolRequests` in `co_cli/_orchestrate.py`.
- The tool surface is broad but coherent: shell, files, background tasks,
  memory, articles, web, Google, Obsidian, todo, capabilities, and delegation.

The tool layer is the product. The architecture is aligned with the book’s
recommendation to make tools the durable capability boundary.

### 2. ReAct-Style Execution Is the Main Runtime Pattern

The main loop is not an explicit planner/executor split, but it is a practical
ReAct system.

What exists now:

- The model runs, emits tool calls, receives observations, and continues until
  completion through `run_turn()` in `co_cli/_orchestrate.py`.
- `prompts/rules/05_workflow.md` explicitly instructs the model to decompose
  multi-step work into sub-goals and execute them in order.
- The orchestration layer supports repeated approval/resume cycles within a
  single turn.

`co-cli` does not have a separate planner agent, but it does use an agentic
action/observation loop. For this product, that is the correct default. A
heavier planning layer would add latency and complexity before it solves a real
problem.

### 3. Human-in-the-Loop Approval Is a First-Class Pattern

This is a defining design choice, not a side feature.

What exists now:

- Deferred approvals are handled centrally in `co_cli/_orchestrate.py`.
- Session-scoped tool approvals exist.
- Persistent shell approvals exist through `_exec_approvals.py`.
- Skills can grant turn-scoped approvals for listed tools.
- Persistent shell approvals exist through `_exec_approvals.py`.

Important nuance:

- The risk classifier (`_approval_risk.py`, `approval_risk_enabled`) was removed in the
  `approval-simplify` delivery (March 2026). Approval policy is now a deterministic
  three-tier chain: skill grants → session tool approvals → user prompt.

This is a canonical fit for the book’s human-in-the-loop pattern. The main
future improvement is not “add approvals” but “make risk-sensitive approval
smarter without weakening user control.”

### 4. Memory Retrieval Is Structural, Not Optional

What exists now:

- `inject_opening_context()` in `co_cli/_history.py` recalls memories on every
  new user turn and injects relevant results into the prompt.
- Project-local memories live in `.co-cli/memory/`.
- External reference material lives as articles in the user-global library.
- Retrieval is backed by grep, FTS5, or hybrid search depending on config and
  availability.

This is not merely “there is a memory tool.” Memory recall is part of prompt
construction. The design is closer to retrieval-augmented agent state than to a
manual note store.

### 5. Delegation Is Shipped

What exists now:

- `delegate_coder`
- `delegate_research`
- `delegate_analysis`

Each delegation path has a dedicated read-only sub-agent with an isolated
`CoDeps` session via `make_subagent_deps()` in `co_cli/deps.py`.

Current capability boundaries:

- coder: read-only file tools
- research: `web_search` and `web_fetch`
- analysis: `search_knowledge` and `search_drive_files`

Delegation is real, but intentionally narrow. This is the right level for
`co-cli`: focused specialist agents, not a supervisor managing a swarm.

### 6. Retrieval / RAG Is Strong

What exists now:

- `KnowledgeIndex` in `co_cli/knowledge_index.py` supports FTS5 BM25.
- Hybrid search is implemented with sqlite-vec.
- Weighted merge of text and vector scores is implemented.
- Reranking is implemented and can use local, Ollama, or Gemini providers.
- Articles, memories, Obsidian notes, and Drive docs share one search surface.

The current system is already beyond “basic RAG.” The most important remaining
gap is chunking strategy, not whether hybrid retrieval exists.

## Patterns That Are Partial but Valuable

### 7. Prompt Chaining Exists Implicitly, Not as a Formal Pipeline

`co-cli` does not currently serialize intermediate reasoning into structured
JSON stages the way a classic prompt-chaining pipeline would.

What does exist:

- workflow instructions that tell the model to decompose work
- delegated sub-agents with structured outputs
- approval/resume as a natural checkpoint boundary

The pattern is partially present, but as conversational orchestration rather
than a formal staged pipeline. That is appropriate today. A stricter chain only
becomes necessary if turns start mixing too many goals or if delegated work
becomes compositional.

### 8. Routing Exists in Narrow, Useful Places

There is no dedicated front-door intent router for the main chat loop.

What exists now:

- skill dispatch in slash-command flow
- optional approval risk routing
- explicit toolset boundaries for delegated agents
- prompt-level intent guidance in `05_workflow.md`

This is enough for the current product. A separate classifier in front of every
turn would likely be latency without enough gain.

### 9. Exception Handling and Recovery Are Good, but Still Uneven

What exists now:

- provider error classification and retry policy
- fallback model chains via `model_roles`
- dangling tool-call patching on interrupts
- doom-loop detection and shell reflection caps in `co_cli/_history.py`
- empty-result retry in `delegate_research`

The remaining weakness is that recovery logic is still pattern-specific. It is
not yet a generalized “critic and reformulate” recovery layer.

### 10. Reflection Is Present as Safety Injection, Not as a Critic Loop

There is still no true producer-critic-revise loop for normal task execution.

What exists now:

- shell reflection caps
- loop detection
- personality critique injection

These are guardrails, not real reflection. A dedicated critic pass would likely
pay off first in two places: shell-command proposals and memory-save quality.

## Patterns Still Deferred

### 11. Parallel Execution Is Not a Real Runtime Pattern Yet

Even though delegation is shipped, the system does not orchestrate independent
tool calls or sub-agents concurrently.

This remains deferred. The current bottleneck is usually model latency and
approval flow, not Python concurrency. Parallelism becomes worth it only when
the product starts composing multiple independent evidence-gathering branches in
one task.

### 12. MCP Is Implemented as a Client, Not a Platform

What exists now:

- MCP server configuration in settings
- stdio plus HTTP transports
- approval inheritance into MCP tool usage

What does not exist:

- exposing `co-cli` itself as an MCP server

Client support is enough for current scope. Server mode is ecosystem expansion,
not a pressing product need.

## Concrete Gaps That Actually Matter

These are the improvements most justified by the current codebase and by the
book’s patterns.

### 1. Add a Real Critic Loop for High-Risk Actions

Best target:

- shell command review before execution or approval

Why:

- `co-cli` already has approvals, shell policy, and risk buckets
- a critic layer would improve quality where mistakes are expensive

### 2. Improve Memory and Article Quality with Better Retrieval Units

Best target:

- chunk long articles before indexing and retrieval

Why:

- current search is strong, but full-document indexing limits precision on long
  references
- this matters more now that hybrid search and reranking already exist

### 3. Make Recovery More Deliberate

Best target:

- reformulate-and-retry for failed searches, empty recalls, and weak evidence

Why:

- isolated examples exist already, especially in `delegate_research`
- turning that into a shared recovery pattern would raise overall reliability

### 4. Keep Delegation Narrow Until There Is a Proven Need for a Supervisor

Why:

- the current read-only specialist agents are understandable and safe
- a larger multi-agent supervisor pattern would add coordination overhead and
  harder-to-debug failure modes

## Bottom Line

The most important correction is this: `co-cli` is no longer a mostly
single-agent tool-calling CLI with a few emerging agentic ideas. It already has
a clear agentic architecture:

- ReAct-style main loop
- structured tool protocol
- approval-centered execution
- automatic memory injection
- focused delegated sub-agents
- hybrid retrieval with reranking

The biggest missing pattern is not planning, routing, or “more agents.” It is
targeted reflection: a critic loop for risky actions and weak retrieval paths.

That is where the next design-pattern investment is most likely to pay off.
